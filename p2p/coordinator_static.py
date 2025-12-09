#!/usr/bin/env python3
"""
coordinator_static.py — web coordinator with per-strand SubCoordinator support.

- /register: register peer into strand (creates SubCoordinator if needed)
- /lookup: lookup peer info
- /peers: list peers (grouped by strand)
- /sc/{strand}/{name}: WebSocket endpoint for strand-specific signaling handled by SubCoordinator
- /ws/{name}: legacy global websocket (kept for compatibility)
- serves ./www/ static files (peer.html / peer.js)
"""
import asyncio
import json
import os
import signal
from aiohttp import web, WSMsgType
import aiohttp_cors

HOST = "0.0.0.0"
PORT = 9000
BASE_DIR = os.path.dirname(__file__)
WWW_DIR = os.path.join(BASE_DIR, "www")

LOCK = asyncio.Lock()
routes = web.RouteTableDef()

# Global flat storage (for global discovery)
PEERS = []         # list of entries: {name, port, ctrl, strand, _ws?}
PEER_MAP = {}      # name -> entry
SUBCOORDS = {}     # strand -> SubCoordinator instance


def make_entry(name, port, ctrl, strand):
    return {"name": name, "port": port, "ctrl": ctrl, "strand": strand, "_ws": None}


class SubCoordinator:
    """
    Manages a single strand:
      - ordered entries list for that strand
      - notifies previous peer (UPDATE_NEXT) on registration
      - broadcasts NEW_PEER within strand
      - handles ws signaling routing for peers in this strand
    """
    def __init__(self, strand_id):
        self.strand = strand_id
        self.entries = []   # ordered entries in this strand
        self.map = {}       # name -> entry (same object as global PEER_MAP points to)
        self.lock = asyncio.Lock()

    async def add_entry(self, entry):
        async with self.lock:
            if entry["name"] in self.map:
                # Already present — maybe re-registration; update
                return self.map[entry["name"]]
            self.entries.append(entry)
            self.map[entry["name"]] = entry
            # notify previous in strand about next
            prev = self.entries[-2] if len(self.entries) > 1 else None
            if prev and prev.get("_ws"):
                try:
                    await prev["_ws"].send_json({
                        "type": "UPDATE_NEXT",
                        "next_name": entry["name"],
                        "next_port": entry["port"],
                        "strand": self.strand
                    })
                    print(f"[SubCoord:{self.strand}] Sent UPDATE_NEXT to {prev['name']} -> {entry['name']}")
                except Exception as e:
                    print(f"[SubCoord:{self.strand}] failed to notify prev: {e}")

            # notify all existing peers in this strand about the new peer (NEW_PEER)
            for p in self.entries:
                if p is entry:
                    continue
                if p.get("_ws"):
                    try:
                        await p["_ws"].send_json({
                            "type": "NEW_PEER",
                            "name": entry["name"],
                            "port": entry["port"],
                            "ctrl": entry["ctrl"],
                            "strand": self.strand
                        })
                        print(f"[SubCoord:{self.strand}] Sent NEW_PEER to {p['name']} about {entry['name']}")
                    except Exception:
                        pass
            return entry

    async def set_ws_for(self, name, ws):
        """
        Associate websocket with this entry. If entry does not exist yet, create a placeholder.
        When WS connects, send current view (peers list and next if any).
        """
        async with self.lock:
            entry = self.map.get(name)
            if not entry:
                # create placeholder entry in this strand (no port/ctrl yet)
                entry = make_entry(name, None, None, self.strand)
                self.entries.append(entry)
                self.map[name] = entry
                PEERS.append(entry)
                PEER_MAP[name] = entry
            entry["_ws"] = ws

            # send initial peer list for this strand
            try:
                peers_list = [{"name": p["name"], "port": p["port"], "ctrl": p["ctrl"]} for p in self.entries]
                await ws.send_json({"type": "STRAND_STATE", "strand": self.strand, "peers": peers_list})
            except Exception:
                pass

            # Send UPDATE_NEXT to this ws if it has a previous (inform what its next is if any)
            idx = self.entries.index(entry)
            if idx < len(self.entries) - 1:
                next_ent = self.entries[idx + 1]
                if next_ent.get("name"):
                    try:
                        await ws.send_json({"type": "UPDATE_NEXT", "next_name": next_ent["name"], "next_port": next_ent["port"], "strand": self.strand})
                    except Exception:
                        pass

            return entry

    async def clear_ws_for(self, name, ws):
        async with self.lock:
            entry = self.map.get(name)
            if entry and entry.get("_ws") is ws:
                entry["_ws"] = None

    async def route_message(self, from_name, obj):
        """
        Route a signaling object within this strand. obj should have 'to' property.
        """
        to = obj.get("to")
        if not to:
            return False
        async with self.lock:
            target = self.map.get(to)
            if target and target.get("_ws"):
                try:
                    await target["_ws"].send_json(obj)
                    return True
                except Exception:
                    return False
            else:
                return False


# ---------- HTTP endpoints ----------
@routes.post("/register")
async def register(request):
    data = await request.json()
    name = data.get("name")
    port = int(data.get("port")) if data.get("port") is not None else None
    ctrl = int(data.get("ctrl")) if data.get("ctrl") is not None else None
    strand = data.get("strand") or "default"

    async with LOCK:
        if not name:
            name = f"peer{len(PEERS)+1}"
        entry = PEER_MAP.get(name)
        if entry:
            # update existing entry's port/ctrl/strand if changed
            entry["port"] = port
            entry["ctrl"] = ctrl
            # if strand changed, remove from old subcoord and add to new
            if entry.get("strand") != strand:
                old_strand = entry.get("strand")
                if old_strand in SUBCOORDS:
                    sc = SUBCOORDS[old_strand]
                    # remove from old list if present
                    try:
                        sc.entries.remove(entry)
                        sc.map.pop(name, None)
                    except Exception:
                        pass
                entry["strand"] = strand
        else:
            entry = make_entry(name, port, ctrl, strand)
            PEERS.append(entry)
            PEER_MAP[name] = entry

        # ensure subcoordinator exists for this strand
        sc = SUBCOORDS.get(strand)
        if not sc:
            sc = SubCoordinator(strand)
            SUBCOORDS[strand] = sc
            print(f"[Coordinator] Created SubCoordinator for strand '{strand}'")

        # register with subcoordinator (handles UPDATE_NEXT and NEW_PEER within strand)
        await sc.add_entry(entry)
        prev = None
        # compute prev for return
        async with sc.lock:
            if len(sc.entries) > 1:
                prev = sc.entries[-2]
        print(f"[Coordinator] REGISTER {name} port={port} ctrl={ctrl} strand={strand}")
        return web.json_response(prev or {})


@routes.post("/lookup")
async def lookup(request):
    data = await request.json()
    name = data.get("name")
    return web.json_response(PEER_MAP.get(name, {}) or {})


@routes.get("/peers")
async def peers_list(request):
    """
    Returns: { peers: [...], strands: { strand_id: [...] } }
    """
    try:
        flat = [{"name": p["name"], "port": p["port"], "ctrl": p["ctrl"], "strand": p.get("strand", "default")} for p in PEERS]
        strands = {}
        for sid, sc in SUBCOORDS.items():
            async def mklist(sco):
                return [{"name": p["name"], "port": p["port"], "ctrl": p["ctrl"]} for p in sco.entries]
            # make shallow sync-safe copy
            strands[sid] = [{"name": p["name"], "port": p["port"], "ctrl": p["ctrl"]} for p in sc.entries]
        return web.json_response({"peers": flat, "strands": strands})
    except Exception as e:
        print("[Coordinator] peers_list error:", e)
        return web.json_response({"peers": [], "strands": {}})


# ---------- SubCoordinator WebSocket endpoint ----------
@routes.get("/sc/{strand}/{name}")
async def subcoord_ws_handler(request):
    strand = request.match_info["strand"]
    name = request.match_info["name"]
    sc = SUBCOORDS.get(strand)
    # If no subcoordinator exists yet for the strand, create it (this can happen if someone connects before POST /register)
    if not sc:
        sc = SubCoordinator(strand)
        SUBCOORDS[strand] = sc
        print(f"[Coordinator] Lazy-created SubCoordinator for strand '{strand}' (via WS connect)")

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    # Associate this ws with entry in subcoordinator
    await sc.set_ws_for(name, ws)
    print(f"[SubCoord:{strand}] WS CONNECT {name}")

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    obj = json.loads(msg.data)
                except Exception:
                    await ws.send_json({"status": "bad-json"})
                    continue

                # If message includes a 'to' field, forward inside this subcoordinator
                to = obj.get("to")
                if to:
                    ok = await sc.route_message(name, obj)
                    if ok:
                        await ws.send_json({"status": "routed", "to": to})
                    else:
                        await ws.send_json({"status": "unroutable", "to": to})
                else:
                    # echo ack
                    await ws.send_json({"status": "ok", "received": obj})
            elif msg.type == WSMsgType.ERROR:
                print(f"[SubCoord:{strand}] WS error for {name}: {ws.exception()}")
    except Exception as e:
        print(f"[SubCoord:{strand}] WS loop exception for {name}:", e)
    finally:
        await sc.clear_ws_for(name, ws)
        print(f"[SubCoord:{strand}] WS DISCONNECT {name}")
    return ws


# ---------- Legacy global WS handler for backward compatibility ----------
@routes.get("/ws/{name}")
async def websocket_handler(request):
    name = request.match_info["name"]
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    async with LOCK:
        entry = PEER_MAP.get(name)
        if not entry:
            entry = make_entry(name, None, None, "default")
            PEERS.append(entry)
            PEER_MAP[name] = entry
        entry["_ws"] = ws
    print(f"[Coordinator] WS CONNECT {name}")

    async def safe_send_to(target_name, obj):
        target = PEER_MAP.get(target_name)
        if target and target.get("_ws"):
            try:
                await target["_ws"].send_json(obj)
                return True
            except:
                return False
        return False

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    obj = json.loads(msg.data)
                except Exception:
                    await ws.send_json({"status":"bad-json"})
                    continue

                to = obj.get("to")
                if to:
                    ok = await safe_send_to(to, obj)
                    if ok:
                        await ws.send_json({"status":"routed","to":to})
                    else:
                        await ws.send_json({"status":"unroutable","to":to})
                else:
                    await ws.send_json({"status":"ok","received": obj})
            elif msg.type == WSMsgType.ERROR:
                print(f"[Coordinator] WS error for {name}: {ws.exception()}")
    except Exception as e:
        print("[Coordinator] WS loop exception:", e)
    finally:
        async with LOCK:
            ent = PEER_MAP.get(name)
            if ent and ent.get("_ws") is ws:
                ent["_ws"] = None
        print(f"[Coordinator] WS DISCONNECT {name}")
    return ws


# ---------- Static files ----------
@routes.get("/{tail:.*}")
async def static_handler(request):
    path = request.match_info['tail'] or "peer.html"
    if path == "":
        path = "peer.html"
    safe_path = os.path.normpath(os.path.join(WWW_DIR, path))
    if not safe_path.startswith(os.path.abspath(WWW_DIR)):
        return web.Response(status=403, text="Forbidden")
    if not os.path.exists(safe_path):
        return web.Response(status=404, text="Not found")
    return web.FileResponse(safe_path)


# ---------- App init ----------
def init_app():
    app = web.Application()
    app.add_routes(routes)
    cors = aiohttp_cors.setup(app)
    for r in list(app.router.routes()):
        cors.add(r)
    return app


# ---------- Run ----------
async def start():
    app = init_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    print(f"[Coordinator] Running on http://{HOST}:{PORT}  (serving {WWW_DIR})")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal():
        loop.call_soon_threadsafe(stop.set)

    try:
        loop.add_signal_handler(signal.SIGINT, _on_signal)
        loop.add_signal_handler(signal.SIGTERM, _on_signal)
    except (NotImplementedError, AttributeError):
        import signal as _sig
        _sig.signal(_sig.SIGINT, lambda *_: _on_signal())
        _sig.signal(_sig.SIGTERM, lambda *_: _on_signal())

    await stop.wait()
    print("[Coordinator] Shutdown...")
    await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        print("[Coordinator] Interrupted, exiting.")

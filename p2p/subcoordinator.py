#!/usr/bin/env python3
"""
subcoordinator.py — per-strand process (verbose, safer).

Run like:
  python3 subcoordinator.py --port 10080 --strand alpha

Provides:
 - POST /register  {name, port, ctrl} -> returns prev peer in this strand
 - GET  /peers
 - GET  /health
 - WebSocket /ws/{name} for signaling inside this strand
This variant logs handshake attempts and errors so connection problems are visible.
"""
import argparse
import asyncio
import json
import os
import traceback
from aiohttp import web, WSMsgType
import aiohttp_cors
import sys

routes = web.RouteTableDef()

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--strand", type=str, default="default")
args = parser.parse_args()

PORT = args.port
STRAND = args.strand

PEERS = []   # ordered list of entries {name, port, ctrl, _ws?}
PEER_MAP = {}
LOCK = asyncio.Lock()

# Store latest frame for automatic delivery
LATEST_FRAMES = {}  # STRAND -> bytes

def make_entry(name, port, ctrl):
    return {"name": name, "port": port, "ctrl": ctrl, "_ws": None}

# --- Helpers for logging ---
def log(*parts):
    print(f"[SubCoord:{STRAND}]", *parts)
    sys.stdout.flush()

def log_exc(prefix=""):
    print(f"[SubCoord:{STRAND}]", prefix)
    traceback.print_exc()
    sys.stdout.flush()

async def broadcast_frame(frame_bytes):
    """Send frame to all connected peers in this strand."""
    async with LOCK:
        for entry in PEER_MAP.values():
            ws = entry.get("_ws")
            if ws is not None:
                try:
                    await ws.send_bytes(frame_bytes)
                except Exception as e:
                    log(f"Failed to send frame to {entry['name']}: {e}")

async def broadcast_message(strand, msg, sender=None):
    """Send JSON message to all peers in the strand except sender."""
    async with LOCK:
        for entry in PEER_MAP.values():
            ws = entry.get("_ws")
            if ws is not None and ws != sender:
                try:
                    await ws.send_json(msg)
                except Exception as e:
                    log(f"Failed to send message to {entry['name']}: {e}")

# --- HTTP routes ---

@routes.post("/register")
async def register(request):
    try:
        data = await request.json()
    except Exception as e:
        log("register: invalid json body:", e)
        return web.json_response({"error": "invalid json"}, status=400)

    name = data.get("name")
    port = data.get("port")
    ctrl = data.get("ctrl")

    try:
        async with LOCK:
            if not name:
                name = f"peer{len(PEERS)+1}"
            entry = PEER_MAP.get(name)
            if entry:
                entry.update({"port": port, "ctrl": ctrl})
            else:
                entry = make_entry(name, port, ctrl)
                PEERS.append(entry)
                PEER_MAP[name] = entry

            # --- Notify all existing peers of the new peer for auto-connect ---
            for p in PEER_MAP.values():
                ws = p.get("_ws")
                if ws and p["name"] != name:
                    try:
                        await ws.send_json({
                            "type": "NEW_PEER",
                            "name": name,
                            "port": port,
                            "ctrl": ctrl,
                            "strand": STRAND
                        })
                    except Exception as e:
                        log(f"Failed to notify {p['name']} of new peer {name}: {e}")

            # prev peer (if any)
            prev = PEERS[-2] if len(PEERS) > 1 else {}
            if prev and prev.get("_ws"):
                try:
                    await prev["_ws"].send_json({
                        "type": "UPDATE_NEXT",
                        "next_name": name,
                        "next_port": port,
                        "strand": STRAND
                    })
                except Exception as e:
                    log("failed notify prev:", e)

        # success — return prev info (safe for JSON)
        try:
            if prev:
                safe_prev = {k: v for k, v in prev.items() if k != "_ws"}
            else:
                safe_prev = {}
            return web.json_response(safe_prev)
        except Exception:
            log_exc("Unexpected error in /register (returning prev)")
            return web.json_response({"error": "internal error"}, status=500)

    except Exception:
        log_exc("Unexpected error in /register")
        return web.json_response({"error": "internal error"}, status=500)

@routes.get("/peers")
async def peers_list(request):
    try:
        return web.json_response({"peers": [{"name": p["name"], "port": p.get("port"), "ctrl": p.get("ctrl")} for p in PEERS]})
    except Exception:
        log_exc("peers_list error")
        return web.json_response({"peers": []})

@routes.get("/health")
async def health(request):
    return web.json_response({"status": "ok", "strand": STRAND})

# --- WebSocket handler ---

@routes.get("/ws/{name}")
async def ws_handler(request):
    name = request.match_info["name"]
    peer_addr = request.remote or "unknown"
    log(f"WS connect attempt for '{name}' from {peer_addr}")
    ws = web.WebSocketResponse()
    try:
        await ws.prepare(request)
    except Exception as e:
        log("WebSocket handshake failed for", name, "-", e)
        try:
            await ws.close()
        except Exception:
            pass
        return ws

    # Associate ws with entry
    async with LOCK:
        entry = PEER_MAP.get(name)
        if not entry:
            entry = make_entry(name, None, None)
            PEERS.append(entry)
            PEER_MAP[name] = entry
        entry["_ws"] = ws

    log(f"WS CONNECT {name} (remote={peer_addr})")

    # --- Send latest frame immediately to new peer ---
    frame = LATEST_FRAMES.get(STRAND)
    if frame is not None:
        try:
            await ws.send_bytes(frame)
        except Exception as e:
            log(f"Failed to send initial frame to {name}: {e}")

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
                    target = PEER_MAP.get(to)
                    if target and target.get("_ws"):
                        try:
                            await target["_ws"].send_json(obj)
                            await ws.send_json({"status":"routed","to":to})
                        except Exception as e:
                            log("Forward failed:", e)
                            await ws.send_json({"status":"failed","error": str(e)})
                    else:
                        await ws.send_json({"status":"unroutable","to":to})
                else:
                    # Broadcast message to all in the strand
                    await broadcast_message(STRAND, obj, sender=ws)

            elif msg.type == WSMsgType.BINARY:
                # Optional: treat binary frames as webcam frames
                # Update latest frame and broadcast to all peers
                LATEST_FRAMES[STRAND] = msg.data
                await broadcast_frame(msg.data)

            elif msg.type == WSMsgType.ERROR:
                log(f"WS error for {name}: {ws.exception()}")
    except Exception as e:
        log_exc(f"WS loop exception for {name}: {e}")
        try:
            await ws.close()
        except Exception:
            pass
    finally:
        async with LOCK:
            ent = PEER_MAP.get(name)
            if ent and ent.get("_ws") is ws:
                ent["_ws"] = None
        log(f"WS DISCONNECT {name}")
    return ws

# --- App setup ---

def init_app():
    app = web.Application()
    app.add_routes(routes)
    cors = aiohttp_cors.setup(app)
    for r in list(app.router.routes()):
        try:
            cors.add(r)
        except Exception:
            pass
    return app

async def start():
    app = init_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log(f"running on port {PORT}")
    log("registered routes:")
    for r in list(app.router.routes()):
        log(" -", r)

    # Run forever (Windows-safe: avoids add_signal_handler oddities for subprocesses)
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass

    log("shutdown")
    await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        print("Interrupted")

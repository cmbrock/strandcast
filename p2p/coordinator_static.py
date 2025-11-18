#!/usr/bin/env python3
"""
coordinator_static.py
aiohttp-based static file + signaling server for StrandCast.
Serves ./www/ (peer.html + peer.js), provides /register, /lookup, /peers,
and WebSocket /ws/{name} for signaling and control.

This version is cross-platform and avoids using loop.add_signal_handler directly
on platforms (like Windows) where it's not implemented.
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

PEERS = []     # ordered list of peer dicts {name, port, ctrl, _ws?}
PEER_MAP = {}
LOCK = asyncio.Lock()
routes = web.RouteTableDef()

# ---------------------------
# Safe peer list
# ---------------------------
async def list_peers_safe():
    try:
        raw = json.dumps([p['name'] for p in PEERS])
        start = raw.find('[')
        end = raw.rfind(']') + 1
        if start == -1 or end == -1:
            print("[WARN] No valid JSON found in peer list")
            return []
        return json.loads(raw[start:end])
    except Exception as e:
        print("[ERROR] list_peers_safe failed:", e)
        return []

# ---------------------------
# Register user
# ---------------------------
@routes.post("/register")
async def register(request):
    data = await request.json()
    name = data.get("name")
    port = int(data.get("port"))
    ctrl = int(data.get("ctrl"))
    async with LOCK:
        if not name:
            name = f"peer{len(PEERS)+1}"
        entry = {"name": name, "port": port, "ctrl": ctrl}
        PEERS.append(entry)
        PEER_MAP[name] = entry
        prev = PEERS[-2] if len(PEERS) > 1 else {}
        print(f"[Coordinator] REGISTER {name} (port={port} ctrl={ctrl})")

        # Notify previous peer about next peer
        if prev:
            ws = prev.get("_ws")
            if ws:
                try:
                    await ws.send_json({"type":"UPDATE_NEXT","next_name":name,"next_port":port})
                    print(f"[Coordinator] Sent UPDATE_NEXT to {prev['name']} -> {name}:{port}")
                except Exception as e:
                    print("[Coordinator] failed notify prev:", e)

        # Notify all peers about new peer
        for p in PEERS:
            if p.get("_ws") and p["name"] != name:
                try:
                    await p["_ws"].send_json({"type":"NEW_PEER","name":name,"port":port,"ctrl":ctrl})
                    print(f"[Coordinator] Sent NEW_PEER to {p['name']}")
                except Exception:
                    pass

    return web.json_response(prev or {})

# ---------------------------
# Lookup user
# ---------------------------
@routes.post("/lookup")
async def lookup(request):
    data = await request.json()
    name = data.get("name")
    return web.json_response(PEER_MAP.get(name, {}) or {})

# ---------------------------
# List peers
# ---------------------------
@routes.get("/peers")
async def peers_list(request):
    peers_safe = await list_peers_safe()
    return web.json_response({"peers": peers_safe})

# ---------------------------
# WebSocket handler
# ---------------------------
@routes.get("/ws/{name}")
async def websocket_handler(request):
    name = request.match_info["name"]
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    async with LOCK:
        entry = PEER_MAP.get(name)
        if not entry:
            entry = {"name": name, "port": None, "ctrl": None}
            PEERS.append(entry)
            PEER_MAP[name] = entry
        entry["_ws"] = ws
    print(f"[Coordinator] WS CONNECT {name}")

    async def safe_send(target_name, obj):
        target = PEER_MAP.get(target_name)
        if target and target.get("_ws"):
            # Retry until WS is open
            for _ in range(50):
                if not target["_ws"].closed:
                    try:
                        await target["_ws"].send_json(obj)
                        return True
                    except Exception:
                        await asyncio.sleep(0.1)
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
                    success = await safe_send(to, obj)
                    if success:
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

# ---------------------------
# Static files
# ---------------------------
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

# ---------------------------
# App init
# ---------------------------
def init_app():
    app = web.Application()
    app.add_routes(routes)
    cors = aiohttp_cors.setup(app)
    for r in list(app.router.routes()):
        cors.add(r)
    return app

# ---------------------------
# Start server
# ---------------------------
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
        signal.signal(signal.SIGINT, lambda *_: _on_signal())
        signal.signal(signal.SIGTERM, lambda *_: _on_signal())

    await stop.wait()
    print("[Coordinator] Shutdown...")
    await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        print("[Coordinator] Interrupted, exiting.")

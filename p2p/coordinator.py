#!/usr/bin/env python3
"""
coordinator.py — root coordinator that spawns per-strand subcoordinator processes.

Endpoints:
 - POST /register  {name, port, ctrl, strand} -> forwards to subcoordinator; returns prev + sub_ws_url
 - POST /lookup    {name} -> returns peer info (global)
 - GET  /peers     -> returns global peers and strands mapping
Serves ./www/ static (peer.html / peer.js).
This version reliably spawns subcoordinator processes and logs their output to files.
"""
import asyncio
import json
import os
import signal
import sys
import subprocess
import time
from aiohttp import web
import aiohttp_cors
import socket

BASE_DIR = os.path.dirname(__file__)
WWW_DIR = os.path.join(BASE_DIR, "www")
HOST = "0.0.0.0"
PORT = 9000

# Subcoordinator spawn base port (will try increasing ports from here)
SUB_BASE_PORT = 10080
SUB_PORT_LIMIT = 200  # try ports SUB_BASE_PORT .. SUB_BASE_PORT+SUB_PORT_LIMIT

# Globals
PEERS = []        # flat list of all peer entries
PEER_MAP = {}     # name -> entry
STRAND_TO_INFO = {}  # strand -> {port, proc, url, log}

LOCK = asyncio.Lock()
routes = web.RouteTableDef()

def wait_for_port(host, port, timeout=5.0):
    start = time.time()
    while time.time() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.connect((host, port))
                return True
            except ConnectionRefusedError:
                time.sleep(0.1)
    raise TimeoutError(f"Port {port} not ready after {timeout}s")

def find_free_port(start=SUB_BASE_PORT, limit=SUB_PORT_LIMIT):
    p = start
    for _ in range(limit):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                p += 1
    raise RuntimeError("no free ports found for subcoordinator")

def spawn_subcoordinator(strand, port):
    py = sys.executable or "python3"
    script = os.path.abspath(os.path.join(BASE_DIR, "subcoordinator.py"))

    if not os.path.exists(script):
        raise FileNotFoundError(f"subcoordinator.py not found at {script}")

    print(f"[Coordinator] Spawning subcoord at ABSOLUTE PATH: {script}")

    proc = subprocess.Popen(
        [py, script, "--port", str(port), "--strand", strand],
        cwd=BASE_DIR,
        stdout=None,  # avoids buffer blocking
        stderr=None
    )

    # Wait until subcoordinator is actually listening
    wait_for_port("127.0.0.1", port)
    print(f"[Coordinator] Subcoordinator for strand '{strand}' ready on port {port}")
    return proc

async def wait_subcoordinator_ready(port, timeout=6.0):
    """
    Poll the subcoordinator /health endpoint until ready or timeout.
    Returns True if ready, False otherwise.
    """
    import aiohttp
    url = f"http://127.0.0.1:{port}/health"
    start = time.time()
    async with aiohttp.ClientSession() as session:
        while time.time() - start < timeout:
            try:
                async with session.get(url, timeout=1) as r:
                    if r.status == 200:
                        print(f"[Coordinator] subcoordinator on port {port} ready")
                        return True
            except Exception:
                await asyncio.sleep(0.2)
    print(f"[Coordinator] subcoordinator on port {port} did NOT become ready in {timeout}s")
    return False

@routes.post("/register")
async def register(request):
    """
    Register a peer. Expected JSON: {name, port, ctrl, strand}
    Coordinator ensures a subcoordinator exists for the strand, forwards the register request
    to the subcoordinator, and returns the subcoordinator WS URL and prev peer info.
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    name = data.get("name")
    port = data.get("port")
    ctrl = data.get("ctrl")
    strand = data.get("strand") or "default"

    if not name:
        return web.json_response({"error": "missing name"}, status=400)

    async with LOCK:
        # create or update global entry
        entry = PEER_MAP.get(name)
        if entry:
            entry.update({"port": port, "ctrl": ctrl, "strand": strand})
        else:
            entry = {"name": name, "port": port, "ctrl": ctrl, "strand": strand}
            PEERS.append(entry)
            PEER_MAP[name] = entry

        info = STRAND_TO_INFO.get(strand)
        if not info:
            # need to spawn a subcoordinator for this strand
            sub_port = find_free_port()
            try:
                proc = spawn_subcoordinator(strand, sub_port)    # <--- FIX: only 1 return value
                log_path = f"subcoord_{strand}_{sub_port}.log"
            except Exception as e:
                return web.json_response({"error": f"failed to spawn subcoordinator: {e}"}, status=500)

            STRAND_TO_INFO[strand] = {
                "port": sub_port,
                "proc": proc,
                "url": f"http://127.0.0.1:{sub_port}",
                "log": log_path
            }

            # wait for it to respond
            ready = await wait_subcoordinator_ready(sub_port, timeout=6.0)
            if not ready:
                return web.json_response({
                    "error": "subcoordinator failed to start",
                    "log": log_path
                }, status=500)

        info = STRAND_TO_INFO[strand]
        sub_url = info["url"]

    # Forward register to subcoordinator (outside LOCK)
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{sub_url}/register", json={"name": name, "port": port, "ctrl": ctrl}) as resp:
                text = await resp.text()
                ct = resp.headers.get("Content-Type", "")
                if "application/json" in ct:
                    try:
                        j = json.loads(text) if text else {}
                    except Exception:
                        j = {}
                else:
                    if resp.status >= 400:
                        return web.json_response({"error": "subcoordinator error text", "detail": text}, status=500)
                    try:
                        j = json.loads(text) if text else {}
                    except Exception:
                        j = {}
    except Exception as e:
        return web.json_response({"error": f"failed to contact subcoordinator: {e}"}, status=500)

    # Build WS endpoint returned to browser
    host_header = request.headers.get('X-Forwarded-Host') or request.headers.get('Host') or request.remote
    host_only = host_header.split(':')[0]

    sub_ws = f"{'wss' if request.secure else 'ws'}://{host_only}:{info['port']}/ws/{name}"

    return web.json_response({"prev": j or {}, "sub_ws": sub_ws})

@routes.post("/lookup")
async def lookup(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({}, status=400)
    name = data.get("name")
    return web.json_response(PEER_MAP.get(name, {}) or {})

@routes.get("/peers")
async def peers_list(request):
    # group by strand
    strands = {}
    for name, info in PEER_MAP.items():
        s = info.get("strand", "default")
        strands.setdefault(s, []).append({"name": name, "port": info.get("port"), "ctrl": info.get("ctrl")})
    return web.json_response({"peers": list(PEER_MAP.values()), "strands": strands})

# Optional simple health endpoint
@routes.get("/health")
async def health(request):
    return web.json_response({"status": "ok", "server": "coordinator"})

# Serve static files (peer.html / peer.js)
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

def init_app():
    app = web.Application()
    app.add_routes(routes)
    cors = aiohttp_cors.setup(app)
    for r in list(app.router.routes()):
        cors.add(r)
    return app

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
    # attempt to terminate subcoordinator processes
    for s, info in STRAND_TO_INFO.items():
        proc = info.get("proc")
        if proc and proc.poll() is None:
            print(f"[Coordinator] terminating subcoordinator for strand {s}")
            try:
                proc.terminate()
            except Exception:
                pass
    await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        print("[Coordinator] Interrupted, exiting.")

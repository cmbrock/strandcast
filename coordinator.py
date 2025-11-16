# coordinator.py
# Coordinator accepts peer registrations and lookup queries.
# It notifies the previous peer (via its control port) when a new peer joins
# so the previous peer can update its "next" pointer.

import socket
import threading
import json
import signal
import sys
import time

HOST = "127.0.0.1"
COORD_PORT = 9000

peers = []            # list of dicts: {"name":..., "port":..., "ctrl_port":...}
lock = threading.Lock()
running = True

def notify_next(ctrl_port, next_name, next_port):
    """Notify a peer's control server about its new downstream neighbor."""
    try:
        with socket.create_connection((HOST, ctrl_port), timeout=2) as s:
            msg = json.dumps({"cmd": "UPDATE_NEXT", "next_name": next_name, "next_port": next_port})
            s.sendall(msg.encode())
            # optional ack read
            try:
                s.settimeout(1.0)
                _ = s.recv(1024)
            except:
                pass
    except Exception as e:
        print(f"[Coordinator] Failed to notify ctrl {ctrl_port}: {e}")

def handle_connection(conn, addr):
    try:
        raw = conn.recv(8192).decode()
        if not raw:
            return
        req = json.loads(raw)
        typ = req.get("type")
        if typ == "register":
            name = req.get("name") or f"peer{len(peers)+1}"
            port = int(req["port"])
            ctrl_port = int(req["ctrl_port"])
            with lock:
                entry = {"name": name, "port": port, "ctrl_port": ctrl_port}
                peers.append(entry)
                print(f"[Coordinator] Registered {name} (udp={port}, ctrl={ctrl_port})")
                prev = peers[-2] if len(peers) > 1 else None
            # reply with previous peer info (or empty object)
            conn.sendall(json.dumps(prev or {}).encode())
            # notify previous peer about its new next
            if prev:
                notify_next(prev["ctrl_port"], entry["name"], entry["port"])
        elif typ == "lookup":
            # lookup by name and return peer info if exists
            target = req.get("name")
            found = {}
            with lock:
                for p in peers:
                    if p["name"] == target:
                        found = p
                        break
            conn.sendall(json.dumps(found).encode())
        elif typ == "list":
            with lock:
                conn.sendall(json.dumps(peers).encode())
        else:
            conn.sendall(json.dumps({"error":"unknown type"}).encode())
    except Exception as e:
        print("[Coordinator] connection handler error:", e)
    finally:
        try:
            conn.close()
        except:
            pass

def listener():
    global running
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, COORD_PORT))
    sock.listen(16) 
    sock.settimeout(1.0)
    print(f"[Coordinator] Listening on {HOST}:{COORD_PORT}")
    while running:
        try:
            conn, addr = sock.accept()
            threading.Thread(target=handle_connection, args=(conn, addr), daemon=True).start()
        except socket.timeout:
            continue
        except Exception as e:
            print("[Coordinator] listener error:", e)
            break
    sock.close()
    print("[Coordinator] Exiting.")

def sigint_handler(sig, frame):
    global running
    print("\n[Coordinator] Caught interrupt, shutting down...")
    running = False

if __name__ == "__main__":
    signal.signal(signal.SIGINT, sigint_handler)
    listener()

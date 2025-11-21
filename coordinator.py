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

subcoordinators = []            # list of dicts: {"name":..., "port":..., "ctrl_port":...}
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
            port = req.get("port")
            with lock:
                entry = port
                subcoordinators.append(entry)
                print(f"[Coordinator] Registered port {port}")
            # reply with previous peer info (or empty object)
            reply = {"reply": f"[Coordinator] Registered port {port}"}
            conn.sendall(json.dumps(reply or {}).encode())
            # notify previous peer about its new next
        elif typ == "lookup":
            # lookup by name and return peer info if exists
            
            target = req.get("port")
            found = ""
            with lock:
                for s in subcoordinators:
                    if s == target:
                        found = s
                        break
            conn.sendall(json.dumps(found).encode())
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

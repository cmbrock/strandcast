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

subcoordinators = []
strands = [[]]
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
        action = req.get("action")
        typ = req.get("type")
        if action == "register":
            if typ == "subcoordinator":
                port: int = int(req.get("port"))
                with lock:
                    subcoordinators.append(port)
                print(f"Subcoordinator with port {port} registered.")
                reply = {"reply": f"Successfuly registered with Coordinator at port {COORD_PORT}"}
                conn.sendall(json.dumps(reply or {}).encode())
            elif typ == "peer":
                if len(subcoordinators) == 0:
                    raise Exception(f"No subcoordinators registered yet to sign on peer {name}")
                name = req.get("name") 
                port = int(req["port"])
                ctrl_port = int(req["ctrl_port"])
                with lock:
                    entry = {"name": name, "port": port, "ctrl_port": ctrl_port}
                    lastStrand = strands[-1]
                    if len(lastStrand) >= 3 and len(subcoordinators) > len(strands):
                        strands.append([entry])
                        subcoordinatorIndex = len(strands)-1
                        sub = subcoordinators[subcoordinatorIndex]
                        message = {"prev" : None, "subport" : str(sub)}

                        conn.sendall(json.dumps(message or {}).encode())
                    else:
                        lastStrand.append(entry)
                        prev = lastStrand[-2] if len(lastStrand) > 1 else None
                        # reply with previous peer info (or empty object)
                        subcoordinatorIndex = len(strands)-1
                        sub = subcoordinators[subcoordinatorIndex]
                        message = {"prev" : prev, "subport" : str(sub)}

                        conn.sendall(json.dumps(message or {}).encode())
                        # notify previous peer about its new next
                        if prev:
                            notify_next(prev["ctrl_port"], entry["name"], entry["port"])

                        print(f"[Coordinator] Registered {name} (udp={port}, ctrl={ctrl_port}) at subcoordinator port {sub}")
                        
        else:
            raise Exception(f"action type unknown") 
              
        # else:
        #     conn.sendall(json.dumps({"error":"unknown type"}).encode())
    except Exception as e:
        print("[Coordinator] connection handler error:", e)
        error = {"error": f"[Coordinator] connection handler error: {e}" }
        conn.sendall(json.dumps(error or {}).encode())
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

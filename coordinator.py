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

HOST = "0.0.0.0"  # Bind to all network interfaces to accept external connections
COORD_PORT = 9000
buffer = []
subcoordinators = []
peerQueue = []
strands = []
lock = threading.Lock()
running = True

all_strands_have_peers = False

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

def register_subcoordinator(req, conn):
    port: int = int(req.get("port"))
    with lock:
        subcoordinators.append(port)
        strands.append([])
        buffer.append(3)
        peerQueue.append([])
    print(f"Subcoordinator with port {port} registered.")
    reply = {"reply": f"Successfuly registered with Coordinator at port {COORD_PORT}"}
    conn.sendall(json.dumps(reply or {}).encode())



def register_peer_queue(index: int):

    target_queue = peerQueue[index]
    with lock:
        for i in range(buffer[index]):
            SUB_COORD_PORT = int(subcoordinators[index])
            target = target_queue[i]
            try:
                with socket.create_connection((HOST, SUB_COORD_PORT), timeout=5) as s:
                    reg = {"type":"register","name": target['name'], "port": target['port'], "ctrl_port": target['ctrl_port'], "ip": target.get('ip', '127.0.0.1')}
                    s.sendall(json.dumps(reg).encode())
                    resp = s.recv(8192).decode()
                    meta_info = json.loads(resp) if resp else {}
                    
                    # Check for error in response
                    if meta_info.get("error"):
                        error_msg = meta_info.get("error")
                        raise Exception(f"Registration failed: {error_msg}")
                    
                    # If registration was successful, notify the peer's control port
                    peer_ctrl_port = int(target['ctrl_port'])
                    peer_ip = target.get('ip', HOST)
                    try:
                        with socket.create_connection((peer_ip, peer_ctrl_port), timeout=2) as ctrl_s:
                            # Prepare notification message
                            notification = {
                                "cmd": "SUBCOORDINATOR_INFO",
                                "subcoordinator_port": SUB_COORD_PORT
                            }
                            
                            # Add previous peer info if this is not the first peer (i >= 1)
                            if i >= 1:
                                prev_peer = target_queue[i-1]
                                notification["prev_peer"] = {
                                    "name": prev_peer['name'],
                                    "port": prev_peer['port'],
                                }
                            
                            ctrl_s.sendall(json.dumps(notification).encode())
                            # Optional ack read
                            try:
                                ctrl_s.settimeout(1.0)
                                _ = ctrl_s.recv(1024)
                            except:
                                pass
                    except Exception as ctrl_e:
                        print(f"[Coordinator] Failed to notify peer {target['name']} ctrl port {peer_ctrl_port}: {ctrl_e}")
                            
            except Exception as e:
                print(f"[Coordinator] Failed to register peer {target['name']} with subcoordinator: {e}")
                sys.exit(1)
        
        # These operations should be done after the for loop but inside the lock
        sliceNumber = buffer[index]
        subList = target_queue[:sliceNumber]
        strands[index].extend(subList)
        peerQueue[index] = peerQueue[index][sliceNumber:]
        buffer[index] = 0
        global all_strands_have_peers
        if (index+1 == len(strands)):
            all_strands_have_peers = True




def register_peer(req, conn):
    if len(subcoordinators) == 0:
        peer_name = req.get("name", "unknown")
        raise Exception(f"No subcoordinators registered yet to sign on peer {peer_name}")

    
    name = req.get("name") 
    port = int(req["port"])
    ctrl_port = int(req["ctrl_port"])
    ip = req.get("ip", "127.0.0.1")  # Default to localhost if not provided
    entry = {"name": name, "port": port, "ctrl_port": ctrl_port, "ip": ip}
    accepted = False

    
    with lock:
        for i in range(len(peerQueue)):
            global all_strands_have_peers
            condition = (len(peerQueue[i]) < 3) if all_strands_have_peers else (len(peerQueue[i]) < 3 and buffer[i] != 0)
            if(condition):
                peerQueue[i].append(entry)
                accepted = True
                if (buffer[i] > 0 and len(peerQueue[i]) == buffer[i]):
                    register_thread = threading.Thread(target=register_peer_queue, args=(i,), daemon=True)
                    register_thread.start()
                break
    
    message = None
    if accepted:
        message = {"message": "Peer queued to the coordinator. Searching for a subcoordinator..."}
    else:
        message = {"message": "Queue filled up. Try connecting later"}
    conn.sendall(json.dumps(message or {}).encode())
        




def handle_connection(conn, addr):
    try:
        raw = conn.recv(8192).decode()
        if not raw:
            return
        req = json.loads(raw)
        action = req.get("action")
        typ = req.get("type")


        if action == "status":
            if req.get("status") == "done":
                port = int(req.get("port"))
                index = subcoordinators.index(port)
                buffer[index] = len(peerQueue[index])
                reply = {"buffer" : buffer[index]}
                conn.sendall(json.dumps(reply or {}).encode())
                register_peer_queue(index)
        elif action == "register":
            if typ == "subcoordinator":
                register_subcoordinator(req, conn)
            elif typ == "peer":
                register_peer(req, conn)
            else:
                raise Exception("Entity type unknown")
                        
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

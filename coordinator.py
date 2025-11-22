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
subQueue = []
peersQueue = []
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

def notify_peer_assignment(ctrl_port, prev_peer, subcoord_port):
    """Notify a peer about its previous peer and assigned subcoordinator."""
    try:
        with socket.create_connection((HOST, ctrl_port), timeout=2) as s:
            msg = json.dumps({
                "cmd": "ASSIGN_CHAIN",
                "prev_peer": prev_peer,
                "subcoord_port": subcoord_port
            })
            s.sendall(msg.encode())
            # optional ack read
            try:
                s.settimeout(1.0)
                _ = s.recv(1024)
            except:
                pass
            print(f"[Coordinator] Notified peer at ctrl {ctrl_port} with prev={prev_peer}, subcoord={subcoord_port}")
    except Exception as e:
        print(f"[Coordinator] Failed to notify peer at ctrl {ctrl_port}: {e}")

def register_subcoordinator(req, conn):
    port: int = int(req.get("port"))
    with lock:
        subcoordinators.append(port)
        subQueue.append(3)
    print(f"Subcoordinator with port {port} registered.")
    reply = {"reply": f"Successfuly registered with Coordinator at port {COORD_PORT}", "numberOfPeers" : 3}
    conn.sendall(json.dumps(reply or {}).encode())


# def register_peer(req, conn):
#     if len(subcoordinators) == 0:
#         raise Exception(f"No subcoordinators registered yet to sign on peer {name}")
#     name = req.get("name") 
#     port = int(req["port"])
#     ctrl_port = int(req["ctrl_port"])
#     with lock:
#         entry = {"name": name, "port": port, "ctrl_port": ctrl_port}
#         lastStrand = strands[-1]
#         if len(lastStrand) >= 3 and len(subcoordinators) > len(strands):
#             strands.append([entry])
#             subcoordinatorIndex = len(strands)-1
#             sub = subcoordinators[subcoordinatorIndex]
#             message = {"prev" : None, "subport" : str(sub)}

#             conn.sendall(json.dumps(message or {}).encode())
#         else:
#             lastStrand.append(entry)
#             prev = lastStrand[-2] if len(lastStrand) > 1 else None
#             # reply with previous peer info (or empty object)
#             subcoordinatorIndex = len(strands)-1
#             sub = subcoordinators[subcoordinatorIndex]
#             message = {"prev" : prev, "subport" : str(sub)}

#             conn.sendall(json.dumps(message or {}).encode())
#             # notify previous peer about its new next
#             if prev:
#                 notify_next(prev["ctrl_port"], entry["name"], entry["port"])

#             print(f"[Coordinator] Registered {name} (udp={port}, ctrl={ctrl_port}) at subcoordinator port {sub}")


def process_queue_init():
    """Process peers from queue and assign them to subcoordinators."""
    with lock:
        for i in range(len(subQueue)):
            if len(peersQueue) >= subQueue[i] and subQueue[i] != 0:
                # Get the first N elements to process
                first_n_elements = peersQueue[:subQueue[i]]
                
                # Get the subcoordinator port for this strand
                subcoord_port = subcoordinators[i]
                
                # Send assignment notification to each peer
                for idx, peer in enumerate(first_n_elements):
                    # Previous peer is the one before in the list, or None if first
                    prev_peer = first_n_elements[idx - 1] if idx > 0 else None
                    
                    # Notify this peer about its assignment
                    notify_peer_assignment(
                        peer["ctrl_port"],
                        prev_peer,
                        subcoord_port
                    )
                    
                    # If this peer has a previous peer, notify the previous peer
                    # about its downstream neighbor
                    if prev_peer:
                        notify_next(
                            prev_peer["ctrl_port"],
                            peer["name"],
                            peer["port"]
                        )
                
                # Add processed peers to the strand
                strands[i].extend(first_n_elements)
                
                # Remove processed peers from queue
                del peersQueue[:subQueue[i]]
                
                # Mark this subcoordinator slot as processed
                subQueue[i] = 0
                
                print(f"[Coordinator] Processed {len(first_n_elements)} peers for subcoordinator {i} (port {subcoord_port})")

                return

def process_queue_on_call(subcoord_port, conn):
    """Process queue when subcoordinator requests more peers."""
    with lock:
        # Find the index of this subcoordinator
        try:
            subcoord_index = subcoordinators.index(subcoord_port)
        except ValueError:
            print(f"[Coordinator] Unknown subcoordinator port {subcoord_port}")
            conn.sendall(json.dumps({"queue_length": 0}).encode())
            return
        
        # Return current queue length
        queue_len = len(peersQueue)
        conn.sendall(json.dumps({"queue_length": queue_len}).encode())
        print(f"[Coordinator] Subcoordinator {subcoord_port} available, queue_length={queue_len}")
        
        # If we have enough peers in queue, assign them
        buffer_size = 1  # Could make this dynamic
        if queue_len >= buffer_size:
            subQueue[subcoord_index] = queue_len
            # Process this subcoordinator's queue
            process_queue_for_subcoord(subcoord_index)

def process_queue_for_subcoord(subcoord_index):
    """Process queue for a specific subcoordinator."""
    with lock:
        if len(peersQueue) >= subQueue[subcoord_index] and subQueue[subcoord_index] != 0:
            # Get the first N elements to process
            first_n_elements = peersQueue[:subQueue[subcoord_index]]
            
            # Get the subcoordinator port for this strand
            subcoord_port = subcoordinators[subcoord_index]
            
            # Send assignment notification to each peer
            for idx, peer in enumerate(first_n_elements):
                # Previous peer is the one before in the list, or None if first
                prev_peer = first_n_elements[idx - 1] if idx > 0 else None
                
                # Notify this peer about its assignment
                notify_peer_assignment(
                    peer["ctrl_port"],
                    prev_peer,
                    subcoord_port
                )
                
                # If this peer has a previous peer, notify the previous peer
                # about its downstream neighbor
                if prev_peer:
                    notify_next(
                        prev_peer["ctrl_port"],
                        peer["name"],
                        peer["port"]
                    )
            
            # Add processed peers to the strand
            strands[subcoord_index].extend(first_n_elements)
            
            # Remove processed peers from queue
            del peersQueue[:subQueue[subcoord_index]]
            
            # Mark this subcoordinator slot as processed
            subQueue[subcoord_index] = 0
            
            print(f"[Coordinator] Processed {len(first_n_elements)} peers for subcoordinator {subcoord_index} (port {subcoord_port})")        



def handle_connection(conn, addr):


    try:
        raw = conn.recv(8192).decode()
        if not raw:
            return
        req = json.loads(raw)
        action = req.get("action")
        typ = req.get("type")
        status = req.get("status")
        
        if status == "available":
            # Subcoordinator requesting more peers
            subcoord_port = req.get("port")
            process_queue_on_call(subcoord_port, conn)
            return
        
        elif action == "register":
            if typ == "subcoordinator":
                register_subcoordinator(req, conn)
            elif typ == "peer":
                name = req.get("name") 
                port = int(req["port"])
                ctrl_port = int(req["ctrl_port"])
                entry = {"name": name, "port": port, "ctrl_port": ctrl_port}
                peersQueue.append(entry)
                print(f"[Coordinator] Added peer {name} to queue")
                
                # Start process_queue thread to handle assignments
                threading.Thread(target=process_queue_init, daemon=True).start()
                
                # Send acknowledgment to peer
                conn.sendall(json.dumps({"status": "queued"}).encode())
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

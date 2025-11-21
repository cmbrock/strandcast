# coordinator.py
# Subcoordinator accepts peer registrations and lookup queries.
# It notifies the previous peer (via its control port) when a new peer joins
# so the previous peer can update its "next" pointer.

import socket
import threading
import json
import signal
import sys
import time

HOST = None
SUPER_COORD_PORT = None
COORD_PORT = None

peers = []            # list of dicts: {"name":..., "port":..., "ctrl_port":...}
lock = threading.Lock()
running = True
downstream_started = False  # flag to ensure downstream thread starts only once

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
        print(f"[Subcoordinator] Failed to notify ctrl {ctrl_port}: {e}")


def update_host(ctrl_port, new_cord_port):
    """Notify a peer's control server about its new downstream neighbor."""
    try:
        with socket.create_connection((HOST, ctrl_port), timeout=2) as s:
            msg = json.dumps({"cmd": "NEW_CORD", "port": new_cord_port})
            s.sendall(msg.encode())
            # optional ack read
            try:
                s.settimeout(1.0)
                _ = s.recv(1024)
            except:
                pass
    except Exception as e:
        print(f"[Subcoordinator] Failed to notify ctrl {ctrl_port}: {e}")




def is_authorized_peer(requester_name):
    """Check if the requesting peer is in the authorized peers list."""
    with lock:
        for p in peers:
            if p["name"] == requester_name:
                return True
    return False

def handle_connection(conn, addr):
    global downstream_started
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
                print(f"[Subcoordinator] Registered {name} (udp={port}, ctrl={ctrl_port})")
                prev = peers[-2] if len(peers) > 1 else None
                
                
                    
            # reply with previous peer info (or empty object)
            conn.sendall(json.dumps(prev or {}).encode())
            # notify previous peer about its new next
            if prev:
                notify_next(prev["ctrl_port"], entry["name"], entry["port"])

            
        elif typ == "lookup":
            # lookup by name and return peer info if exists
            requester = req.get("requester")
            if not requester or not is_authorized_peer(requester):
                print(f"[Subcoordinator] Unauthorized lookup attempt from {requester or 'unknown'}")
                conn.sendall(json.dumps({"error": "unauthorized"}).encode())
                return
            
            target = req.get("name")
            found = {}
            with lock:
                for p in peers:
                    if p["name"] == target:
                        found = p
                        break
            conn.sendall(json.dumps(found).encode())
        elif typ == "list":
            requester = req.get("requester")
            if not requester or not is_authorized_peer(requester):
                print(f"[Subcoordinator] Unauthorized list attempt from {requester or 'unknown'}")
                conn.sendall(json.dumps({"error": "unauthorized"}).encode())
                return
            
            with lock:
                conn.sendall(json.dumps(peers).encode())
        else:
            conn.sendall(json.dumps({"error":"unknown type"}).encode())
    except Exception as e:
        print("[Subcoordinator] connection handler error:", e)
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
    print(f"[Subcoordinator] Listening on {HOST}:{COORD_PORT}")
    while running:
        try:
            conn, addr = sock.accept()
            threading.Thread(target=handle_connection, args=(conn, addr), daemon=True).start()
        except socket.timeout:
            continue
        except Exception as e:
            print("[Subcoordinator] listener error:", e)
            break
    sock.close()
    print("[Subcoordinator] Exiting.")

def sigint_handler(sig, frame):
    global running
    print("\n[Subcoordinator] Caught interrupt, shutting down...")
    running = False


def drop_peer(tokens): 
    target = tokens[1]
    found = {}
    foundIndex = -1
    with lock:
        for i in range(len(peers)):
            if peers[i]["name"] == target:
                found = peers[i]
                foundIndex= i
                break
    if found != {}:
        print(f"Found peer {found["name"]}")
        print(f"found index: {foundIndex}")
        notify_next(found['ctrl_port'], "", "")
        if len(peers) == 1:
            peers.pop(0)
        else:
            if foundIndex == len(peers)-1:
                prev_peer = peers[-2]
                notify_next(prev_peer['ctrl_port'], "", "")
                peers.pop()
            elif foundIndex == 0:
                peers.pop(0)
            else:
                prev_peer = peers[foundIndex-1]
                next_peer = peers[foundIndex+1]
                notify_next(prev_peer['ctrl_port'], next_peer['name'], next_peer['port'])
                peers.pop(foundIndex)
        
        print(f"Dropped peer {found["name"]}")
    
    return found


def lookup_coordinator(port):
    try:
        with socket.create_connection((HOST, SUPER_COORD_PORT), timeout=3) as s:
            s.sendall(json.dumps({"type":"lookup", "port": port}).encode())
            raw = s.recv(4096).decode()
            result = json.loads(raw) if raw else {}
            return result
    except Exception as e:
        print("[peer] coordinator lookup error:", e)
        return {}

def switch_coordinator(peer, new_coord_port):
    port = lookup_coordinator(new_coord_port)
    if port == "":
        print("Coordinator port doesn't exist")
    else:
        update_host(peer['ctrl_port'], port)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, sigint_handler)
    if len(sys.argv) != 4:
        print("Usage: python peer.py <name> <udp_port>")
        sys.exit(1)
    COORD_PORT = int(sys.argv[1])
    HOST = sys.argv[2]
    SUPER_COORD_PORT = int(sys.argv[3])
    # Register with coordinator
    reg = {"type":"subcoordinator", "action":"register", "port" : str(COORD_PORT)}
    try:
        with socket.create_connection((HOST, SUPER_COORD_PORT), timeout=5) as s:
            s.sendall(json.dumps(reg).encode())
            resp = s.recv(8192).decode()
            reply = json.loads(resp) if resp else {}
            print(reply.get("reply"))
    except Exception as e:
        print(f"[{COORD_PORT}] Failed to register with coordinator: {e}")
        sys.exit(1)
    

    t_listener = threading.Thread(target=listener, daemon=True)
    t_listener.start()

    print(f"Interactive sender ready. Commands: drop <peer>")
    print(running)
    while running:
        
        try:
            # input may raise EOFError if stdin closed; handle gracefully
            line = input("> ")
        except EOFError:
            # likely no TTY - shut down gracefully
            print(f"Stdin closed (EOF). Exiting.")
            running = False
            break
        except KeyboardInterrupt:
            running = False
            break

        if not line:
            continue

        tokens = line.split()
        if tokens[0].lower() == "drop" and len(tokens) >= 2:
            drop_peer(tokens)
        elif tokens[0].lower() == "switch" and len(tokens) >= 3:
            peer = drop_peer(tokens)
            if peer != {}:
                switch_coordinator(peer, tokens[2])

            


    # shutdown
    running = False
    print(f"Shutting down, waiting for threads...")
    t_listener.join(timeout=1.0)
    print(f" Exit.")


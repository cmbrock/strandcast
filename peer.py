# peer.py
# Peer process:
# - registers with coordinator (TCP)
# - listens for data on a UDP port
# - runs a TCP control server (ctrl_port = udp_port + 10000) to receive UPDATE_NEXT
# - allows interactive sending from any peer
# - supports "sendto <peername> <message>" to send directly to a specific peer by querying coordinator
# - de-duplicates messages using (origin, seq)
# - per-peer logfile: peer_<name>.log
# - clean Ctrl+C shutdown

import socket
import threading
import sys
import json
import time
import signal
from datetime import datetime

COORD_HOST = None
COORD_PORT = None
SUB_COORD_PORT = None
running = True

def nowts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def log(name, text):
    fname = f"strandcast/peer_{name}.log"
    with open(fname, "a") as f:
        f.write(f"[{nowts()}] {text}\n")

def udp_listener(udp_sock, name, next_udp_port_holder, seen):
    udp_sock.settimeout(1.0)
    
    while running:
        startTime = time.perf_counter()
        try:
            data, addr = udp_sock.recvfrom(65536)
            # try parse
            try:
                msg = json.loads(data.decode())
            except:
                # ignore malformed
                continue
            typ = msg.get("type")
            if typ == "data":
                origin = msg.get("origin")
                seq = msg.get("seq")
                key = (origin, seq)
                if key in seen:
                    # already processed -> ignore
                    continue
                seen.add(key)
                sender = msg.get("sender", "-")
                payload = msg.get("msg", "")
                print(f"[{name} RECV] origin={origin} seq={seq} from {sender}: {payload}")
                log(name, f"RECV origin={origin} seq={seq} from {sender}: {payload}")
                # forward downstream to next peer (if any)
                nxt = next_udp_port_holder.get("port")
                if nxt:
                    try:
                        udp_sock.sendto(data, ("127.0.0.1", nxt))
                        log(name, f"FORWARDED origin={origin} seq={seq} to {next_udp_port_holder.get('name')}:{nxt}")
                    except Exception as e:
                        print(f"[{name}] forward error: {e}")
                        log(name, f"FORWARD_FAILED origin={origin} seq={seq} err={e}")
                    
                    endTime = time.perf_counter()
                    print(f"Elapsed Time: {endTime-startTime}" )
                    log(f"Elapsed Time: {endTime-startTime}" )
                #last peer, so notify subcoordinator to send the next file if any
                else:
                    try:
                        with socket.create_connection((COORD_HOST, SUB_COORD_PORT), timeout=3) as s:
                            msg = json.dumps({"type": "deliveryDone"})
                            s.sendall(msg.encode())
                            # Wait for acknowledgment
                            resp = s.recv(1024).decode()
                            log(name, f"Notified subcoordinator: deliveryDone, response: {resp}")
                    except Exception as e:
                        print(f"[{name}] Failed to notify subcoordinator: {e}")
                        log(name, f"SUBCOORD_NOTIFY_FAILED err={e}")
                    
            # ignore other types on UDP
        except socket.timeout:
            continue
        except Exception:
            continue
    udp_sock.close()
    print(f"[{name}] UDP listener exiting.")

def ctrl_server(ctrl_port, next_udp_port_holder, name):
    serv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    serv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    serv.bind(("127.0.0.1", ctrl_port))
    serv.listen(5)
    serv.settimeout(1.0)
    while running:
        try:
            conn, addr = serv.accept()
            with conn:
                raw = conn.recv(4096).decode()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except:
                    continue
                
                # Check for error messages and raise exception
                if msg.get("error"):
                    error_msg = msg.get("error")
                    print(f"[{name} CTRL] ERROR received: {error_msg}")
                    log(name, f"CTRL ERROR: {error_msg}")
                    raise Exception(f"Control server received error: {error_msg}")
                
                if msg.get("cmd") == "UPDATE_NEXT":
                    next_name = msg.get("next_name")
                    next_port = msg.get("next_port")
                    print(f"[{name} CTRL] UPDATE_NEXT -> {next_name}:{next_port}")
                    log(name, f"CTRL UPDATE_NEXT -> {next_name}:{next_port}")
                    next_udp_port_holder["port"] = next_port
                    next_udp_port_holder["name"] = next_name
                    try:
                        conn.sendall(b"OK")
                    except:
                        pass
        except socket.timeout:
            continue
        except Exception:
            continue
    serv.close()
    print(f"[{name}] control server exiting.")

def sigint_handler(sig, frame):
    global running
    print("\n[Peer] Caught interrupt, shutting down...")
    running = False

def query_coordinator_lookup(target_name, peer_name):
    """Query coordinator for peer info by name. Returns dict or {}."""
    try:
        with socket.create_connection((COORD_HOST, SUB_COORD_PORT), timeout=3) as s:
            s.sendall(json.dumps({"type":"lookup", "name": target_name, "requester" : peer_name}).encode())
            raw = s.recv(4096).decode()
            return json.loads(raw) if raw else {}
    except Exception as e:
        print("[peer] coordinator lookup error:", e)
        return {}

if __name__ == "__main__":


    signal.signal(signal.SIGINT, sigint_handler)

    if len(sys.argv) != 5:
        print("Usage: python peer.py <name> <udp_port>")
        sys.exit(1)

    name = sys.argv[1]
    udp_port = int(sys.argv[2])
    COORD_HOST = sys.argv[3]
    COORD_PORT = int(sys.argv[4])
    ctrl_port = udp_port + 10000

    # Register with coordinator
    reg = {"type":"peer", "action":"register", "name": name, "port": udp_port, "ctrl_port": ctrl_port}
    try:
        with socket.create_connection((COORD_HOST, COORD_PORT), timeout=5) as s:
            s.sendall(json.dumps(reg).encode())
            resp = s.recv(8192).decode()
            meta_info = json.loads(resp) if resp else {}
            
            # Check for error in response
            if meta_info.get("error"):
                error_msg = meta_info.get("error")
                raise Exception(f"Registration failed: {error_msg}")
    except Exception as e:
        print(f"[{name}] Failed to register with coordinator: {e}")
        sys.exit(1)

    prev_info = meta_info['prev']
    prev_name = None
    prev_port = None
    if prev_info != None:
        prev_name = prev_info['name']
        prev_port = prev_info['port']

    is_first = not prev_info  # True if no previous peer

    print(f"[{name}] Registered. previous peer: {prev_name or 'NONE'} (port={prev_port or 'N/A'})")
    log(name, f"Registered with coordinator. prev={prev_name}:{prev_port}")

    if meta_info != None:
        SUB_COORD_PORT = int(meta_info['subport'])

     # Register with subcoordinator
    reg = {"type":"register","name": name, "port": udp_port, "ctrl_port": ctrl_port}
    try:
        with socket.create_connection((COORD_HOST, SUB_COORD_PORT), timeout=5) as s:
            s.sendall(json.dumps(reg).encode())
            resp = s.recv(8192).decode()
            meta_info = json.loads(resp) if resp else {}
            
            # Check for error in response
            if meta_info.get("error"):
                error_msg = meta_info.get("error")
                raise Exception(f"Registration failed: {error_msg}")
    except Exception as e:
        print(f"[{name}] Failed to register with subcoordinator: {e}")
        sys.exit(1)

    # shared holder for next peer
    next_udp_port_holder = {"port": None, "name": None}

    # UDP socket for data
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp_sock.bind(("127.0.0.1", udp_port))

    # seen messages (to avoid duplicates)
    seen = set()

    # start threads
    t_udp = threading.Thread(target=udp_listener, args=(udp_sock, name, next_udp_port_holder, seen), daemon=True)
    t_udp.start()
    t_ctrl = threading.Thread(target=ctrl_server, args=(ctrl_port, next_udp_port_holder, name), daemon=True)
    t_ctrl.start()

    # interactive loop for any peer: supports:
    # - plain text: send a data message originating from this peer downstream
    # - sendto <peername> <message...> : send directly to named peer (bypass chain) by querying coordinator
    seq = 0
    print(f"[{name}] Interactive sender ready. Commands:")
    print("  <text>                     -> broadcast downstream as origin")
    print("  sendto <peername> <text>   -> send directly to peername UDP port (bypass chain)")
    print("  list                       -> ask coordinator for list of peers")
    print("  quit / Ctrl+C              -> exit")

    while running:
        try:
            # input may raise EOFError if stdin closed; handle gracefully
            line = input("> ")
        except EOFError:
            # likely no TTY - shut down gracefully
            print(f"[{name}] Stdin closed (EOF). Exiting.")
            running = False
            break
        except KeyboardInterrupt:
            running = False
            break

        if not line:
            continue
        if line.strip().lower() in ("quit", "exit"):
            running = False
            break
        if line.strip().lower() == "list":
            # ask coordinator for peers
            try:
                with socket.create_connection((COORD_HOST, SUB_COORD_PORT), timeout=3) as s:
                    s.sendall(json.dumps({"type":"list", "requester" : name}).encode())
                    raw = s.recv(8192).decode()
                    lst = json.loads(raw) if raw else []
                    print("Peers:", lst)
            except Exception as e:
                print("Coordinator list error:", e)
            continue

        tokens = line.split()
        if tokens[0].lower() == "sendto" and len(tokens) >= 3:
            target = tokens[1]
            msg_text = " ".join(tokens[2:])
            info = query_coordinator_lookup(target, name)
            if not info:
                print(f"[{name}] Coordinator: no peer named {target}")
                continue
            target_port = info.get("port")
            if not target_port:
                print(f"[{name}] Coordinator returned bad info for {target}")
                continue
            # build a data message (origin is this peer)
            seq += 1
            payload = {"type":"data","origin":name,"seq":seq,"sender":name,"msg":msg_text}
            try:
                udp_sock.sendto(json.dumps(payload).encode(), ("127.0.0.1", target_port))
                print(f"[{name}] Sent direct to {target} ({target_port}) seq={seq}")
                log(name, f"SENT_DIRECT to {target}:{target_port} origin={name} seq={seq} msg={msg_text}")
            except Exception as e:
                print(f"[{name}] direct send error: {e}")
            continue

        # default: originate a message that will propagate downstream
        seq += 1
        payload = {"type":"data","origin":name,"seq":seq,"sender":name,"msg":line}
        nxt = next_udp_port_holder.get("port")
        if nxt:
            try:
                udp_sock.sendto(json.dumps(payload).encode(), ("127.0.0.1", nxt))
                print(f"[{name}] Originated seq={seq} -> forwarded to {next_udp_port_holder.get('name')}:{nxt}")
                log(name, f"SENT origin={name} seq={seq} to {next_udp_port_holder.get('name')}:{nxt} msg={line}")
                # mark as seen so origin doesn't get processed again on receipt
                seen.add((name, seq))
            except Exception as e:
                print(f"[{name}] send error: {e}")
                log(name, f"SEND_ERROR origin={name} seq={seq} err={e}")
        else:
            # no downstream yet — just log it (or optionally queue)
            print(f"[{name}] No next peer yet — message queued (not sent).")
            log(name, f"QUEUED origin={name} seq={seq} msg={line}")

    # shutdown
    running = False
    print(f"[{name}] Shutting down, waiting for threads...")
    t_udp.join(timeout=1.0)
    t_ctrl.join(timeout=1.0)
    try:
        udp_sock.close()
    except:
        pass
    print(f"[{name}] Exit.")

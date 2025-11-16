# peer.py
# Peer process:
# - registers with coordinator (TCP)
# - listens for video frames on a UDP port
# - runs a TCP control server (ctrl_port = udp_port + 10000) to receive UPDATE_NEXT
# - receives video frames, buffers and reassembles chunks, decodes and displays with OpenCV
# - forwards video downstream to next peer
# - de-duplicates frames using frame_num
# - per-peer logfile: peer_<name>.log
# - clean Ctrl+C shutdown

import socket
import threading
import sys
import json
import time
import signal
import cv2
import numpy as np
import pickle
import zlib
import base64
from datetime import datetime
from collections import defaultdict

COORD_HOST = "127.0.0.1"
COORD_PORT = 9000
MAX_DGRAM = 60000
MAX_CHUNK_SIZE = 5000  # Match coordinator chunk size
running = True

# Frame buffer for reassembling chunks
frame_buffers = defaultdict(dict)  # frame_num -> {chunk_id: data}
frame_chunks_expected = {}  # frame_num -> total_chunks
received_frames = set()  # frame numbers already processed

# Queue for frames ready to display (main thread will display)
import queue
display_queue = queue.Queue(maxsize=100)

def nowts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def log(name, text):
    fname = f"strandcast/peer_{name}.log"
    with open(fname, "a") as f:
        f.write(f"[{nowts()}] {text}\n")

def udp_listener(udp_sock, name, next_udp_port_holder, seen):
    global frame_buffers, frame_chunks_expected, received_frames, display_queue
    
    udp_sock.settimeout(1.0)
    frame_count = 0
    
    # Create output directory for saved frames (optional backup)
    import os
    output_dir = f"peer_{name}_frames"
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"[{name}] Ready to receive video frames")
    print(f"[{name}] Frames will be saved to {output_dir}/")
    log(name, f"Output directory: {output_dir}")
    
    while running:
        try:
            data, addr = udp_sock.recvfrom(65536)
            try:
                msg = json.loads(data.decode())
            except:
                continue
            
            typ = msg.get("type")
            
            if typ == "video_frame":
                frame_num = msg.get("frame_num")
                chunk_id = msg.get("chunk_id")
                total_chunks = msg.get("total_chunks")
                chunk_data_hex = msg.get("data")
                origin = msg.get("origin")
                
                if frame_num in received_frames:
                    # Already processed this frame, skip
                    continue
                
                # Store chunk
                chunk_data = base64.b64decode(chunk_data_hex)
                frame_buffers[frame_num][chunk_id] = chunk_data
                frame_chunks_expected[frame_num] = total_chunks
                
                # Check if we have all chunks for this frame
                if len(frame_buffers[frame_num]) == total_chunks:
                    # Reassemble frame
                    compressed_data = b''.join(
                        frame_buffers[frame_num][i] for i in range(total_chunks)
                    )
                    
                    try:
                        # Decompress and decode
                        decompressed = zlib.decompress(compressed_data)
                        buffer = pickle.loads(decompressed)
                        frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
                        
                        if frame is not None:
                            # Save frame to disk (backup)
                            frame_path = f"{output_dir}/frame_{frame_num:06d}.jpg"
                            cv2.imwrite(frame_path, frame)
                            
                            if frame_num % 30 == 0:
                                print(f"[{name}] Received frame {frame_num} from {origin}")
                                log(name, f"RECV_FRAME {frame_num} from {origin}")
                            
                            frame_count += 1
                            received_frames.add(frame_num)
                            
                            # Put frame in display queue for main thread
                            try:
                                display_queue.put(("frame", frame_num, frame), block=False)
                            except queue.Full:
                                # Queue full, skip this frame's display (still saved to disk)
                                pass
                            
                            # Forward to next peer
                            nxt = next_udp_port_holder.get("port")
                            if nxt:
                                # Re-send all chunks to next peer
                                for cid in range(total_chunks):
                                    packet = {
                                        "type": "video_frame",
                                        "origin": origin,
                                        "frame_num": frame_num,
                                        "chunk_id": cid,
                                        "total_chunks": total_chunks,
                                        "data": base64.b64encode(frame_buffers[frame_num][cid]).decode('ascii')
                                    }
                                    try:
                                        udp_sock.sendto(json.dumps(packet).encode(), ("127.0.0.1", nxt))
                                    except Exception as e:
                                        log(name, f"FORWARD_ERROR frame {frame_num} chunk {cid}: {e}")
                                
                                if frame_num % 30 == 0:
                                    log(name, f"FORWARDED frame {frame_num} to {next_udp_port_holder.get('name')}:{nxt}")
                        
                        # Clean up buffer
                        del frame_buffers[frame_num]
                        del frame_chunks_expected[frame_num]
                        
                    except Exception as e:
                        print(f"[{name}] Error decoding frame {frame_num}: {e}")
                        log(name, f"DECODE_ERROR frame {frame_num}: {e}")
            
            elif typ == "video_end":
                frame_num = msg.get("frame_num")
                origin = msg.get("origin")
                print(f"[{name}] End of video stream from {origin}. Total frames received: {frame_count}")
                log(name, f"VIDEO_END from {origin}, total frames: {frame_count}")
                
                # Signal end to display thread
                try:
                    display_queue.put(("end", frame_count, None), block=False)
                except:
                    pass
                
                # Forward end marker
                nxt = next_udp_port_holder.get("port")
                if nxt:
                    try:
                        udp_sock.sendto(data, ("127.0.0.1", nxt))
                        log(name, f"FORWARDED video_end to {next_udp_port_holder.get('name')}:{nxt}")
                    except Exception as e:
                        log(name, f"FORWARD_ERROR video_end: {e}")
            
            elif typ == "data":
                # Legacy text message support
                origin = msg.get("origin")
                seq = msg.get("seq")
                key = (origin, seq)
                if key in seen:
                    continue
                seen.add(key)
                sender = msg.get("sender", "-")
                payload = msg.get("msg", "")
                print(f"[{name} RECV] origin={origin} seq={seq} from {sender}: {payload}")
                log(name, f"RECV origin={origin} seq={seq} from {sender}: {payload}")
                
                nxt = next_udp_port_holder.get("port")
                if nxt:
                    try:
                        udp_sock.sendto(data, ("127.0.0.1", nxt))
                        log(name, f"FORWARDED origin={origin} seq={seq}")
                    except Exception as e:
                        log(name, f"FORWARD_FAILED origin={origin} seq={seq} err={e}")
                        
        except socket.timeout:
            continue
        except Exception as e:
            if running:
                log(name, f"UDP_LISTENER_ERROR: {e}")
            continue
    
    udp_sock.close()
    print(f"[{name}] UDP listener exiting. Total frames received: {frame_count}")

def input_handler(name):
    """Handle user input in a separate thread."""
    global running
    while running:
        try:
            line = input()
            if line.strip().lower() in ("quit", "exit"):
                print(f"[{name}] User requested exit")
                running = False
                break
        except EOFError:
            break
        except:
            break

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

def query_coordinator_lookup(target_name):
    """Query coordinator for peer info by name. Returns dict or {}."""
    try:
        with socket.create_connection((COORD_HOST, COORD_PORT), timeout=3) as s:
            s.sendall(json.dumps({"type":"lookup", "name": target_name}).encode())
            raw = s.recv(4096).decode()
            return json.loads(raw) if raw else {}
    except Exception as e:
        print("[peer] coordinator lookup error:", e)
        return {}

if __name__ == "__main__":
    signal.signal(signal.SIGINT, sigint_handler)

    if len(sys.argv) != 3:
        print("Usage: python peer.py <name> <udp_port>")
        sys.exit(1)

    name = sys.argv[1]
    udp_port = int(sys.argv[2])
    ctrl_port = udp_port + 10000

    # Register with coordinator
    reg = {"type":"register", "name": name, "port": udp_port, "ctrl_port": ctrl_port}
    try:
        with socket.create_connection((COORD_HOST, COORD_PORT), timeout=5) as s:
            s.sendall(json.dumps(reg).encode())
            resp = s.recv(8192).decode()
            prev_info = json.loads(resp) if resp else {}
    except Exception as e:
        print(f"[{name}] Failed to register with coordinator: {e}")
        sys.exit(1)

    prev_name = prev_info.get("name")
    prev_port = prev_info.get("port")
    is_first = not prev_info  # True if no previous peer

    print(f"[{name}] Registered. previous peer: {prev_name or 'NONE'} (port={prev_port or 'N/A'})")
    log(name, f"Registered with coordinator. prev={prev_name}:{prev_port}")

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
    t_input = threading.Thread(target=input_handler, args=(name,), daemon=True)
    t_input.start()

    # OpenCV window setup (must be in main thread)
    window_name = f"Peer {name} - Video Stream"
    window_created = False
    video_ended = False
    
    # Main display loop
    print(f"[{name}] Peer ready. Waiting for video stream...")
    print(f"[{name}] Type 'quit' to exit")
    print(f"[{name}] Video will display in separate window when streaming starts")

    while running:
        # Check display queue for frames to display
        try:
            event_type, data1, data2 = display_queue.get(timeout=0.01)
            
            if event_type == "frame":
                frame_num = data1
                frame = data2
                
                # Create window on first frame
                if not window_created:
                    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
                    cv2.resizeWindow(window_name, 800, 600)
                    window_created = True
                    print(f"[{name}] Video window opened. Press 'q' in window to close.")
                
                # Display frame
                cv2.imshow(window_name, frame)
                key = cv2.waitKey(1) & 0xFF
                
                if key == ord('q'):
                    print(f"[{name}] User pressed 'q', closing video window")
                    running = False
                    break
                
                if frame_num % 30 == 0:
                    print(f"[{name}] Displaying frame {frame_num}")
            
            elif event_type == "end":
                total_frames = data1
                video_ended = True
                print(f"[{name}] Video playback complete. Total frames: {total_frames}")
                print(f"[{name}] Window will remain open. Press 'q' to close or type 'quit'.")
                
        except queue.Empty:
            # No frames to display, process window events if window exists
            if window_created:
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    print(f"[{name}] User pressed 'q', closing video window")
                    running = False
                    break
                
                # Check if window was closed
                try:
                    if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                        print(f"[{name}] Video window closed by user")
                        running = False
                        break
                except:
                    pass
            else:
                # No window yet, just wait a bit
                time.sleep(0.01)

    # shutdown
    running = False
    print(f"[{name}] Shutting down, waiting for threads...")
    t_udp.join(timeout=1.0)
    t_ctrl.join(timeout=1.0)
    
    # Cleanup OpenCV windows
    if window_created:
        try:
            cv2.destroyWindow(window_name)
            cv2.waitKey(1)  # Process the destroy event
        except:
            pass
    
    try:
        udp_sock.close()
    except:
        pass
    print(f"[{name}] Exit.")

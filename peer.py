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
import cv2
import numpy as np
import pickle
import zlib
import base64
import queue
from datetime import datetime
from collections import defaultdict

COORD_HOST = None
COORD_PORT = None
SUB_COORD_PORT = None
MAX_DGRAM = 60000
MAX_CHUNK_SIZE = 5000  # Match coordinator chunk size
running = True
startTime = 0
endTime = 0
oneTimeAck = True


nextPeers = []

# Frame buffer for reassembling chunks
frame_buffers = defaultdict(dict)  # frame_num -> {chunk_id: data}
frame_chunks_expected = {}  # frame_num -> total_chunks
received_frames = set()  # frame numbers already processed

# Storage for all received frames for playback
all_collections = {}  # frame_num -> frame (numpy array)
all_frames = []
video_complete = True
total_video_frames = 0
video_segments = []  # List of (start_frame, end_frame) tuples for each video segment
readyQueue = []
# Next peer connection monitoring
next_peer_last_success = time.time()
next_peer_timeout = 5.0  # 5 seconds without successful forward = dead peer

waiting_for_ack = False
ack_timeout = 5.0  # Max time to wait for ACK

# Queue for frames ready to display (main thread will display)
import queue
display_queue = queue.Queue(maxsize=100)

def nowts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def log(name, text):
    fname = f"peer_{name}.log"
    with open(fname, "a") as f:
        f.write(f"[{nowts()}] {text}\n")


def acknowledgePeer(ctrl_port):
    """Notify a peer's control server about its new downstream neighbor."""
    ctrl_port = int(ctrl_port)
    try:
        with socket.create_connection((COORD_HOST, ctrl_port), timeout=5) as s:
            msg = json.dumps({"cmd": "ack"})
            s.sendall(msg.encode())
            resp = s.recv(8192).decode()
            # optional ack read
            try:
                s.settimeout(1.0)
                _ = s.recv(1024)
            except:
                pass
    except Exception as e:
        print(f"[Peer] Failed to acknowledge ctrl {ctrl_port}: {e}")
        nextPeers.pop(0)


def udp_listener(udp_sock, name, seen):
    global frame_buffers, frame_chunks_expected, received_frames, display_queue, all_frames, video_complete, total_video_frames, video_segments, startTime, endTime, next_peer_last_success, nextPeers, ack_event, oneTimeAck
    
    udp_sock.settimeout(5.0)
    frame_count = 0
    segment_start_frame = 0
    current_segment_offset = 0  # Offset to add to incoming frame numbers
    
    # Create output directory for saved frames (optional backup)
    import os
    output_dir = f"videoOutput/peer_{name}_frames"
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
                if startTime == 0: startTime = time.perf_counter()
                
                # Send ACK back to sender to confirm receipt
                if addr:
                    ack_msg = json.dumps({"type": "ack"}).encode()
                    try:
                        udp_sock.sendto(ack_msg, addr)
                    except:
                        pass  # Don't let ACK failures block frame processing
                
                frame_num = msg.get("frame_num")
                chunk_id = msg.get("chunk_id")
                total_chunks = msg.get("total_chunks")
                chunk_data_hex = msg.get("data")
                origin = msg.get("origin")
                video_number = int(msg.get("video_number"))
                total_frames = int(msg.get("total_frames_incoming"))
                
                

                # Remap frame number to global index
                global_frame_num = frame_num + current_segment_offset
                
                
                
                # Store chunk
                chunk_data = base64.b64decode(chunk_data_hex)
                if global_frame_num not in frame_buffers:
                    frame_buffers[global_frame_num] = {}
                frame_buffers[global_frame_num][chunk_id] = chunk_data
                frame_chunks_expected[global_frame_num] = total_chunks
                
                # Check if we have all chunks for this frame
                if len(frame_buffers[global_frame_num]) == total_chunks:
                    # Reassemble frame
                    compressed_data = b''.join(
                        frame_buffers[global_frame_num][i] for i in range(total_chunks)
                    )
                    
                    try:
                        # Decompress and decode
                        decompressed = zlib.decompress(compressed_data)
                        buffer = pickle.loads(decompressed)
                        frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
                        
                        if frame is not None:
                            # Save frame to disk (backup)
                            frame_path = f"{output_dir}/frame_{global_frame_num:06d}.jpg"
                            cv2.imwrite(frame_path, frame)
                            
                            if global_frame_num % 30 == 0:
                                print(f"[{name}] Received frame {global_frame_num} from {origin}")
                                log(name, f"RECV_FRAME {global_frame_num} from {origin}")
                            
                            frame_count += 1
                            received_frames.add(global_frame_num)
                            
                            # Store frame for later playback with global frame number

                            if video_number not in all_collections.keys():
                                buffer = [None] * total_frames
                                all_collections[video_number] = buffer
                                readyQueue.append(0)


                            all_collections[video_number][frame_num] = frame
                            
                            
                            # Forward to next peer
                            if nextPeers:
                                nxt = nextPeers[0]['port']
                                nxtCtrl = nextPeers[0]['ctrl_port']
                                acknowledgePeer(nxtCtrl)
                                # Re-send all chunks to next peer with original frame numbers
                                for cid in range(total_chunks):
                                    # Wait for ACK from previous chunk before sending next chunk
                                    
                                    packet = {
                                        "total_frames_incoming": total_frames,
                                        "video_number": video_number,
                                        "type": "video_frame",
                                        "origin": origin,
                                        "frame_num": frame_num,  # Send original frame_num, not global
                                        "chunk_id": cid,
                                        "total_chunks": total_chunks,
                                        "data": base64.b64encode(frame_buffers[global_frame_num][cid]).decode('ascii')
                                    }
                                    try:
                                        udp_sock.sendto(json.dumps(packet).encode(), ("127.0.0.1", nxt))
                                    except Exception as e:
                                        log(name, f"FORWARD_ERROR frame {global_frame_num} chunk {cid}: {e}")
                                        ack_event.set()  # Reset on error to avoid permanent block
                                        break
                                
                                if global_frame_num % 30 == 0:
                                    log(name, f"FORWARDED frame {global_frame_num} to {nextPeers[0]['name']}:{nxt}")
                        
                        # Clean up buffer
                        del frame_buffers[global_frame_num]
                        del frame_chunks_expected[global_frame_num]
                        
                    except Exception as e:
                        print(f"[{name}] Error decoding frame {global_frame_num}: {e}")
                        log(name, f"DECODE_ERROR frame {global_frame_num}: {e}")
            
            elif typ == "video_end":
                endTime = time.perf_counter()

                print(f"Total time: {endTime - startTime}")
                startTime = 0
                endTime = 0
                frame_num = msg.get("frame_num")
                video_number = int(msg.get("video_number"))
                origin = msg.get("origin")
                print(f"[{name}] End of video stream from {origin}. Segment frames received: {frame_count}")
                log(name, f"VIDEO_END from {origin}, segment frames: {frame_count}")
                
                targetArray = all_collections[video_number]

                missingFrames = []

                for i in range (len(targetArray)):
                    if targetArray[i] is None:
                        missingFrames.append(i)

                print(f"[{name}] Missing {len(missingFrames)} frames: {missingFrames[:10]}{'...' if len(missingFrames) > 10 else ''}")
                log(name, f"Missing frames in video #{video_number}: {missingFrames}")
                if missingFrames:
                    print(f"[{name}] Missing {len(missingFrames)} frames: {missingFrames[:10]}{'...' if len(missingFrames) > 10 else ''}")
                    log(name, f"Missing frames in video #{video_number}: {missingFrames}")
                    
                    # Request missing frames from subcoordinator
                    try:
                        with socket.create_connection((COORD_HOST, SUB_COORD_PORT), timeout=5) as s:
                            request_msg = json.dumps({
                                "type": "requestMissingFrames",
                                "peer_name": name,
                                "peer_port": udp_port,
                                "video_number": video_number,
                                "missing_frames": missingFrames
                            })
                            s.sendall(request_msg.encode())
                            resp = s.recv(1024).decode()
                            print(f"[{name}] Requested {len(missingFrames)} missing frames from subcoordinator for video #{video_number}: {resp}")
                            log(name, f"Requested {len(missingFrames)} missing frames for video #{video_number}, response: {resp}")
                    except Exception as e:
                        print(f"[{name}] Failed to request missing frames: {e}")
                        log(name, f"MISSING_FRAMES_REQUEST_FAILED: {e}")
                else:
                    print(f"[{name}] All frames received successfully for video #{video_number}!")








                if len(missingFrames) == 0:
                    readyQueue[video_number-1] = True
                    video_segments.append((segment_start_frame, total_frames-1))
                
                # Update total frames and offset for next segment
                total_video_frames += frame_count
                current_segment_offset = total_video_frames  # Next video starts here
                segment_start_frame = total_video_frames
                frame_count = 0  # Reset for next video
                
                
                print(f"Ready Queue length: {len(readyQueue)}")

                for i in range(len(readyQueue)):
                    print(i)
                    if readyQueue[i] != 0:
                        if readyQueue[i] == 1:
                            targetArray = all_collections[i+1]
                            all_frames.extend(targetArray)
                            del all_collections[i+1]
                            readyQueue[i] = 2
                            print(f"popped {i+1}")
                    else:
                        break
                        






                # Forward end marker
                if nextPeers:
                    nxt = nextPeers[0]['port']
                    try:
                        udp_sock.sendto(data, ("127.0.0.1", nxt))
                        log(name, f"FORWARDED video_end to {nextPeers[0]['name']}:{nxt}")
                    except Exception as e:
                        log(name, f"FORWARD_ERROR video_end: {e}")
                    else:
                        nxt = None
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
                
                if nextPeers:
                    nxt = nextPeers[0]['port']
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

def ctrl_server(ctrl_port, name):
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
                    next_name = msg.get("name")
                    next_port = msg.get("port")
                    print(f"[{name} CTRL] UPDATE_NEXT -> {next_name}:{next_port}")
                    nextPeers.append(msg)
                    log(name, f"CTRL UPDATE_NEXT -> {next_name}:{next_port}")
                    
                    try:
                        conn.sendall(b"OK")
                    except:
                        pass
                #legacy command
                elif msg.get("cmd") == "SUBCOORDINATOR_INFO":
                    global SUB_COORD_PORT
                    SUB_COORD_PORT = int(msg.get("subcoordinator_port"))
                    prev_peer = None
                    if "prev_peer" in msg:
                        prev_peer = msg['prev_peer']['name']
                    print(f"Subcoordinator at port {SUB_COORD_PORT} registered")
                    print(f"Previous Peer: {prev_peer}")
                elif msg.get("cmd") == "ack":
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
    
def stdin_reader(q):
    '''Reads real lines from stdin in a separate thread.'''
    for line in sys.stdin:
        q.put(line.strip())

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
        with socket.create_connection((COORD_HOST, COORD_PORT), timeout=10) as s:
            s.sendall(json.dumps(reg).encode())
            resp = s.recv(8192).decode()
            meta_info = json.loads(resp) if resp else {}
            if "message" in meta_info:
                print(meta_info["message"])
    except Exception as e:
        print(f"[{name}] Failed to register with coordinator: {e}")
        sys.exit(1)


    


    # UDP socket for data
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp_sock.bind(("127.0.0.1", udp_port))

    # seen messages (to avoid duplicates)
    seen = set()

    # start threads
    t_udp = threading.Thread(target=udp_listener, args=(udp_sock, name, seen), daemon=True)
    t_udp.start()
    t_ctrl = threading.Thread(target=ctrl_server, args=(ctrl_port, name), daemon=True)
    t_ctrl.start()

    input_queue = queue.Queue()
    threading.Thread(target=stdin_reader, args=(input_queue,), daemon=True).start()

    # Create OpenCV window for video display (must be done in main thread on macOS)
    window_name = f"Peer {name} - Video"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    
    # interactive loop for any peer: supports:
    # - plain text: send a data message originating from this peer downstream
    # - sendto <peername> <message...> : send directly to named peer (bypass chain) by querying coordinator
    seq = 0
    print(f"[{name}] Interactive sender ready. Commands:")
    print("  <text>                     -> broadcast downstream as origin")
    print("  sendto <peername> <text>   -> send directly to peername UDP port (bypass chain)")
    print("  list                       -> ask coordinator for list of peers")
    print("  quit / Ctrl+C              -> exit")
    print("\n  Video will play automatically when data is received")
    print("  Video playback controls (during playback):")
    print("    SPACE    -> pause/resume")
    print("    LEFT     -> rewind 10 frames")
    print("    RIGHT    -> forward 10 frames")
    print("    q        -> quit playback")

    import select
    
    # Video playback state
    playing = False
    paused = False
    current_frame_idx = 0
    fps = 30  # Default FPS
    last_total_frames = 0  # Track when new segments are added
    
    while running:




        # Auto-start playback when video becomes available
        if not playing and video_complete and len(all_frames) > 0:
            playing = True
            print(f"[{name}] Auto-starting video playback ({total_video_frames} frames)")
        
        # Check if new frames were added while playing
        if playing and total_video_frames > last_total_frames:
            print(f"[{name}] New video segment received! Total frames now: {total_video_frames}")
            last_total_frames = total_video_frames
        
        # Video playback mode
        if playing and video_complete:
            if not paused and current_frame_idx < total_video_frames:
                # Display current frame with status bar
                if all_frames[current_frame_idx] is not None:
                    frame = all_frames[current_frame_idx].copy()
                    
                    # Add status bar overlay
                    h, w = frame.shape[:2]
                    bar_height = 60
                    
                    # Create semi-transparent overlay for status bar
                    overlay = frame.copy()
                    cv2.rectangle(overlay, (0, h - bar_height), (w, h), (0, 0, 0), -1)
                    cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)
                    
                    # Calculate playback progress
                    progress = current_frame_idx / max(total_video_frames - 1, 1)
                    progress_bar_width = int((w - 40) * progress)
                    
                    # Draw progress bar
                    cv2.rectangle(frame, (20, h - 45), (w - 20, h - 35), (100, 100, 100), -1)
                    cv2.rectangle(frame, (20, h - 45), (20 + progress_bar_width, h - 35), (0, 255, 0), -1)
                    
                    # Calculate time
                    current_time = current_frame_idx / fps
                    total_time = total_video_frames / fps
                    time_str = f"{int(current_time // 60):02d}:{int(current_time % 60):02d} / {int(total_time // 60):02d}:{int(total_time % 60):02d}"
                    
                    # Status text
                    status_text = "PAUSED" if paused else "PLAYING"
                    frame_info = f"Frame: {current_frame_idx}/{total_video_frames}"
                    
                    # Add text to status bar
                    cv2.putText(frame, status_text, (20, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
                    cv2.putText(frame, time_str, (w // 2 - 80, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
                    cv2.putText(frame, frame_info, (w - 200, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
                    
                    cv2.imshow(window_name, frame)
                    key = cv2.waitKey(int(1000 / fps))  # Wait based on FPS
                    
                    # Handle keyboard controls
                    if key == ord(' '):  # Space bar
                        paused = not paused
                        status = "PAUSED" if paused else "PLAYING"
                        print(f"[{name}] Video {status} at frame {current_frame_idx}/{total_video_frames}")
                    elif key == 81 or key == 2:  # Left arrow (different codes on different systems)
                        current_frame_idx = max(0, current_frame_idx - 10)
                        print(f"[{name}] Rewind to frame {current_frame_idx}/{total_video_frames}")
                    elif key == 83 or key == 3:  # Right arrow
                        current_frame_idx = min(total_video_frames - 1, current_frame_idx + 10)
                        print(f"[{name}] Forward to frame {current_frame_idx}/{total_video_frames}")
                    elif key == ord('q'):  # Quit playback
                        playing = False
                        paused = False
                        current_frame_idx = 0
                        print(f"[{name}] Stopped video playback")
                        continue
                    
                    if not paused:
                        current_frame_idx += 1
                else:
                    current_frame_idx += 1
                
            elif current_frame_idx >= total_video_frames:
                # Video reached end - pause instead of looping
                if not paused:
                    paused = True
                    current_frame_idx = total_video_frames - 1  # Stay on last frame
                    print(f"[{name}] Video playback reached end. PAUSED on last frame.")
                    print(f"[{name}] Press SPACE to resume if more content arrives.")
                # Show the last frame while paused
                if current_frame_idx in all_frames:
                    frame = all_frames[current_frame_idx].copy()
                    
                    # Add status bar overlay
                    h, w = frame.shape[:2]
                    bar_height = 60
                    
                    # Create semi-transparent overlay for status bar
                    overlay = frame.copy()
                    cv2.rectangle(overlay, (0, h - bar_height), (w, h), (0, 0, 0), -1)
                    cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)
                    
                    # Calculate playback progress
                    progress = current_frame_idx / max(total_video_frames - 1, 1)
                    progress_bar_width = int((w - 40) * progress)
                    
                    # Draw progress bar
                    cv2.rectangle(frame, (20, h - 45), (w - 20, h - 35), (100, 100, 100), -1)
                    cv2.rectangle(frame, (20, h - 45), (20 + progress_bar_width, h - 35), (0, 255, 0), -1)
                    
                    # Calculate time
                    current_time = current_frame_idx / fps
                    total_time = total_video_frames / fps
                    time_str = f"{int(current_time // 60):02d}:{int(current_time % 60):02d} / {int(total_time // 60):02d}:{int(total_time % 60):02d}"
                    
                    # Status text
                    status_text = "PAUSED - END"
                    frame_info = f"Frame: {current_frame_idx}/{total_video_frames}"
                    
                    # Add text to status bar
                    cv2.putText(frame, status_text, (20, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
                    cv2.putText(frame, time_str, (w // 2 - 80, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
                    cv2.putText(frame, frame_info, (w - 200, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
                    
                    cv2.imshow(window_name, frame)
                
                # Check for keyboard input
                key = cv2.waitKey(100)
                if key == ord(' '):
                    # If unpause and more frames available, continue
                    if current_frame_idx < total_video_frames - 1:
                        paused = False
                        print(f"[{name}] Video PLAYING from frame {current_frame_idx}/{total_video_frames}")
                    else:
                        print(f"[{name}] At end of video. Waiting for more content...")
                elif key == 81 or key == 2:  # Left arrow
                    current_frame_idx = max(0, current_frame_idx - 10)
                    print(f"[{name}] Rewind to frame {current_frame_idx}/{total_video_frames}")
                elif key == 83 or key == 3:  # Right arrow
                    current_frame_idx = min(total_video_frames - 1, current_frame_idx + 10)
                    print(f"[{name}] Forward to frame {current_frame_idx}/{total_video_frames}")
                elif key == ord('q'):
                    playing = False
                    paused = False
                    current_frame_idx = 0
                    print(f"[{name}] Stopped video playback")
            elif paused:
                # Just wait for key input while paused (show frame with status bar)
                
                frame = all_frames[current_frame_idx].copy()
                
                # Add status bar overlay
                h, w = frame.shape[:2]
                bar_height = 60
                
                # Create semi-transparent overlay for status bar
                overlay = frame.copy()
                cv2.rectangle(overlay, (0, h - bar_height), (w, h), (0, 0, 0), -1)
                cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)
                
                # Calculate playback progress
                progress = current_frame_idx / max(total_video_frames - 1, 1)
                progress_bar_width = int((w - 40) * progress)
                
                # Draw progress bar
                cv2.rectangle(frame, (20, h - 45), (w - 20, h - 35), (100, 100, 100), -1)
                cv2.rectangle(frame, (20, h - 45), (20 + progress_bar_width, h - 35), (0, 255, 0), -1)
                
                # Calculate time
                current_time = current_frame_idx / fps
                total_time = total_video_frames / fps
                time_str = f"{int(current_time // 60):02d}:{int(current_time % 60):02d} / {int(total_time // 60):02d}:{int(total_time % 60):02d}"
                
                # Status text
                status_text = "PAUSED"
                frame_info = f"Frame: {current_frame_idx}/{total_video_frames}"
                
                # Add text to status bar
                cv2.putText(frame, status_text, (20, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
                cv2.putText(frame, time_str, (w // 2 - 80, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
                cv2.putText(frame, frame_info, (w - 200, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
                
                cv2.imshow(window_name, frame)
                
                key = cv2.waitKey(100)
                if key == ord(' '):
                    paused = False
                    print(f"[{name}] Video PLAYING at frame {current_frame_idx}/{total_video_frames}")
                elif key == 81 or key == 2:  # Left arrow
                    current_frame_idx = max(0, current_frame_idx - 10)
                    print(f"[{name}] Rewind to frame {current_frame_idx}/{total_video_frames}")
                    # Redraw frame with status bar will happen on next loop iteration
                elif key == 83 or key == 3:  # Right arrow
                    current_frame_idx = min(total_video_frames - 1, current_frame_idx + 10)
                    print(f"[{name}] Forward to frame {current_frame_idx}/{total_video_frames}")
                    # Redraw frame with status bar will happen on next loop iteration
                elif key == ord('q'):
                    playing = False
                    paused = False
                    current_frame_idx = 0
                    print(f"[{name}] Stopped video playback")
        
        # Check for user input with timeout so we can keep processing frames
        # Modified to be compatible with windows
        try:
            line = input_queue.get_nowait()
        except queue.Empty:
            # No input ready, continue looping
            time.sleep(0.01)
            continue

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
        if nextPeers:
            nxt = nextPeers[0]['port']
            print(nxt)
            try:
                udp_sock.sendto(json.dumps(payload).encode(), ("127.0.0.1", nxt))
                print(f"[{name}] Originated seq={seq} -> forwarded to {nextPeers[0]['name']}:{nxt}")
                log(name, f"SENT origin={name} seq={seq} to {nextPeers[0]['name']}:{nxt} msg={line}")
                # mark as seen so origin doesn't get processed again on receipt
                seen.add((name, seq))
            except Exception as e:
                print(f"[{name}] send error: {e}")
                log(name, f"SEND_ERROR origin={name} seq={seq} err={e}")
        else:
            # no downstream yet — just log it (or optionally queue)
            print(f"[{name}] No next peer yet — message queued (not sent).")
            #log(name, f"QUEUED origin={name} seq={seq} msg={line}")

    # shutdown
    running = False
    print(f"[{name}] Shutting down, waiting for threads...")
    t_udp.join(timeout=1.0)
    t_ctrl.join(timeout=1.0)
    try:
        udp_sock.close()
    except:
        pass
    try:
        cv2.destroyAllWindows()
    except:
        pass
    print(f"[{name}] Exit.")

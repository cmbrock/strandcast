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
import cv2
import numpy as np
import pickle
import zlib
import base64
from collections import defaultdict

HOST = None
SUPER_COORD_PORT = None
COORD_PORT = None
MAX_DGRAM = 60000
MAX_CHUNK_SIZE = 5000  # Match coordinator chunk size
FILE_COUNT = 1
VIDEO_FILE = None
COUNT = 0
BUFFER = 3

# Frame buffer for storing compressed chunks - nested structure: video_number -> frame_num -> [chunks]
frame_buffers = defaultdict(lambda: defaultdict(list))  # video_number -> frame_num -> [chunks array]
frame_chunks_expected = defaultdict(dict)  # video_number -> frame_num -> total_chunks

peers = []            # list of dicts: {"name":..., "port":..., "ctrl_port":...}
lock = threading.Lock()
running = True
downstream_started = False  # flag to ensure downstream thread starts only once
video_streaming = False

def notify_next(ctrl_port, next_name, next_port, next_ctrl_port):
    """Notify a peer's control server about its new downstream neighbor."""
    try:
        with socket.create_connection((HOST, ctrl_port), timeout=2) as s:
            msg = json.dumps({"cmd": "UPDATE_NEXT", "name": next_name, "port": next_port, "ctrl_port": next_ctrl_port})
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


def get_total_frames(video_path):
    """Calculate the total number of frames in a video file."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[Subcoordinator] ERROR: Cannot open video file {video_path} for frame counting")
        return 0
    
    # Fast frame counting using grab()
    total = 0
    while cap.grab():
        total += 1
    
    cap.release()
    print(f"[Subcoordinator] Total frames in {video_path}: {total}")
    return total


def stream_video():
    """Stream video file to the first peer in the chain."""
    global running, video_streaming, FILE_COUNT

    if FILE_COUNT > 3:
            print("all videos have been processed")
            video_streaming = False
            return

    # Store the current video number to avoid race conditions
    current_video_number = FILE_COUNT
    VIDEO_FILE = f"videoFiles/test{current_video_number}.mp4"

    with lock:
        if not peers:
            print("[Coordinator] No peers registered, cannot stream video.")
            return
        first_peer = peers[0]
    
    print(f"[Coordinator] Starting video stream to first peer: {first_peer['name']}:{first_peer['port']}")
    
    # Calculate total frames before streaming
    total_frames = get_total_frames(VIDEO_FILE)
    if total_frames == 0:
        print(f"[Coordinator] ERROR: Video file {VIDEO_FILE} has no frames or cannot be read")
        return
    
    cap = cv2.VideoCapture(VIDEO_FILE)
    if not cap.isOpened():
        print(f"[Coordinator] ERROR: Cannot open video file {VIDEO_FILE}")
        return
    
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_delay = 1.0 / fps if fps > 0 else 0.033  # default to ~30fps
    
    # Reduce delay for faster transmission (send at 2x speed)
    frame_delay = 0
    
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    frame_num = 0
    
    print(f"[Coordinator] Video FPS: {fps}, Frame delay: {frame_delay:.4f}s (4x speed)")
    video_streaming = True
    
    while running and video_streaming:
        ret, frame = cap.read()
        if not ret:
            print("[Coordinator] End of video file reached.")
            # Send end-of-stream marker
            end_msg = {
                "type": "video_end",
                "origin": "coordinator",
                "frame_num": frame_num,
                "video_number": FILE_COUNT
            }
            try:
                udp_sock.sendto(json.dumps(end_msg).encode(), (HOST, first_peer['port']))
            except:
                pass
            break
        
        # Encode frame: compress with JPEG then pickle
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 40]  # Lower quality for smaller size
        _, buffer = cv2.imencode('.jpg', frame, encode_param)
        data = pickle.dumps(buffer)
        
        # Compress with zlib
        compressed = zlib.compress(data, 6)
        size = len(compressed)
        
        # Split into chunks if needed
        total_chunks = (size + MAX_CHUNK_SIZE - 1) // MAX_CHUNK_SIZE
        
        # Initialize frame buffer array
        if not frame_buffers[current_video_number][frame_num]:
            frame_buffers[current_video_number][frame_num] = [None] * total_chunks
            frame_chunks_expected[current_video_number][frame_num] = total_chunks
        
        for chunk_id in range(total_chunks):
            start = chunk_id * MAX_CHUNK_SIZE
            end = min(start + MAX_CHUNK_SIZE, size)
            chunk_data = compressed[start:end]
            
            # Store chunk in buffer for potential resending
            frame_buffers[current_video_number][frame_num][chunk_id] = chunk_data
            
            # Create packet with metadata
            packet = {
                "total_frames_incoming": total_frames,
                "video_number" : current_video_number,
                "type": "video_frame",
                "origin": "coordinator",
                "frame_num": frame_num,
                "chunk_id": chunk_id,
                "total_chunks": total_chunks,
                "data": base64.b64encode(chunk_data).decode('ascii') 
            }
            
            try:
                msg = json.dumps(packet).encode()
                if len(msg) > MAX_DGRAM:
                    print(f"[Coordinator] WARNING: Message too large ({len(msg)} bytes) for frame {frame_num} chunk {chunk_id}")
                udp_sock.sendto(msg, (HOST, first_peer['port']))
                # Small delay between chunks to avoid overwhelming UDP buffer
                if chunk_id < total_chunks - 1:  # Don't delay after last chunk
                    time.sleep(0.0001)  # 0.1ms delay between chunks
            except Exception as e:
                print(f"[Coordinator] Error sending frame {frame_num} chunk {chunk_id}: {e}")
        
        if frame_num % 30 == 0:
            print(f"[Coordinator] Sent frame {frame_num} ({total_chunks} chunks, {size} bytes compressed)")
        
        frame_num += 1
        time.sleep(frame_delay)
    
    FILE_COUNT += 1
    cap.release()
    udp_sock.close()
    video_streaming = False
    print("[Subcoordinator] Video streaming stopped.")

def downstream():
    """Send file1.txt content to peer 1 and downstream."""
    print("[Subcoordinator] Starting downstream thread...")
    global downstream_started
    global FILE_COUNT

    time.sleep(7)

    try:

        if FILE_COUNT > 5:
            print("all files have been processed")
            downstream_started = False
            return

        # Read file1.txt
        with open(f'text_folder/file{FILE_COUNT}.txt', 'r') as f:
            content = f.read()
        
        print(f"[Subcoordinator] Read file{FILE_COUNT}.txt: {len(content)} bytes")
        
        # Get peer 1 (first peer in the list)
        with lock:
            if len(peers) < 1:
                print("[Subcoordinator] No peers available for downstream")
                return
            first_peer = peers[0]
        
        # Send to peer 1 via UDP
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        message = json.dumps({
            "type": "data",
            "origin": "subcoordinator",
            "seq": FILE_COUNT,
            "msg": content,
            "direct": False
        })
        
        sock.sendto(message.encode(), (HOST, first_peer["port"]))
        print(f"[Subcoordinator] Sent file{FILE_COUNT}.txt to {first_peer['name']} (port {first_peer['port']})")
        downstream_started = False
        sock.close()
        FILE_COUNT += 1
        
        
    except FileNotFoundError:
        print("[Subcoordinator] Error: text_folder/file1.txt not found")
    except Exception as e:
        print(f"[Subcoordinator] Error in downstream: {e}")



def is_authorized_peer(requester_name):
    """Check if the requesting peer is in the authorized peers list."""
    with lock:
        for p in peers:
            if p["name"] == requester_name:
                return True
    return False


def register_peer(req, conn):
    global downstream_started
    global BUFFER
    global COUNT
    global video_streaming
    name = req.get("name") or f"peer{len(peers)+1}"
    port = int(req["port"])
    ctrl_port = int(req["ctrl_port"])
    with lock:
        entry = {"name": name, "port": port, "ctrl_port": ctrl_port}
        peers.append(entry)
        print(f"[Subcoordinator] Registered {name} (udp={port}, ctrl={ctrl_port})")
        prev = peers[-2] if len(peers) > 1 else None
        COUNT += 1
        
        # Start downstream thread when we have 3 peers
        if COUNT == BUFFER and not video_streaming:
            COUNT = 0
            BUFFER = 0
            video_streaming = True
            threading.Thread(target=stream_video, daemon=True).start()
            
    # reply with previous peer info (or empty object)
    conn.sendall(json.dumps(prev or {}).encode())
    # notify previous peer about its new next
    count = 0
    for i in range(len(peers)-2, -1, -1):
        if count >= 3: break
        prev = peers[i]
        notify_next(prev["ctrl_port"], entry["name"], entry["port"], entry["ctrl_port"])
        count += 1


def delivery_done(conn):
    global downstream_started
    global BUFFER
    global video_streaming 
    # Send next file downstream
    print(f"[Subcoordinator] Received deliveryDone, downstream_started={downstream_started}")
    conn.sendall(json.dumps({"status": "acknowledged"}).encode())
    
    # Wait for video streaming to complete before proceeding
    print(f"[Subcoordinator] Waiting for stream_video to complete (video_streaming={video_streaming})...")
    wait_count = 0
    while video_streaming and wait_count < 300:  # Wait up to 30 seconds (300 * 0.1s)
        time.sleep(0.1)
        wait_count += 1
    
    if video_streaming:
        print(f"[Subcoordinator] WARNING: stream_video still running after timeout")
    else:
        print(f"[Subcoordinator] stream_video completed, proceeding with delivery_done")
    
    #Notify coordinator about completion and get new buffer size
    try:
        with socket.create_connection((HOST, SUPER_COORD_PORT), timeout=5) as coord_s:
            status_msg = {"action": "status", "type": "status", "status": "done", "port": COORD_PORT}
            coord_s.sendall(json.dumps(status_msg).encode())
            resp = coord_s.recv(8192).decode()
            reply = json.loads(resp) if resp else {}
            
            # Update buffer size from coordinator response
            if "buffer" in reply:
                new_buffer = int(reply["buffer"])
                print(f"[Subcoordinator] Received new buffer size from coordinator: {new_buffer}")
                BUFFER = new_buffer
            else:
                print(f"[Subcoordinator] Coordinator response: {reply}")
                
    except Exception as e:
        print(f"[Subcoordinator] Failed to notify coordinator about completion: {e}")
    
    if BUFFER == 0 and not video_streaming:
        print("inside")
        print("[Subcoordinator] Starting new downstream thread")
        video_streaming = True
        threading.Thread(target=stream_video, daemon=True).start()


def lookup(req, conn):
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


def list_items(req, conn):
    requester = req.get("requester")
    if not requester or not is_authorized_peer(requester):
        print(f"[Subcoordinator] Unauthorized list attempt from {requester or 'unknown'}")
        conn.sendall(json.dumps({"error": "unauthorized"}).encode())
        return
    
    with lock:
        conn.sendall(json.dumps(peers).encode())


def request_missing_frames(req, conn):
    """Handle request for missing frames from a peer."""
    peer_name = req.get("peer_name")
    peer_port = req.get("peer_port")
    video_number = req.get("video_number")
    missing_frames = req.get("missing_frames", [])
    
    print(f"[Subcoordinator] Received request for {len(missing_frames)} missing frames from {peer_name} (video #{video_number})")
    
    # Send acknowledgment
    conn.sendall(json.dumps({"status": "ok", "message": f"Resending {len(missing_frames)} frames"}).encode())
    
    # Spawn thread to resend missing frames
    threading.Thread(target=resend_missing_frames, args=(video_number, missing_frames, peer_port), daemon=True).start()


def resend_missing_frames(video_number, frame_numbers, peer_port):
    """Resend specific frames from frame_buffer to a peer."""
    
    print(f"[Subcoordinator] Starting to resend {len(frame_numbers)} frames from frame_buffer to port {peer_port}")
    print(f"[Subcoordinator] Video number: {video_number}, Frame numbers: {frame_numbers[:10]}{'...' if len(frame_numbers) > 10 else ''}")
    
    # Debug: Check what's in the buffer
    print(f"[Subcoordinator] Available videos in frame_buffers: {list(frame_buffers.keys())}")
    if video_number in frame_buffers:
        available_frames = [k for k, v in frame_buffers[video_number].items() if v]
        print(f"[Subcoordinator] Available frames for video {video_number}: {len(available_frames)} frames")
        if available_frames:
            print(f"[Subcoordinator] Sample available frames: {available_frames[:10]}{'...' if len(available_frames) > 10 else ''}")
    
    # Get total frames for this video
    VIDEO_FILE = f"videoFiles/test{video_number}.mp4"
    total_frames = get_total_frames(VIDEO_FILE)
    
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    resent_count = 0
    missing_in_buffer = 0
    for frame_num in frame_numbers:
        # Check if frame exists in buffer (check if it's not an empty list)
        if not frame_buffers[video_number][frame_num]:
            missing_in_buffer += 1
            if missing_in_buffer <= 5:  # Only print first 5
                print(f"[Subcoordinator] WARNING: Frame {frame_num} not found in frame_buffer for video {video_number}")
            continue
        
        # Get chunks and total_chunks for this frame
        chunks_array = frame_buffers[video_number][frame_num]
        total_chunks = frame_chunks_expected[video_number].get(frame_num, len(chunks_array))
        
        # Check if all chunks are available
        if None in chunks_array or len(chunks_array) != total_chunks:
            print(f"[Subcoordinator] WARNING: Frame {frame_num} incomplete ({len([c for c in chunks_array if c is not None])}/{total_chunks} chunks)")
            continue
        
        # Resend all chunks for this frame
        for chunk_id in range(total_chunks):
            chunk_data = chunks_array[chunk_id]
            
            # Create packet with metadata (include total_frames_incoming for compatibility)
            packet = {
                "video_number": video_number,
                "type": "video_frame",
                "origin": "coordinator",
                "frame_num": frame_num,
                "chunk_id": chunk_id,
                "total_chunks": total_chunks,
                "total_frames_incoming": total_frames,
                "data": base64.b64encode(chunk_data).decode('ascii')
            }
            
            try:
                msg = json.dumps(packet).encode()
                udp_sock.sendto(msg, (HOST, peer_port))
                time.sleep(0.0001)  # Small delay between chunks
            except Exception as e:
                print(f"[Subcoordinator] Error resending frame {frame_num} chunk {chunk_id}: {e}")
        
        resent_count += 1
        if frame_num % 10 == 0 or resent_count <= 5:
            print(f"[Subcoordinator] Resent frame {frame_num} ({total_chunks} chunks)")
    
    if missing_in_buffer > 5:
        print(f"[Subcoordinator] ... and {missing_in_buffer - 5} more frames not found in buffer")
    
    print(f"[Subcoordinator] Completed resending {resent_count}/{len(frame_numbers)} frames (missing in buffer: {missing_in_buffer})")
    
    # Send video_end message after all missing frames have been resent
    end_msg = {
        "type": "video_end",
        "origin": "coordinator",
        "video_number": video_number,
        "frame_num": max(frame_numbers) if frame_numbers else 0
    }
    try:
        udp_sock.sendto(json.dumps(end_msg).encode(), (HOST, peer_port))
        print(f"[Subcoordinator] Sent video_end confirmation to port {peer_port} after recovery")
    except Exception as e:
        print(f"[Subcoordinator] Error sending video_end after recovery: {e}")
    
    udp_sock.close()
    print(f"[Subcoordinator] Frame recovery complete for video {video_number}")


def handle_connection(conn, addr):
    global downstream_started
    try:
        raw = conn.recv(8192).decode()
        if not raw:
            return
        req = json.loads(raw)
        typ = req.get("type")
        if typ == "register":
            register_peer(req, conn)
        elif typ == "deliveryDone":
           delivery_done(conn)
        elif typ == "lookup":
            lookup(req, conn)
        elif typ == "list":
            list_items(req, conn)
        elif typ == "requestMissingFrames":
            request_missing_frames(req, conn)
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


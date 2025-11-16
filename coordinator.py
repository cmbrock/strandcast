# coordinator.py
# Coordinator accepts peer registrations and lookup queries.
# It notifies the previous peer (via its control port) when a new peer joins
# so the previous peer can update its "next" pointer.
# Interactive mode: type 'startVideo' to stream testAyush.mp4 to peers

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

HOST = "127.0.0.1"
COORD_PORT = 9000
VIDEO_FILE = "strandcast/test.mp4"
MAX_DGRAM = 60000  # Max UDP datagram size (safe limit)
MAX_CHUNK_SIZE = 5000  # Very small chunks: 5KB data -> ~7KB base64 -> ~10KB with JSON

peers = []            # list of dicts: {"name":..., "port":..., "ctrl_port":...}
lock = threading.Lock()
running = True
video_streaming = False

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

def stream_video():
    """Stream video file to the first peer in the chain."""
    global running, video_streaming
    
    with lock:
        if not peers:
            print("[Coordinator] No peers registered, cannot stream video.")
            return
        first_peer = peers[0]
    
    print(f"[Coordinator] Starting video stream to first peer: {first_peer['name']}:{first_peer['port']}")
    
    cap = cv2.VideoCapture(VIDEO_FILE)
    if not cap.isOpened():
        print(f"[Coordinator] ERROR: Cannot open video file {VIDEO_FILE}")
        return
    
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_delay = 1.0 / fps if fps > 0 else 0.033  # default to ~30fps
    
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    frame_num = 0
    
    print(f"[Coordinator] Video FPS: {fps}, Frame delay: {frame_delay:.4f}s")
    video_streaming = True
    
    while running and video_streaming:
        ret, frame = cap.read()
        if not ret:
            print("[Coordinator] End of video file reached.")
            # Send end-of-stream marker
            end_msg = {
                "type": "video_end",
                "origin": "coordinator",
                "frame_num": frame_num
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
        
        for chunk_id in range(total_chunks):
            start = chunk_id * MAX_CHUNK_SIZE
            end = min(start + MAX_CHUNK_SIZE, size)
            chunk_data = compressed[start:end]
            
            # Create packet with metadata
            packet = {
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
            except Exception as e:
                print(f"[Coordinator] Error sending frame {frame_num} chunk {chunk_id}: {e}")
        
        if frame_num % 30 == 0:
            print(f"[Coordinator] Sent frame {frame_num} ({total_chunks} chunks, {size} bytes compressed)")
        
        frame_num += 1
        time.sleep(frame_delay)
    
    cap.release()
    udp_sock.close()
    video_streaming = False
    print("[Coordinator] Video streaming stopped.")

def interactive_loop():
    """Interactive command loop for coordinator."""
    global running, video_streaming
    
    print("[Coordinator] Interactive mode ready. Commands:")
    print("  startVideo  -> Stream testAyush.mp4 to peers")
    print("  list        -> Show registered peers")
    print("  quit        -> Exit coordinator")
    
    while running:
        try:
            line = input("> ")
        except EOFError:
            print("[Coordinator] Stdin closed (EOF). Exiting.")
            running = False
            break
        except KeyboardInterrupt:
            running = False
            break
        
        if not line:
            continue
        
        cmd = line.strip().lower()
        
        if cmd == "quit" or cmd == "exit":
            running = False
            break
        elif cmd == "startvideo":
            if video_streaming:
                print("[Coordinator] Video is already streaming!")
            else:
                # Start video streaming in a new thread
                threading.Thread(target=stream_video, daemon=True).start()
        elif cmd == "list":
            with lock:
                if peers:
                    print(f"[Coordinator] Registered peers ({len(peers)}):")
                    for p in peers:
                        print(f"  - {p['name']}: udp={p['port']}, ctrl={p['ctrl_port']}")
                else:
                    print("[Coordinator] No peers registered yet.")
        else:
            print(f"[Coordinator] Unknown command: {cmd}")
    
    print("[Coordinator] Shutting down...")

def sigint_handler(sig, frame):
    global running, video_streaming
    print("\n[Coordinator] Caught interrupt, shutting down...")
    running = False
    video_streaming = False

if __name__ == "__main__":
    signal.signal(signal.SIGINT, sigint_handler)
    
    # Start listener in separate thread
    listener_thread = threading.Thread(target=listener, daemon=True)
    listener_thread.start()
    
    # Run interactive loop in main thread
    interactive_loop()
    
    # Wait for listener to finish
    running = False
    listener_thread.join(timeout=2.0)
    print("[Coordinator] Shutdown complete.")

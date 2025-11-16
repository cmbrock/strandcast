# Interactive Video Streaming - Architecture

## System Overview

```
┌──────────────────────────────────────────────────────────────┐
│                     User Interaction                          │
└──────────────────────────────────────────────────────────────┘
                              │
                              │ Types "startVideo"
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                      COORDINATOR                              │
│                     (Port 9000 TCP)                           │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  1. Accept peer registrations                       │    │
│  │  2. Maintain peer chain (A→B→C→D)                  │    │
│  │  3. Wait for "startVideo" command                   │    │
│  │  4. Read testAyush.mp4                             │    │
│  │  5. For each frame:                                 │    │
│  │     - Encode as JPEG (quality=80)                   │    │
│  │     - Compress with zlib                            │    │
│  │     - Split into UDP chunks (<60KB)                 │    │
│  │     - Send to first peer (A)                        │    │
│  └─────────────────────────────────────────────────────┘    │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         │ UDP packets (video chunks)
                         ▼
┌──────────────────────────────────────────────────────────────┐
│                       PEER A                                  │
│                  (UDP: 10001, Ctrl: 20001)                   │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  1. Receive video chunks                            │    │
│  │  2. Buffer chunks by frame number                   │    │
│  │  3. When all chunks arrive:                         │    │
│  │     - Reassemble compressed data                    │    │
│  │     - Decompress with zlib                          │    │
│  │     - Decode JPEG to image                          │    │
│  │     - Display in OpenCV window                      │    │
│  │     - Save frame to peer_A_frames/                  │    │
│  │     - Forward chunks to Peer B                      │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                               │
│  ┌──────────────────────────────┐                           │
│  │  OpenCV Window                │                           │
│  │  "Peer A - Video Stream"      │                           │
│  │  [Video playing at 30fps]     │                           │
│  │  Press 'q' to close           │                           │
│  └──────────────────────────────┘                           │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         │ Forward chunks
                         ▼
┌──────────────────────────────────────────────────────────────┐
│                       PEER B                                  │
│                  (UDP: 10002, Ctrl: 20002)                   │
│                                                               │
│  Same process: Receive → Display → Forward                   │
│                                                               │
│  ┌──────────────────────────────┐                           │
│  │  OpenCV Window                │                           │
│  │  "Peer B - Video Stream"      │                           │
│  │  [Video playing at 30fps]     │                           │
│  └──────────────────────────────┘                           │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│                       PEER C                                  │
│                  (UDP: 10003, Ctrl: 20003)                   │
│                                                               │
│  Same process: Receive → Display → Forward                   │
│                                                               │
│  ┌──────────────────────────────┐                           │
│  │  OpenCV Window                │                           │
│  │  "Peer C - Video Stream"      │                           │
│  │  [Video playing at 30fps]     │                           │
│  └──────────────────────────────┘                           │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│                       PEER D                                  │
│                  (UDP: 10004, Ctrl: 20004)                   │
│                                                               │
│  Same process: Receive → Display (no forward, last peer)     │
│                                                               │
│  ┌──────────────────────────────┐                           │
│  │  OpenCV Window                │                           │
│  │  "Peer D - Video Stream"      │                           │
│  │  [Video playing at 30fps]     │                           │
│  └──────────────────────────────┘                           │
└──────────────────────────────────────────────────────────────┘
```

## Data Flow

### Frame Encoding (Coordinator)
```
Video Frame (1620x1080 RGB)
    ↓ cv2.imencode('.jpg', quality=80)
JPEG Buffer (~100-200 KB)
    ↓ pickle.dumps()
Serialized Data
    ↓ zlib.compress(level=6)
Compressed Data (~50-150 KB)
    ↓ Split into chunks
Chunks (each <60 KB)
    ↓ Convert to hex + JSON
UDP Packets
```

### Frame Decoding (Peers)
```
UDP Packets
    ↓ Parse JSON + hex decode
Chunks
    ↓ Buffer and reassemble
Compressed Data
    ↓ zlib.decompress()
Serialized Data
    ↓ pickle.loads()
JPEG Buffer
    ↓ cv2.imdecode()
Video Frame (1620x1080 RGB)
    ↓
┌────────────────┐
│ cv2.imshow()   │ → Display in window
└────────────────┘
│ cv2.imwrite()  │ → Save to disk
└────────────────┘
│ Forward chunks │ → Send to next peer
└────────────────┘
```

## Packet Structure

```json
{
    "type": "video_frame",
    "origin": "coordinator",
    "frame_num": 123,
    "chunk_id": 0,
    "total_chunks": 3,
    "data": "89504e470d0a1a0a..." // hex-encoded chunk data
}
```

## Control Flow

### Startup Sequence
```
1. run_network.py starts all processes
2. Coordinator starts TCP listener (port 9000)
3. Peers start UDP listeners + control servers
4. Peers register with coordinator
5. Coordinator updates peer chain (A→B→C→D)
6. All ready, waiting for command
```

### Video Streaming Sequence
```
1. User types "startVideo" in coordinator
2. Coordinator opens testAyush.mp4
3. For each frame (0-410):
   a. Encode + compress + chunk
   b. Send all chunks to Peer A
   c. Sleep for frame_delay (1/30 sec)
4. Send end-of-stream marker
5. Done!
```

### Peer Processing
```
1. Receive chunk via UDP
2. Store in buffer: frame_buffers[frame_num][chunk_id]
3. Check if all chunks received
4. If complete:
   a. Reassemble chunks
   b. Decompress + decode
   c. Display in OpenCV window
   d. Save to disk
   e. Forward to next peer
   f. Mark frame as processed
5. Continue listening
```

## Timing Diagram

```
Time →
Coord:  ─[Read]─[Encode]─[Send F0]─[Send F1]─[Send F2]─...
         0ms    10ms     15ms      48ms      81ms

Peer A:         ─────────[Recv F0]─[Display]─[Fwd]─[Recv F1]─...
                         15ms      20ms      22ms   48ms

Peer B:         ──────────────────────────[Recv F0]─[Display]─...
                                           22ms     27ms

Peer C:         ─────────────────────────────────[Recv F0]─...
                                                  29ms

Peer D:         ────────────────────────────────────────[Recv F0]
                                                        36ms
```

## Memory Usage

```
Coordinator:
- Video file loaded frame-by-frame: ~10 MB
- Encoding buffers: ~5 MB
- Total: ~15 MB

Each Peer:
- Frame buffers (incomplete frames): ~10-20 MB
- OpenCV display buffer: ~20 MB
- Decoded frame cache: ~10 MB
- Total: ~40-50 MB per peer
```

## Network Bandwidth

```
Per Frame:
- Original: 1620×1080×3 = 5.24 MB
- JPEG encoded: ~150 KB
- Compressed: ~100 KB
- Network overhead: +10%
- Total: ~110 KB/frame

Per Second (30 fps):
- Data: 110 KB × 30 = 3.3 MB/s
- Bandwidth: 26 Mbps

Per Hop (4 peers):
- Total network: 26 Mbps × 4 = 104 Mbps
```

## Key Features

✅ **Interactive Control**: Start streaming on demand
✅ **Real-time Display**: All peers show video simultaneously
✅ **Frame Buffering**: Reliable reassembly of UDP chunks
✅ **De-duplication**: Frames processed only once
✅ **Backup Storage**: Frames saved to disk
✅ **Chain Forwarding**: Efficient downstream propagation
✅ **Clean Shutdown**: Proper resource cleanup
✅ **Error Handling**: Graceful handling of network issues

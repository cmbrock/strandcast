# Usage Guide - Interactive Video Streaming

## Quick Start

### 1. Start the Network
```bash
python run_network.py
```

This opens 5 terminal windows:
- **Coordinator** - Command center (port 9000)
- **Peer A, B, C, D** - Video receivers/forwarders

### 2. Wait for Peers to Register
In the coordinator terminal, you'll see:
```
[Coordinator] Listening on 127.0.0.1:9000
[Coordinator] Registered A (udp=10001, ctrl=20001)
[Coordinator] Registered B (udp=10002, ctrl=20002)
[Coordinator] Registered C (udp=10003, ctrl=20003)
[Coordinator] Registered D (udp=10004, ctrl=20004)
[Coordinator] Interactive mode ready. Commands:
  startVideo  -> Stream testAyush.mp4 to peers
  list        -> Show registered peers
  quit        -> Exit coordinator
>
```

### 3. Start Video Streaming
In the **coordinator terminal**, type:
```
startVideo
```

Press Enter!

### 4. Watch the Magic Happen! 🎥

**What happens:**
1. Coordinator reads `testAyush.mp4`
2. Encodes each frame as JPEG + compresses
3. Sends to Peer A
4. Peer A displays the video in an OpenCV window AND forwards to Peer B
5. Peer B displays AND forwards to Peer C
6. Peer C displays AND forwards to Peer D
7. Peer D displays the video

**You'll see 4 video windows open simultaneously!**

### 5. Enjoy the Video

- **Resolution**: 1620x1080
- **Frame rate**: 30 fps
- **Duration**: ~13.7 seconds
- **Total frames**: 411

Each peer window shows: `Peer X - Video Stream`

### 6. Control Playback

**Close a video window:**
- Press `q` in the video window

**Stop a peer:**
- Type `quit` in the peer terminal, or
- Press Ctrl+C in the peer terminal

**Stop everything:**
- Type `quit` in the coordinator terminal

## Commands

### Coordinator Commands
```
startVideo  - Stream testAyush.mp4 to all peers
list        - Show registered peers
quit        - Shutdown coordinator
```

### Peer Commands
```
quit        - Exit this peer
```

## Expected Output

### Coordinator Terminal
```
[Coordinator] Starting video stream to first peer: A:10001
[Coordinator] Video FPS: 30.003650079085048, Frame delay: 0.0333s
[Coordinator] Sent frame 0 (3 chunks, 45678 bytes compressed)
[Coordinator] Sent frame 30 (3 chunks, 46234 bytes compressed)
...
[Coordinator] End of video file reached.
[Coordinator] Video streaming stopped.
```

### Peer Terminals
```
[A] Ready to receive video frames
[A] Frames will be saved to peer_A_frames/
[A] Press 'q' in video window to close display
[A] Displaying frame 0 from coordinator
[A] Displaying frame 30 from coordinator
...
[A] End of video stream from coordinator. Total frames received: 411
[A] Video playback complete. Window will remain open.
```

## Video Pipeline

```
┌─────────────┐
│ Coordinator │ Reads testAyush.mp4
│   (9000)    │ Encodes frames (JPEG)
└──────┬──────┘ Compresses (zlib)
       │        Chunks for UDP
       ▼
┌─────────────┐
│   Peer A    │ Receives chunks
│  (10001)    │ Reassembles frame
└──────┬──────┘ Decodes JPEG
       │        Displays in window
       │        Saves to disk
       │        Forwards chunks
       ▼
┌─────────────┐
│   Peer B    │ Same process
│  (10002)    │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Peer C    │ Same process
│  (10003)    │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Peer D    │ Same process
│  (10004)    │ (Last in chain)
└─────────────┘
```

## Frame Storage

Frames are automatically saved to:
```
peer_A_frames/frame_000000.jpg
peer_A_frames/frame_000001.jpg
...
peer_A_frames/frame_000410.jpg
```

Same for peers B, C, and D.

## Verifying Results

### Check frame counts
```bash
for peer in A B C D; do
    count=$(ls peer_${peer}_frames/*.jpg 2>/dev/null | wc -l)
    echo "Peer $peer: $count frames"
done
```

Expected output:
```
Peer A: 411 frames
Peer B: 411 frames
Peer C: 411 frames
Peer D: 411 frames
```

### Compare frames (verify forwarding)
```bash
md5 peer_A_frames/frame_000100.jpg
md5 peer_B_frames/frame_000100.jpg
md5 peer_C_frames/frame_000100.jpg
md5 peer_D_frames/frame_000100.jpg
```

All checksums should match!

### Reconstruct video
```bash
python reconstruct_video.py A
# Creates: reconstructed_A.mp4
```

Or manually:
```bash
ffmpeg -framerate 30 -i peer_A_frames/frame_%06d.jpg -c:v libx264 output.mp4
```

## Troubleshooting

### Video windows don't open
- Make sure you typed `startVideo` in the coordinator terminal
- Check that all peers are registered first (use `list` command)
- Verify OpenCV is installed: `python -c "import cv2; print(cv2.__version__)"`

### Only some peers show video
- Check peer terminals for error messages
- Verify UDP ports aren't blocked
- Check logs: `cat peer_X.log`

### Video is choppy
- Normal for high-resolution video over UDP
- Check network/CPU load
- Frames are saved correctly even if display stutters

### "Cannot open video file" error
- Ensure `testAyush.mp4` is in the project directory
- Check file permissions
- Try: `ls -lh testAyush.mp4`

## Advanced Usage

### Stream multiple times
In coordinator terminal:
```
> startVideo
[streaming happens...]
[Coordinator] Video streaming stopped.
> startVideo
[streams again!]
```

### Check peer list anytime
In coordinator terminal:
```
> list
[Coordinator] Registered peers (4):
  - A: udp=10001, ctrl=20001
  - B: udp=10002, ctrl=20002
  - C: udp=10003, ctrl=20003
  - D: udp=10004, ctrl=20004
```

### View specific frame
```bash
open peer_A_frames/frame_000100.jpg  # macOS
xdg-open peer_A_frames/frame_000100.jpg  # Linux
```

## Performance Metrics

**Expected performance for testAyush.mp4:**
- Streaming time: ~14 seconds (real-time)
- Bandwidth per hop: ~5-8 Mbps
- CPU per peer: 5-10%
- Memory per peer: 50-100 MB
- Latency between peers: 10-50ms
- Total network bandwidth: 20-32 Mbps (4 peers)

## Tips

1. **Start video when ready**: The `startVideo` command gives you control
2. **Multiple runs**: You can stream the video multiple times
3. **Partial playback**: Press 'q' to stop viewing anytime
4. **Frames are saved**: Even if you close the window, frames are saved
5. **Check logs**: Use `peer_X.log` files for detailed debugging

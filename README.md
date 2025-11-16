# StrandCast - P2P Video Streaming Network

A peer-to-peer video streaming system that distributes video frames downstream through a chain of peers.

## Features

- **Video Streaming**: Coordinator streams `testAyush.mp4` to the peer network
- **Frame Encoding**: JPEG compression + zlib compression for efficient transmission
- **UDP Chunking**: Large frames split into chunks for UDP transmission
- **Frame Buffering**: Each peer buffers and reassembles chunks before forwarding
- **De-duplication**: Frames are processed only once using frame numbers
- **Frame Storage**: Each peer saves received frames to `peer_<name>_frames/` directory
- **Separate Terminals**: Each peer runs in its own terminal window

## Architecture

```
Coordinator (port 9000)
    ↓ (video stream)
Peer A (UDP: 10001, Control: 20001)
    ↓ (forward)
Peer B (UDP: 10002, Control: 20002)
    ↓ (forward)
Peer C (UDP: 10003, Control: 20003)
    ↓ (forward)
Peer D (UDP: 10004, Control: 20004)
```

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

Required packages:
- `opencv-python` - Video encoding/decoding
- `numpy` - Array operations

## Usage

1. Place your video file as `testAyush.mp4` in the project directory

2. Run the network:
```bash
python run_network.py
```

This will:
- Open coordinator in a new terminal window
- Open each peer (A, B, C, D) in separate terminal windows
- Wait 5 seconds, then start streaming video from coordinator
- Each peer will receive, save, and forward frames downstream

3. Monitor progress:
- Check terminal windows for real-time logs
- Check `peer_<name>_frames/` directories for saved frames
- Check `peer_<name>.log` files for detailed logs

4. Stop the network:
- Type `quit` in any peer terminal, or
- Press Ctrl+C in the terminal windows

## How It Works

### Coordinator
1. Reads video file using OpenCV
2. Encodes each frame as JPEG
3. Compresses with zlib
4. Splits into UDP-sized chunks (<60KB each)
5. Sends chunks to first peer with metadata (frame_num, chunk_id, total_chunks)
6. Maintains original video frame rate

### Peers
1. Register with coordinator on startup
2. Receive video frame chunks via UDP
3. Buffer chunks until all chunks for a frame arrive
4. Reassemble and decompress frame data
5. Decode JPEG to image
6. Save frame to disk as `frame_XXXXXX.jpg`
7. Forward all chunks to next peer in chain
8. De-duplicate using frame numbers

### Frame Format
Each UDP packet contains:
```json
{
    "type": "video_frame",
    "origin": "coordinator",
    "frame_num": 123,
    "chunk_id": 0,
    "total_chunks": 3,
    "data": "hexadecimal_encoded_chunk"
}
```

## Output

Each peer creates:
- `peer_<name>_frames/` - Directory containing saved video frames
- `peer_<name>.log` - Detailed log file with timestamps

To reconstruct video from frames:
```bash
ffmpeg -framerate 30 -i peer_A_frames/frame_%06d.jpg -c:v libx264 output_A.mp4
```

## Configuration

Edit `run_network.py` to:
- Change number of peers
- Modify port assignments
- Adjust startup delays

Edit `coordinator.py` to:
- Change video file (`VIDEO_FILE`)
- Adjust JPEG quality (line: `cv2.IMWRITE_JPEG_QUALITY`)
- Modify compression level (zlib parameter)
- Change max datagram size (`MAX_DGRAM`)

## Performance Notes

- JPEG quality: 80 (balance between size and quality)
- zlib compression level: 6 (balanced)
- Max UDP datagram: 60KB (safe for most networks)
- Frame rate: Preserves original video FPS

## Troubleshooting

**Connection Refused Error:**
- Ensure coordinator starts before peers
- Check if port 9000 is available
- Increase startup delay in `run_network.py`

**Missing Frames:**
- Check UDP buffer sizes
- Verify network stability
- Check disk space in output directories

**Terminal Not Opening (macOS):**
- Ensure Terminal.app has proper permissions
- Try manually running: `python coordinator.py` and `python peer.py A 10001`

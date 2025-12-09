# Strandcast - Distributed P2P Video Streaming

A peer-to-peer video streaming system with coordinator/subcoordinator architecture that supports distributed deployment across multiple computers.

## System Architecture

- **Coordinator**: Manages peer registration and assigns peers to subcoordinators
- **Subcoordinator**: Streams video to peers and handles missing frame recovery
- **Peers**: Receive video frames, display them, and forward to next peer in chain

## Prerequisites

- Python 3.x
- Required packages: `opencv-python`, `numpy`
- Video files in `videoFiles/` directory (e.g., `test1.mp4`, `test2.mp4`, `test3.mp4`)

## Network Requirements

- All computers must be on the same network or have network connectivity
- Firewall must allow incoming connections on required ports
- For testing, disable firewall or add exceptions for Python

## Distributed Deployment Instructions

### Step 1: Find IP Addresses

On each computer, find its IP address:

**macOS/Linux:**
```bash
ifconfig | grep "inet " | grep -v "127.0.0.1"
```

**Windows:**
```cmd
ipconfig
```

Example IP addresses:
- Coordinator/Subcoordinator Computer: `192.168.1.100`
- Peer A Computer: `192.168.1.101`
- Peer B Computer: `192.168.1.102`
- Peer C Computer: `192.168.1.103`

### Step 2: Start Coordinator and Subcoordinator (Server Computer)

On the computer running coordinator/subcoordinator (e.g., `192.168.1.100`):

**Terminal 1 - Start Coordinator:**
```bash
cd /path/to/strandcast
python coordinator.py
```
Should show: `[Coordinator] Running on 0.0.0.0:9000`

**Terminal 2 - Start Subcoordinator:**
```bash
cd /path/to/strandcast
python subcoordinator.py 9001 192.168.1.100 9000
```
Arguments:
- `9001` - Subcoordinator port
- `192.168.1.100` - Coordinator host IP
- `9000` - Coordinator port

Should show: `[Subcoordinator] Listening on 0.0.0.0:9001`

### Step 3: Start Peers (Individual Computers)

On each peer computer, run the peer with its own IP address.

**Peer A Computer (192.168.1.101):**
```bash
cd /path/to/strandcast
python peer.py A 10001 192.168.1.100 9000 192.168.1.101
```

**Peer B Computer (192.168.1.102):**
```bash
cd /path/to/strandcast
python peer.py B 10002 192.168.1.100 9000 192.168.1.102
```

**Peer C Computer (192.168.1.103):**
```bash
cd /path/to/strandcast
python peer.py C 10003 192.168.1.100 9000 192.168.1.103
```

**Peer Command Format:**
```bash
python peer.py <name> <udp_port> <coordinator_ip> <coordinator_port> <this_peer_ip>
```

Arguments:
- `<name>` - Unique peer name (e.g., A, B, C)
- `<udp_port>` - UDP port for receiving video (e.g., 10001, 10002, 10003)
- `<coordinator_ip>` - IP address of coordinator computer
- `<coordinator_port>` - Coordinator port (default: 9000)
- `<this_peer_ip>` - This peer's own IP address

### Step 4: Video Streaming

Once 3 peers are registered, the subcoordinator automatically starts streaming video:
1. First video streams to peers
2. Peers display video in OpenCV windows
3. Each peer forwards frames to the next peer in chain
4. After video ends, peers request any missing frames
5. Process repeats for next video

## Local Testing (Single Computer)

For testing on one computer, use the automated launcher:

```bash
python run_network.py
```

This starts:
- 1 Coordinator (port 9000)
- 1 Subcoordinator (port 9001)
- Multiple peers (ports 10001, 10002, 10003, etc.)

All use `127.0.0.1` as their IP address.

## Port Usage

### Coordinator Computer:
- **9000** - Coordinator (TCP)
- **9001** - Subcoordinator (TCP and UDP)

### Each Peer Computer:
- **UDP Port** - Video data reception (e.g., 10001, 10002, 10003)
- **UDP Port + 10000** - Control port (e.g., 20001, 20002, 20003)

## Troubleshooting

### Connection Timeout
- Verify all computers are on same network
- Check firewall settings (allow Python on all ports)
- Use `nc -zv <ip> <port>` to test connectivity
- Ensure coordinator/subcoordinator show "0.0.0.0" in bind messages

### Peer Not Registering
- Verify coordinator IP address is correct
- Check coordinator is running and listening
- Ensure peer's own IP address is correct

### Video Not Playing
- Verify video files exist in `videoFiles/` directory
- Check file names match pattern: `test1.mp4`, `test2.mp4`, etc.
- Ensure OpenCV is installed: `pip install opencv-python`

### Missing Frames
- System automatically requests missing frames after video ends
- Check network bandwidth if many frames are missing
- UDP packets may be dropped on poor connections

## Video Controls (Peer Display Window)

When video window is active:
- **Space** - Pause/Resume
- **Q** - Quit
- **R** - Restart current video
- **Arrow Keys** - Seek forward/backward

## System Requirements

- **Network**: Stable connection between all computers
- **Bandwidth**: Sufficient for video streaming (depends on video quality)
- **Storage**: Space for video files and frame output
- **Display**: Monitor for video playback on peer computers


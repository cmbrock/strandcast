StrandCast Prototype – Project Description

This repository contains a simplified research prototype of a StrandCast-style peer-to-peer forwarding system. The current implementation creates a single ordered chain of peers. The coordinator streams video frames to the head of the chain, and each peer reconstructs and forwards the data to the next peer.

Description of Existing Code:

coordinator.py

* Accepts TCP control connections from peers.
* Registers peers and assigns them a position in a single linear strand by sending each peer its “prev” and “next” neighbors.
* Reads frames from a video file using cv2.VideoCapture.
* JPEG-encodes each frame, compresses the encoded bytes with zlib, and splits them into fixed-size chunks.
* Sends these chunks via UDP to the first peer’s UDP port.
* After all frames are sent, issues a “VIDEO_END” message to each peer.
  (Direct references: frame encoding, compression, chunking, and send loop appear in coordinator.py main execution block.)

peer.py

* Connects to the coordinator over TCP and receives assigned neighbor information through messages like “UPDATE_NEXT” and “UPDATE_PREV”.
* Opens a UDP socket and listens for incoming video chunks.
* Reassembles full frames by storing chunks until “total_chunks” are received.
* Decompresses the data using zlib and decodes the JPEG using cv2.imdecode.
* Saves reconstructed frames into a local folder named “peer_<id>_frames”.
* Forwards chunks to its “next” peer via UDP.
* Logs events such as “RECV_FRAME”, “FORWARD_FRAME”, and “VIDEO_END”.
  (Direct references: chunk collection, decode, file saving, and packet forwarding functions located inside Peer class.)

run_network.py

* Starts the coordinator and multiple peers (A, B, C, D).
* Launches each component as its own process using subprocess.
* Establishes the single-strand configuration used for tests.
  (Direct references: process launching code at bottom of run_network.py.)

videoStreamTest.py

* Used for initial testing of reading a video file and sending encoded frames to the coordinator or directly to peers.
* Contains early experimentation with JPEG encoding, compression, and frame sending.
  (Direct references: frame reading and print statements contained in the file.)

Logs (peer_A.log, peer_B.log, peer_C.log, peer_D.log)

* Show the frame-by-frame forwarding behavior.
* Confirm that peers are receiving frames in sequence and forwarding them to the next peer.
* Include markers such as “RECV_FRAME”, “FORWARD_FRAME”, and final “VIDEO_END”.
  (Direct references: log entries exactly matching print statements in peer.py.)

Summary of Current Behavior:

1. Coordinator builds a single ordered chain of peers.
2. Frames are encoded, compressed, chunked, and sent to the head peer.
3. Each peer reconstructs the full frame, saves it to disk, and forwards it to the next peer.
4. The system logs all forwarding operations for debugging and measurement.
5. This provides a working base to test delayed forwarding, hop-by-hop propagation times, and simple P2P streaming behavior.

Next Steps for the Project:

1. Multiple Strands

   * Extend the coordinator to maintain more than one ordered list of peers instead of only a single chain.
   * Allow each peer to store separate “prev” and “next” relationships for each strand.
   * This change builds directly on the existing neighbor assignment logic in coordinator.py and peer.py.

2. Metrics Collection

   * Add timestamps and identifiers to forwarded chunks and frames.
   * Compute end-to-end latency by comparing timestamps issued in coordinator.py with peer-side receive times logged in peer.py.
   * Measure forwarding hop count using the existing forwarding loop in peer.py.

3. Failure Handling

   * Use timeouts to detect when a peer stops forwarding data.
   * Coordinator or peers can adjust neighbor assignments using the same “UPDATE_NEXT” and “UPDATE_PREV” control messages already implemented.

4. Network Expansion

   * Move beyond local UDP loopback by allowing peers to specify external IP addresses instead of only using “127.0.0.1”.
   * No structural changes needed, since UDP send and receive are already separated per peer.

5. MPEG-DASH Segment Level Integration

   * Replace raw frame-by-frame streaming with fixed-size video segments.
   * Reuse the existing chunking, compression, and forwarding logic in coordinator.py and peer.py.
   * Treat each segment as a larger “frame” that goes through the same pipeline.

Overall Goal:

The final system will support multiple strands, real-time metric collection, basic churn and failure recovery, and segment-based streaming. The current code provides the correct foundation: peer registration, neighbor updates, chunk forwarding, frame reconstruction, and detailed logs.


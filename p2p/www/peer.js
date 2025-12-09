// peer.js - StrandCast browser peer (CHAIN topology, forwarded media)
// Behavior:
//  - Chain topology: coordinator tells the previous peer to connect to the new peer via UPDATE_NEXT.
//  - Each peer forwards any streams it has (local webcam OR remote stream(s) it received)
//    into the outgoing RTCPeerConnection to the next peer. That way peer C receives A's stream
//    forwarded through B.
//  - Prevents duplicate connects, uses single PC per peer, reuses tracks where possible,
//    and provides robust logging and defensive logic.

(() => {
  const logEl = document.getElementById('log');
  const append = (s) => { logEl.textContent += s + "\n"; logEl.scrollTop = logEl.scrollHeight; };

  const metrics = {};
  const knownPeers = new Set();
  const nameInput = document.getElementById('name');
  const portInput = document.getElementById('port');
  const strandInput = document.getElementById('strand');
  const btnList = document.getElementById('btnList');
  const btnRegister = document.getElementById('btnRegister');
  const btnSend = document.getElementById('btnSend');
  const txtMsg = document.getElementById('msg');

  const localVideo = document.getElementById('localVideo');
  const remoteVideo = document.getElementById('remoteVideo');

  const COORD = `${location.protocol}//${location.hostname}:9000`;
  const ICE_CFG = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] };

  let myName, myPort, myStrand;
  let ws = null;

  // RTCPeerConnection and DataChannel maps keyed by peer name
  const pcMap = {};        // peer -> RTCPeerConnection
  const dcMap = {};        // peer -> DataChannel (if created locally)
  const pcState = {};      // peer -> 'idle'|'connecting'|'connected'

  // Local and remote media
  let localStream = null;
  const remoteStreams = {}; // peer -> MediaStream received from that peer (first remote stream)
  let mediaReady = false;
  let queuedEvents = [];    // holds UPDATE_NEXT events until mediaReady

  // connection locks to prevent concurrent connects
  const connectLocks = {};

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  function now() { return Date.now(); }

  // ---------- Media setup ----------
  async function initLocalMedia() {
    append("[MEDIA] requesting local media...");
    try {
      localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      localVideo.srcObject = localStream;
      append("[MEDIA] local stream active");
    } catch (e) {
      append("[MEDIA] getUserMedia error: " + e);
      localStream = null;
    }
    mediaReady = true;

    // process queued UPDATE_NEXT events (chain uses UPDATE_NEXT for connecting)
    for (const ev of queuedEvents) {
      append(`[QUEUE] processing queued UPDATE_NEXT -> ${ev.next_name}`);
      // process but don't block too long
      handleUpdateNext(ev.next_name).catch(err => append("[ERR] queued handleUpdateNext: " + err));
    }
    queuedEvents = [];
  }

  function updatePeerListUI() {
    const ui = document.getElementById("peerList");
    if (!ui) return;
    ui.innerHTML = "";

    knownPeers.forEach(p => {
        const li = document.createElement("li");
        li.textContent = p + (p === window.nextPeer ? "  (next)" : "");
        ui.appendChild(li);
    });
  }

  function updateMetricsUI() {
    const box = document.getElementById("metricsBox");
    if (!box) return;

    let out = "=== Peer Metrics ===\n";

    for (const [peer, m] of Object.entries(metrics)) {
      out += `Peer: ${peer}\n`;
      out += `  Packets Received: ${m.recvPackets}\n`;
      out += `  Packets Lost:     ${m.lostPackets}\n`;
      out += `  Loss %:           ${m.lossPct.toFixed(2)}%\n\n`;
    }

    box.textContent = out;
  }


  async function collectStats() {
    for (const peer of Object.keys(pcMap)) {
      const pc = pcMap[peer];
      if (!pc) continue;

      const stat = {
        recvPackets: 0,
        lostPackets: 0,
        lossPct: 0
      };

      try {
        const report = await pc.getStats(null);
        report.forEach(res => {
          // Inbound RTP (remote → local)
          if (res.type === "inbound-rtp" && !res.isRemote) {
            stat.recvPackets = res.packetsReceived || 0;
            stat.lostPackets = res.packetsLost || 0;

            if (stat.recvPackets + stat.lostPackets > 0) {
              stat.lossPct =
                (stat.lostPackets / (stat.recvPackets + stat.lostPackets)) * 100;
            }
          }
        });
      } catch (e) {
        console.warn("stats error", e);
      }

      metrics[peer] = stat;
    }

    updateMetricsUI();
  }


  // ---------- Register with coordinator ----------
  btnRegister.onclick = async () => {
    myName = nameInput.value.trim();
    myPort = parseInt(portInput.value);
    myStrand = strandInput.value.trim() || "default";
    if (!myName || !myPort) return alert("enter name and port");

    append(`[UI] Registering ${myName} on strand=${myStrand}`);
    try {
      const r = await fetch(`${COORD}/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: myName, port: myPort, ctrl: myPort + 10000, strand: myStrand })
      });
      const j = await r.json();
      if (j.error) { append("[ERR] register: " + j.error); return; }
      append("[UI] Registered. prev=" + (j.prev && j.prev.name ? j.prev.name : "NONE"));
      append("[WS] Subcoordinator WS: " + j.sub_ws);

      ws = new WebSocket(j.sub_ws);

      ws.onopen = () => append("[WS] connected to subcoordinator");
      ws.onclose = (ev) => append("[WS] subcoordinator closed code=" + (ev && ev.code));
      ws.onerror = (e) => append("[WS] error event");

      ws.onmessage = async (evt) => {
        let obj;
        try { obj = JSON.parse(evt.data); } catch { append("[WS] malformed message"); return; }

        // UPDATE_NEXT controls the chain: previous peer receives this and should connect to next.
        if (obj.type === "UPDATE_NEXT") {
          append("[WS] UPDATE_NEXT -> " + obj.next_name);
          // If media not ready, queue this event
          if (!mediaReady) {
            append("[QUEUE] update_next queued until media ready");
            queuedEvents.push(obj);
            return;
          }
          await handleUpdateNext(obj.next_name);
          return;
        }

        // NEW_PEER is useful for UI only in chain topology (we rely on UPDATE_NEXT for connections)
        if (obj.type === "NEW_PEER") {
          append("[WS] NEW_PEER -> " + obj.name);
          // do not auto-mesh; chain connects via UPDATE_NEXT notifications
          knownPeers.add(obj.name);
          updatePeerListUI();
          return;
        }

        // Signaling messages
        if (obj.type === "offer" && obj.to === myName) {
          append("[SIG] OFFER from " + obj.from);
          await handleOffer(obj.from, obj.sdp);
          return;
        }
        if (obj.type === "answer" && obj.to === myName) {
          append("[SIG] ANSWER from " + obj.from);
          await handleAnswer(obj.from, obj.sdp);
          return;
        }
        if (obj.type === "candidate" && obj.to === myName) {
          await handleCandidate(obj.from, obj.candidate);
          return;
        }
      };

      // start local media AFTER ws handlers are set (so incoming offers are handled)
      await initLocalMedia();
      setInterval(collectStats, 1000);

    } catch (e) {
      append("[ERR] register failed: " + e);
    }
  };

  // ---------- Chain connection logic ----------
  // Called when coordinator tells us our next peer (previous peer receives UPDATE_NEXT).
  async function handleUpdateNext(nextName) {
    if (!nextName) return;
    window.nextPeer = nextName;
    updatePeerListUI();
    // avoid connecting to self
    if (nextName === myName) { append("[CHAIN] nextName == myName; ignoring"); return; }

    // If already connected or connecting, skip
    const state = pcState[nextName];
    if (state === 'connected' || state === 'connecting') {
      append(`[CHAIN] connection to ${nextName} already ${state}`);
      return;
    }

    // Acquire lock per target
    if (connectLocks[nextName]) {
      append(`[CHAIN] connect to ${nextName} already in progress (lock)`);
      return;
    }
    connectLocks[nextName] = true;

    try {
      append(`[CHAIN] connecting to next peer -> ${nextName}`);
      await ensureSignalingOpen();
      await connectChainPeer(nextName);
    } catch (e) {
      append("[ERR] handleUpdateNext failed: " + e);
    } finally {
      delete connectLocks[nextName];
    }
  }

  async function ensureSignalingOpen(timeout = 4000) {
    const start = now();
    while (!ws || ws.readyState !== 1) {
      if (!ws) throw "no-ws";
      if (now() - start > timeout) throw "signaling-not-open";
      await sleep(100);
    }
  }

  // Core: connect previous -> next in chain.
  // This will create an offer from this peer to nextName, and importantly it will
  // add all available tracks to the outgoing RTCPeerConnection:
  //   - localStream tracks (if this peer has a camera)
  //   - any remote stream tracks this peer already received (so we forward A->B->C)
  async function connectChainPeer(nextName) {
    pcState[nextName] = 'connecting';
    append(`[CONNECT] creating PC for ${nextName}`);

    const pc = new RTCPeerConnection(ICE_CFG);
    pcMap[nextName] = pc;

    // Add local tracks if present
    if (localStream) {
      localStream.getTracks().forEach(t => {
        try { pc.addTrack(t, localStream); } catch (e) { append("[WARN] addTrack local failed: " + e); }
      });
      append("[CONNECT] added local stream tracks (if any)");
    }

    // Also forward any remote streams we have; these are the streams we received from upstream peers.
    // This is what implements forwarding: B forwards the stream it received from A into PC->C.
    Object.values(remoteStreams).forEach(stream => {
      try {
        stream.getTracks().forEach(t => pc.addTrack(t, stream));
      } catch (e) {
        append("[WARN] addTrack remote failed: " + e);
      }
    });
    append("[CONNECT] added remote streams (forwarding) if present");

    // DataChannel - optional for control or metrics; create a labeled DC for this connection.
    let dc;
    try {
      dc = pc.createDataChannel("strand_dc");
      dcMap[nextName] = dc;
      dc.onopen = () => append("[DC] open -> " + nextName);
      dc.onmessage = (m) => {
        // optional application-level data; keep it light
        try { const p = JSON.parse(m.data); append("[DC] msg: " + JSON.stringify(p)); } catch(e) {}
      };
    } catch (e) {
      append("[WARN] createDataChannel failed: " + e);
    }

    // PC handlers
    pc.onicecandidate = (ev) => {
      if (ev.candidate) {
        // send candidate via coordinator/subcoordinator
        try {
          ws.send(JSON.stringify({ type: 'candidate', to: nextName, from: myName, candidate: ev.candidate }));
          append("[WS] Sent ICE candidate to " + nextName);
        } catch (e) {
          append("[ERR] send candidate failed: " + e);
        }
      }
    };

    // When the remote peer (downstream) later sends us tracks (unlikely in chain since we mostly forward downstream),
    // capture them and store so we can forward further if needed.
    pc.ontrack = (ev) => {
      append("[PC] ontrack from " + nextName + " streams=" + ev.streams.length);
      try {
        // store first stream for forwarding to future peers
        const s = ev.streams[0];
        if (s) remoteStreams[nextName] = s;
        // show remote stream in UI (for this peer, we display what we receive from next too? Usually chain shows previous)
        remoteVideo.srcObject = s;
      } catch (e) {
        append("[WARN] ontrack handler error: " + e);
      }
    };

    pc.onconnectionstatechange = () => {
      append("[PC] connectionState for " + nextName + ": " + pc.connectionState);
      if (pc.connectionState === 'connected' || pc.connectionState === 'completed') {
        pcState[nextName] = 'connected';
      }
      if (pc.connectionState === 'failed' || pc.connectionState === 'closed') {
        pcState[nextName] = 'idle';
      }
    };

    // Create offer, set local description, send offer
    try {
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      // Ensure ws is open
      await ensureSignalingOpen(5000);

      ws.send(JSON.stringify({ type: 'offer', to: nextName, from: myName, sdp: pc.localDescription.sdp }));
      append("[SIG] Sent OFFER to " + nextName);
    } catch (e) {
      append("[ERR] offer/send failed: " + e);
      pcState[nextName] = 'idle';
      try { pc.close(); } catch(_) {}
      throw e;
    }

    // Wait for connection (datachannel open or connectionState 'connected') - 30s timeout
    const start = now();
    return new Promise((resolve, reject) => {
      const iv = setInterval(() => {
        const state = pcState[nextName];
        const dcNow = dcMap[nextName];
        if (state === 'connected') { clearInterval(iv); append("[CONNECT] connected to " + nextName); resolve(pc); return; }
        // if a DC exists and is open, consider connected
        if (dcNow && dcNow.readyState === 'open') { clearInterval(iv); append("[CONNECT] dc open -> " + nextName); resolve(pc); return; }
        if (now() - start > 30000) {
          clearInterval(iv);
          append("[CONNECT] timeout connecting to " + nextName);
          try { pc.close(); } catch(_) {}
          pcState[nextName] = 'idle';
          reject("connect-timeout");
          return;
        }
      }, 200);
    });
  }

  // ---------- Incoming signaling handlers ----------
  async function handleOffer(from, sdp) {
    append("[SIG] handling OFFER from " + from);
    // If we already have a PC to this peer, reuse it, otherwise create a new PC
    let pc = pcMap[from];
    if (!pc) {
      pc = new RTCPeerConnection(ICE_CFG);
      pcMap[from] = pc;

      // If we have localStream, add tracks so the upstream can receive forwarded media
      if (localStream) {
        localStream.getTracks().forEach(t => pc.addTrack(t, localStream));
      }
      // Also add remoteStreams (streams we received from upstream) so we forward them upstream? Typically,
      // incoming offers are from upstream peers so on an incoming offer you may want to forward the stream
      // further later when connecting to next. For now, ensure pc is ready to handle tracks.
      Object.values(remoteStreams).forEach(stream => {
        stream.getTracks().forEach(t => pc.addTrack(t, stream));
      });

      pc.ontrack = (ev) => {
        append("[PC] ontrack (incoming) from " + from);
        try {
          const s = ev.streams[0];
          if (s) {
            remoteStreams[from] = s;
            // update UI to show the upstream stream (this peer always displays what it receives)
            remoteVideo.srcObject = s;
          }
        } catch (e) { append("[WARN] handleOffer ontrack error: " + e); }
      };

      pc.onicecandidate = (ev) => {
        if (ev.candidate) {
          try {
            ws.send(JSON.stringify({ type: 'candidate', to: from, from: myName, candidate: ev.candidate }));
            append("[WS] Sent ICE candidate to " + from);
          } catch (e) {
            append("[ERR] send candidate failed: " + e);
          }
        }
      };

      pc.onconnectionstatechange = () => {
        append("[PC] connectionState for " + from + ": " + pc.connectionState);
        if (pc.connectionState === 'connected') pcState[from] = 'connected';
      };
    } else {
      append("[SIG] reusing existing PC for " + from);
    }

    try {
      await pc.setRemoteDescription({ type: 'offer', sdp });
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      await ensureSignalingOpen(5000);
      ws.send(JSON.stringify({ type: 'answer', to: from, from: myName, sdp: pc.localDescription.sdp }));
      append("[SIG] Sent ANSWER to " + from);
    } catch (e) {
      append("[ERR] handleOffer failed: " + e);
    }
  }

  async function handleAnswer(from, sdp) {
    append("[SIG] handling ANSWER from " + from);
    const pc = pcMap[from];
    if (!pc) { append("[ERR] no pcMap entry for answer from " + from); return; }
    try {
      await pc.setRemoteDescription({ type: 'answer', sdp });
      append("[SIG] applied ANSWER from " + from);
    } catch (e) {
      append("[ERR] setRemoteDescription(answer) failed: " + e);
    }
  }

  async function handleCandidate(from, candidate) {
    const pc = pcMap[from];
    if (!pc) { append("[WARN] candidate for unknown pc from " + from); return; }
    try {
      await pc.addIceCandidate(candidate);
      append("[ICE] added candidate from " + from);
    } catch (e) {
      append("[WARN] addIceCandidate failed: " + e);
    }
  }

  btnList.onclick = async () => {
    try {
      const r = await fetch(`${COORD}/peers`);
      const j = await r.json();
      append("[UI] peers: " + JSON.stringify(j.peers));
      append("[UI] strands: " + JSON.stringify(j.strands));
    } catch (e) { append("[ERR] list failed: " + e); }
  };

  // ---------- Simple message send button (data channel) ----------
  btnSend.onclick = async () => {
    const raw = txtMsg.value.trim();
    if (!raw) return;
    // chain semantics: send to nextPeer only (window.nextPeer set by UPDATE_NEXT)
    const next = window.nextPeer;
    if (!next) return append("[ERR] no next peer known");
    // ensure pc exists and dc exists (if not, create handshake by connecting)
    try {
      if (!pcMap[next] || pcState[next] !== 'connected') {
        append("[SEND] ensuring connection to next before sending");
        await handleUpdateNext(next);
      }
      const dc = dcMap[next];
      if (dc && dc.readyState === 'open') {
        dc.send(JSON.stringify({ type: "text", from: myName, msg: raw }));
        append("[SENT] to " + next);
      } else {
        append("[ERR] no open datachannel to next");
      }
    } catch (e) {
      append("[ERR] send failed: " + e);
    }
  };

  // Expose a small helper for debugging in console
  window._strandDebug = {
    pcMap, dcMap, remoteStreams
  };

  append("[INIT] peer.js chain-forward ready");
})();

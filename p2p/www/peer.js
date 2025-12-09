// peer.js - StrandCast browser peer (registers with root coordinator, then connects to per-strand subcoordinator)
(() => {
  const logEl = document.getElementById('log');
  const append = (s) => { logEl.textContent += s + "\n"; logEl.scrollTop = logEl.scrollHeight; };

  const nameInput = document.getElementById('name');
  const portInput = document.getElementById('port');
  const strandInput = document.getElementById('strand');
  const btnRegister = document.getElementById('btnRegister');
  const btnSend = document.getElementById('btnSend');
  const txtMsg = document.getElementById('msg');
  const localVideo = document.getElementById('localVideo');
  const remoteContainer = document.getElementById('remoteVideos');

  const COORD = `${location.protocol}//${location.hostname}:9000`;

  let myName, myPort, myStrand;
  let ws = null;
  let pcMap = {}, dcMap = {};
  let localStream = null;
  let seen = new Set();
  let metrics = [];

  const ICE_CFG = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] };

  async function initLocalMedia() {
    try {
      localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      localVideo.srcObject = localStream;
      append("[MEDIA] Local stream active");
    } catch (e) {
      append("[MEDIA] getUserMedia error: " + e);
      localStream = null; // continue even if no local webcam
    }
  }

  btnRegister.onclick = async () => {
    myName = nameInput.value.trim();
    myPort = parseInt(portInput.value);
    myStrand = strandInput.value.trim() || "default";
    if (!myName || !myPort) { alert("Enter name and port"); return; }
    append(`[UI] Registering ${myName} strand=${myStrand}`);

    try {
      const res = await fetch(`${COORD}/register`, {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ name: myName, port: myPort, ctrl: myPort + 10000, strand: myStrand })
      });
      const j = await res.json();
      if (j.error) { append("[ERR] register: " + j.error); return; }
      append("[UI] Registered. prev=" + (j.prev && j.prev.name ? j.prev.name : "NONE"));
      const sub_ws = j.sub_ws;
      append("[UI] subcoordinator ws: " + sub_ws);

      ws = new WebSocket(sub_ws);

      ws.onopen = () => {
        append("[WS] connected to subcoordinator");
        // Auto-connect to nextPeer if known, even without localStream
        if (window.nextPeer) connectToPeer(window.nextPeer).catch(e => append("[ERR] auto-connect: " + e));
      };
      ws.onclose = () => append("[WS] subcoordinator closed");
      ws.onerror = (e) => append("[WS] error: " + (e && e.message));

      ws.onmessage = async (evt) => {
        let obj;
        try { obj = JSON.parse(evt.data); } catch { return; }
        switch (obj.type) {
          case "UPDATE_NEXT":
            append("[WS] UPDATE_NEXT -> " + obj.next_name);
            window.nextPeer = obj.next_name;
            connectToPeer(obj.next_name).catch(e => append("[ERR] auto-connect UPDATE_NEXT: " + e));
            break;
          case "NEW_PEER":
            append("[WS] NEW_PEER -> " + obj.name);
            // Auto-connect to the new peer immediately
            if (obj.name !== myName) {
              connectToPeer(obj.name).catch(e => append("[ERR] auto-connect NEW_PEER: " + e));
            }
            break;
          case "offer":
            if (obj.to === myName) await handleOffer(obj.from, obj.sdp);
            break;
          case "answer":
            if (obj.to === myName) await handleAnswer(obj.from, obj.sdp);
            break;
          case "candidate":
            if (obj.to === myName) await handleCandidate(obj.from, obj.candidate);
            break;
        }
      };


      await initLocalMedia();
    } catch (e) {
      append("[ERR] register failed: " + e);
    }
  };

  async function buildPC(peer) {
    if (pcMap[peer]) return pcMap[peer];
    const pc = new RTCPeerConnection(ICE_CFG);
    pcMap[peer] = pc;

    if (localStream) localStream.getTracks().forEach(t => pc.addTrack(t, localStream));

    pc.ontrack = (ev) => {
      let vid = document.getElementById(`remoteVideo-${peer}`);
      if (!vid) {
        vid = document.createElement("video");
        vid.id = `remoteVideo-${peer}`;
        vid.autoplay = true;
        vid.playsInline = true;
        vid.width = 320;
        vid.height = 240;
        vid.style.margin = "5px";
        remoteContainer.appendChild(vid);
      }
      vid.srcObject = ev.streams[0];
      append(`[MEDIA] Remote track attached from ${peer}`);
    };

    pc.ondatachannel = (ev) => {
      const ch = ev.channel;
      dcMap[peer] = ch;
      ch.onopen = () => append("[DC] incoming open from " + peer);
      ch.onmessage = (m) => { try { onData(JSON.parse(m.data)); } catch {} };
    };

    pc.onicecandidate = (ev) => {
      if (ev.candidate) {
        ws.send(JSON.stringify({ type: "candidate", to: peer, from: myName, candidate: ev.candidate }));
      }
    };

    return pc;
  }

  async function handleOffer(from, sdp) {
    const pc = await buildPC(from);
    if (localStream) localStream.getTracks().forEach(t => pc.addTrack(t, localStream));
    await pc.setRemoteDescription({ type: 'offer', sdp });
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    ws.send(JSON.stringify({ type: 'answer', to: from, from: myName, sdp: pc.localDescription.sdp }));
    append("[WS] Sent ANSWER to " + from);
  }

  async function handleAnswer(from, sdp) {
    const pc = pcMap[from];
    if (!pc) { append("[WARN] no pc for answer " + from); return; }
    await pc.setRemoteDescription({ type: 'answer', sdp });
    append("[WS] Applied ANSWER from " + from);
  }

  async function handleCandidate(from, cand) {
    const pc = pcMap[from];
    if (!pc) return;
    try { await pc.addIceCandidate(cand); } catch {}
  }

  async function connectToPeer(target) {
    if (dcMap[target] && dcMap[target].readyState === "open") return dcMap[target];
    append("[UI] connecting to " + target);

    const pc = await buildPC(target);

    // Only add local stream if available
    if (localStream) localStream.getTracks().forEach(t => pc.addTrack(t, localStream));

    const ch = pc.createDataChannel(target);
    dcMap[target] = ch;
    ch.onopen = () => append("[DC] open -> " + target);
    ch.onmessage = (m) => { try { onData(JSON.parse(m.data)); } catch {} };

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    ws.send(JSON.stringify({ type: 'offer', to: target, from: myName, sdp: pc.localDescription.sdp }));
    append("[WS] Sent OFFER to " + target);

    return new Promise((resolve, reject) => {
      const start = Date.now();
      const interval = setInterval(() => {
        if (ch.readyState === "open") { clearInterval(interval); resolve(ch); }
        if (Date.now() - start > 15000) { clearInterval(interval); reject("timeout"); }
      }, 100);
    });
  }


  function onData(msg) {
    if (!msg || msg.type !== "data") return;
    const key = `${msg.origin}:${msg.seq}`;
    if (seen.has(key)) return;
    seen.add(key);
    append(`[MSG] ${msg.origin} -> ${msg.msg}`);

    const nxt = window.nextPeer;
    if (!nxt) return;
    const ch = dcMap[nxt];
    if (ch && ch.readyState === "open") {
      ch.send(JSON.stringify(msg));
      append("[FORWARD] to " + nxt);
    } else {
      connectToPeer(nxt).then(c => { c.send(JSON.stringify(msg)); append("[FORWARD] connected+sent to " + nxt); });
    }
  }

  // Simple send button
  document.getElementById('btnSend').onclick = async () => {
    const raw = txtMsg.value.trim();
    if (!raw || !myName) return;
    const nxt = window.nextPeer;
    if (!nxt) { append("[QUEUE] No next peer known"); return; }
    const seq = Date.now();
    const m = { type: "data", origin: myName, seq, sender: myName, msg: raw };
    seen.add(`${myName}:${seq}`);
    try {
      const ch = await connectToPeer(nxt);
      ch.send(JSON.stringify(m));
      append("[SENT] to " + nxt);
    } catch (e) { append("[ERR] send failed: " + e); }
  };

})();

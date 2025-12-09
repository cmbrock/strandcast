// peer.js - StrandCast browser peer (registers with root coordinator, then connects to per-strand subcoordinator)
(() => {
  const logEl = document.getElementById('log');
  const append = (s) => { logEl.textContent += s + "\n"; logEl.scrollTop = logEl.scrollHeight; };

  const nameInput = document.getElementById('name');
  const portInput = document.getElementById('port');
  const strandInput = document.getElementById('strand');
  const btnRegister = document.getElementById('btnRegister');
  const btnList = document.getElementById('btnList');
  const btnSend = document.getElementById('btnSend');
  const btnDownload = document.getElementById('btnDownload');
  const txtMsg = document.getElementById('msg');

  const localVideo = document.getElementById('localVideo');
  const remoteVideo = document.getElementById('remoteVideo');

  const COORD = `${location.protocol}//${location.hostname}:9000`;

  let myName, myPort, myStrand;
  let ws = null;               // ws to subcoordinator after register
  let pcMap = {}, dcMap = {};
  let localStream = null;
  let seen = new Set(), metrics = [];

  const ICE_CFG = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] };

  function metricsAdd(r) { metrics.push(r); }

  async function initLocalMedia() {
    try {
      localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      localVideo.srcObject = localStream;
      append("[MEDIA] Local stream active");
    } catch (e) {
      append("[MEDIA] getUserMedia error: " + e);
    }
  }

  btnRegister.onclick = async () => {
    myName = nameInput.value.trim();
    myPort = parseInt(portInput.value);
    myStrand = strandInput.value.trim() || "default";
    if (!myName || !myPort) { alert("Enter name and port"); return; }
    append(`[UI] Registering ${myName} strand=${myStrand}`);

    // Register with coordinator
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

      // Connect to subcoordinator WS
      ws = new WebSocket(sub_ws);
      ws.onopen = () => append("[WS] connected to subcoordinator");
      ws.onclose = () => append("[WS] subcoordinator closed");
      ws.onerror = (e) => append("[WS] error: " + (e && e.message));

      ws.onmessage = async (evt) => {
        let obj;
        try { obj = JSON.parse(evt.data); } catch { return; }
        if (obj.type === "UPDATE_NEXT") {
          append("[WS] UPDATE_NEXT -> " + obj.next_name);
          window.nextPeer = obj.next_name;
          return;
        }
        if (obj.type === "NEW_PEER") {
          append("[WS] NEW_PEER -> " + obj.name);
          return;
        }
        if (obj.type === "offer" && obj.to === myName) {
          append("[WS] Received OFFER from " + obj.from);
          await handleOffer(obj.from, obj.sdp);
          return;
        }
        if (obj.type === "answer" && obj.to === myName) {
          append("[WS] Received ANSWER from " + obj.from);
          await handleAnswer(obj.from, obj.sdp);
          return;
        }
        if (obj.type === "candidate" && obj.to === myName) {
          await handleCandidate(obj.from, obj.candidate);
          return;
        }
      };

      // start camera
      await initLocalMedia();
    } catch (e) {
      append("[ERR] register failed: " + e);
    }
  };

  btnList.onclick = async () => {
    try {
      const r = await fetch(`${COORD}/peers`);
      const j = await r.json();
      append("[UI] peers: " + JSON.stringify(j.peers));
      append("[UI] strands: " + JSON.stringify(j.strands));
    } catch (e) { append("[ERR] list failed: " + e); }
  };

  async function buildPC(peer) {
    const pc = new RTCPeerConnection(ICE_CFG);
    pcMap[peer] = pc;

    if (localStream) localStream.getTracks().forEach(t => pc.addTrack(t, localStream));

    pc.ontrack = (ev) => {
      remoteVideo.srcObject = ev.streams[0];
      append("[MEDIA] Remote track attached from " + peer);
    };

    pc.ondatachannel = (ev) => {
      dcMap[peer] = ev.channel;
      dcMap[peer].onopen = () => append("[DC] incoming open from " + peer);
      dcMap[peer].onmessage = (m) => { try { onData(JSON.parse(m.data)); } catch {} };
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
    metricsAdd(["RECV", msg.origin, msg.seq, msg.sender, myName, new Date().toISOString(), msg.msg]);
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

  btnSend.onclick = async () => {
    const raw = txtMsg.value.trim();
    if (!raw) return;
    if (!myName) { alert("register first"); return; }
    if (raw.startsWith("sendto ")) {
      const parts = raw.split(" ");
      const tgt = parts[1];
      const text = parts.slice(2).join(" ");
      try {
        const ch = await connectToPeer(tgt);
        const seq = Date.now();
        const m = { type: "data", origin: myName, seq, sender: myName, msg: text };
        ch.send(JSON.stringify(m));
        append("[SENT_DIRECT] to " + tgt);
      } catch (e) { append("[ERR] direct send failed: " + e); }
      return;
    }
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

  btnDownload.onclick = () => {
    const csv = metrics.map(r => r.join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `metrics_${myName||'peer'}.csv`; a.click();
    URL.revokeObjectURL(url);s
  };

})();

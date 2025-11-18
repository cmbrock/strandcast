// peer.js - browser WebRTC client for StrandCast signaling via coordinator_static.py
(() => {
  const logEl = document.getElementById('log');
  const append = (s) => { logEl.textContent += s + "\n"; logEl.scrollTop = logEl.scrollHeight; };

  const nameInput = document.getElementById('name');
  const portInput = document.getElementById('port');
  const btnRegister = document.getElementById('btnRegister');
  const btnList = document.getElementById('btnList');
  const btnSend = document.getElementById('btnSend');
  const txtMsg = document.getElementById('msg');
  const btnDownload = document.getElementById('btnDownload');

  // auto-fill fields from query params
  const params = new URLSearchParams(window.location.search);
  if (params.get('name')) nameInput.value = params.get('name');
  if (params.get('port')) portInput.value = params.get('port');

  const COORD = `${location.protocol}//${location.hostname}:9000`;
  const WS_BASE = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.hostname}:9000/ws`;

  let ws = null;
  let myName = null;
  let myPort = null;
  let pcMap = {};   // peerName -> RTCPeerConnection
  let dcMap = {};   // peerName -> DataChannel
  let seen = new Set();
  let metrics = [];
  let peers = {};   // peerName -> {port, ctrl, dataChannel}

  const ICE_CFG = {iceServers: [{urls: 'stun:stun.l.google.com:19302'}]};

  function metricsAdd(row) { metrics.push(row); }

  // Add a peer dynamically
  function addPeer(name, port, ctrl) {
    if (peers[name]) return; // already exists
    peers[name] = { port, ctrl, dataChannel: null };
    append(`[PEER] Added ${name} port=${port} ctrl=${ctrl}`);
    // Optionally start connection immediately
    connectToPeer(name).catch(e => append(`[ERR] connect to ${name} failed: ${e}`));
  }

  async function register() {
    myName = nameInput.value.trim();
    myPort = parseInt(portInput.value);
    if (!myName || !myPort) { alert('enter name and port'); return; }

    append(`[UI] Registering ${myName}`);
    try {
      const r = await fetch(`${COORD}/register`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({name: myName, port: myPort, ctrl: myPort + 10000})
      });
      const prev = await r.json();
      append(`[UI] Registered. prev=${prev.name || 'NONE'}`);
    } catch (e) {
      append(`[UI] register failed: ${e}`);
      return;
    }

    try {
      ws = new WebSocket(`${WS_BASE}/${myName}`);
    } catch (e) {
      append(`[WS] WebSocket error: ${e}`);
      return;
    }

    ws.onopen = () => { append('[WS] connected'); };
    ws.onmessage = async (evt) => {
      let obj;
      try { obj = JSON.parse(evt.data); } catch (e) { return; }

      // handle NEW_PEER
      if (obj.type === 'NEW_PEER') {
        addPeer(obj.name, obj.port, obj.ctrl);
        return;
      }

      // handle UPDATE_NEXT
      if (obj.type === 'UPDATE_NEXT') {
        append(`[WS] UPDATE_NEXT -> ${obj.next_name}:${obj.next_port}`);
        window.nextPeer = obj.next_name;
        return;
      }

      // handle standard WebRTC signaling
      if (obj.type === 'offer' && obj.to === myName) {
        append(`[WS] Received OFFER from ${obj.from}`);
        await handleOffer(obj.from, obj.sdp);
        return;
      }
      if (obj.type === 'answer' && obj.to === myName) {
        append(`[WS] Received ANSWER from ${obj.from}`);
        await handleAnswer(obj.from, obj.sdp);
        return;
      }
      if (obj.type === 'candidate' && obj.to === myName) {
        append(`[WS] Received ICE candidate from ${obj.from}`);
        await handleCandidate(obj.from, obj.candidate);
        return;
      }
    };
    ws.onclose = () => append('[WS] closed');
  }

  btnRegister.onclick = register;

  btnList.onclick = async () => {
    try {
      const r = await fetch(`${COORD}/peers`);
      const j = await r.json();
      append('[UI] Peers: ' + JSON.stringify(j.peers));
    } catch (e) { append('[UI] list failed ' + e); }
  };

  // RTCPeerConnection setup for incoming offer
  async function handleOffer(from, sdp) {
    const pc = new RTCPeerConnection(ICE_CFG);
    pcMap[from] = pc;

    pc.ondatachannel = (ev) => {
      const ch = ev.channel;
      dcMap[from] = ch;
      peers[from] = peers[from] || { port: null, ctrl: null, dataChannel: ch };
      append(`[PC] DataChannel incoming from ${from} label=${ch.label}`);
      ch.onopen = () => { append(`[DC] open from ${from}`); metricsAdd(['CHANNEL_OPEN','', '', from, new Date().toISOString(), 'incoming']); };
      ch.onmessage = (m) => { try { const payload = JSON.parse(m.data); onData(payload); } catch (e) {} };
    };

    pc.onicecandidate = (ev) => {
      if (ev.candidate) {
        ws.send(JSON.stringify({type:'candidate', to: from, from: myName, candidate: ev.candidate}));
      }
    };

    await pc.setRemoteDescription({type:'offer', sdp});
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    ws.send(JSON.stringify({type:'answer', to: from, from: myName, sdp: pc.localDescription.sdp}));
    append(`[WS] Sent ANSWER to ${from}`);
  }

  async function handleAnswer(from, sdp) {
    const pc = pcMap[from];
    if (!pc) { append(`[WARN] No pc for answer from ${from}`); return; }
    await pc.setRemoteDescription({type:'answer', sdp});
    append(`[WS] Applied ANSWER from ${from}`);
  }

  async function handleCandidate(from, cand) {
    const pc = pcMap[from];
    if (!pc) { append(`[WARN] No pc for candidate from ${from}`); return; }
    try { await pc.addIceCandidate(cand); } catch(e) {}
  }

  async function connectToPeer(target) {
    if (dcMap[target]) return dcMap[target];
    append(`[UI] Connecting to ${target}...`);
    const pc = new RTCPeerConnection(ICE_CFG);
    pcMap[target] = pc;
    const ch = pc.createDataChannel(target);
    dcMap[target] = ch;
    peers[target] = peers[target] || { port:null, ctrl:null, dataChannel: ch };

    ch.onopen = () => { append(`[DC] open -> ${target}`); metricsAdd(['CHANNEL_OPEN','', '', target, new Date().toISOString(), 'outgoing']); };
    ch.onmessage = (m) => { try { const payload = JSON.parse(m.data); onData(payload); } catch(e){} };

    pc.onicecandidate = (ev) => {
      if (ev.candidate) ws.send(JSON.stringify({type:'candidate', to: target, from: myName, candidate: ev.candidate}));
    };

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    ws.send(JSON.stringify({type:'offer', to: target, from: myName, sdp: pc.localDescription.sdp}));
    append(`[WS] Sent OFFER to ${target}`);

    return new Promise((resolve, reject) => {
      const start = Date.now();
      const interval = setInterval(() => {
        if (ch.readyState === 'open') { clearInterval(interval); resolve(ch); }
        if (Date.now() - start > 15000) { clearInterval(interval); reject('timeout'); }
      }, 100);
    });
  }

  function onData(msg) {
    if (!msg || msg.type !== 'data') return;
    const key = `${msg.origin}:${msg.seq}`;
    if (seen.has(key)) return;
    seen.add(key);
    append(`[MSG] origin=${msg.origin} seq=${msg.seq} from=${msg.sender}: ${msg.msg}`);
    metricsAdd(['RECV', msg.origin, msg.seq, msg.sender, myName, new Date().toISOString(), (msg.msg||'').replace(',', ' ')]);

    // forward downstream if coordinator set next
    const nxt = window.nextPeer;
    if (nxt) {
      const ch = dcMap[nxt];
      if (ch && ch.readyState === 'open') {
        ch.send(JSON.stringify(msg));
        metricsAdd(['FORWARD', msg.origin, msg.seq, msg.sender, nxt, new Date().toISOString(), '']);
        append(`[FORWARD] to ${nxt}`);
      } else {
        connectToPeer(nxt).then(c => { c.send(JSON.stringify(msg)); append(`[FORWARD] connected+sent to ${nxt}`); })
          .catch(e => append(`[FORWARD] failed connect to ${nxt}: ${e}`));
      }
    }
  }

  btnSend.onclick = async () => {
    const raw = txtMsg.value.trim();
    if (!raw) return;
    if (!myName) { alert('Register first'); return; }
    if (raw.startsWith('sendto ')) {
      const parts = raw.split(' ');
      const tgt = parts[1];
      const text = parts.slice(2).join(' ');
      try {
        const ch = await connectToPeer(tgt);
        const seq = Date.now();
        const m = {type:'data', origin: myName, seq, sender: myName, msg: text};
        ch.send(JSON.stringify(m));
        metricsAdd(['SENT_DIRECT', myName, seq, myName, tgt, new Date().toISOString(), text.replace(',', ' ')]);
        append(`[SENT_DIRECT] to ${tgt}`);
      } catch (e) { append(`[ERR] direct send failed ${e}`); }
      return;
    }

    // originate downstream
    const seq = Date.now();
    const m = {type:'data', origin: myName, seq, sender: myName, msg: raw};
    seen.add(`${myName}:${seq}`);
    metricsAdd(['SENT', myName, seq, myName, window.nextPeer || '', new Date().toISOString(), raw.replace(',', ' ')]);
    const nxt = window.nextPeer;
    if (!nxt) {
      append('[QUEUE] No next peer known (wait for coordinator UPDATE_NEXT)');
      return;
    }
    try {
      const ch = await connectToPeer(nxt);
      ch.send(JSON.stringify(m));
      append(`[SENT] origin -> ${nxt}`);
    } catch (e) { append(`[ERR] send to ${nxt} failed: ${e}`); }
  };

  btnDownload.onclick = () => {
    const csv = metrics.map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], {type:'text/csv'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `metrics_${myName||'peer'}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

})();

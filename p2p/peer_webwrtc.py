#!/usr/bin/env python3
# peer_webrtc.py
# WebRTC-based peer that registers with coordinator and uses DataChannels for StrandCast.
# Metrics: writes CSV lines to peer_<name>_metrics.csv with columns:
#   event,origin,seq,sender,peer,timestamp,extra
# Events: SENT, RECV, FORWARD, CONNECT, CHANNEL_OPEN, CHANNEL_FAIL

import argparse, asyncio, json, signal, sys, time
from datetime import datetime
import aiohttp
from aiohttp import ClientSession, WSMsgType
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer

COORD_URL = "http://127.0.0.1:9000"
COORD_WS_BASE = "http://127.0.0.1:9000/ws"
ICE_SERVERS = [{"urls":"stun:stun.l.google.com:19302"}]

def nowts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def log(name, text):
    fn = f"peer_{name}.log"
    with open(fn, "a") as f:
        f.write(f"[{nowts()}] {text}\n")

def metrics_write(name, row):
    fn = f"peer_{name}_metrics.csv"
    header = "event,origin,seq,sender,peer,timestamp,extra\n"
    try:
        first = False
        try:
            with open(fn, 'r'):
                first = True
        except FileNotFoundError:
            first = False
        with open(fn, 'a') as f:
            if not first:
                f.write(header)
            f.write(','.join(map(str, row)) + '\n')
    except Exception:
        pass

class Peer:
    def __init__(self, name, port):
        self.name = name
        self.port = port
        self.ctrl_port = port + 10000
        self.next = {"name": None, "port": None}
        self.session = None
        self.ws = None
        self.running = True

        self.rtc_config = RTCConfiguration([RTCIceServer(**s) for s in ICE_SERVERS])
        self.pc_map = {}
        self.dc_map = {}
        self._pending_answer = {}
        self._pending_channel_open = {}
        self.seen = set()

    async def register(self):
        self.session = ClientSession()
        url = COORD_URL + "/register"
        payload = {"name": self.name, "port": self.port, "ctrl": self.ctrl_port}
        async with self.session.post(url, json=payload) as resp:
            prev = await resp.json()
        if prev:
            print(f"[{self.name}] Registered. prev={prev.get('name')}")
            log(self.name, f"Registered. prev={prev.get('name')}:{prev.get('port')}")
        else:
            print(f"[{self.name}] Registered as first in chain")
            log(self.name, "Registered as first")
        await self._open_ws()

    async def _open_ws(self):
        ws_url = COORD_WS_BASE + f"/{self.name}"
        self.ws = await self.session.ws_connect(ws_url)
        print(f"[{self.name}] WS connected to coordinator")
        asyncio.ensure_future(self._ws_loop())

    async def _ws_loop(self):
        async for msg in self.ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    obj = json.loads(msg.data)
                except Exception:
                    continue
                await self._handle_ws_message(obj)
            elif msg.type == WSMsgType.ERROR:
                print(f"[{self.name}] WS error: {self.ws.exception()}")
        print(f"[{self.name}] WS closed")

    async def _handle_ws_message(self, obj):
        if obj.get("type") == "UPDATE_NEXT":
            self.next["name"] = obj.get("next_name")
            self.next["port"] = obj.get("next_port")
            print(f"[{self.name}] UPDATED next -> {self.next['name']}:{self.next['port']}")
            log(self.name, f"CTRL UPDATE_NEXT -> {self.next['name']}:{self.next['port']}")
            return
        typ = obj.get("type")
        if typ == "offer" and obj.get("to") == self.name:
            await self._handle_incoming_offer(obj)
            return
        if typ == "answer" and obj.get("to") == self.name:
            await self._handle_incoming_answer(obj)
            return
        if typ == "candidate" and obj.get("to") == self.name:
            await self._handle_incoming_candidate(obj)
            return

    async def _handle_incoming_offer(self, obj):
        from_name = obj.get("from")
        sdp = obj.get("sdp")
        print(f"[{self.name}] Received OFFER from {from_name}")
        pc = RTCPeerConnection(self.rtc_config)
        self.pc_map[from_name] = pc

        @pc.on("datachannel")
        def on_datachannel(channel):
            print(f"[{self.name}] DataChannel incoming from {from_name} label={channel.label}")
            self.dc_map[from_name] = channel
            ev = self._pending_channel_open.get(from_name)
            if ev is None:
                ev = asyncio.Event()
                self._pending_channel_open[from_name] = ev

            @channel.on("open")
            def _on_open():
                print(f"[{self.name}] DataChannel open (from {from_name})")
                ev.set()
                log(self.name, f"DATACHANNEL_OPEN from {from_name}")
                metrics_write(self.name, ("CHANNEL_OPEN", "", "", from_name, nowts(), "incoming"))

            @channel.on("message")
            def _on_message(msg):
                try:
                    payload = json.loads(msg)
                except Exception:
                    return
                asyncio.ensure_future(self._on_data(payload))

        @pc.on("icecandidate")
        async def on_icecandidate(candidate):
            if candidate:
                cand = {"candidate": candidate.to_sdp(), "sdpMid": candidate.sdpMid, "sdpMLineIndex": candidate.sdpMLineIndex}
                await self.ws.send_json({"type":"candidate","to":from_name,"from":self.name,"candidate": cand})

        await pc.setRemoteDescription(RTCSessionDescription(sdp, "offer"))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await self.ws.send_json({"type":"answer","to":from_name,"from":self.name,"sdp": pc.localDescription.sdp})
        print(f"[{self.name}] Sent ANSWER to {from_name}")

    async def _handle_incoming_answer(self, obj):
        from_name = obj.get("from")
        sdp = obj.get("sdp")
        fc = self._pending_answer.get(from_name)
        pc = self.pc_map.get(from_name)
        if pc is None:
            print(f"[{self.name}] Unexpected ANSWER from {from_name}")
            return
        await pc.setRemoteDescription(RTCSessionDescription(sdp, "answer"))
        if fc and not fc.done():
            fc.set_result(True)
        print(f"[{self.name}] Set remote (answer) from {from_name}")

    async def _handle_incoming_candidate(self, obj):
        from_name = obj.get("from")
        cand = obj.get("candidate")
        pc = self.pc_map.get(from_name)
        if not pc:
            return
        try:
            await pc.addIceCandidate(cand)
        except Exception:
            pass

    async def connect_to_peer(self, target):
        if target in self.dc_map:
            return self.dc_map[target]

        print(f"[{self.name}] Initiate connection to {target}")
        pc = RTCPeerConnection(self.rtc_config)
        self.pc_map[target] = pc

        channel = pc.createDataChannel(target)
        self.dc_map[target] = channel

        self._pending_answer[target] = asyncio.get_event_loop().create_future()
        self._pending_channel_open[target] = asyncio.Event()

        @channel.on("open")
        def on_open():
            print(f"[{self.name}] DataChannel to {target} OPEN")
            ev = self._pending_channel_open.get(target)
            if ev:
                ev.set()
            log(self.name, f"DATACHANNEL_OPEN to {target}")
            metrics_write(self.name, ("CHANNEL_OPEN", "", "", target, nowts(), "outgoing"))

        @channel.on("message")
        def on_message(msg):
            try:
                payload = json.loads(msg)
            except Exception:
                return
            asyncio.ensure_future(self._on_data(payload))

        @pc.on("icecandidate")
        async def on_icecandidate(candidate):
            if candidate:
                cand = {"candidate": candidate.to_sdp(), "sdpMid": candidate.sdpMid, "sdpMLineIndex": candidate.sdpMLineIndex}
                await self.ws.send_json({"type":"candidate","to":target,"from":self.name,"candidate": cand})

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await self.ws.send_json({"type":"offer","to": target, "from": self.name, "sdp": pc.localDescription.sdp})
        print(f"[{self.name}] Sent OFFER to {target} via coordinator")

        try:
            await asyncio.wait_for(self._pending_answer[target], timeout=15.0)
        except asyncio.TimeoutError:
            raise RuntimeError("timeout waiting for answer")

        try:
            await asyncio.wait_for(self._pending_channel_open[target].wait(), timeout=15.0)
        except asyncio.TimeoutError:
            raise RuntimeError("timeout waiting for datachannel open")

        metrics_write(self.name, ("CONNECT", "", "", target, nowts(), "success"))
        return channel

    async def _on_data(self, msg):
        if not isinstance(msg, dict):
            return
        if msg.get("type") != "data":
            return
        origin = msg.get("origin")
        seq = msg.get("seq")
        key = (origin, seq)
        if key in self.seen:
            return
        self.seen.add(key)
        sender = msg.get("sender")
        text = msg.get("msg")
        print(f"[{self.name}] RECV origin={origin} seq={seq} from {sender}: {text}")
        log(self.name, f"RECV origin={origin} seq={seq} from {sender}: {text}")
        metrics_write(self.name, ("RECV", origin, seq, sender, self.name, nowts(), text.replace(',', ' ')))

        nxt = self.next.get("name")
        if nxt:
            ch = self.dc_map.get(nxt)
            if not ch:
                try:
                    ch = await self.connect_to_peer(nxt)
                except Exception as e:
                    print(f"[{self.name}] failed connect to next {nxt}: {e}")
                    log(self.name, f"CONNECT_NEXT_FAIL {nxt} {e}")
                    return
            try:
                ch.send(json.dumps(msg))
                log(self.name, f"FORWARDED to {nxt} origin={origin} seq={seq}")
                metrics_write(self.name, ("FORWARD", origin, seq, sender, nxt, nowts(), ""))
            except Exception as e:
                print(f"[{self.name}] forward error to {nxt}: {e}")
                log(self.name, f"FORWARD_FAIL {nxt} {e}")

    async def originate(self, text):
        seq = int(time.time() * 1000)
        m = {"type":"data","origin": self.name, "seq": seq, "sender": self.name, "msg": text}
        self.seen.add((self.name, seq))
        nxt = self.next.get("name")
        if not nxt:
            print(f"[{self.name}] No next to send to. Queued.")
            log(self.name, f"QUEUED origin={self.name} seq={seq} msg={text}")
            return
        ch = self.dc_map.get(nxt)
        if not ch:
            try:
                ch = await self.connect_to_peer(nxt)
            except Exception as e:
                print(f"[{self.name}] connect to next {nxt} failed: {e}")
                log(self.name, f"CONNECT_NEXT_FAIL {nxt} {e}")
                return
        try:
            ch.send(json.dumps(m))
            print(f"[{self.name}] Sent origin seq={seq} to {nxt}")
            log(self.name, f"SENT origin={self.name} seq={seq} to {nxt}")
            metrics_write(self.name, ("SENT", self.name, seq, self.name, nxt, nowts(), text.replace(',', ' ')))
        except Exception as e:
            print(f"[{self.name}] send failed: {e}")
            log(self.name, f"SEND_FAIL {e}")

    async def sendto(self, target, text):
        ch = self.dc_map.get(target)
        if not ch:
            try:
                ch = await self.connect_to_peer(target)
            except Exception as e:
                print(f"[{self.name}] direct connect to {target} failed: {e}")
                return
        seq = int(time.time() * 1000)
        msg = {"type":"data","origin": self.name, "seq": seq, "sender": self.name, "msg": text}
        try:
            ch.send(json.dumps(msg))
            print(f"[{self.name}] Sent direct to {target} seq={seq}")
            log(self.name, f"SENT_DIRECT to {target} seq={seq} msg={text}")
            metrics_write(self.name, ("SENT_DIRECT", self.name, seq, self.name, target, nowts(), text.replace(',', ' ')))
        except Exception as e:
            print(f"[{self.name}] direct send error: {e}")

    async def interactive(self):
        print(f"[{self.name}] Interactive ready. 'sendto <peer> <msg>' or plain text to originate. 'list' to list peers. 'quit' to exit.")
        loop = asyncio.get_event_loop()
        while self.running:
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
            except Exception:
                break
            if not line:
                await asyncio.sleep(0.1)
                continue
            line = line.strip()
            if not line:
                continue
            if line.lower() in ("quit","exit"):
                self.running = False
                break
            if line.lower() == "list":
                async with self.session.get(COORD_URL + "/peers") as resp:
                    data = await resp.json()
                    print("Peers:", data.get("peers"))
                continue
            parts = line.split()
            if parts[0].lower() == "sendto" and len(parts) >= 3:
                target = parts[1]
                text = " ".join(parts[2:])
                await self.sendto(target, text)
                continue
            await self.originate(line)

    async def close(self):
        self.running = False
        for ch in list(self.dc_map.values()):
            try:
                ch.close()
            except:
                pass
        for pc in list(self.pc_map.values()):
            try:
                await pc.close()
            except:
                pass
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()

    async def run(self):
        await self.register()
        try:
            await self.interactive()
        finally:
            await self.close()
            print(f"[{self.name}] exited.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("port", type=int)
    args = parser.parse_args()

    peer = Peer(args.name, args.port)
    loop = asyncio.get_event_loop()

    def _on_sig():
        peer.running = False
    loop.add_signal_handler(signal.SIGINT, _on_sig)
    loop.add_signal_handler(signal.SIGTERM, _on_sig)

    try:
        loop.run_until_complete(peer.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(asyncio.sleep(0.1))
        loop.close()

if __name__ == "__main__":
    main()

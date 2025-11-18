#!/usr/bin/env python3
# run_network.py
# Launcher: starts coordinator_static.py and several peers. When possible opens new consoles and opens browser pages for HTML clients.

import subprocess, sys, time, platform, os, webbrowser
PY = sys.executable

# adjust peer list as desired
peers = [("A",10001), ("B",10002), ("C",10003)]

def start_console(cmd):
    system = platform.system()
    if system == "Windows":
        CREATE_NEW_CONSOLE = 0x00000010
        return subprocess.Popen(cmd, creationflags=CREATE_NEW_CONSOLE)
    elif system == "Darwin":
        # macOS: open new Terminal window running the command
        # Using 'osascript' to open a new Terminal and run the command
        # It may be simpler to just run in background for macOS devs
        return subprocess.Popen(cmd)
    else:
        # Try common terminals, else run in background
        try:
            return subprocess.Popen(["gnome-terminal","--"] + cmd)
        except Exception:
            try:
                return subprocess.Popen(["xterm","-e"] + cmd)
            except Exception:
                return subprocess.Popen(cmd)

if __name__ == '__main__':
    procs = []
    # start coordinator (static + signaling)
    print("Starting coordinator (static + signaling)...")
    procs.append(start_console([PY, "coordinator_static.py"]))
    time.sleep(1.0)

    # start peers in consoles
    for name, port in peers:
        print(f"Starting peer {name} (python) ...")
        procs.append(start_console([PY, "peer_webrtc.py", name, str(port)]))
        time.sleep(0.6)

    # open browser pages for peers (so people can join via link)
    # We'll open a browser tab for each peer name at http://localhost:9000/peer.html?name=<Name>
    time.sleep(1.0)
    base = "http://127.0.0.1:9000/peer.html"
    for name, port in peers:
        url = f"{base}?name={name}&port={port}"
        try:
            webbrowser.open_new_tab(url)
            print(f"Opened browser tab for {name}: {url}")
            time.sleep(0.4)
        except Exception as e:
            print("Could not open browser tab:", e)

    print("Launcher done. Coordinator is serving static files from ./www (peer.html).")
    print("You can also share this link: http://<host>:9000/peer.html?name=<yourname> for browser-based join.")
    print("Press Ctrl+C to stop this launcher (child processes will keep running).")

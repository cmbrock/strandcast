# run_network.py
# Opens coordinator and peers in separate consoles so interactive peers have a TTY.
# Windows: uses CREATE_NEW_CONSOLE so input() works. On Linux/macOS tries gnome-terminal / xterm,
# otherwise falls back to background processes (non-interactive).

import subprocess
import sys
import time
import platform
import os

def start_in_new_console(cmd):
    system = platform.system()
    if system == "Windows":
        CREATE_NEW_CONSOLE = 0x00000010
        return subprocess.Popen(cmd, creationflags=CREATE_NEW_CONSOLE)
    else:
        # try common terminals
        try:
            return subprocess.Popen(["gnome-terminal", "--"] + cmd)
        except Exception:
            try:
                return subprocess.Popen(["xterm", "-e"] + cmd)
            except Exception:
                # fallback to background (no TTY)
                return subprocess.Popen(cmd)

if __name__ == "__main__":
    coord_cmd = [sys.executable, "coordinator.py"]
    peers = [("A", 10001), ("B", 10002), ("C", 10003), ("D", 10004)]

    procs = []
    print("Starting coordinator...")
    p = start_in_new_console(coord_cmd)
    procs.append(p)
    time.sleep(1.0)

    for name, port in peers:
        cmd = [sys.executable, "peer.py", name, str(port)]
        print(f"Starting peer {name} on port {port} ...")
        p = start_in_new_console(cmd)
        procs.append(p)
        time.sleep(0.8)

    print("All processes started. Close consoles to stop nodes, or Ctrl+C this launcher (does not close spawned consoles).")

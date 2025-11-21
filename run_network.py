# run_network.py
# Opens coordinator and peers in separate consoles so interactive peers have a TTY.
# Windows: uses CREATE_NEW_CONSOLE so input() works. On Linux/macOS tries gnome-terminal / xterm,
# otherwise falls back to background processes (non-interactive).

import subprocess
import sys
import time
import platform
import os


COORD_HOST = "127.0.0.1"
COORD_PORT = 9000

def start_in_new_console(cmd):
    system = platform.system()
    if system == "Windows":
        CREATE_NEW_CONSOLE = 0x00000010
        return subprocess.Popen(cmd, creationflags=CREATE_NEW_CONSOLE)
    elif system == "Darwin":  # macOS
        # Use osascript to open a new Terminal.app window
        script = f'tell application "Terminal" to do script "{" ".join(cmd)}"'
        subprocess.Popen(["osascript", "-e", script])
        # Return a dummy process since Terminal.app manages the actual process
        return subprocess.Popen(["sleep", "0"])
    else:
        # Linux: try common terminals
        try:
            return subprocess.Popen(["gnome-terminal", "--"] + cmd)
        except Exception:
            try:
                return subprocess.Popen(["xterm", "-e"] + cmd)
            except Exception:
                try:
                    return subprocess.Popen(["konsole", "-e"] + cmd)
                except Exception:
                    # fallback to background (no TTY)
                    print(f"Warning: No terminal emulator found, running in background")
                    return subprocess.Popen(cmd)

if __name__ == "__main__":
    coord_cmd = [sys.executable, "strandcast/coordinator.py"]
    sub_coord_cmd = [sys.executable, "strandcast/subcoordinator.py", "9001", COORD_HOST, str(COORD_PORT)]
    sub_coord_cmd2 = [sys.executable, "strandcast/subcoordinator.py", "9002", COORD_HOST, str(COORD_PORT)]
    peers = [("A", 10001), ("B", 10002), ("C", 10003), ("D", 10004), ("E", 10005), ("F", 10006)]

    procs = []
    print("Starting coordinator...")
    p = start_in_new_console(coord_cmd)
    procs.append(p)
    time.sleep(1.0)

    print("Starting subcoordinator...")
    p = start_in_new_console(sub_coord_cmd)
    procs.append(p)
    time.sleep(1.0)

    print("Starting subcoordinator 2...")
    p = start_in_new_console(sub_coord_cmd2)
    procs.append(p)
    time.sleep(2.0)



    for name, port in peers:
        cmd = [sys.executable, "strandcast/peer.py", name, str(port), COORD_HOST, str(COORD_PORT)]
        print(f"Starting peer {name} on port {port} ...")
        p = start_in_new_console(cmd)
        procs.append(p)
        time.sleep(0.8)

    print("All processes started. Close consoles to stop nodes, or Ctrl+C this launcher (does not close spawned consoles).")

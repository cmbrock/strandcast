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
        # Get the directory where this script is located
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Use osascript to open a new Terminal.app window with proper working directory
        cmd_str = " ".join(cmd)
        script = f'tell application "Terminal" to do script "cd {script_dir} && {cmd_str}"'
        subprocess.Popen(["osascript", "-e", script])
        # Return a dummy process since Terminal.app manages the actual process
        return subprocess.Popen(["sleep", "0"])
    else:
        # Linux: try common terminals with better error handling
        cmd_str = " ".join(cmd)
        
        # First try tmux/screen as they're more reliable
        try:
            # Check if tmux is available and create a new session
            tmux_session = f"strandcast_{int(time.time())}"
            return subprocess.Popen([
                "tmux", "new-session", "-d", "-s", tmux_session,
                "bash", "-c", f"{cmd_str}; read -p 'Press Enter to close...'"
            ], stderr=subprocess.DEVNULL)
        except Exception:
            pass
        
        # Try different terminal emulators in order of preference
        terminal_configs = [
            # xterm - most reliable, widely available
            ["xterm", "-hold", "-e", f"bash -c '{cmd_str}'"],
            # gnome-terminal with different approaches
            ["gnome-terminal", "--wait", "--", "bash", "-c", f"{cmd_str}; read -p 'Press Enter to close...'"],
            ["gnome-terminal", "--", "bash", "-c", f"{cmd_str}; read -p 'Press Enter to close...'"],
            # konsole
            ["konsole", "--hold", "-e", "bash", "-c", cmd_str],
            # mate-terminal
            ["mate-terminal", "-e", f"bash -c '{cmd_str}; read -p \"Press Enter to close...\"'"],
            # xfce4-terminal
            ["xfce4-terminal", "--hold", "-e", f"bash -c '{cmd_str}'"],
            # Generic fallback
            ["x-terminal-emulator", "-e", f"bash -c '{cmd_str}; read -p \"Press Enter to close...\"'"],
        ]
        
        for terminal_cmd in terminal_configs:
            try:
                # Set environment to avoid D-Bus issues
                env = os.environ.copy()
                env['DISPLAY'] = env.get('DISPLAY', ':0')
                return subprocess.Popen(terminal_cmd, env=env, stderr=subprocess.DEVNULL)
            except Exception as e:
                continue
        
        # If all terminals fail, try a simple approach without new windows
        print(f"Warning: No terminal emulator found, running in background")
        print(f"Command: {cmd_str}")
        return subprocess.Popen(cmd)

if __name__ == "__main__":
    # Get the directory where this script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    coord_cmd = [sys.executable, "coordinator.py"]
    sub_coord_cmd = [sys.executable, "subcoordinator.py", "9001", COORD_HOST, str(COORD_PORT)]
    sub_coord_cmd2 = [sys.executable, "subcoordinator.py", "9002", COORD_HOST, str(COORD_PORT)]
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
        cmd = [sys.executable, "peer.py", name, str(port), COORD_HOST, str(COORD_PORT)]
        print(f"Starting peer {name} on port {port} ...")
        p = start_in_new_console(cmd)
        procs.append(p)
        time.sleep(0.8)


    

    print("All processes started. Close consoles to stop nodes, or Ctrl+C this launcher (does not close spawned consoles).")

#!/usr/bin/env python3
"""
Run the StrandCast network using tmux sessions.
This is more reliable than spawning terminal emulators and avoids D-Bus issues.
"""

import subprocess
import sys
import time
import os

def run_tmux_command(session_name, command, script_dir):
    """Run a command in a new tmux session."""
    try:
        full_cmd = f"cd {script_dir} && {command}"
        subprocess.run([
            "tmux", "new-session", "-d", "-s", session_name,
            "bash", "-c", f"{full_cmd}; echo 'Process finished. Press Ctrl+C to exit.'; sleep infinity"
        ], check=True)
        return True
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        return False

def main():
    # Check if tmux is available
    try:
        subprocess.run(["tmux", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: tmux is not installed or not available.")
        print("Please install tmux: sudo apt-get install tmux")
        print("Or use run_network_simple.py for manual commands.")
        sys.exit(1)
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    print("Starting StrandCast network with tmux...")
    
    # Kill any existing sessions
    try:
        subprocess.run(["tmux", "kill-server"], capture_output=True)
    except:
        pass
    
    # Start coordinator
    if run_tmux_command("coordinator", "python3 coordinator.py", script_dir):
        print("✓ Started coordinator in tmux session 'coordinator'")
    else:
        print("✗ Failed to start coordinator")
        return
    
    time.sleep(1)
    
    # Start subcoordinators
    if run_tmux_command("subcoord1", "python3 subcoordinator.py 9001 127.0.0.1 9000", script_dir):
        print("✓ Started subcoordinator 1 in tmux session 'subcoord1'")
    else:
        print("✗ Failed to start subcoordinator 1")
        return
    
    time.sleep(1)
    
    if run_tmux_command("subcoord2", "python3 subcoordinator.py 9002 127.0.0.1 9000", script_dir):
        print("✓ Started subcoordinator 2 in tmux session 'subcoord2'")
    else:
        print("✗ Failed to start subcoordinator 2")
        return
    
    time.sleep(2)
    
    # Start peers
    peers = [("A", 10001), ("B", 10002), ("C", 10003), ("D", 10004), ("E", 10005), ("F", 10006)]
    
    for name, port in peers:
        session_name = f"peer_{name}"
        command = f"python3 peer.py {name} {port} 127.0.0.1 9000"
        if run_tmux_command(session_name, command, script_dir):
            print(f"✓ Started peer {name} in tmux session '{session_name}'")
        else:
            print(f"✗ Failed to start peer {name}")
            return
        time.sleep(0.5)
    
    print("\n=== Network Started Successfully! ===")
    print("Use these tmux commands to interact:")
    print("  tmux list-sessions          # Show all sessions")
    print("  tmux attach -t coordinator  # Attach to coordinator")
    print("  tmux attach -t peer_A       # Attach to peer A")
    print("  tmux kill-server            # Stop all sessions")
    print("\nPress Ctrl+B then D to detach from a tmux session.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Simple network runner that prints commands to run manually.
Use this if the terminal spawning version has D-Bus issues.
"""

import sys
import os

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    print("=== StrandCast Network Setup ===")
    print("Run these commands in separate terminals:\n")
    
    print("1. Start Coordinator:")
    print(f"cd {script_dir} && python3 coordinator.py")
    print()
    
    print("2. Start Subcoordinator 1:")
    print(f"cd {script_dir} && python3 subcoordinator.py 9001 127.0.0.1 9000")
    print()
    
    print("3. Start Subcoordinator 2:")
    print(f"cd {script_dir} && python3 subcoordinator.py 9002 127.0.0.1 9000")
    print()
    
    print("4. Start Peers (wait a moment between each):")
    peers = [("A", 10001), ("B", 10002), ("C", 10003), ("D", 10004), ("E", 10005), ("F", 10006)]
    
    for name, port in peers:
        print(f"cd {script_dir} && python3 peer.py {name} {port} 127.0.0.1 9000")
    
    print("\n=== Alternative: Use tmux ===")
    print("If you have tmux installed, you can run:")
    print(f"cd {script_dir} && python3 run_network_tmux.py")

if __name__ == "__main__":
    main()

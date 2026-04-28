import sys
import argparse
import os

# Add CCTVSee to path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CCTVSEE_DIR = os.path.join(os.path.dirname(BASE_DIR), "CCTVSee")
if CCTVSEE_DIR not in sys.path:
    sys.path.append(CCTVSEE_DIR)

from core.tactical_agent import TacticalAgent

def main():
    parser = argparse.ArgumentParser(description="Tactical Mission Bridge for ConciergeWeb")
    parser.add_argument("--target", help="Target IP address")
    parser.add_argument("pos_target", nargs="?", help="Target IP (positional)")
    
    args = parser.parse_args()
    target_ip = args.target or args.pos_target
    
    if not target_ip:
        print("ERROR: No target IP provided.")
        sys.exit(1)

    print(f"[*] Initializing Agentic Mission for: {target_ip}")
    agent = TacticalAgent()
    
    # run_mission is now a generator
    for update in agent.run_mission(target_ip):
        print(update, end='', flush=True)

if __name__ == "__main__":
    main()

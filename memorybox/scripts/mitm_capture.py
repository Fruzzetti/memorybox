#!/usr/bin/env python3
import subprocess
import os
import sys
import time
import datetime

CAPTURES_DIR = "/home/fuzz/concierge_wiki/captures"

def log(msg):
    print(f"[*] [MITM_CAPTURE]: {msg}")

def run_sniff(interface, target_ip, duration=30, filename=None):
    if not os.path.exists(CAPTURES_DIR):
        os.makedirs(CAPTURES_DIR, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if not filename:
        filename = f"capture_{target_ip.replace('.', '_')}_{timestamp}.pcap"
    
    filepath = os.path.join(CAPTURES_DIR, filename)
    relative_path = os.path.join("captures", filename)
    
    # 1. Start tcpdump in the background
    # -U: packet-buffered (immediate write)
    # -i: interface
    # -w: write to file
    # host target_ip: filter for the camera
    log(f"Starting Level 1 MITM capture on {interface} for {target_ip}...")
    log(f"Archive target: {relative_path}")
    
    cmd = ["sudo", "tcpdump", "-U", "-i", interface, "-w", filepath, "host", target_ip]
    
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        log(f"Sniffing for {duration} seconds...")
        time.sleep(duration)
        
        # 2. Terminate capture
        subprocess.run(["sudo", "pkill", "-n", "tcpdump"])
        log("Capture segment complete.")
        
        # 3. Generate Analysis Summary for Concierge
        # We use 'tcpdump -r' to read the first 10 packets for the AI to see
        summary_cmd = ["tcpdump", "-r", filepath, "-n", "-c", "20"]
        summary_result = subprocess.run(summary_cmd, capture_output=True, text=True)
        
        print("\n[OBSERVATION_SUMMARY]")
        print(f"File: {relative_path}")
        print(f"Duration: {duration}s")
        print("Packet Preview (First 20):")
        print(summary_result.stdout)
        print("[END_SUMMARY]\n")
        
        return True
    except Exception as e:
        log(f"Sniff Error: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: mitm_capture.py {interface} {target_ip} [duration]")
        sys.exit(1)
    
    iface = sys.argv[1]
    tip = sys.argv[2]
    dur = int(sys.argv[3]) if len(sys.argv) > 3 else 30
    
    run_sniff(iface, tip, dur)

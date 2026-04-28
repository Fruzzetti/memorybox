import json
import os
import sys
import argparse
from datetime import datetime

# v0.1: Bluetooth Proximity Auth Gate
# Checks the Sentry logs for a specific MAC address at 'Imminent' range.

LOG_FILE = "bluetooth_entries.json"

def check_auth(target_mac, max_age_seconds=30):
    # Locate the log file (relative to script or absolute)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(script_dir, LOG_FILE)
    
    if not os.path.exists(log_path):
        print("[ERROR]: Bluetooth Sentry log not found. Is the service running?")
        return False
        
    try:
        with open(log_path, 'r') as f:
            data = json.load(f)
            
        # Check if the overall log is stale
        log_time = datetime.fromisoformat(data.get("timestamp"))
        if (datetime.now() - log_time).total_seconds() > 60:
            print("[WARNING]: Sentry log is stale (>60s).")

        devices = data.get("devices", {})
        if target_mac not in devices:
            print(f"[DENIED]: Device {target_mac} not in proximity.")
            return False
            
        device = devices[target_mac]
        last_seen = datetime.fromisoformat(device["last_seen"])
        
        # Verify proximity and freshness
        if (datetime.now() - last_seen).total_seconds() > max_age_seconds:
            print(f"[DENIED]: Device {target_mac} last seen too long ago.")
            return False
            
        # Success criteria: RSSI > -60 (Imminent)
        if device["rssi"] > -60:
            print(f"[GRANTED]: Authorized device {target_mac} is IMMINENT (RSSI: {device['rssi']})")
            return True
        else:
            print(f"[DENIED]: Device {target_mac} is too distant (RSSI: {device['rssi']})")
            return False
            
    except Exception as e:
        print(f"[ERROR]: Auth check failure: {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mac", required=True, help="Target Bluetooth MAC to authorize")
    parser.add_argument("--age", type=int, default=30, help="Max age of detection in seconds")
    args = parser.parse_args()
    
    if check_auth(args.mac.upper(), args.age):
        sys.exit(0) # Logic PASS
    else:
        sys.exit(1) # Logic FAIL

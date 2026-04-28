#!/usr/bin/env python3
import os
import subprocess
import datetime
import json

# Tactical Context: LAN Sensing Tool
# v0.9-beta.062: Logic migrated to standalone script for AI accessibility

LEASES_FILE = "/var/lib/misc/dnsmasq.leases"

def main():
    if not os.path.exists(LEASES_FILE):
        print("SYSTEM ERROR: No active DHCP leases found (dnsmasq offline).")
        return
    
    try:
        # Use sudo to ensure hardware access to DHCP logs
        cmd = ["sudo", "cat", LEASES_FILE]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"PERMISSION ERROR: {res.stderr}")
            return
        
        leases = []
        lines = res.stdout.strip().splitlines()
        
        if not lines:
            print("No active devices detected on the tactical LAN.")
            return

        print(f"--- TACTICAL LAN DISCOVERY ({datetime.datetime.now().strftime('%H:%M:%S')}) ---")
        for line in lines:
            parts = line.split()
            if len(parts) >= 5:
                # 1774677846 a0:ce:c8:fb:d3:e0 172.16.0.114 lappy3 01:a0:ce:c8:fb:d3:e0
                expiry_ts = int(parts[0])
                mac = parts[1]
                ip = parts[2]
                hostname = parts[3] if parts[3] != "*" else "Unknown"
                
                print(f"[*] Found: {ip:<15} | Host: {hostname:<20} | MAC: {mac}")
        print(f"--- SCAN COMPLETE: {len(lines)} devices found ---")
        
    except Exception as e:
        print(f"SYSTEM ERROR: {str(e)}")

if __name__ == "__main__":
    main()

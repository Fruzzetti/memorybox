import sys
import os
import time
from datetime import datetime
from scapy.all import sniff, Dot11, Dot11Beacon, Dot11ProbeResp

# wifi_discover.py: Passive SSID Discovery Tool
# Purpose: Identify all nearby Access Points (SSIDs) in real-time.

def run_wifi_discovery(interface="mon0"):
    print(f"[*] ConciergeHub WiFi Discovery: Scanning for all SSIDs on {interface}")
    print(f"[*] Format: [Timestamp] | RSSI (dBm) | BSSID             | SSID")
    print("-" * 75)

    seen_ssids = set()

    def packet_callback(pkt):
        if not (pkt.haslayer(Dot11Beacon) or pkt.haslayer(Dot11ProbeResp)):
            return

        bssid = pkt.addr3
        rssi = getattr(pkt, "dBm_AntSignal", -100)
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        try:
            ssid = pkt.info.decode('utf-8') if pkt.info else "<Hidden SSID>"
        except: ssid = "<Unknown>"

        if not ssid: ssid = "<Hidden SSID>"
        
        # Unique logging: Update on first discovery or signal change
        unique_key = f"{bssid}:{ssid}"
        if unique_key not in seen_ssids:
            seen_ssids.add(unique_key)
            print(f"[{timestamp}] | {rssi:4} dBm | {bssid} | {ssid}")

    try:
        sniff(iface=interface, prn=packet_callback, store=0)
    except Exception as e:
        print(f"[!] WiFi Discovery Error: {e}")
        print("TIP: Ensure interface is in Monitor Mode: sudo ip link set mon0 up")

if __name__ == "__main__":
    iface = sys.argv[1] if len(sys.argv) > 1 else "mon0"
    
    try:
        run_wifi_discovery(iface)
    except KeyboardInterrupt:
        print("\n[*] WiFi Discovery session terminated.")

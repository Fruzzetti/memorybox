import sys
import os
import time
from datetime import datetime
from scapy.all import sniff, Dot11

# wifi_science.py: Tactical WiFi RSSI Calibration Tool
# Purpose: Map physical boundaries using 802.11 monitor mode signal strength.

def run_wifi_science(target_mac, interface="mon0"):
    print(f"[*] ConciergeHub WiFi Science Mode: Calibrating {target_mac} on {interface}")
    print(f"[*] Format: [Timestamp] | RSSI (dBm) | Frame Type | Proximity")
    print("-" * 65)

    def packet_callback(pkt):
        if not pkt.haslayer(Dot11):
            return

        # Check for target MAC in any address field (Source, Receiver, Transmitter)
        macs = [pkt.addr1, pkt.addr2, pkt.addr3]
        if any(m and m.lower() == target_mac.lower() for m in macs):
            rssi = getattr(pkt, "dBm_AntSignal", -100)
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            
            # Identify frame type
            ftype = "Data"
            if pkt.type == 0: ftype = "Mgmt"
            elif pkt.type == 1: ftype = "Ctrl"
            
            proximity = "Ambient"
            if rssi > -45: proximity = "Imminent"
            elif rssi > -65: proximity = "Proximate"
            
            print(f"[{timestamp}] | {rssi:4} dBm | {ftype:10} | {proximity}")

    try:
        sniff(iface=interface, prn=packet_callback, store=0)
    except Exception as e:
        print(f"[!] WiFi Science Error: {e}")
        print("TIP: Ensure interface is in Monitor Mode (e.g., sudo ip link set mon0 up)")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 wifi_science.py <MAC_ADDRESS> [interface]")
        sys.exit(1)
    
    target = sys.argv[1]
    iface = sys.argv[2] if len(sys.argv) > 2 else "mon0"
    
    try:
        run_wifi_science(target, iface)
    except KeyboardInterrupt:
        print("\n[*] WiFi Science session terminated.")

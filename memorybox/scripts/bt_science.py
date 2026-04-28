import asyncio
import sys
import os
from datetime import datetime
from bleak import BleakScanner

# bt_science.py: Tactical RSSI Calibration Tool
# Purpose: Map physical boundaries to AX210 signal strength levels.

async def run_science(target_mac):
    print(f"[*] ConciergeHub Science Mode: Calibrating {target_mac}")
    print(f"[*] Format: [Timestamp] | RSSI (dBm) | Est. Distance (m) | Stability")
    print("-" * 65)

    def detection_callback(device, advertisement_data):
        if device.address.lower() == target_mac.lower():
            rssi = advertisement_data.rssi
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            
            # Simple Free Space Path Loss (FSPL) approximation for calibration
            # This is a heuristic and will be adjusted based on user feedback.
            # distance = 10^((Measured Power - RSSI) / (10 * n))
            # Assuming Measured Power at 1m is -59 and n=2 for indoor space.
            dist_est = 10 ** ((-59 - rssi) / (10 * 2.0))
            
            stability = "+++" if rssi > -60 else "---" if rssi < -80 else "~~"
            print(f"[{timestamp}] | {rssi:4} dBm | {dist_est:>6.2f}m | {stability}")

    scanner = BleakScanner(detection_callback=detection_callback)
    
    await scanner.start()
    try:
        while True:
            await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        await scanner.stop()
        print("\n[*] Science session terminated.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 bt_science.py <MAC_ADDRESS>")
        sys.exit(1)
    
    mac = sys.argv[1]
    try:
        asyncio.run(run_science(mac))
    except Exception as e:
        print(f"[!] Science Error: {e}")

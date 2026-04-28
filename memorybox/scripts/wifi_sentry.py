import sys
import os
import json
import time
import logging
import subprocess
import threading
from datetime import datetime
from scapy.all import sniff, Dot11ProbeReq, Dot11Beacon, Dot11, Dot11Elt

# v0.9-beta.045: Passive WiFi Recon Sentry
# Sniffs 802.11 Probe Requests to identify nearby devices and network intent.

# v0.2: Tactical UI Sync
# Ensures the main server can find the logs from the scripts/ directory.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(BASE_DIR, "wifi_entries.json")
REVISIT_THRESHOLD = 1800  # 30 minutes to minimize log spam

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("wifi_sentry")

class WifiSentry:
    def __init__(self, interface="mon0"):
        self.interface = interface
        self.raw_interface = "wlp5s0" # The physical AX210
        self.seen_devices = {}  # MAC: {timestamp, ssid, rssi}
        self.active_counts = {"total": 0, "probing": 0}
        self.ensure_monitor_mode()
        self.load_historical()

    def ensure_monitor_mode(self):
        """v0.2: Self-healing monitor mode for AX210/Shared AP usage."""
        if not os.path.exists(f"/sys/class/net/{self.interface}"):
            logger.info(f"Monitor interface {self.interface} not found. Attempting to create virtual sensor on {self.raw_interface}...")
            try:
                # Try creating a virtual monitor interface (Shared mode)
                subprocess.run(f"sudo iw dev {self.raw_interface} interface add {self.interface} type monitor", shell=True, check=True)
                subprocess.run(f"sudo ip link set {self.interface} up", shell=True, check=True)
                logger.info(f"[+] Virtual Sensor {self.interface} deployed successfully.")
            except Exception as e:
                logger.error(f"[!] Could not create monitor interface: {e}")

    def load_historical(self):
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, 'r') as f:
                    data = json.load(f)
                    self.seen_devices = data.get("devices", {})
                    self.active_counts = data.get("active_counts", {"total": 0, "probing": 0})
            except: pass

    def save_state(self):
        with open(LOG_FILE, 'w') as f:
            json.dump({
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "active_counts": self.active_counts,
                "devices": self.seen_devices
            }, f, indent=4)

    def packet_callback(self, pkt):
        if not pkt.haslayer(Dot11):
            return

        mac = pkt.addr2
        if not mac: return
        
        rssi = getattr(pkt, "dBm_AntSignal", -100)
        if rssi is None: rssi = -100
        
        now = time.time()
        
        # Determine device intent
        ssid = "N/A"
        is_probe = pkt.haslayer(Dot11ProbeReq)
        if is_probe:
            try:
                ssid = pkt.info.decode('utf-8') if pkt.info else "Wildcard Scan"
            except: ssid = "Unknown"

        # Update or Create Entry
        if mac not in self.seen_devices or (now - self.seen_devices[mac]['last_seen_ts'] > REVISIT_THRESHOLD):
            logger.info(f"SIGHTING: {mac} | SSID: {ssid} | RSSI: {rssi} dBm")
            # v0.2: Active Sensing - Respond to probes to capture more caps
            if is_probe and rssi > -65:
                self.send_active_probe_response(pkt, ssid)
            
        self.seen_devices[mac] = {
            "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_seen_ts": now,
            "ssid": ssid,
            "rssi": rssi,
            "proximity": self.get_proximity_label(rssi)
        }
        
        # Maintenance: Clean up old counts
        self.update_stats()
        self.save_state()

    def send_active_probe_response(self, query_pkt, ssid):
        """v0.2: 'Hard Query' Sensing. Respond to device probes to force capability reveal."""
        from scapy.all import Dot11ProbeResp, Dot11Beacon, sendp
        
        target = query_pkt.addr2
        try:
            # Construct a 'shadow' probe response (mimicking the intent)
            # This makes the device think there is an AP it knows nearby
            resp = (Dot11(addr1=target, addr2="00:11:22:33:44:55", addr3="00:11:22:33:44:55") /
                    Dot11ProbeResp(timestamp=int(time.time()), beacon_interval=0x0064, cap=0x1101) /
                    Dot11Elt(ID="SSID", info=ssid) /
                    Dot11Elt(ID="Rates", info="\x82\x84\x8b\x96\x0c\x12\x18\x24") /
                    Dot11Elt(ID="DSset", info="\x01"))
            
            logger.info(f"HARD_QUERY: Sent shadow response to {target} for SSID: '{ssid}'")
            sendp(resp, iface=self.interface, count=1, verbose=0)
        except Exception as e:
             logger.error(f"Failed to send active probe: {e}")

    def get_proximity_label(self, rssi):
        if rssi > -45: return "Imminent"
        if rssi > -65: return "Proximate"
        if rssi > -85: return "Ambient"
        return "Ghost"

    def update_stats(self):
        now = time.time()
        active = [d for mac, d in self.seen_devices.items() if now - d['last_seen_ts'] < 300]
        probing = [d for d in active if d['ssid'] != "N/A"]
        self.active_counts = {
            "total": len(active),
            "probing": len(probing)
        }

    def start(self):
        logger.info(f"Sniffing on {self.interface}... (Active 'Hard Query' Sensor ENGAGED)")
        # v0.2: Start Channel Hopper in background to catch all 2.4GHz traffic
        hop_thread = threading.Thread(target=self.channel_hopper, daemon=True)
        hop_thread.start()
        
        try:
            # We must use L2 socket for active injecting
            sniff(iface=self.interface, prn=self.packet_callback, store=0, monitor=True)
        except Exception as e:
            logger.error(f"Sniffing failed: {e}")

    def channel_hopper(self):
        """v0.2: Cyclical 2.4GHz Channel Hopping for AX210."""
        channels = [1, 6, 11, 2, 7, 12, 3, 8, 13, 4, 9, 5, 10]
        while True:
            for ch in channels:
                try:
                    # v0.2: Silent fail if device is busy (e.g., hosting an AP)
                    subprocess.run(
                        f"sudo iw dev {self.interface} set channel {ch}", 
                        shell=True, 
                        capture_output=True, 
                        check=False
                    )
                    time.sleep(1.0) # Hop every 1s 
                except: pass
            logger.info("TIP: Ensure interface is in Monitor Mode: sudo iw dev <iface> interface add mon0 type monitor; sudo ip link set mon0 up")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        iface = sys.argv[1]
    else:
        iface = "mon0"
    
    sentry = WifiSentry(iface)
    sentry.start()

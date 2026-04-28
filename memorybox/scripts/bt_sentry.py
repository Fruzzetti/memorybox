import asyncio
import json
import os
import time
import re
from datetime import datetime, timedelta
from bleak import BleakScanner, BleakClient
import sys

# v0.1: Bluetooth Sentry for Concierge Hub
# Passive presence detection via AX210 BLE advertisements.

# v0.2: Tactical UI Sync
# Ensures the main server can find the logs from the scripts/ directory.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(BASE_DIR, "bluetooth_entries.json")
WIKI_LOG = "/home/fuzz/concierge_wiki/logs/bt_presence.log"
SCAN_INTERVAL = 5.0 # Seconds between active window flushes
SESSION_TIMEOUT = 1800 # 30 minutes before a device is 'new' again
IGNORE_LIST_FILE = os.path.join(os.path.dirname(__file__), "ignore_list.json")
CENSUS_FILE = os.path.join(os.path.dirname(__file__), "device_census.json")

# Manufacturer OUI mapping for common devices (Mobile/Watch)
OUI_MAP = {
    "Apple": ["Apple, Inc.", "Apple Inc."],
    "Samsung": ["Samsung Electronics", "Samsung"],
    "Tile": ["Tile, Inc."],
    "Google": ["Google Inc.", "Google"]
}

# v0.1: Trusted Device Registry
TRUSTED_DEVICES = {
    "78:B6:FE:FA:B7:5A": "Owner (Samsung)"
}

class BTSentry:
    def __init__(self):
        self.seen_devices = {}
        self.total_detections = 0
        self.ignore_list = self.load_ignore_list()
        self.census = self.load_census()
        self.probing_queue = set()
        # v0.2: Concurrency Guard - BlueZ only allows one connection attempt at a time
        self.probe_semaphore = asyncio.Semaphore(1)

    def load_ignore_list(self):
        if os.path.exists(IGNORE_LIST_FILE):
            try:
                with open(IGNORE_LIST_FILE, 'r') as f:
                    return json.load(f)
            except: pass
        return {"ignore_macs": [], "ignore_ouis": []}

    def is_ignored(self, mac):
        mac = mac.upper()
        if mac in [m.upper() for m in self.ignore_list.get("ignore_macs", [])]:
            return True
        oui = mac.replace(":", "")[:6]
        if oui in [o.replace(":", "")[:6].upper() for o in self.ignore_list.get("ignore_ouis", [])]:
            return True
        return False

    def load_census(self):
        if os.path.exists(CENSUS_FILE):
            try:
                with open(CENSUS_FILE, 'r') as f:
                    return json.load(f)
            except: pass
        return {}

    def save_census(self):
        try:
            with open(CENSUS_FILE, 'w') as f:
                json.dump(self.census, f, indent=2)
        except: pass

    def get_manufacturer(self, advertisement_data):
        """Extract brand (Apple, Samsung, etc.) from BLE metadata."""
        m_data = getattr(advertisement_data, 'manufacturer_data', {})
        if m_data:
            # Common Bluetooth SIG IDs:
            # 0x004c = Apple, 0x0075 = Samsung, 0x011B = Google
            if 0x004C in m_data: return "Apple"
            if 0x0075 in m_data: return "Samsung"
            if 0x011B in m_data: return "Google"
        return "Unknown"

    async def device_found(self, device, advertisement_data):
        """Callback triggered on every BLE packet."""
        mac = device.address
        if self.is_ignored(mac): return

        rssi = advertisement_data.rssi
        if rssi is None: rssi = -100
        
        name = device.name or "Unnamed Device"
        vendor = self.get_manufacturer(advertisement_data)
        
        # Proximity heuristic: RSSI > -60 is "Imminent", -60 to -80 is "Nearby"
        proximity = "Distant"
        if rssi > -60: proximity = "Imminent"
        elif rssi > -80: proximity = "Nearby"

        # Identity Override for Owner Device
        if mac in TRUSTED_DEVICES:
            name = TRUSTED_DEVICES[mac]
            is_trusted = True
        else:
            is_trusted = False

        # Persistent Census Tracking (First Seen)
        is_first_ever = mac not in self.census
        if is_first_ever:
            self.census[mac] = {
                "first_seen": datetime.now().isoformat(),
                "visits": 1,
                "name": name,
                "vendor": vendor,
                "probed": False
            }
            self.save_census()
        
        # Update local tracking
        is_new_session = mac not in self.seen_devices
        self.seen_devices[mac] = {
            "name": name,
            "vendor": vendor,
            "rssi": rssi,
            "proximity": proximity,
            "trusted": is_trusted,
            "last_seen": datetime.now().isoformat(),
            "hits": self.seen_devices.get(mac, {}).get("hits", 0) + 1,
            "is_first_visit": is_first_ever
        }
        
        if is_new_session:
            self.total_detections += 1
            if not is_first_ever:
                self.census[mac]["visits"] += 1
                self.save_census()
            
            # Trigger Historical Log
            self.log_historical_sighting(mac, name, vendor, rssi, proximity, is_first_ever=is_first_ever)
            
            # v0.2: Trigger Silent Background Probe only for new/unprobed targets
            if is_first_ever or (not self.census[mac].get("probed") and proximity != "Distant"):
                if mac not in self.probing_queue:
                    # Determine if BLE or Classic for probing
                    if ":" in mac and len(mac) == 17:
                        asyncio.create_task(self.probe_device(mac))
        else:
            # Session check: If last seen > 30 mins ago, treat as a new visit
            last_dt = datetime.fromisoformat(self.seen_devices[mac]["last_seen"])
            if (datetime.now() - last_dt).total_seconds() > SESSION_TIMEOUT:
                self.census[mac]["visits"] += 1
                self.save_census()
                self.log_historical_sighting(mac, name, vendor, rssi, proximity, is_revisit=True)

    def log_historical_sighting(self, mac, name, vendor, rssi, proximity, is_revisit=False, is_first_ever=False):
        """Append a sighting event to the persistent wiki log."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = "REVISIT" if is_revisit else "NEW"
        if is_first_ever: status = "FIRST_EVER"
        
        # Tag Owner in logs
        tag = "[OWNER]" if mac in TRUSTED_DEVICES else ""
        log_entry = f"[{timestamp}] {status:10} {tag:7} | {mac} | {vendor:15} | {name:20} | RSSI: {rssi:4} | {proximity}\n"
        
        # Ensure log directory exists
        os.makedirs(os.path.dirname(WIKI_LOG), exist_ok=True)
        try:
            with open(WIKI_LOG, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        except Exception as e:
            print(f"[ERROR]: Failed to write to historical log: {e}")

    async def probe_device(self, mac):
        """v0.2: Silent GATT Background Probe. Enumerates services without pairing."""
        if mac in self.probing_queue: return
        
        async with self.probe_semaphore:
            self.probing_queue.add(mac)
            print(f"[*] SILENT PROBE: Initiating session with {mac}...")
            try:
                async with BleakClient(mac, timeout=10.0) as client:
                    if await client.connect():
                        services = []
                        for service in client.services:
                            services.append({
                                "uuid": service.uuid,
                                "description": service.description
                            })
                        
                        # Update census with findings
                        self.census[mac]["probed"] = True
                        self.census[mac]["services"] = services
                        self.census[mac]["last_probed"] = datetime.now().isoformat()
                        self.save_census()
                        
                        print(f"[+] PROBE SUCCESS: {mac} | Found {len(services)} services.")
                        # Log finding as a generic audit event
                        self.log_audit_event(f"PROBE_INTEL | {mac} | {len(services)} GATT Services Discovered")
                    else:
                        print(f"[-] PROBE FAILED: {mac} | Connection refused.")
            except Exception as e:
                print(f"[-] PROBE ERROR: {mac} | {str(e)}")
            finally:
                if mac in self.probing_queue:
                    self.probing_queue.remove(mac)

    def log_audit_event(self, message):
        """Log a systemic audit event to the historical wiki log."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] AUDIT      | {message}\n"
        try:
            with open(WIKI_LOG, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        except: pass

    def prune_logs(self):
        """v0.2: Enforce 365-day retention on historical presence log."""
        from datetime import timedelta
        if not os.path.exists(WIKI_LOG): return
        
        print(f"[*] MAINTENANCE: Pruning audit logs (Retention: 365 days)...")
        cutoff = datetime.now() - timedelta(days=365)
        
        try:
            temp_log = WIKI_LOG + ".tmp"
            with open(WIKI_LOG, 'r', encoding='utf-8') as fin, \
                 open(temp_log, 'w', encoding='utf-8') as fout:
                for line in fin:
                    # Match pattern like [2026-03-29 03:22:57]
                    match = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
                    if match:
                        try:
                             line_dt = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                             if line_dt > cutoff:
                                  fout.write(line)
                        except:
                             fout.write(line)
                    else:
                        fout.write(line)
            os.replace(temp_log, WIKI_LOG)
        except Exception as e:
            print(f"[!] Log prune failed: {e}")

    async def scan_loop(self):
        # v0.2: Maintenance on startup
        self.prune_logs()
        
        # Start BLE Scanner
        scanner = BleakScanner(detection_callback=self.device_found)
        await scanner.start()
        
        print(f"[*] Starting Dual-Mode Bluetooth Sentry Service (AX210)")
        print(f"[*] Active Sensing: BLE + Classic (SDP Enumeration) enabled.")
        print(f"[*] Logging to {LOG_FILE} and system backplane...")
        
        # Start Classic Bluetooth (Async task)
        asyncio.create_task(self.classic_scan_loop())
        
        try:
            while True:
                await asyncio.sleep(SCAN_INTERVAL)
                self.flush_log()
        finally:
            await scanner.stop()

    async def classic_scan_loop(self):
        """v0.2: Background Classic Bluetooth (BR/EDR) Discovery."""
        while True:
            try:
                # Use bluetoothctl for a non-blocking classic scan
                # 'scan on' for classic requires 'agent on' and 'default-agent' in some envs
                # but 'devices' usually shows cached info we can 'scan' to refresh.
                proc = await asyncio.create_subprocess_shell(
                    "hcitool scan --flush",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                
                lines = stdout.decode().splitlines()
                # Skip header "Scanning ..."
                for line in lines[1:]:
                    parts = line.split(maxsplit=1)
                    if len(parts) >= 1:
                        mac = parts[0].strip()
                        name = parts[1].strip() if len(parts) > 1 else "Unknown Classic"
                        
                        # Handle classic sightings via fake advertisement for compatibility
                        class FakeAdv:
                            def __init__(self, rssi): self.rssi = rssi
                            def __dict__(self): return {}
                        
                        # We don't get RSSI from hcitool scan directly, assume 'Nearby' (-70)
                        await self.device_found(type('Dev', (), {'address': mac, 'name': name}), FakeAdv(-70))
            except Exception as e:
                print(f"[!] Classic Scan Error: {e}")
            
            await asyncio.sleep(30) # Classic scans are heavy, run less frequently

    def flush_log(self):
        """Write current presence state to a structured JSON file."""
        # Filter for devices seen in the last 60 seconds (Active Presence)
        now = datetime.now()
        active_presence = {}
        
        for mac, data in self.seen_devices.items():
            last_seen = datetime.fromisoformat(data["last_seen"])
            if (now - last_seen).total_seconds() < 60:
                active_presence[mac] = data

        # Write to JSON for the Hub's frontend/backend to eat
        try:
            with open(LOG_FILE, 'w') as f:
                json.dump({
                    "timestamp": now.isoformat(),
                    "active_counts": {
                        "total": len(active_presence),
                        "imminent": sum(1 for d in active_presence.values() if d["proximity"] == "Imminent")
                    },
                    "devices": active_presence
                }, f, indent=2)
        except Exception as e:
            print(f"[ERROR]: Failed to flush BT log: {e}")

if __name__ == "__main__":
    sentry = BTSentry()
    try:
        asyncio.run(sentry.scan_loop())
    except KeyboardInterrupt:
        print("\n[*] Shutting down Sentry...")

#!/usr/bin/env python3
import subprocess
import os
import sys
import time

# v0.9-beta.065: Hardened Privilege Model
# Internal sudo removed; script must be run as root.
if os.geteuid() != 0:
    print("[ERROR]: This script must be run as root (via sudo).")
    sys.exit(1)

def get_interfaces():
    """
    v0.9-beta.056: Hardened Interface Discovery
    Identifies WAN via default route and Tactical by excluding WAN/Loopback/WiFi.
    """
    try:
        wan = None
        # 1. Detect WAN (Interface with the default route)
        try:
            # Try specific check first
            wan_out = subprocess.check_output("ip route get 8.8.8.8", shell=True, stderr=subprocess.DEVNULL).decode()
            wan = wan_out.split("dev")[1].split()[0].strip()
        except subprocess.CalledProcessError:
            # Fallback: Find any default gateway
            try:
                route_out = subprocess.check_output("ip route list | grep default", shell=True).decode()
                wan = route_out.split("dev")[1].split()[0].strip()
            except:
                pass
        
        # 2. Detect Tactical Port
        # First, check for the known primary tactical port on this hardware
        if os.path.exists("/sys/class/net/enp6s0"):
            return wan, "enp6s0"

        # Second, try to find it by its known static IP (if currently assigned)
        try:
            tac_out = subprocess.check_output("ip -o addr show | grep '172.16.0.1'", shell=True).decode()
            return wan, tac_out.split()[1].strip()
        except subprocess.CalledProcessError:
            pass 
            
        # Third, detect by exclusion
        links = subprocess.check_output("ip -br link show", shell=True).decode().splitlines()
        for line in links:
            parts = line.split()
            if not parts: continue
            name = parts[0]
            if name in ['lo', wan or ''] or name.startswith(('br', 'mon', 'wlp', 'wlan', 'docker', 'veth')):
                continue
            if name.startswith(('en', 'eth')):
                return wan, name
        
        return wan, None
    except Exception as e:
        print(f"Error detecting interfaces: {e}")
        return None, None

def _update_netplan(mode, wan, tac):
    """ Writes persistent Netplan config for the given mode. """
    if mode == "bridge":
        config = f"""
network:
  version: 2
  renderer: networkd
  ethernets:
    {wan}:
      dhcp4: false
      optional: true
    {tac}:
      dhcp4: false
      optional: true
  bridges:
    br0:
      interfaces: [{wan}, {tac}]
      dhcp4: true
      addresses: [10.0.0.4/24]
      optional: true
      parameters:
        stp: false
        forward-delay: 0
"""
    else: # private
        config = f"""
network:
  version: 2
  renderer: networkd
  ethernets:
    {wan}:
      dhcp4: true
      optional: true
    {tac}:
      addresses: [172.16.0.1/24]
      optional: true
"""
    
    config_path = "/etc/netplan/99-concierge-network.yaml"
    # Unified filename for consistency and persistence
    print(f"[*] Updating persistent Netplan profile: {config_path} ({mode} mode)")
    
    # Use absolute path for temp file in /tmp to ensure writability
    temp_path = "/tmp/temp_netplan.yaml"
    with open(temp_path, "w") as f:
        f.write(config.strip() + "\n")
    os.chmod(temp_path, 0o600)
    
    os.system(f"mv {temp_path} {config_path}")
    os.system(f"chown root:root {config_path}")
    
    # Remove old naming scheme if it exists
    old_config = "/etc/netplan/99-concierge-bridge.yaml"
    if os.path.exists(old_config):
        os.system(f"rm {old_config}")
    
    print("[!] APPLYING NETWORK CHANGES...")
    os.system("netplan apply")

def deploy_bridge():
    wan, tac = get_interfaces()
    if not wan or not tac:
        print("Could not identify interfaces automatically.")
        return

    print(f"[*] Identified WAN: {wan}, Tactical: {tac}")
    
    print("[*] Preparing interfaces: Stopping dnsmasq...")
    os.system("systemctl stop dnsmasq")
    
    _update_netplan("bridge", wan, tac)
    
    # Force flush physical interfaces to prevent DHCP "ghosting"
    print(f"[*] Post-Bridge Cleanup: Flushing IPs from {wan} and {tac}...")
    time.sleep(1) 
    os.system(f"ip addr flush dev {wan}")
    os.system(f"ip addr flush dev {tac}")
    
    print("[+] Bridge deployed. Hub is now transparent.")

def revert_private():
    wan, tac = get_interfaces()
    # Fallback discovery
    if not tac and os.path.exists("/sys/class/net/enp6s0"): tac = "enp6s0"
    if not wan: wan = "eno1" # Common fallback for this hardware

    print(f"[*] Reverting to Private mode (WAN: {wan}, Tactical: {tac})...")
    
    _update_netplan("private", wan, tac)
    
    print("[*] Cleaning up bridge remnants...")
    os.system("ip link set br0 down 2>/dev/null")
    os.system("ip link delete br0 2>/dev/null")
    
    if tac:
        print(f"[*] FORCING TACTICAL IP RECOVERY: 172.16.0.1 on {tac}")
        os.system(f"ip addr flush dev {tac}")
        # Use 'broadcast +' to ensure the correct broadcast address (172.16.0.255) is set
        os.system(f"ip addr add 172.16.0.1/24 broadcast + dev {tac}")
        # Ensure the link is fresh for DHCP (Layer 2 carrier re-init)
        os.system(f"ip link set {tac} down")
        time.sleep(1)
        os.system(f"ip link set {tac} up")
    
    # Restart dnsmasq
    print("[*] Restarting DHCP server (dnsmasq)...")
    os.system("systemctl restart dnsmasq")
    time.sleep(1)
    os.system("systemctl status dnsmasq --no-pager")
    print("[+] Reverted to Private mode. Tactical network (172.16.0.0/24) is live.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: bridge_real.py {bridge|private|status}")
    else:
        cmd = sys.argv[1]
        if cmd == "bridge":
            deploy_bridge()
        elif cmd == "private":
            revert_private()
        elif cmd == "status":
            wan, tac = get_interfaces()
            print(f"WAN: {wan}")
            print(f"Tactical: {tac}")
            os.system("ip -br addr show")


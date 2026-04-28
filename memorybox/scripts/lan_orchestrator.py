#!/usr/bin/env python3
import os
import sys
import subprocess
import time

# v0.9.5: Triple-Mode LAN Orchestrator
# Handles enp6s0 (Wired) for Bridge, NAT, and Private modes.

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_FILE = os.path.join(BASE_DIR, "dnsmasq_lan.conf")
INTERFACE_LAN = "enp6s0"
INTERFACE_WAN = "eno1"
BRIDGE_NAME = "br0"
DNSMASQ_PID = "/tmp/concierge_dnsmasq_lan.pid"

def run_cmd(cmd, check=True):
    try:
        res = subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True, timeout=15)
        return res
    except Exception as e:
        if check: print(f"[CMD ERROR]: {cmd}\n{str(e)}")
        return None

def cleanup():
    """ Resets the LAN port and stops all services. """
    print(f"[*] Stopping LAN services and flushing {INTERFACE_LAN}...")
    
    # 1. Kill dnsmasq
    subprocess.run("sudo pkill -f dnsmasq_lan.conf", shell=True, check=False)
    
    # 2. Remove Bridge
    run_cmd(f"sudo ip link set {BRIDGE_NAME} down", check=False)
    run_cmd(f"sudo brctl delbr {BRIDGE_NAME}", check=False)
    
    # 3. Clear IPTables (Only LAN-specific NAT)
    run_cmd(f"sudo iptables -t nat -D POSTROUTING -o {INTERFACE_WAN} -j MASQUERADE", check=False)
    run_cmd(f"sudo iptables -D FORWARD -i {INTERFACE_LAN} -o {INTERFACE_WAN} -j ACCEPT", check=False)
    run_cmd(f"sudo iptables -D FORWARD -m state --state ESTABLISHED,RELATED -j ACCEPT", check=False)
    
    # 4. Flush Interface
    run_cmd(f"sudo ip addr flush dev {INTERFACE_LAN}", check=False)
    run_cmd(f"sudo ip link set {INTERFACE_LAN} down", check=False)
    run_cmd(f"sudo ip link set {INTERFACE_LAN} up", check=False)

def switch_to_bridge():
    """ Layer 2 Transparent Bridge (Direct Discovery). """
    cleanup()
    print(f"[*] Switching to TACTICAL BRIDGE Mode...")
    
    # 1. Create Bridge
    run_cmd(f"sudo brctl addbr {BRIDGE_NAME}")
    run_cmd(f"sudo brctl addif {BRIDGE_NAME} {INTERFACE_LAN}")
    run_cmd(f"sudo brctl addif {BRIDGE_NAME} {INTERFACE_WAN}")
    
    # 2. Set UP
    run_cmd(f"sudo ip link set {BRIDGE_NAME} up")
    run_cmd(f"sudo ip link set {INTERFACE_LAN} up")
    run_cmd(f"sudo ip link set {INTERFACE_WAN} up")
    
    print("[+] Bridge mode active. Hub is now a transparent wire.")

def switch_to_nat(internet=True):
    """ Layer 3 Gateway (Double NAT). """
    cleanup()
    mode_name = "NAT" if internet else "PRIVATE"
    print(f"[*] Switching to TACTICAL {mode_name} Mode...")
    
    # 1. Configure Gateway IP
    run_cmd(f"sudo ip addr add 172.16.0.1/24 dev {INTERFACE_LAN}")
    run_cmd(f"sudo ip link set {INTERFACE_LAN} up")
    
    # 2. Start DHCP Trap
    print(f"[*] Launching DHCP Trap (.100) on {INTERFACE_LAN}...")
    subprocess.Popen(["sudo", "dnsmasq", "-C", CONF_FILE, "-k"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    
    # 3. Routing
    run_cmd("sudo sysctl -w net.ipv4.ip_forward=1")
    if internet:
        # v0.9.5: Hardened Forwarding Protocol
        run_cmd(f"sudo iptables -t nat -A POSTROUTING -o {INTERFACE_WAN} -j MASQUERADE")
        run_cmd(f"sudo iptables -A FORWARD -i {INTERFACE_LAN} -o {INTERFACE_WAN} -j ACCEPT")
        run_cmd(f"sudo iptables -A FORWARD -m state --state ESTABLISHED,RELATED -j ACCEPT")
        print("[+] Internet forwarding enabled.")
    else:
        print("[+] Isolation active. Camera is in a blackout room.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: lan_orchestrator.py <bridge|nat|private|stop>")
        sys.exit(1)

    mode = sys.argv[1].lower()
    try:
        if mode == "bridge":
            switch_to_bridge()
        elif mode == "nat":
            switch_to_nat(internet=True)
        elif mode == "private":
            switch_to_nat(internet=False)
        elif mode == "stop":
            cleanup()
        else:
            print(f"Unknown mode: {mode}")
        sys.exit(0)
    except Exception as e:
        print(f"[LAN ORCHESTRATOR ERROR]: {str(e)}")
        sys.exit(1)

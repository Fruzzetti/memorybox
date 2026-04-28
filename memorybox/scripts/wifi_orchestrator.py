#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import signal

# v0.9-beta.072: Unified WiFi Orchestrator with Persistent Ground-Truth
# Handles Strict Triple-Mode Switching for wlp5s0.

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_DIR = os.path.join(BASE_DIR, "conf")
INTERFACE = "wlp5s0"
STATE_FILE = "/tmp/concierge_wifi_state.txt"

# Files
HOSTAPD_CONF = os.path.join(CONF_DIR, "hostapd.conf")
DNSMASQ_CONF = os.path.join(CONF_DIR, "dnsmasq_api.conf")
HOSTAPD_PID = "/tmp/concierge_hostapd.pid"
DNSMASQ_PID = "/tmp/concierge_dnsmasq_wifi.pid"

DEBUG_LOG = "/tmp/wifi_debug.log"

def run_cmd(cmd, check=True):
    try:
        # v0.9-beta.076: 15s internal timeout to prevent web engine hangs
        res = subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True, timeout=15)
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[*] CMD: {cmd}\n[+] STDOUT: {res.stdout}\n[!] STDERR: {res.stderr}\n")
        return res
    except subprocess.CalledProcessError as e:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[*] CMD ERROR: {cmd}\n[!] STDERR: {e.stderr}\n")
        if check: return e
        return e

def kill_process_by_name(name):
    try:
        subprocess.run(f"sudo pkill -9 {name}", shell=True, check=False)
        with open(DEBUG_LOG, "a") as f: f.write(f"[*] Killed: {name}\n")
    except: pass

def update_state(mode):
    try:
        with open(STATE_FILE, "w") as f:
            f.write(mode)
        # v0.9-beta.073: Ensure the web engine can read the state file created by sudo
        os.chmod(STATE_FILE, 0o666)
        with open(DEBUG_LOG, "a") as f: f.write(f"[+] State updated: {mode}\n")
    except Exception as e:
        print(f"[STATE ERROR]: {str(e)}")
        with open(DEBUG_LOG, "a") as f: f.write(f"[!] State Error: {str(e)}\n")

def cleanup():
    """ Hard reset for wlp5s0 and all associated processes. """
    print(f"[*] Cleaning up {INTERFACE} and stopping AP services...")
    
    # 1. Kill AP Processes
    kill_process_by_name("hostapd")
    kill_process_by_name("dnsmasq")
    
    # Enable systemd-resolved back (for LINK mode)
    try:
        run_cmd("sudo systemctl start systemd-resolved", check=False)
    except: pass
    
    for pid_file in [HOSTAPD_PID, DNSMASQ_PID]:
        if os.path.exists(pid_file): 
            try: os.remove(pid_file)
            except: pass

    # 2. Flush Interface
    run_cmd(f"sudo ip addr flush dev {INTERFACE}", check=False)
    run_cmd(f"sudo ip link set {INTERFACE} down", check=False)
    
    update_state("link")
    print(f"[+] Cleanup complete.")

def switch_to_link():
    """ Restores Managed (Station) mode via Netplan. """
    cleanup()
    print(f"[*] Switching to LINK mode (Managed)...")
    
    target_netplan = "/etc/netplan/99-concierge-wifi.yaml"
    if os.path.exists(target_netplan):
        run_cmd("sudo netplan apply", check=False)
        print(f"[+] Netplan applied.")
    
    update_state("link")

def switch_to_wap(ssid, passphrase, internet=False, open_network=False):
    """ Deploys a functional Access Point. """
    try:
        cleanup()
        print(f"[*] Switching to WAP mode (SSID: {ssid})...")
        
        # Handle Port 53 Battle: Kill systemd-resolved stub
        run_cmd("sudo systemctl stop systemd-resolved", check=False)

        # 1. Generate configs
        os.makedirs(CONF_DIR, exist_ok=True)
        if open_network:
            conf_content = f"interface={INTERFACE}\ndriver=nl80211\nssid={ssid}\nhw_mode=g\nchannel=6\nauth_algs=1\nwpa=0\n"
        else:
            conf_content = f"interface={INTERFACE}\ndriver=nl80211\nssid={ssid}\nhw_mode=g\nchannel=6\nwpa=2\nwpa_passphrase={passphrase}\nwpa_key_mgmt=WPA-PSK\nwpa_pairwise=TKIP\nrsn_pairwise=CCMP\n"
        
        with open(HOSTAPD_CONF, "w") as f:
            f.write(conf_content.strip())

        dns_content = f"interface={INTERFACE}\ndhcp-range=10.20.0.10,10.20.0.100,12h\ndomain=concierge.local\naddress=/#/10.20.0.1\n"
        with open(DNSMASQ_CONF, "w") as f:
            f.write(dns_content.strip())

        # 2. Configure Interface IP
        run_cmd(f"sudo ip link set {INTERFACE} up", check=False)
        run_cmd(f"sudo ip addr add 10.20.0.1/24 dev {INTERFACE}", check=False)

        # 3. Start Daemons
        print("[*] Launching hostapd and dnsmasq...")
        subprocess.Popen(["sudo", "hostapd", HOSTAPD_CONF], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        subprocess.Popen(["sudo", "dnsmasq", "-C", DNSMASQ_CONF, "-k"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

        # 4. Handle Routing
        run_cmd("sudo sysctl -w net.ipv4.ip_forward=1", check=False)
        if internet:
            run_cmd("sudo iptables -t nat -A POSTROUTING -o eno1 -j MASQUERADE", check=False)
        else:
            run_cmd("sudo iptables -t nat -D POSTROUTING -o eno1 -j MASQUERADE", check=False)

        update_state("wap")
        print(f"[+] WAP Mode Live at 10.20.0.1")
    except Exception as e:
        print(f"[WAP LOG]: {str(e)}")
        with open(DEBUG_LOG, "a") as f: f.write(f"[!] WAP Exception: {str(e)}\n")

def switch_to_honeypot():
    """ Deploys the 'Guest' Karma trap as an OPEN network. """
    try:
        switch_to_wap("Guest", "", internet=False, open_network=True)
        update_state("honeypot")
        print(f"[+] HONEYPOT Mode active (SSID: Guest - OPEN)")
    except Exception as e:
        print(f"[Honeypot LOG]: {str(e)}")
        with open(DEBUG_LOG, "a") as f: f.write(f"[!] Honeypot Exception: {str(e)}\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: wifi_orchestrator.py <link|wap|honeypot|stop> [ssid] [pass] [internet:on|off]")
        sys.exit(1)

    mode = sys.argv[1].lower()
    
    try:
        if mode == "link":
            switch_to_link()
        elif mode == "wap":
            ssid = sys.argv[2] if len(sys.argv) > 2 else "ConciergeHub"
            pw = sys.argv[3] if len(sys.argv) > 3 else "concierge123"
            inet = True if len(sys.argv) > 4 and sys.argv[4] == "on" else False
            switch_to_wap(ssid, pw, inet)
        elif mode == "honeypot":
            switch_to_honeypot()
        elif mode == "stop":
            cleanup()
        else:
            print(f"Unknown mode: {mode}")
            sys.exit(1)
        
        sys.exit(0) # v0.9-beta.073: Explicit clean exit
    except Exception as e:
        print(f"[ORCHESTRATOR CRASH]: {str(e)}")
        sys.exit(1)

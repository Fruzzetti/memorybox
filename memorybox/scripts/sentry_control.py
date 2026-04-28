import sys
import os
import subprocess
import signal
import time

# v0.9-beta.048: Sentry Control & PID Manager
# Handles start/stop/status for bt_sentry and wifi_sentry.

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
VENV_PYTHON = os.path.join(BASE_DIR, "venv", "bin", "python3")

SERVICES = {
    "bt": {
        "script": "bt_sentry.py",
        "pid_file": "/tmp/concierge_bt.pid",
        "log_file": "bt_presence.log"
    },
    "wifi": {
        "script": "wifi_sentry.py",
        "pid_file": "/tmp/concierge_wifi.pid",
        "log_file": "wifi_sentry.log"
    }
}

def get_pid(name):
    pid_file = SERVICES[name]["pid_file"]
    if os.path.exists(pid_file):
        with open(pid_file, "r") as f:
            try:
                return int(f.read().strip())
            except: return None
    return None

def is_running(name):
    pid = get_pid(name)
    if pid:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    return False

def start_service(name):
    if is_running(name):
        print(f"[-] Sentry-{name} is already running.")
        return

    if name == "wifi":
        # Check if mon0 exists. if not, run setup_wifi.sh
        if not os.path.exists("/sys/class/net/mon0"):
            print("[*] mon0 not found. Executing setup_wifi.sh...")
            setup_script = os.path.join(BASE_DIR, "setup_wifi.sh")
            subprocess.run(["sudo", "bash", setup_script], check=True)

    script = os.path.join(SCRIPTS_DIR, SERVICES[name]["script"])
    log_path = os.path.join(BASE_DIR, SERVICES[name]["log_file"])
    
    print(f"[*] Starting Sentry-{name}...")
    
    # Launch in background
    with open(log_path, "a") as log:
        process = subprocess.Popen(
            [VENV_PYTHON, script],
            stdout=log,
            stderr=log,
            start_new_session=True
        )
        
    with open(SERVICES[name]["pid_file"], "w") as f:
        f.write(str(process.pid))
    
    print(f"[+] Sentry-{name} launched (PID: {process.pid})")

def stop_service(name):
    pid = get_pid(name)
    if not pid or not is_running(name):
        print(f"[-] Sentry-{name} is not running.")
        return

    print(f"[*] Stopping Sentry-{name} (PID: {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        if is_running(name):
            os.kill(pid, signal.SIGKILL)
        os.remove(SERVICES[name]["pid_file"])
        print(f"[+] Sentry-{name} stopped.")
    except Exception as e:
        print(f"[!] Error stopping service: {e}")

def status_report():
    print("\n--- Concierge Sentry Status ---")
    for name in SERVICES:
        running = is_running(name)
        status = "RUNNING" if running else "OFFLINE"
        pid = get_pid(name) if running else "N/A"
        print(f"{name.upper():<5} | Status: {status:<8} | PID: {pid}")
    print("-------------------------------\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        status_report()
        sys.exit(0)

    cmd = sys.argv[1].lower()
    
    if cmd == "status":
        status_report()
    elif cmd in SERVICES:
        action = sys.argv[2].lower() if len(sys.argv) > 2 else "status"
        if action == "start":
            start_service(cmd)
        elif action == "stop":
            stop_service(cmd)
        else:
            print(f"Sentry-{cmd} is {'RUNNING' if is_running(cmd) else 'OFFLINE'}")
    else:
        print("Usage: python3 sentry_control.py <bt|wifi|status> [start|stop]")

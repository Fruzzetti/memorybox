#!/bin/bash
# ConciergeHub Tactical Sudoers Setup (V2 - Fixed Paths)
# Enables passwordless hardware control for the conciergeweb service (user: fuzz)

SUDOERS_FILE="/etc/sudoers.d/concierge-tactical"

echo "[*] Creating $SUDOERS_FILE with absolute paths..."

# Use absolute paths directly to avoid variable expansion issues
cat <<EOF | sudo tee $SUDOERS_FILE > /dev/null
# Concierge Tactical Permissions
fuzz ALL=(ALL) NOPASSWD: /home/fuzz/Projects/conciergeweb/venv/bin/python3 /home/fuzz/Projects/conciergeweb/scripts/bridge_real.py *
fuzz ALL=(ALL) NOPASSWD: /home/fuzz/Projects/conciergeweb/venv/bin/python3 /home/fuzz/Projects/conciergeweb/scripts/sentry_control.py *
fuzz ALL=(ALL) NOPASSWD: /home/fuzz/Projects/conciergeweb/venv/bin/python3 /home/fuzz/Projects/conciergeweb/power_shaker.py
fuzz ALL=(ALL) NOPASSWD: /usr/sbin/reboot
fuzz ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart conciergeweb
fuzz ALL=(ALL) NOPASSWD: /usr/bin/cat /var/lib/misc/dnsmasq.leases
EOF

sudo chmod 0440 $SUDOERS_FILE
echo "[*] Sudoers rule applied. Testing syntax..."
sudo visudo -c

if [ $? -eq 0 ]; then
    echo "[SUCCESS] Tactical permissions hardened."
else
    echo "[ERROR] Sudoers syntax invalid. Please check the output above."
fi

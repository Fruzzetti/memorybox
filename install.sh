#!/bin/bash
# 🛸 MemoryBox Appliance Genesis Installer [v2.1.10]
# Purpose: Zero-touch transformation of a fresh Ubuntu server into a MemoryBox Appliance.

set -e

# --- Configuration ---
APP_USER="concierge"
APP_DIR="/home/$APP_USER/memorybox"
VAULT_MOUNT="/home/$APP_USER/memories"
PORT=8001
HOSTNAME="memorybox.local"

echo "################################################"
echo "# 🛸 MEMORYBOX APPLIANCE GENESIS INSTALLER      #"
echo "################################################"

# 1. Root Check
if [ "$EUID" -ne 0 ]; then
  echo "[!] Please run as root (sudo ./install.sh)"
  exit 1
fi

# 2. System Identity
echo "[*] Setting system identity to $HOSTNAME..."
hostnamectl set-hostname "$HOSTNAME"
grep -q "$HOSTNAME" /etc/hosts || echo "127.0.0.1 $HOSTNAME" >> /etc/hosts

# 2.5 Idempotency: Cleanup stale state before retry
echo "[*] Cleaning up stale state (if any)..."
systemctl stop memorybox || true
umount "$VAULT_MOUNT" || true
cryptsetup luksClose memories || true

# 3. User Onboarding
if id "$APP_USER" &>/dev/null; then
    echo "[*] User $APP_USER already exists."
else
    echo "[*] Creating $APP_USER user..."
    useradd -m -s /bin/bash "$APP_USER"
    # Generate a random 24-character password
    RAND_PASS=$(openssl rand -base64 24)
    echo "$APP_USER:$RAND_PASS" | chpasswd
    echo "[+] Secured $APP_USER with a unique random password."
    usermod -aG sudo,disk "$APP_USER"
fi

# 4. Core Dependencies
echo "[*] Waiting for other package managers to finish (checking for APT locks)..."
while fuser /var/lib/apt/lists/lock >/dev/null 2>&1 ; do
    echo "    [!] APT is locked by another process. Waiting 5 seconds..."
    sleep 5
done

echo "[*] Installing core dependencies (this may take a few minutes)..."
export DEBIAN_FRONTEND=noninteractive
echo "iptables-persistent iptables-persistent/autosave_v4 boolean true" | debconf-set-selections
echo "iptables-persistent iptables-persistent/autosave_v6 boolean true" | debconf-set-selections
apt-get update
apt-get install -y python3-venv python3-pip ffmpeg cryptsetup iptables-persistent curl git nginx avahi-daemon

# 5. Ollama Installation
if ! command -v ollama &> /dev/null; then
    echo "[*] Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

# 6. App Deployment
echo "[*] Deploying MemoryBox logic..."
mkdir -p "$APP_DIR"

# Smart Detection: Look for 'memorybox' in current dir, parent dir, or script's dir
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[*] Current Directory: $(pwd)"

if [ ! -d "./memorybox" ]
then
    echo "[*] Source not found locally. Fetching from GitHub..."
    rm -rf temp_install
    git clone https://github.com/Fruzzetti/memorybox.git temp_install
    cd temp_install || exit 1
fi

if [ -d "./memorybox" ]
then
    SRC_DIR="./memorybox"
elif [ -d "../memorybox" ]; then
    SRC_DIR="../memorybox"
elif [ -d "$SCRIPT_DIR/../memorybox" ]; then
    SRC_DIR="$SCRIPT_DIR/../memorybox"
else
    echo "[!] Error: 'memorybox' source folder not found."
    exit 1
fi

echo "[*] Copying logic from $SRC_DIR to $APP_DIR..."
cp -ar "$SRC_DIR/." "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod -R 755 "$APP_DIR/static"

# 7. Virtual Environment
echo "[*] Establishing Python Virtual Environment..."
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install fastapi uvicorn[standard] jinja2 httpx psutil faster-whisper requests python-multipart pillow pillow-heif aiofiles

# 8. Storage Engine (Vault Provisioning)
echo "------------------------------------------------"
echo "STORAGE PROVISIONING"
lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS | grep -v "loop"
echo ""
echo "1) Use a dedicated block device (e.g. /dev/sdb1)"
echo "2) Create a 20GB Portable Vault File (Recommended for VMs)"
read -p "[?] Select storage mode [1/2]: " VAULT_MODE

mkdir -p "$VAULT_MOUNT"
chown "$APP_USER:$APP_USER" "$VAULT_MOUNT"

if [ "$VAULT_MODE" == "1" ]; then
    while true; do
        read -p "[?] Enter block device path (e.g. /dev/sdc1): " VAULT_DEV
        if [ ! -b "$VAULT_DEV" ]; then
            echo "[!] Error: '$VAULT_DEV' is not a valid block device."
            continue
        fi

        echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        echo "!!! WARNING: DATA DESTRUCTION IMMINENT       !!!"
        echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        echo "The device $VAULT_DEV will be COMPLETELY ERASED"
        echo "to create your MemoryBox private archive."
        
        if mount | grep -q "$VAULT_DEV"; then
            echo "[!] ALERT: This device is currently MOUNTED."
        fi
        echo ""
        read -p "[?] Type YES to continue or NO to go back: " CONFIRM
        if [ "$CONFIRM" == "YES" ]; then
            VAULT_SOURCE="$VAULT_DEV"
            break
        else
            echo "[*] Aborting selection. Returning to storage menu..."
            echo "1) Use a dedicated block device (e.g. /dev/sdb1)"
            echo "2) Create a 20GB Portable Vault File (Recommended for VMs)"
            read -p "[?] Select storage mode [1/2]: " VAULT_MODE
            if [ "$VAULT_MODE" != "1" ]; then
                VAULT_SOURCE="/home/$APP_USER/vault.img"
                if [ ! -f "$VAULT_SOURCE" ]; then
                    echo "[*] Creating 20GB Portable Vault file..."
                    fallocate -l 20G "$VAULT_SOURCE"
                    chown "$APP_USER:$APP_USER" "$VAULT_SOURCE"
                    modprobe loop || true
                fi
                break
            fi
        fi
    done
else
    VAULT_SOURCE="/home/$APP_USER/vault.img"
    if [ ! -f "$VAULT_SOURCE" ]; then
        echo "[*] Creating 20GB Portable Vault file..."
        fallocate -l 20G "$VAULT_SOURCE"
        chown "$APP_USER:$APP_USER" "$VAULT_SOURCE"
        modprobe loop || true
    fi
fi

# 9. Sudoers & Path Hardening
echo "[*] Hardening Sudoers for $APP_USER..."

# Verified Binary Locations
CRYPTSETUP_PATH="/usr/sbin/cryptsetup"
MKFS_PATH="/usr/sbin/mkfs.ext4"
MOUNT_PATH="/usr/bin/mount"
UMOUNT_PATH="/usr/bin/umount"
MKDIR_PATH="/usr/bin/mkdir"
CHOWN_PATH="/usr/bin/chown"
SYSTEMCTL_PATH="/usr/bin/systemctl"
RM_PATH="/usr/bin/rm"

# Fallback if standard paths missing
[ ! -f "$CRYPTSETUP_PATH" ] && CRYPTSETUP_PATH=$(which cryptsetup)
[ ! -f "$MKFS_PATH" ] && MKFS_PATH=$(which mkfs.ext4)

# Verification: Abort if binaries missing (prevents empty paths in main.py)
if [ -z "$CRYPTSETUP_PATH" ] || [ -z "$MKFS_PATH" ]; then
    echo "[!] ERROR: Critical binaries (cryptsetup/mkfs) not found!"
    exit 1
fi

# Update main.py logic
sed -i "s|VAULT_TYPE = .*|VAULT_TYPE = \"LUKS\"|g" "$APP_DIR/main.py"
sed -i "s|VAULT_SOURCE = .*|VAULT_SOURCE = \"$VAULT_SOURCE\"|g" "$APP_DIR/main.py"
sed -i "s|VAULT_DEVICE = .*|VAULT_DEVICE = \"/dev/mapper/memories\"|g" "$APP_DIR/main.py"
sed -i "s|port=[0-9]*|port=$PORT|g" "$APP_DIR/main.py"

# Inject paths into main.py for strict sudoers matching
sed -i "s|/usr/sbin/cryptsetup|$CRYPTSETUP_PATH|g" "$APP_DIR/main.py"
sed -i "s|/usr/sbin/mkfs.ext4|$MKFS_PATH|g" "$APP_DIR/main.py"
sed -i "s|/usr/bin/mount|$MOUNT_PATH|g" "$APP_DIR/main.py"
sed -i "s|/usr/bin/umount|$UMOUNT_PATH|g" "$APP_DIR/main.py"

SUDOERS_FILE="/etc/sudoers.d/memorybox-appliance"
tee "$SUDOERS_FILE" <<EOF
Defaults:$APP_USER !requiretty
$APP_USER ALL=(ALL) NOPASSWD: $CRYPTSETUP_PATH *, $MKFS_PATH *, $MOUNT_PATH *, $UMOUNT_PATH *, $MKDIR_PATH *, $CHOWN_PATH *, $RM_PATH *, $SYSTEMCTL_PATH *
EOF
chmod 0440 "$SUDOERS_FILE"

# 10. Nginx Gateway Configuration
echo "[*] Configuring Nginx Gateway..."
chmod 755 "/home/$APP_USER"
chmod 644 "$APP_DIR/nginx_memorybox.conf"

NGINX_CONF="/etc/nginx/sites-available/memorybox"
cat > "$NGINX_CONF" <<EOF
server {
    listen 80;
    server_name $HOSTNAME;
    include $APP_DIR/nginx_memorybox.conf;
    location / { return 301 /memorybox/; }
}
EOF
ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/memorybox
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

# 11. mDNS Discovery (Avahi)
systemctl enable avahi-daemon
systemctl restart avahi-daemon

# 12. Systemd Service
echo "[*] Installing Systemd Service..."
tee /etc/systemd/system/memorybox.service <<EOF
[Unit]
Description=MemoryBox Appliance Service
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python3 main.py
Restart=always
RestartSec=5
StandardOutput=append:$APP_DIR/memorybox.log
StandardError=append:$APP_DIR/memorybox.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable memorybox
systemctl restart memorybox

# 13. Service Health Check
echo "[*] Waiting for MemoryBox service to bind to Port $PORT..."
for i in {1..15}; do
    if curl -s "http://127.0.0.1:$PORT/api/vault/status" > /dev/null; then
        echo "    [+] Service is LIVE and responding."
        HEALTHY=true
        break
    fi
    echo "    [!] Service not responding yet... (Attempt $i/15)"
    sleep 3
done

if [ "$HEALTHY" != "true" ]; then
    echo "[!] ERROR: Service failed to respond within 45 seconds."
    echo "------------------------------------------------"
    echo "DIAGNOSTIC DUMP: journalctl logs"
    journalctl -u memorybox --no-pager -n 50
    exit 1
fi

# 14. Model Warm-up (Background)
echo "[*] Waking AI Engines..."
ollama pull mistral &
ollama pull moondream &

echo "################################################"
echo "# 🚀 INSTALLATION COMPLETE                     #"
echo "################################################"
echo "Access the portal at: http://$HOSTNAME/memorybox/"
echo "Vault Source: $VAULT_SOURCE"
echo "################################################"

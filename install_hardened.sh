#!/bin/bash
# 🛸 MemoryBox Appliance Genesis Installer [v2.2.19]
# Purpose: Zero-touch transformation of a fresh Ubuntu server into a MemoryBox Appliance.
# Hardening: LVM/LUKS automation, Iron Curtain protocol, and AI Engine Patience Loop.

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
    echo "[*] User $APP_USER already exists. Hardening existing account..."
    usermod -aG sudo,disk "$APP_USER" || true
else
    echo "[*] Creating $APP_USER user..."
    useradd -m -s /bin/bash "$APP_USER"
    # Generate a random 24-character password
    RAND_PASS=$(openssl rand -base64 24)
    echo "$APP_USER:$RAND_PASS" | chpasswd
    echo "[+] Secured $APP_USER with a unique random password."
    usermod -aG sudo,disk "$APP_USER"
fi

# 4. Binary Path Discovery
echo "[*] Discovering system binaries..."
CRYPTSETUP_PATH=$(which cryptsetup || echo "/usr/sbin/cryptsetup")
MKFS_PATH=$(which mkfs.ext4 || echo "/usr/sbin/mkfs.ext4")
MOUNT_PATH=$(which mount || echo "/usr/bin/mount")
UMOUNT_PATH=$(which umount || echo "/usr/bin/umount")
MKDIR_PATH=$(which mkdir || echo "/usr/bin/mkdir")
CHOWN_PATH=$(which chown || echo "/usr/bin/chown")
SYSTEMCTL_PATH=$(which systemctl || echo "/usr/bin/systemctl")
RM_PATH=$(which rm || echo "/usr/bin/rm")
FALLOCATE_PATH=$(which fallocate || echo "/usr/bin/fallocate")
VGS_PATH=$(which vgs || echo "/usr/sbin/vgs")
LVS_PATH=$(which lvs || echo "/usr/sbin/lvs")
LVCREATE_PATH=$(which lvcreate || echo "/usr/sbin/lvcreate")

# 5. Core Dependencies
echo "[*] Waiting for other package managers to finish (checking for APT locks)..."
while fuser /var/lib/apt/lists/lock >/dev/null 2>&1 ; do
    echo "    [!] APT is locked by another process. Waiting 5 seconds..."
    sleep 5
done

echo "[*] Installing core dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv python3-pip ffmpeg cryptsetup curl git nginx avahi-daemon lvm2

# 6. Storage Provisioning (LVM/LUKS)
echo "------------------------------------------------"
echo "STORAGE PROVISIONING"
echo "------------------------------------------------"

VAULT_SOURCE=""

# Check for LVM Group (Standard Beelink Ubuntu Install)
if [ -x "$VGS_PATH" ]; then
    VG_NAME=$($VGS_PATH --noheadings -o vg_name | xargs | grep -o "ubuntu-vg" || echo "")
    if [ -n "$VG_NAME" ]; then
        echo "[+] LVM Volume Group '$VG_NAME' detected."
        if $LVS_PATH "$VG_NAME/private" &>/dev/null; then
            echo "[*] Existing 'private' Logical Volume found."
            VAULT_SOURCE="/dev/$VG_NAME/private"
        else
            echo "[?] Would you like to create a 40GB 'private' LV for the vault?"
            read -p "[?] Create LVM volume? (y/n): " DO_LVM < /dev/tty
            if [ "$DO_LVM" == "y" ]; then
                echo "[*] Creating 40GB Logical Volume 'private' in $VG_NAME..."
                $LVCREATE_PATH -L 40G -n private "$VG_NAME"
                VAULT_SOURCE="/dev/$VG_NAME/private"
            fi
        fi
    fi
fi

if [ -z "$VAULT_SOURCE" ]; then
    lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS | grep -v "loop"
    echo ""
    echo "1) Use a dedicated block device (e.g. /dev/sdb1)"
    echo "2) Create a 20GB Portable Vault File (Recommended for VMs)"
    read -p "[?] Select storage mode [1/2]: " VAULT_MODE < /dev/tty
    
    if [ "$VAULT_MODE" == "1" ]; then
        read -p "[?] Enter block device path (e.g. /dev/sdc1): " VAULT_DEV < /dev/tty
        VAULT_SOURCE="$VAULT_DEV"
    else
        VAULT_SOURCE="/home/$APP_USER/vault.img"
        if [ ! -f "$VAULT_SOURCE" ]; then
            echo "[*] Creating 20GB Portable Vault file..."
            $FALLOCATE_PATH -l 20G "$VAULT_SOURCE"
            $CHOWN_PATH "$APP_USER:$APP_USER" "$VAULT_SOURCE"
            modprobe loop || true
        fi
    fi
fi

# [v1.8.12] Iron Curtain Protocol: Perform initial format and lock
echo "[*] Initializing LUKS Vault (Passphrase Required)..."
if ! cryptsetup isLuks "$VAULT_SOURCE"; then
    echo "[!] Formatting new encrypted volume. DO NOT FORGET THIS PASSPHRASE."
    cryptsetup luksFormat "$VAULT_SOURCE" < /dev/tty
fi

echo "[*] Verifying Vault Integrity..."
cryptsetup luksOpen "$VAULT_SOURCE" memories < /dev/tty
if [ ! -b "/dev/mapper/memories" ]; then
    echo "[!] Encryption failed to map. Aborting."
    exit 1
fi

if ! blkid /dev/mapper/memories | grep -q "ext4"; then
    echo "[*] Creating filesystem (ext4)..."
    $MKFS_PATH /dev/mapper/memories
fi

mkdir -p "$VAULT_MOUNT"
$MOUNT_PATH /dev/mapper/memories "$VAULT_MOUNT"
mkdir -p "$VAULT_MOUNT/thumbnails"
mkdir -p "$VAULT_MOUNT/proxies"
$CHOWN_PATH -R "$APP_USER:$APP_USER" "$VAULT_MOUNT"
$UMOUNT_PATH "$VAULT_MOUNT"

echo "[*] SLAMMING THE VAULT SHUT. Continuing install offline from the vault..."
cryptsetup luksClose memories
echo "[+] Vault Locked. Initial security established."
echo "------------------------------------------------"

# 7. App Deployment
echo "[*] Deploying MemoryBox logic..."
mkdir -p "$APP_DIR"

if [ -f "./memorybox.tgz" ]; then
    echo "[*] memorybox.tgz detected. Unpacking payload..."
    tar -xzf ./memorybox.tgz -C "$APP_DIR" --strip-components=1
elif [ -d "./memorybox" ]; then
    cp -ar "./memorybox/." "$APP_DIR/"
else
    echo "[*] Autonomous payload retrieval..."
    curl -L https://github.com/Fruzzetti/memorybox/raw/main/memorybox.tgz | tar -xz -C "$APP_DIR" --strip-components=1
fi

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod -R 755 "$APP_DIR/static"

# 8. Virtual Environment
echo "[*] Establishing Python Virtual Environment..."
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
if [ -f "$APP_DIR/requirements.txt" ]; then
    sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"
else
    sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install fastapi uvicorn[standard] jinja2 httpx psutil faster-whisper requests python-multipart pillow pillow-heif aiofiles cryptography
fi

# 9. Configuration Injection
echo "[*] Configuring app logic..."
sed -i "s|VAULT_TYPE = .*|VAULT_TYPE = \"LUKS\"|g" "$APP_DIR/main.py"
sed -i "s|VAULT_SOURCE = .*|VAULT_SOURCE = \"$VAULT_SOURCE\"|g" "$APP_DIR/main.py"
sed -i "s|VAULT_DEVICE = .*|VAULT_DEVICE = \"/dev/mapper/memories\"|g" "$APP_DIR/main.py"
sed -i "s|/usr/sbin/cryptsetup|$CRYPTSETUP_PATH|g" "$APP_DIR/main.py"
sed -i "s|/usr/sbin/mkfs.ext4|$MKFS_PATH|g" "$APP_DIR/main.py"
sed -i "s|/usr/bin/mount|$MOUNT_PATH|g" "$APP_DIR/main.py"
sed -i "s|/usr/bin/umount|$UMOUNT_PATH|g" "$APP_DIR/main.py"

# Sudoers hardening
SUDOERS_FILE="/etc/sudoers.d/memorybox-appliance"
tee "$SUDOERS_FILE" <<EOF
Defaults:$APP_USER !requiretty
$APP_USER ALL=(ALL) NOPASSWD: $CRYPTSETUP_PATH *, $MKFS_PATH *, $MOUNT_PATH *, $UMOUNT_PATH *, $MKDIR_PATH *, $CHOWN_PATH *, $RM_PATH *, $SYSTEMCTL_PATH *
EOF
chmod 0440 "$SUDOERS_FILE"

# 10. Nginx Gateway (Production Hardened)
echo "[*] Configuring Nginx Gateway..."
chmod 755 "/home/$APP_USER"
NGINX_CONF="/etc/nginx/sites-available/memorybox"
cat > "$NGINX_CONF" <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    location / {
        return 301 /memorybox/;
    }

    location /memorybox/static/ {
        alias /home/concierge/memorybox/static/;
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }

    location /memorybox/ {
        proxy_pass http://127.0.0.1:8001/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # [v2.1.9] Archival Capacity Hardening
        client_max_body_size 50G;
        proxy_read_timeout 3600;
        proxy_connect_timeout 3600;
        proxy_send_timeout 3600;
        
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF
ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/memorybox
rm -f /etc/nginx/sites-enabled/default
systemctl restart nginx

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

# 13. AI Engine Patience Loop (Mistral, Moondream, Whisper)
if ! command -v ollama &> /dev/null; then
    echo "[*] Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

echo "[*] Waiting for AI Engine to initialize..."
for i in {1..15}; do
    if curl -s http://127.0.0.1:11434/api/tags > /dev/null; then
        echo "    [+] AI Engine is awake."
        break
    fi
    echo "    [!] AI Engine warming up... (Attempt $i/15)"
    sleep 3
done

echo "[*] Pre-loading models (this may take a while)..."
ollama pull mistral
ollama pull moondream

echo "[*] Waking Whisper (Local Sensing)..."
sudo -u "$APP_USER" "$APP_DIR/venv/bin/python3" -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"

# 14. Service Health Check
echo "[*] Finalizing Appliance Status..."
for i in {1..15}; do
    if curl -s "http://127.0.0.1:$PORT/api/vault/status" > /dev/null; then
        echo "    [+] MemoryBox is LIVE."
        HEALTHY=true
        break
    fi
    echo "    [!] Waiting for service... (Attempt $i/15)"
    sleep 3
done

echo "################################################"
echo "# 🚀 INSTALLATION COMPLETE                     #"
echo "################################################"
echo "Access: http://$HOSTNAME/memorybox/"
echo "Vault Source: $VAULT_SOURCE"
echo "################################################"

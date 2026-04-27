#!/bin/bash
# 🛸 MemoryBox Appliance Bootstrap Installer [v1.2.0]
# Hosted at github.com/Fruzzetti/memorybox

set -e

VERSION="1.8"
URL="https://raw.githubusercontent.com/Fruzzetti/memorybox/main/memorybox_v$VERSION.tgz"
TMP_DIR=$(mktemp -d)

echo "################################################"
echo "# 🛸 MEMORYBOX APPLIANCE BOOTSTRAP             #"
echo "################################################"
echo "[*] Preparing environment..."

# Ensure wget and tar are present
if ! command -v wget &> /dev/null; then
    echo "[*] Installing wget..."
    apt-get update -y && apt-get install -y wget
fi

echo "[*] Downloading MemoryBox v$VERSION from dfracknstack.com..."
if ! wget -q "$URL" -O "$TMP_DIR/bundle.tgz"; then
    echo "[!] Error: Failed to download the bundle. Is the URL correct?"
    echo "URL: $URL"
    exit 1
fi

echo "[*] Extracting bundle..."
tar -xzf "$TMP_DIR/bundle.tgz" -C "$TMP_DIR"

# Identify the bundle directory (Flexible detection)
BUNDLE_DIR=$(find "$TMP_DIR" -maxdepth 1 -type d -name "MemoryBox*" | head -n 1)

if [ ! -d "$BUNDLE_DIR" ]; then
    echo "[!] Error: Bundle directory starting with 'MemoryBox' not found after extraction."
    ls -la "$TMP_DIR"
    exit 1
fi

cd "$BUNDLE_DIR"

echo "[*] Launching Genesis Installer..."
echo "------------------------------------------------"
chmod +x ./install_memorybox_appliance.sh
./install_memorybox_appliance.sh

# Cleanup
echo "[*] Cleaning up temporary files..."
rm -rf "$TMP_DIR"

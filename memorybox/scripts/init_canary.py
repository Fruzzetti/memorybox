import os
import hashlib
import base64
from cryptography.fernet import Fernet

# [v1.2.0] Master Appliance Key for Synchronizing the Canary
MASTER_KEY = "Iron-6#3-Eagle"
CANARY_PATH = "/home/concierge/memories/.vault_canary"

def initialize_canary():
    print(f"[*] Synchronizing Vault Canary with key: {MASTER_KEY}")
    
    try:
        # 1. Derive a 32-byte Fernet-compatible key from the Master Key
        key_hash = hashlib.sha256(MASTER_KEY.encode()).digest()
        fernet_key = base64.urlsafe_b64encode(key_hash)
        f = Fernet(fernet_key)
        
        # 2. Encrypt the activation signature
        # This matches the verify_vault_key logic in main.py
        signature = f.encrypt(b"VAULT_ACTIVE").decode()
        
        # 3. Ensure target directory exists (though /home/concierge/memories should exist)
        os.makedirs(os.path.dirname(CANARY_PATH), exist_ok=True)
        
        # 4. Write the signature to the vault partition
        with open(CANARY_PATH, "w") as canary_file:
            canary_file.write(signature)
        
        print(f"[+] Canary synchronized at {CANARY_PATH}")
        print("[!] Access Handshake Restored. Please refresh your browser.")
    except Exception as e:
        print(f"[!] Critical Error during synchronization: {e}")

if __name__ == "__main__":
    initialize_canary()

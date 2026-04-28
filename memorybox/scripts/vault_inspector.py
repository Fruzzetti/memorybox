import sqlite3
import os
import hashlib
import base64
from cryptography.fernet import Fernet

# --- Hub Logic Replication ---
DB_PATH = "/home/concierge/memories/personal_memory.db"
# MASTER_KEY should be passed as an environment variable for security
MASTER_KEY = os.environ.get("MEMORYBOX_MASTER_KEY", "Iron-6#3-Eagle")

def get_fernet():
    if not MASTER_KEY: return None
    key_hash = hashlib.sha256(MASTER_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key_hash))

def decrypt_content(content: str):
    f = get_fernet()
    if not f or not content: return content
    try:
        return f.decrypt(content.encode()).decode()
    except:
        return "[Decryption Failed: Invalid Key or Corrupt Data]"

def inspect():
    print(f"[*] MemoryBox Vault Inspector: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print("[!] ERROR: Database not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    print("\n--- Decrypted Memory Registry ---")
    c.execute("SELECT id, img_path, description FROM image_cache")
    rows = c.fetchall()
    
    if not rows:
        print("  (Vault is currently empty)")
    
    for r in rows:
        idx, path, cipher = r
        plain = decrypt_content(cipher)
        fname = os.path.basename(path)
        print(f"[{idx}] {fname}:")
        print(f"    {plain}\n")

    conn.close()
    print("[*] Inspection Complete.")

if __name__ == "__main__":
    inspect()

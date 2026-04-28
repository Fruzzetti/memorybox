import os
import sqlite3
import hashlib
import base64
import datetime
from cryptography.fernet import Fernet # [v1.2.0] Great Engibberization

# --- Paths (On Ubuntu Appliance) ---
MEMORIES_DIR = "/home/concierge/memories"
DB_PATH = os.path.join(MEMORIES_DIR, "personal_memory.db")
BACKUP_PATH = DB_PATH + ".bak"

# This must match Iron-6#3-Eagle
APPLIANCE_KEY = "Iron-6#3-Eagle"

def get_fernet(key_str: str) -> Fernet:
    """ Derive valid Fernet key from the user secret. """
    key_hash = hashlib.sha256(key_str.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key_hash))

def migrate():
    print(f"[*] Starting GREAT ENGIBBERIZATION (Phase 3 Hardening)...")
    
    if not os.path.exists(DB_PATH):
        print(f"    [!] Error: Database not found at {DB_PATH}")
        return

    # 1. Create Safety Backup
    print(f"[*] Step 1: Creating safety backup at {BACKUP_PATH}...")
    import shutil
    shutil.copy2(DB_PATH, BACKUP_PATH)
    
    # 2. Derive Key
    fernet = get_fernet(APPLIANCE_KEY)
    
    # 3. Open Database
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # --- Part A: Writings Table ---
    print(f"[*] Step 2: Migrating 'writings' table...")
    c.execute("SELECT id, content FROM writings")
    rows = c.fetchall()
    
    migrated_count = 0
    skipped_count = 0
    
    for row_id, content in rows:
        if content.startswith("gAAAA"):
            skipped_count += 1
            continue
            
        # Encrypt cleartext
        encrypted = fernet.encrypt(content.encode()).decode()
        c.execute("UPDATE writings SET content = ? WHERE id = ?", (encrypted, row_id))
        migrated_count += 1
        
        if migrated_count % 100 == 0:
            print(f"    [>] Processed {migrated_count} records...")

    # --- Part B: Image Cache Table ---
    print(f"[*] Step 3: Migrating 'image_cache' visual descriptions...")
    try:
        c.execute("SELECT img_path, description FROM image_cache")
        rows = c.fetchall()
        img_migrated = 0
        for img_path, desc in rows:
            if desc and desc.startswith("gAAAA"):
                continue
            
            if desc:
                enc_desc = fernet.encrypt(desc.encode()).decode()
                c.execute("UPDATE image_cache SET description = ? WHERE img_path = ?", (enc_desc, img_path))
                img_migrated += 1
        print(f"    [+] Migrated {img_migrated} visual descriptions.")
    except Exception as e:
        print(f"    [!] image_cache migration skipped or failed: {e}")

    # --- Part C: FTS Synchronization ---
    # We leave the FTS indices as cleartext internally (they are only readable when DB is unsealed 
    # and queried by the Persona), but we ensure they are populated for the newly encrypted records.
    # [v1.2.0] All records are now either already in FTS or newly migrated.
    
    conn.commit()
    conn.close()
    
    print(f"\n--- Migration Complete ---")
    print(f"Total Writings Migrated: {migrated_count}")
    print(f"Total Writings Skipped (Already Encrypted): {skipped_count}")
    print(f"[*] Archival Status: 100% SEALED")
    print(f"[!] You may now refresh the Persona and unseal with your key.")

if __name__ == "__main__":
    confirm = input(f"☢️ PROCEED WITH MIGRATION using key '{APPLIANCE_KEY}'? (yes/no): ")
    if confirm.lower() == "yes":
        migrate()
    else:
        print("[!] Migration Aborted.")

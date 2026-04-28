import sys
import os
import sqlite3
import datetime
from cryptography.fernet import Fernet

# --- Paths (relative to A:\) ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORIES_DIR = "b:\\" # The target mount
DB_PATH = os.path.join(MEMORIES_DIR, "personal_memory.db")
CANARY_PATH = os.path.join(MEMORIES_DIR, ".vault_canary")

def test_cycle(key_str: str):
    print(f"[*] Starting Key Cycle Test with Appliance Key: {key_str}")
    
    # 1. Initialize Canary SIGNATURE
    f = Fernet(key_str.encode())
    signature = f.encrypt(b"VAULT_ACTIVE").decode()
    with open(CANARY_PATH, "w") as f_canary:
        f_canary.write(signature)
    print(f"    [+] Vault Canary created at {CANARY_PATH}")

    # 2. Setup Test Data
    test_root = os.path.join(MEMORIES_DIR, "Archive", "VerificationTest")
    os.makedirs(test_root, exist_ok=True)
    
    file1 = os.path.join(test_root, "test_secret.txt")
    with open(file1, "w") as f_test:
        f_test.write("CONFIDENTIAL: The vault unseal cycle is working. Reference Alpha-9.")
    
    print(f"    [+] Test data staged at {test_root}")

    # 3. Call Ingestion Script via Environment
    print("[*] Calling ingest_writings.py...")
    # Add key to environment
    os.environ["MEMORYBOX_MASTER_KEY"] = key_str
    
    # Run the ingestion script
    import subprocess
    cmd = ["python", os.path.join(BASE_DIR, "scripts", "ingest_writings.py"), test_root]
    result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ.copy())
    
    print("      --- Ingestion Output ---")
    print(result.stdout)
    if result.stderr: print(result.stderr)
    
    # 4. Verify Database at Rest
    print("[*] Auditing Database at Rest...")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT content FROM writings WHERE source_file LIKE '%test_secret.txt' LIMIT 1")
    row = c.fetchone()
    conn.close()
    
    if row:
        stored_content = row[0]
        print(f"    [+] Found Entry!")
        print(f"    [+] Raw Stored Data (Terminal View): {stored_content[:64]}...")
        
        if stored_content.startswith("gAAAAA"):
            print("    [!] CONFIRMED: Data is Fernet-encrypted at rest.")
        else:
            print("    [!] FAILED: Data is in cleartext.")
    else:
        print("    [!] FAILED: Record not found in DB.")

if __name__ == "__main__":
    test_key = "MEMORY-BOX-ALPHA-TEST-2026"
    test_cycle(test_key)

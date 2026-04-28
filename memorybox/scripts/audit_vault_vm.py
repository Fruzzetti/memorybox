import sqlite3
import os

DB_PATH = "/home/concierge/memories/personal_memory.db"

def audit():
    print(f"[*] Auditing Memory Vault: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print("[!] ERROR: Database not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 1. Image Cache Count
    c.execute("SELECT count(*) FROM image_cache")
    img_count = c.fetchone()[0]
    print(f"[+] Total Images in Cache: {img_count}")

    # 2. FTS Index Count
    c.execute("SELECT count(*) FROM image_cache_fts")
    fts_count = c.fetchone()[0]
    print(f"[+] Total FTS Indexed Records: {fts_count}")

    # 3. List 5 Images
    print("\n--- Image Registry (Last 5) ---")
    c.execute("SELECT img_path, description FROM image_cache LIMIT 5")
    for r in c.fetchall():
        path, desc = r
        # Truncate description for readability
        d_short = (desc[:75] + '...') if len(desc) > 75 else desc
        print(f"  - {os.path.basename(path)}: {d_short}")

    conn.close()
    print("\n[*] Audit Complete.")

if __name__ == "__main__":
    audit()

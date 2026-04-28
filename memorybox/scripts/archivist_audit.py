import sqlite3
import os

DB_PATH = "/home/concierge/memories/personal_memory.db"

def run_audit():
    print("--- MEMORYBOX ARCHIVAL AUDIT ---")
    if not os.path.exists(DB_PATH):
        print(f"[!] ERROR: Database not found at {DB_PATH}")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # 1. Table Census
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [t[0] for t in c.fetchall()]
        print(f"Tables Found: {', '.join(tables)}")

        # 2. image_cache Schema
        print("\n--- image_cache Columns ---")
        c.execute("PRAGMA table_info(image_cache)")
        cols = c.fetchall()
        for col in cols:
            print(f"Prop: {col[1]} | Type: {col[2]} | Default: {col[4]}")

        # 3. Specimen Audit
        print("\n--- First 5 Specimens ---")
        c.execute("SELECT id, owner_id, visibility, revision_count, img_path FROM image_cache LIMIT 5")
        rows = c.fetchall()
        for r in rows:
            print(f"ID:{r[0]} | Owner:{r[1]} | Vis:{r[2]} | Rev:{r[3]} | Path:{r[4]}")

        # 4. Revision Distribution
        print("\n--- Revision Distribution ---")
        c.execute("SELECT revision_count, COUNT(*) FROM image_cache GROUP BY revision_count")
        dist = c.fetchall()
        for d in dist:
            print(f"Revision {d[0]}: {d[1]} items")

        # 5. Profile Check
        print("\n--- Active Profiles ---")
        c.execute("SELECT id, name, role FROM profiles")
        profiles = c.fetchall()
        for p in profiles:
            print(f"ID:{p[0]} | Name:{p[1]} | Role:{p[2]}")

        conn.close()
        print("\n--- AUDIT COMPLETE ---")
    except Exception as e:
        print(f"[!] CRITICAL AUDIT FAILURE: {e}")

if __name__ == "__main__":
    run_audit()

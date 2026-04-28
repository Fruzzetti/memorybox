#!/usr/bin/env python3
import os
import tarfile
import datetime
import shutil
import argparse
import sys

# Configuration
WIKI_DIR = "/home/fuzz/concierge_wiki" if os.path.isdir("/home/fuzz/concierge_wiki") else "/home/fuzz/Projects/CCTVSee/research_wiki"
BACKUP_BASE_DIR = "/home/fuzz/Projects/Backups"
WIKI_ALLOWLIST = [
    "directives/personality.md", 
    "directives/preferences.md", 
    "directives/knowledge_graph.md", 
    "references/websites.md", 
    "concierge_logic.md",
    "directives/concierge_logic.md",
    "current_conversation.md"
]

def ensure_dirs():
    if not os.path.exists(BACKUP_BASE_DIR):
        print(f"Creating backup directory: {BACKUP_BASE_DIR}")
        os.makedirs(BACKUP_BASE_DIR, exist_ok=True)

def perform_backup(is_full=False):
    now = datetime.datetime.now()
    tag = "full" if is_full else "daily"
    filename = f"concierge_brain_{tag}_{now.strftime('%Y-%m-%d')}.tgz"
    target_path = os.path.join(BACKUP_BASE_DIR, filename)

    print(f"Starting {tag} backup to {target_path}...")
    
    try:
        with tarfile.open(target_path, "w:gz") as tar:
            if is_full:
                # Full backup of the entire wiki directory
                tar.add(WIKI_DIR, arcname=os.path.basename(WIKI_DIR))
            else:
                # Selective backup of allowlisted files
                for rel_path in WIKI_ALLOWLIST:
                    full_path = os.path.join(WIKI_DIR, rel_path)
                    if os.path.exists(full_path):
                        tar.add(full_path, arcname=rel_path)
                    else:
                        print(f"Warning: Allowlisted file not found: {rel_path}")
        
        print(f"Backup successful: {filename}")
        return True
    except Exception as e:
        print(f"Backup failed: {e}")
        return False

def rotate_backups():
    print("Checking for expired backups...")
    now = datetime.datetime.now()
    
    # 365 days for daily, 52 weeks (364 days) for weekly
    retention_days = 365
    
    count = 0
    for f in os.listdir(BACKUP_BASE_DIR):
        if not f.endswith(".tgz"): continue
        if "concierge_brain_" not in f: continue
        
        path = os.path.join(BACKUP_BASE_DIR, f)
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        age = (now - mtime).days
        
        if age > retention_days:
            print(f"Deleting expired backup: {f} (Age: {age} days)")
            try:
                os.remove(path)
                count += 1
            except Exception as e:
                print(f"Failed to delete {f}: {e}")
                
    print(f"Rotation complete. {count} files removed.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Concierge Wiki Backup System")
    parser.add_argument("--type", choices=["daily", "weekly"], default="daily", help="Type of backup to perform")
    args = parser.parse_args()

    ensure_dirs()
    if perform_backup(is_full=(args.type == "weekly")):
        rotate_backups()
    else:
        sys.exit(1)

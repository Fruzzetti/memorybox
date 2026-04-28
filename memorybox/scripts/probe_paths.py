import os
import re
import requests
import html

# --- CONFIG ---
TARGET_HTML = "/home/fuzz/Writings/fb/your_facebook_activity/posts/your_photos.html"
ARCHIVE_ROOT = "/home/fuzz/Writings"
OLLAMA_URL = "http://127.0.0.1:11434/api/tags"

def probe():
    print(f"--- PATH PROBE STARTING ---")
    
    # 1. Check Ollama
    print(f"[*] Checking Ollama connection at {OLLAMA_URL}...")
    try:
        resp = requests.get(OLLAMA_URL, timeout=3)
        if resp.status_code == 200:
            print(f"    [OK] Connected to Ollama. Models: {[m['name'] for m in resp.json().get('models', [])[:3]]}...")
        else:
            print(f"    [!] Error: Received status {resp.status_code}")
    except Exception as e:
        print(f"    [FAIL] Could not connect to Ollama. Ensure it's running on the server.")

    # 2. Check HTML File
    target_html = TARGET_HTML
    if not os.path.exists(target_html):
        # Try common Facebook export locations
        candidates = [
            os.path.join(ARCHIVE_ROOT, "your_facebook_activity/posts/your_photos.html"),
            os.path.join(ARCHIVE_ROOT, "fb/your_facebook_activity/posts/your_photos.html"),
        ]
        for cand in candidates:
            if os.path.exists(cand):
                target_html = cand
                break
        
        if not os.path.exists(target_html):
            print(f"    [FAIL] Target HTML not found. Scanned: {TARGET_HTML} and archive subfolders.")
            return

    print(f"[*] Analyzing {target_html}...")
    with open(target_html, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    # 3. Find Image Links
    found_links = re.findall(r'<img[^>]+src="([^"]+)"', content)
    print(f"    - Found {len(found_links)} image links in {os.path.basename(target_html)}.")

    # 4. Try Resolution Logic
    base_dir = os.path.dirname(target_html)
    found_on_disk = 0

    for link in found_links[:10]: # Check first 10 for speed
        img_name = os.path.basename(link)
        print(f"\n    [?] Searching for: {img_name}")
        
        # Candidate 1: Local
        p1 = os.path.normpath(os.path.join(base_dir, link))
        # Candidate 2: Global Root
        p2 = os.path.normpath(os.path.join(ARCHIVE_ROOT, link))
        # Candidate 3: Subfolder fix (Stripping 'fb/' or similar if root is Writings)
        p3 = os.path.normpath(os.path.join(ARCHIVE_ROOT, "fb", link))
        
        match = None
        for p in [p1, p2, p3]:
            if os.path.exists(p):
                match = p
                break
        
        if match:
            print(f"      [MATCH] Found at: {match}")
            found_on_disk += 1
        else:
            print(f"      [MISS] Could not locate file on disk.")

    print(f"\n--- PROBE SUMMARY ---")
    print(f"Status: {'READY' if found_on_disk > 0 else 'NOT READY'}")
    print(f"Images Found: {found_on_disk} out of {min(10, len(found_links))} tested.")
    print(f"----------------------")

if __name__ == "__main__":
    probe()

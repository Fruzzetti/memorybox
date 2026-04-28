#!/usr/bin/env python3
import os
import json
import sqlite3
import datetime
import requests
import base64
import time
import re
import zipfile
import shutil
import subprocess
import base64
import hashlib
import argparse
from typing import List, Dict, Optional

# v1.2.0: AI Engine Configuration (Meteor Lake Ready)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "127.0.0.1")
OLLAMA_API_URL = f"http://{OLLAMA_HOST}:11434/api/generate"
VISION_MODEL = "moondream"
SKIP_VISION = False 

# Images to ignore (UI icons, etc)
IGNORE_IMAGES = ["google_logo.png", "chat_icon.webp", "avatar_placeholder.png"]

# Optional dependencies logic
try: import docx
except ImportError: docx = None

try: import pypdf
except ImportError: pypdf = None

try: import pypdfium2 as pdfium
except ImportError: pdfium = None

try: 
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError: pass

try: import whisper
except ImportError:
    whisper = None
    print("[!] Warning: 'whisper' module not found. Audio transcription will be skipped.")
    print("    If running manually, make sure to use the virtual environment:")
    print("    ~/Projects/conciergeweb/venv/bin/python3 scripts/ingest_writings.py")

# --- Ingestion Framework ---

class DocumentHandler:
    def can_handle(self, filename: str) -> bool:
        return False
    def parse(self, filepath: str) -> List[Dict]:
        return []

# --- Google Chat JSON Handler ---

class GoogleChatHandler(DocumentHandler):
    """
    Handles Google Takeout Chat exports (messages.json).
    [v1.0.1] Participant-aware and chunk-safe.
    """
    def can_handle(self, filename: str) -> bool:
        return filename.lower() == "messages.json"

    def parse(self, filepath: str) -> List[Dict]:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
            
            messages = data.get("messages", [])
            entries = {}
            
            for msg in messages:
                ts = msg.get("created_date", "")
                if not ts: continue
                date_key = ts.split("T")[0]
                
                if date_key not in entries:
                    entries[date_key] = {
                        "text": [], 
                        "authors": set(), 
                        "recipients": set(),
                        "ts": ts
                    }
                
                sender = msg.get("creator", {}).get("name", "Unknown")
                text = msg.get("text", "")
                
                # Metadata Extraction
                entries[date_key]["authors"].add(sender)
                
                time_str = ts.split("T")[1].split(".")[0] if "T" in ts else ts
                line = f"[{time_str}] {sender}: {text}" if text else f"[{time_str}] {sender} (media/attachment)"
                entries[date_key]["text"].append(line)
                
            return [
                {
                    "content": "\n".join(v["text"]),
                    "timestamp": v["ts"],
                    "author": ", ".join(v["authors"]),
                    "doc_type": "google_chat"
                } for v in entries.values()
            ]
        except Exception as e:
            print(f"Error parsing Google Chat {filepath}: {e}")
            return []

# --- Hangouts JSON Handler ---

class HangoutsHandler(DocumentHandler):
    """
    Handler for older Google Hangouts exports (Hangouts.json).
    """
    def can_handle(self, filename: str) -> bool:
        return filename.lower() == "hangouts.json"

    def parse(self, filepath: str) -> List[Dict]:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
        except Exception as e:
            print(f"    [!] Failed to parse Hangouts JSON: {e}")
            return []

        all_entries = []
        conversations = data.get("conversations", [])
        
        for conv in conversations:
            participants = {}
            participant_data = conv.get("conversation", {}).get("participant_data", [])
            for p in participant_data:
                gaia_id = p.get("id", {}).get("gaia_id")
                name = p.get("fallback_name", "Unknown")
                if gaia_id:
                    participants[gaia_id] = name

            daily_groups = {}
            for event in conv.get("events", []):
                if "chat_message" not in event: continue
                
                ts_mcs = int(event.get("timestamp", "0"))
                dt = datetime.datetime.fromtimestamp(ts_mcs / 1000000.0)
                date_str = dt.strftime("%Y-%m-%d")
                time_str = dt.strftime("%H:%M")
                
                if date_str not in daily_groups:
                    daily_groups[date_str] = {"text": [], "authors": set()}
                
                segments = event.get("chat_message", {}).get("message_content", {}).get("segment", [])
                text = "".join([s.get("text", "") for s in segments])
                
                sender_id = event.get("sender_id", {}).get("gaia_id")
                sender_name = participants.get(sender_id, "Unknown")
                
                if text.strip():
                    daily_groups[date_str]["text"].append(f"[{time_str}] {sender_name}: {text}")
                    daily_groups[date_str]["authors"].add(sender_name)

            for date_str, v in daily_groups.items():
                if not v["text"]: continue
                all_entries.append({
                    "content": "\n".join(v["text"]),
                    "timestamp": date_str,
                    "author": ", ".join(v["authors"]),
                    "doc_type": "hangouts_export"
                })
        
        return all_entries

class AudioHandler(DocumentHandler):
    """
    [v1.0.2] Private Audio Captioning Handler.
    Uses Whisper to transcribe voice messages and memos.
    """
    def __init__(self, model_size="base"):
        self.model_size = model_size
        self._model = None

    def can_handle(self, filename: str) -> bool:
        audio_exts = ('.mp3', '.wav', '.m4a', '.ogg', '.flac')
        video_exts = ('.mp4', '.mkv', '.mov', '.avi', '.flv', '.3gp', '.wmv')
        return filename.lower().endswith(audio_exts + video_exts)

    def parse(self, filepath: str) -> List[Dict]:
        if not whisper:
            print(f"    [!] Skipping {os.path.basename(filepath)} (Whisper not installed).")
            return []
            
        temp_audio = None
        try:
            filename = os.path.basename(filepath).lower()
            is_video = any(filename.endswith(ext) for ext in ('.mp4', '.mkv', '.mov', '.avi', '.flv', '.3gp', '.wmv'))
            
            target_file = filepath
            if is_video:
                print(f"  - Extracting audio from video: {os.path.basename(filepath)}...")
                temp_audio = filepath + ".tmp.wav"
                # Surgical ffmpeg extraction: 16khz mono (best for Whisper)
                cmd = ["ffmpeg", "-i", filepath, "-ar", "16000", "-ac", "1", "-vn", temp_audio, "-y"]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                target_file = temp_audio

            if not self._model:
                print(f"[*] Loading Whisper model ({self.model_size})...")
                self._model = whisper.load_model(self.model_size)
            
            print(f"  - Transcribing {os.path.basename(filepath)}...")
            # [v1.0.2] Force fp16=False for GTX 16-series stability (prevents 'nan' tensor errors)
            result = self._model.transcribe(target_file, fp16=False)
            transcript = result.get("text", "").strip()
            
            if transcript:
                return [{
                    "content": f"[MEDIA TRANSCRIPT]: {transcript}",
                    "timestamp": datetime.datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d"),
                    "doc_type": "media_transcript"
                }]
        except Exception as e:
            print(f"    [!] Transcription error: {e}")
        finally:
            # Self-Cleaning Pipeline: Delete temporary audio from video extraction
            if temp_audio and os.path.exists(temp_audio):
                try: os.remove(temp_audio)
                except: pass
        return []

# --- Facebook HTML Handler ---
class FacebookHTMLHandler(DocumentHandler):
    """
    Placeholder for Facebook Message HTML exports.
    """
    def can_handle(self, filename: str) -> bool:
        return "message_" in filename.lower() and filename.endswith(".html")

    def parse(self, filepath: str) -> List[Dict]:
        # Minimal extraction for text search
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            # Rip text from common Facebook tags
            clean_texts = re.findall(r'<div class="_3-96 _2pio _2lek _2riB">(.*?)</div>', content)
            return [{
                "content": " | ".join(clean_texts),
                "timestamp": datetime.datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d"),
                "doc_type": "facebook_msg"
            }]
        except:
            return []

# --- Standard File Handlers ---

class PdfHandler(DocumentHandler):
    def can_handle(self, filename: str) -> bool:
        return filename.lower().endswith(".pdf")
    
    def parse(self, filepath: str) -> List[Dict]:
        if not pypdf: return []
        try:
            reader = pypdf.PdfReader(filepath)
            text = ""
            for page in reader.pages:
                text += (page.extract_text() or "") + "\n"
            
            # [v1.4.0] PDF Thumbnailing
            thumbnail_path = None
            if pdfium:
                try:
                    pdf = pdfium.PdfDocument(filepath)
                    page = pdf.get_page(0)
                    bitmap = page.render(scale=1.0) # Low res for thumbnail
                    pil_image = bitmap.to_pil()
                    
                    # Generate deterministic thumbnail path
                    thumb_dir = "/home/concierge/memorybox/static/assets/thumbnails"
                    os.makedirs(thumb_dir, exist_ok=True)
                    hash_id = hashlib.md5(filepath.encode()).hexdigest()
                    thumbnail_filename = f"thumb_{hash_id}.webp"
                    thumbnail_path = os.path.join(thumb_dir, thumbnail_filename)
                    
                    pil_image.save(thumbnail_path, "WEBP", quality=80)
                    thumbnail_path = f"static/assets/thumbnails/{thumbnail_filename}"
                    page.close()
                    pdf.close()
                except Exception as te:
                    print(f"      [!] PDF Thumbnail Error: {te}")

            return [{
                "content": text.strip(),
                "timestamp": datetime.datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d"),
                "doc_type": "pdf_document",
                "thumbnail": thumbnail_path
            }]
        except:
            return []

class TextHandler(DocumentHandler):
    def can_handle(self, filename: str) -> bool:
        return filename.endswith((".txt", ".md"))
    def parse(self, filepath: str) -> List[Dict]:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return [{
            "content": content,
            "timestamp": datetime.datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d"),
            "doc_type": "plaintext"
        }]

class DocxHandler(DocumentHandler):
    def can_handle(self, filename: str) -> bool:
        if filename.startswith(("~$", ".")): return False
        return filename.lower().endswith(".docx")

    def parse(self, filepath: str) -> List[Dict]:
        if not docx: return []
        try:
            doc = docx.Document(filepath)
            text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
            
            # [v1.4.0] DOCX Sensing Summary (Ollama)
            # We skip heavy thumbnailing for now, but mark it for curation summary
            return [{
                "content": text,
                "timestamp": datetime.datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d"),
                "doc_type": "docx_document",
                "needs_summary": True
            }]
        except:
            return []

class ImageHandler(DocumentHandler):
    def can_handle(self, filename: str) -> bool:
        return filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.heic', '.heif'))

    def parse(self, filepath: str) -> List[Dict]:
        return [{
            "content": f"Visual Memory: {os.path.basename(filepath)}",
            "timestamp": datetime.datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d"),
            "doc_type": "standalone_image"
        }]

# --- Vision Processor ---

class VisionProcessor:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def describe_image(self, img_path: str, retry_count=1, deep_scan=False, temperature=0.2, user_context: str = None) -> str:
        if not os.path.exists(img_path): return ""
        try:
            with open(img_path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
            
            filename = os.path.basename(img_path)
            rel_path = os.path.dirname(img_path).split('/')[-1] # Parent folder hint
            
            prompt = "Describe this image."
            if user_context:
                prompt = f"Archivist Record: '{user_context}'. Identify elements in this image that corroborate or expand on this context. No preamble."
            elif deep_scan:
                prompt = "DEEP SCAN: Identify all visible text, brands, logos, and UI elements. No preamble."

            # [v1.5.0] Structural Grounding injection
            metadata_context = f"METADATA ANCHORS: Filename={filename}, Folder={rel_path}. "
            if not filename.startswith("IMG_") and not filename.startswith("WIN_"): # Ignore system gibberish
                prompt = metadata_context + prompt
            elif rel_path and rel_path != "Incoming":
                 prompt = f"FOLDER ANCHOR: {rel_path}. " + prompt
            
            # [v1.0.6] Hardware Protection: Strict token limit (num_predict) prevents infinite loops
            # We also pass the temperature to allow "jitter" on retries.
            options = {
                "num_predict": 300 if deep_scan else 150,
                "temperature": temperature,
                "stop": ["\n\n"] 
            }
            
            payload = {
                "model": self.model_name, 
                "prompt": prompt, 
                "stream": False, 
                "images": [encoded_string],
                "options": options
            }
            
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=300)
            if response.status_code == 200:
                text = response.json().get("response", "").strip()
                
                # [v1.0.6] Software Safety: Stutter-Detection Heuristic
                # If the model repeats the same word/phrase too many times, it's hallucinating.
                words = text.split()
                if len(words) > 20:
                    # Check for "The Giggles" (repetitive words)
                    for i in range(len(words) - 5):
                        chunk = words[i:i+3]
                        if words.count(chunk[0]) > 15: # Broad repetitive word check
                            print(f"      [!] Hallucination detected (repetitive tokens). Scrubbing...")
                            return self.describe_image(img_path, retry_count - 1, deep_scan, temperature=0.8) if retry_count > 0 else "[Vision Error: Hallucination Loop]"

                return text
            else:
                print(f"      [!] Ollama Error HTTP {response.status_code}: {response.text}")
                return ""
        except Exception as e:
            print(f"      [!] Vision Exception: {str(e)}")
            if retry_count > 0:
                time.sleep(1)
                return self.describe_image(img_path, retry_count - 1, deep_scan, temperature=0.5)
        return ""

    def summarize_text(self, text: str, filename: str, rel_path: str, user_context: str = None, retry_count: int = 3) -> str:
        """ [v1.5.0] Text Sensing Engine: Produces a first-round sense description of textual artifacts. """
        # Take a significant sample for sensing (up to 4KB)
        sample = text[:4000]
        
        # [v1.5.0] Specialized Journal Sensing
        is_journal = filename.lower().startswith("journal_chat_")
        
        prompt_instructions = (
            "Analyze this archival chat journal. Summarize the key topics discussed and the final outcome of the conversation." 
            if is_journal else 
            "Analyze this textual artifact. Produce a concise sensing description (2-3 sentences), highlighting key entities and sentiment."
        )

        prompt = (
            f"As the MemoryBox Sense Engine, {prompt_instructions}\n"
            f"METADATA: Filename={filename}, Folder={rel_path}\n"
            f"ARCHIVIST CONTEXT: {user_context or 'None provided'}\n\n"
            f"DOCUMENT SAMPLE:\n{sample}\n\n"
            "No preamble, just the summary."
        )

        for attempt in range(retry_count):
            try:
                payload = {
                    "model": "mistral", 
                    "prompt": prompt, 
                    "stream": False,
                    "options": {"num_predict": 300, "temperature": 0.2 if attempt == 0 else 0.5}
                }
                # [v1.5.0] Exponential-ish backoff to let the VM breathe
                if attempt > 0:
                    wait_time = (attempt * 10)
                    print(f"      [!] Retrying text sensing ({attempt}/{retry_count}) in {wait_time}s...")
                    time.sleep(wait_time)

                response = requests.post(OLLAMA_API_URL, json=payload, timeout=180)
                if response.status_code == 200:
                    summary = response.json().get("response", "").strip()
                    if summary:
                        if attempt > 0: print(f"      [+] Sensing recovered after {attempt} retries.")
                        return summary
            except Exception as e:
                print(f"      [!] Text sensing attempt {attempt+1} failed for {filename}: {e}")
        
        return "[Textual Memory]: Sensing pending revision due to system congestion."

# --- Main Ingestor ---

class Ingestor:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.master_key = None # No longer used (LUKS level)
        self.fernet = None
        
        self.handlers = [
            GoogleChatHandler(), 
            HangoutsHandler(), 
            FacebookHTMLHandler(), 
            PdfHandler(), 
            DocxHandler(), 
            ImageHandler(), 
            AudioHandler(model_size="base"),
            TextHandler()
        ]
        self.vision = VisionProcessor(VISION_MODEL) if not SKIP_VISION else None
        self._init_db()
        self.status_file = "/home/concierge/memories/ingestion_status.json"
        self.notes = {}
        self.visibility = 'SHARED'
        self.author_id = 1

    def set_metadata(self, notes: Dict, author_id: int, visibility: str = 'SHARED'):
        self.notes = notes
        self.author_id = author_id
        self.visibility = visibility

    def get_next_vault_id(self, archive_root: str) -> str:
        """ [v1.2.0] Finds the next sequential hex ID (e.g. 0x0001). Infinite rollover. """
        vault_dir = os.path.join(archive_root, "Vault")
        if not os.path.exists(vault_dir):
            os.makedirs(vault_dir)
            return "0x0001"
        
        vaults = [d for d in os.listdir(vault_dir) if d.startswith("0x")]
        if not vaults:
            return "0x0001"
            
        try:
            max_id = max([int(v, 16) for v in vaults])
            next_id = max_id + 1
            # Dynamic padding: at least 4 chars, but expands as needed
            return f"0x{next_id:04x}"
        except:
            return f"0x{len(vaults) + 1:04x}"

    def _update_status(self, state: str, progress: float = 0, current_file: str = ""):
        """ [v1.2.0] Deep Sensing: Export status for the web UI. """
        try:
            with open(self.status_file, "w") as f:
                json.dump({
                    "state": state,
                    "progress": round(progress, 2),
                    "current_file": current_file,
                    "timestamp": datetime.datetime.now().isoformat()
                }, f)
        except Exception as e:
            print(f"      [!] Status update failed: {e}")

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('PRAGMA journal_mode=WAL')
        
        # [v1.0.1] Participant-aware schema
        c.execute('''CREATE TABLE IF NOT EXISTS writings 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      content TEXT, 
                      description TEXT,
                      author TEXT,
                      recipient TEXT,
                      timestamp TEXT, 
                      source_file TEXT, 
                      doc_type TEXT,
                      owner_id INTEGER DEFAULT 0,
                      visibility TEXT DEFAULT 'SHARED',
                      fs_mtime TIMESTAMP,
                      fs_ctime TIMESTAMP,
                      revision_count INTEGER DEFAULT 0,
                      revision_note TEXT,
                      thumbnail_path TEXT)''')
        c.execute('CREATE VIRTUAL TABLE IF NOT EXISTS writings_fts USING fts5(content, description, content_id UNINDEXED)')
        c.execute('''CREATE TABLE IF NOT EXISTS image_cache 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      owner_id INTEGER DEFAULT 0,
                      visibility TEXT DEFAULT 'SHARED',
                      img_path TEXT UNIQUE, 
                      description TEXT,
                      fs_mtime TIMESTAMP,
                      fs_ctime TIMESTAMP,
                      revision_count INTEGER DEFAULT 0,
                      revision_note TEXT,
                      thumbnail_path TEXT)''')
        # [v1.2.0] Enhanced FTS (Porter Stemming + Path Search)
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS image_cache_fts USING fts5(description, img_path, content_id UNINDEXED, tokenize='porter')")
        
        # Ensure columns exist if updating existing DB [v1.2.0: fs_metadata]
        try:
            c.execute("PRAGMA table_info(writings)")
            cols = [col[1] for col in c.fetchall()]
            if "author" not in cols: c.execute('ALTER TABLE writings ADD COLUMN author TEXT')
            if "recipient" not in cols: c.execute('ALTER TABLE writings ADD COLUMN recipient TEXT')
            if "fs_mtime" not in cols: c.execute('ALTER TABLE writings ADD COLUMN fs_mtime TIMESTAMP')
            if "fs_ctime" not in cols: c.execute('ALTER TABLE writings ADD COLUMN fs_ctime TIMESTAMP')
            if "revision_count" not in cols: c.execute('ALTER TABLE writings ADD COLUMN revision_count INTEGER DEFAULT 0')
            if "revision_note" not in cols: c.execute('ALTER TABLE writings ADD COLUMN revision_note TEXT')
            if "thumbnail_path" not in cols: c.execute('ALTER TABLE writings ADD COLUMN thumbnail_path TEXT')
            
            c.execute("PRAGMA table_info(image_cache)")
            cols = [col[1] for col in c.fetchall()]
            if "owner_id" not in cols: c.execute('ALTER TABLE image_cache ADD COLUMN owner_id INTEGER DEFAULT 0')
            if "visibility" not in cols: c.execute("ALTER TABLE image_cache ADD COLUMN visibility TEXT DEFAULT 'SHARED'")
            if "fs_mtime" not in cols: c.execute('ALTER TABLE image_cache ADD COLUMN fs_mtime TIMESTAMP')
            if "fs_ctime" not in cols: c.execute('ALTER TABLE image_cache ADD COLUMN fs_ctime TIMESTAMP')
            if "revision_count" not in cols: c.execute('ALTER TABLE image_cache ADD COLUMN revision_count INTEGER DEFAULT 0')
            if "source_note" not in cols: c.execute('ALTER TABLE image_cache ADD COLUMN source_note TEXT')
            if "source_note_author" not in cols: c.execute('ALTER TABLE image_cache ADD COLUMN source_note_author INTEGER')
        except Exception as e:
            print(f"[!] Migration Error in ingest_writings: {e}")
            
        conn.commit()
        conn.close()

    def ingest(self, root_dir: str):
        if not os.path.exists(root_dir):
            print(f"[!] Target directory {root_dir} does not exist.")
            return

        archive_base = os.path.dirname(root_dir)  # Assumes root_dir is Archive/Incoming
        vault_id = self.get_next_vault_id(archive_base)
        vault_path = os.path.join(archive_base, "Vault", vault_id)
        
        print(f"[*] Starting ingestion cycle {vault_id} for: {root_dir}")
        self._update_status("SEALING", progress=0, current_file="Verifying integrity...")
        
        all_files = []
        for root, _, files in os.walk(root_dir):
            for f in files:
                all_files.append(os.path.join(root, f))
        
        if not all_files:
            print("[+] No files to ingest.")
            self._update_status("DONE", progress=100, current_file="No files found.")
            return

        os.makedirs(vault_path, exist_ok=True)
        self._update_status("VAULTING", progress=10, current_file=f"Moving to {vault_id}...")
        
        vaulted_files = []
        for i, filepath in enumerate(all_files):
            # [v1.2.6] Structured Vaulting: Preserve original subdirectory mapping
            rel_path = os.path.relpath(filepath, root_dir)
            dest_path = os.path.join(vault_path, rel_path)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            
            # Verified Move Logic
            try:
                shutil.copy2(filepath, dest_path)
                if os.path.exists(dest_path) and os.path.getsize(dest_path) == os.path.getsize(filepath):
                    os.remove(filepath)
                    vaulted_files.append(dest_path)
                else:
                    print(f"      [!] Move verification failed for {filename}")
            except Exception as e:
                print(f"      [!] Vaulting error {filename}: {e}")

        total = len(vaulted_files)
        for i, filepath in enumerate(vaulted_files):
            filename = os.path.basename(filepath)
            handler = next((h for h in self.handlers if h.can_handle(filename)), None)
            
            if handler:
                progress = (i / total) * 100
                self._update_status("INDEXING", progress=progress, current_file=filename)
                print(f"  - Processing: {filename}...")
                entries = handler.parse(filepath)
                if entries:
                    # [v1.2.6] Deep Grounding: Inject parent folder names into search index
                    rel_path_in_vault = os.path.relpath(filepath, vault_path)
                    parent_dirs = os.path.dirname(rel_path_in_vault)
                    folder_context = f"[FOLDER: {parent_dirs}] " if parent_dirs and parent_dirs != "." else ""
                    
                    # [v1.5.0] First-Round Sensing (Textual)
                    user_note = self.notes.get(filename)
                    if not SKIP_VISION and entries[0].get('doc_type') != "standalone_image":
                        # Sensing Engine Pass
                        # Note: We summarize only the first entry if multi-entry (e.g. Chat logs usually group by day anyway)
                        summary = self.vision.summarize_text(
                            entries[0]['content'], 
                            filename, 
                            parent_dirs, 
                            user_context=user_note
                        )
                        for e in entries:
                            e['description'] = summary
                            print(f"    [Sensed]: {summary}")

                    # [v1.2.6] Injection: Always ground the raw content with folder/note context
                    for e in entries:
                        if user_note:
                            e['content'] = f"{folder_context}[ARCHIVIST NOTE]: {user_note}\n{e['content']}"
                        else:
                            e['content'] = f"{folder_context}{e['content']}"

                    self.add_writings_bulk(entries, filepath)

        self._update_status("COMPLETED", progress=100, current_file="Vault Solidified.")

    def add_writings_bulk(self, entries: List[Dict], source_file: str):
        """ [v1.0.3] Turbo Ingestion: One transaction per file. """
        if not entries: return
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            inserts = []
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(source_file)).isoformat()
            ctime = datetime.datetime.fromtimestamp(os.path.getctime(source_file)).isoformat()
            
            for e in entries:
                content = e['content']
                if self.fernet:
                    try:
                        content = self.fernet.encrypt(content.encode()).decode()
                    except Exception as ex:
                        print(f"      [!] Encryption error: {ex}")
                
                inserts.append((
                    content, 
                    e.get('author'), 
                    e.get('recipient'), 
                    e.get('timestamp'), 
                    source_file, 
                    e.get('doc_type', 'unknown'),
                    e.get('description'), # [v1.5.0] Sense result
                    mtime,
                    ctime,
                    self.author_id,
                    self.visibility,
                    e.get('thumbnail')
                ))
            
            c.executemany('''INSERT INTO writings (content, author, recipient, timestamp, source_file, doc_type, description, fs_mtime, fs_ctime, owner_id, visibility, thumbnail_path) 
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', inserts)
            
            # FTS5 Indexing (Batch)
            # Fetch the newly inserted IDs
            count = len(inserts)
            c.execute("SELECT id, content, description FROM writings ORDER BY id DESC LIMIT ?", (count,))
            new_rows = c.fetchall()
            
            fts_inserts = []
            for i, row in enumerate(new_rows):
                cid, encrypted_content, sensing = row
                entry = entries[count - 1 - i]
                cleartext = entry['content']
                # Sensing may have been generated during ingest()
                sense_summary = entry.get('description') or sensing or ""
                fts_inserts.append((cleartext, sense_summary, cid))
                
            c.executemany("INSERT INTO writings_fts (content, description, content_id) VALUES (?,?,?)", fts_inserts)
            
            conn.commit()
            conn.close()
            print(f"    [+] Bulk Indexed {len(entries)} records.")
        except Exception as e:
            print(f"    [!] Bulk Ingestion Error: {e}")

        print("[+] Ingestion complete.")

    def backfill_vision(self):
        """ [v1.0.5] Turbo Vision Loop: Processes all images lacking a description. """
        print("[*] Starting Visual Backfill (Moondream)...")
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # 1. [v1.2.0] Alignment Logic: Find images that are not in cache OR not in the search index
        c.execute('''SELECT img_path FROM image_cache 
                     WHERE id NOT IN (SELECT content_id FROM image_cache_fts)''')
        missing_index = [r[0] for r in c.fetchall()]
        
        c.execute('''SELECT DISTINCT source_file FROM writings 
                     WHERE doc_type = "standalone_image" 
                     AND source_file NOT IN (SELECT img_path FROM image_cache)''')
        missing_cache = [r[0] for r in c.fetchall()]
        
        missing = list(set(missing_index + missing_cache))
        
        if not missing:
            print("[+] Vision archive is 100% synchronized.")
            conn.close()
            return

        print(f"[*] Found {len(missing)} images needing re-indexing or description.")
        
        for i, img_path in enumerate(missing):
            if not os.path.exists(img_path): continue
            filename = os.path.basename(img_path)
            progress = (i / len(missing)) * 100
            
            # [v1.2.5] Deep Sensing: Signal the UI before processing heavy vision
            self._update_status("SENSING", progress=progress, current_file=filename)
            
            # AI Grounding: pass user note to vision prompt
            user_note = self.notes.get(filename)
            desc = self.vision.describe_image(img_path, user_context=user_note)
            if desc:
                print(f"    [Sensed]: {desc}")
            if not desc:
                desc = "[Unidentified UI Element]"
            
            # [v1.3.1] Sensing Synthesis: If we have a note, don't just use the raw caption.
            # Sharpen it immediately so it hits the archive with full context.
            if user_note and desc and not desc.startswith("["):
                print(f"    [*] Grounding found for {filename}. Synthesizing...")
                desc = self.vision.synthesize_description(desc, user_note)
                
            # [v1.2.0] LUKS Volume mode: No encryption overhead
            encrypted_desc = desc
                
            c.execute("INSERT OR REPLACE INTO image_cache (img_path, description, fs_mtime, fs_ctime, revision_count, source_note, source_note_author, owner_id, visibility) VALUES (?,?,?,?,?,?,?,?,?)", 
                      (img_path, encrypted_desc, 
                       datetime.datetime.fromtimestamp(os.path.getmtime(img_path)).isoformat(),
                       datetime.datetime.fromtimestamp(os.path.getctime(img_path)).isoformat(),
                       0, user_note, self.author_id, self.author_id, self.visibility)) # Initial vision is Revision #0 (Ready for Curation)
            
            # [v1.2.0] Update Image FTS with CLEAR-TEXT + Filename
            row_id = c.lastrowid
            if not row_id: # If REPLACE didn't change row_id or it already existed
                c.execute("SELECT id FROM image_cache WHERE img_path = ?", (img_path,))
                row_id = c.fetchone()[0]
                
            c.execute("DELETE FROM image_cache_fts WHERE content_id = ?", (row_id,))
            c.execute("INSERT INTO image_cache_fts (description, img_path, content_id) VALUES (?,?,?)", 
                      (desc, os.path.basename(img_path), row_id))
            
            conn.commit()
            
            # Rate limit/Safety to prevent Ollama timeout
            time.sleep(0.5)

        conn.close()
        print("[+] Visual Backfill complete.")

    def backfill_audio(self, root_dir: str):
        """ [v1.0.5] Scan-and-Transcribe Loop: Captures missed media. """
        print(f"[*] Starting Audio Backfill for {root_dir}")
        audio_handler = next((h for h in self.handlers if isinstance(h, AudioHandler)), None)
        if not audio_handler: return

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        for root, _, files in os.walk(root_dir):
            for filename in files:
                if audio_handler.can_handle(filename):
                    filepath = os.path.join(root, filename)
                    # Check if already transcribed
                    c.execute("SELECT count(*) FROM writings WHERE source_file = ? AND doc_type = 'media_transcript'", (filepath,))
                    if c.fetchone()[0] == 0:
                        print(f"  - Backfilling Transcript: {filename}...")
                        entries = audio_handler.parse(filepath)
                        if entries:
                            self.add_writings_bulk(entries, filepath)
        
        conn.close()
        print("[+] Audio Backfill complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ConciergeHub Memory Ingestor")
    parser.add_argument("target", nargs="?", default="/home/fuzz/Writings", help="Path to archive")
    parser.add_argument("--backfill-vision", action="store_true", help="Process descriptions for existing images")
    parser.add_argument("--backfill-audio", action="store_true", help="Scan and transcribe missed media")
    parser.add_argument("--rebuild", action="store_true", help="Nuclear Option: Delete DB and re-ingest")
    
    args = parser.parse_args()
    
    # [v1.2.0] Appliance Standard: Prioritize the memories partition (B:)
    db = "/home/concierge/memories/personal_memory.db"
    if not os.path.exists("/home/concierge/memories"):
        # Fallback to relative path for local development
        db = os.path.join(os.path.dirname(__file__), "..", "personal_memory.db")
    
    # [v1.2.0] Unified Sequential Pipeline: allow Ingest -> Vision -> Audio backfill
    active_ingestor = Ingestor(db)
    
    # Check for metadata passed via file (for large batches)
    notes_file = "/home/concierge/memories/ingest_notes.json"
    if os.path.exists(notes_file):
        try:
            with open(notes_file, "r") as f:
                meta = json.load(f)
                active_ingestor.set_metadata(meta.get("notes", {}), meta.get("author_id", 1), meta.get("visibility", "SHARED"))
            os.remove(notes_file)
        except Exception as e:
            print(f"[!] Metadata parse error: {e}")

    if args.rebuild:
        if os.path.exists(db):
            print(f"[*] Nuclear Option: Deleting {db}...")
            os.remove(db)
        active_ingestor = Ingestor(db)
        active_ingestor.ingest(args.target)
        active_ingestor.backfill_vision()
    else:
        # Standard Flow
        active_ingestor.ingest(args.target)
        
        if args.backfill_vision:
            active_ingestor.backfill_vision()
            
        if args.backfill_audio:
            active_ingestor.backfill_audio(args.target)

    # Final Signal to Deep Sensing UI
    active_ingestor._update_status("DONE", progress=100, current_file="Vault Solidified.")
    print("[+] Ingestion Pipeline Complete.")

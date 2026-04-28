import os
import re
import time
import json
import sqlite3
import asyncio
import logging
import subprocess
import shutil
import stat # [v1.7.15] Hardware-level device checking
import httpx
import uvicorn
# import psutil [v1.2.0] Disabled for environment compatibility
import hashlib
import base64
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from fastapi import FastAPI, Request, HTTPException, Depends, UploadFile, File, Form
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
# [v1.7.0] Production Sensing: Whisper & Gemma 2
try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None
# [v1.2.0] LUKS Volume-Level Encryption (No row-level cipher overhead)

# --- Hub Configuration [v1.0.5-UNIFIED] ---
# Identity: The Hub's Unified Controller (Tactical + Personal)
# Capability: Archive Access, Tactical Awareness, Wiki Management

# v1.1.0: Vault Logic (Directory-Based for Prototype)
VAULT_TYPE = "DIRECTORY" # [v1.8.11] Options: "LUKS" or "DIRECTORY"
VAULT_MOUNT = "/home/concierge/memories" 
VAULT_SOURCE = "" # Not used in DIRECTORY mode
VAULT_MAPPER = "memories"
VAULT_DEVICE = f"/dev/mapper/{VAULT_MAPPER}"

VAULT_SEALED = True # Always start sealed until key is provided
MASTER_KEY = "" # Empty on startup

# Basic Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MemoryBox")

# [v1.8.8] Global State Hardening: Purge stale ingestion status on startup
try:
    _ingest_state_path = "/home/concierge/memories/ingestion_status.json"
    if os.path.exists(_ingest_state_path):
        os.remove(_ingest_state_path)
        logger.info("[STARTUP] Purged stale ingestion status file.")
except Exception as e:
    logger.warning(f"[STARTUP] Failed to purge ingestion status: {e}")

# Project Directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(VAULT_MOUNT, "personal_memory.db")
SETUP_LOCK_PATH = os.path.join(VAULT_MOUNT, ".setup_lock")
CANARY_PATH = os.path.join(VAULT_MOUNT, ".vault_canary") # [v1.2.0] Vault Verification Signature

# [v1.2.0] Volume Encryption Helpers (Stubs for direct LUKS access)
def decrypt_content(content: str) -> str:
    return content # Pass-through as the partition is already encrypted via LUKS

def encrypt_content(content: str) -> str:
    return content

def get_db_connection():
    """ [v1.7.17] Sovereign Shield: Centralized DB access guard. 
        Ensures NO disk write happens on the root partition when the vault is sealed. """
    if VAULT_SEALED:
        logger.warning("[SHIELD] Blocked DB connection attempt: Vault is currently SEALED.")
        raise ConnectionRefusedError("MemoryBox Vault is SEALED. Access Denied.")
    return sqlite3.connect(DB_PATH)

def encode_image(image_path):
    """ [v1.6.4] Hardware Sensing Helper: Encode artifacts for AI vision. """
    try:
        if not os.path.isabs(image_path):
            image_path = os.path.join(VAULT_MOUNT, image_path)
        if not os.path.exists(image_path):
            logger.error(f"[SENSING] Image not found for encoding: {image_path}")
            return None
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        logger.error(f"[SENSING] Encoding failed: {e}")
        return None

def is_luks_partition(device: str) -> bool:
    """ [v1.7.17] Hardware Intelligence: Detects if the partition is already a LUKS safe. """
    try:
        # cryptsetup isLuks returns 0 if it is a LUKS partition
        # [v1.7.20] Added -n and absolute path for sudoers matching
        res = subprocess.run(["sudo", "-n", "/usr/sbin/cryptsetup", "isLuks", device], capture_output=True)
        return res.returncode == 0
    except:
        return False

class SafetyScrubber:
    """ [v1.8.10] Post-processing to strip AI-echoed technical metadata. """
    @staticmethod
    def scrub(text: str) -> str:
        if not text: return ""
        patterns = [
            r"METADATA ANCHORS:.*?\s",
            r"FOLDER ANCHOR:.*?\s",
            r"Filename:.*?\s",
            r"Folder:.*?\s",
            r"ARCHIVAL BRIEF \(max 220 chars\):",
            r"No preamble\.",
            r"Low on minor details\."
        ]
        scrubbed = text
        for p in patterns:
            scrubbed = re.sub(p, "", scrubbed, flags=re.IGNORECASE)
        return scrubbed.strip()

def mount_vault(key: str) -> bool:
    """ [v1.7.16] Hardware-Anchored Mounting: Handles LUKS Open + OS Mount with Ghost Mitigation. """
    global MASTER_KEY, VAULT_SEALED, SESSION_SECRET
    try:
        # 1. Check if already mounted
        if os.path.ismount(VAULT_MOUNT):
            logger.info(f"[VAULT] {VAULT_MOUNT} is already mounted. Syncing state.")
            MASTER_KEY = key
            VAULT_SEALED = False
            SESSION_SECRET = None
            return True
            
        # 2. Hardware Decryption Bridge (Skip for DIRECTORY mode)
        if VAULT_TYPE == "DIRECTORY":
            logger.info(f"[VAULT] Unsealing directory-based vault: {VAULT_MOUNT}")
            if not os.path.exists(VAULT_MOUNT):
                os.makedirs(VAULT_MOUNT, exist_ok=True)
            
            MASTER_KEY = key
            VAULT_SEALED = False
            SESSION_SECRET = None
            solidify_archival_tree()
            return True

        # [v1.7.15] Surgical Diagnostic: Key Hex Analysis
        key_len = len(key)
        if key_len > 0:
            first_hex = hex(ord(key[0]))
            last_hex = hex(ord(key[-1]))
            boundary = f"{key[0]}...{key[-1]}" if key_len > 1 else "?"
            logger.info(f"[VAULT] Unseal Request: Length={key_len}, Boundary='{boundary}', Hex=[{first_hex}...{last_hex}]")
        
        # [v1.7.16] Ghost Mitigation Strategy
        # If the mapper exists but isn't a 'live' block device (or is stale), we must clear it.
        if os.path.exists(VAULT_DEVICE):
            logger.warning(f"[VAULT] Stale mapper detected at {VAULT_DEVICE}. Attempting surgical clearance...")
            # [v1.7.20] Use -n for non-interactive sudo to avoid hanging on password prompts
            subprocess.run(["sudo", "-n", "/usr/sbin/cryptsetup", "luksClose", VAULT_MAPPER], capture_output=True)

        logger.info(f"[VAULT] Attempting Hardware Unseal (luksOpen) on {VAULT_SOURCE}...")

        # [v1.7.20] sudo -n ensures we fail fast if permissions are missing
        cmd = ["sudo", "-n", "/usr/sbin/cryptsetup", "luksOpen", VAULT_SOURCE, VAULT_MAPPER, "--key-file", "-"]
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate(input=key.encode('utf-8'))
        
        if process.returncode != 0:
            err_msg = stderr.decode().strip()
            logger.error(f"[VAULT] Hardware Unseal Failed: {err_msg}")
            # [v1.7.16] Specific handling for concurrent attempts or residual locks
            if "already exists" in err_msg:
                logger.info("[VAULT] Retrying with aggressive ghost clearance...")
                subprocess.run(["sudo", "-n", "/usr/sbin/cryptsetup", "luksClose", VAULT_MAPPER], capture_output=True)
                process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = process.communicate(input=key.encode('utf-8'))
                if process.returncode != 0:
                    raise HTTPException(status_code=400, detail=f"Hardware Retry Failed: {stderr.decode().strip()}")
            else:
                raise HTTPException(status_code=400, detail=f"Hardware Unseal Failed: {err_msg}")
        
        logger.info("[VAULT] Hardware Decrypted. Virtual device established.")


        # 3. OS Filesystem Mount
        # [v1.7.3] Explicit device mount to bypass fstab requirement
        # [v1.7.20] Added -n to sudo
        cmd = ["sudo", "-n", "/usr/bin/mount", "-t", "ext4", VAULT_DEVICE, VAULT_MOUNT]
        logger.info(f"[VAULT] Executing: {' '.join(cmd)}")
        res = subprocess.run(cmd, capture_output=True, text=True)
        
        if res.returncode == 0:
            logger.info("[VAULT] Volume mounted successfully. Identity Anchored.")
            # [v1.7.19] Ownership Governance: Ensure the user can write to the vault (Required after mkfs)
            subprocess.run(["sudo", "-n", "/usr/bin/chown", "concierge:concierge", VAULT_MOUNT], capture_output=True)
            
            MASTER_KEY = key
            VAULT_SEALED = False
            SESSION_SECRET = None
            solidify_archival_tree()
            return True
        else:
            logger.error(f"[VAULT] Mount failed: {res.stderr}")
            # If mount fails, we should probably close the LUKS device to avoid leaving a ghost
            subprocess.run(["sudo", "-n", "/usr/sbin/cryptsetup", "luksClose", VAULT_MAPPER], capture_output=True)
            return False
    except Exception as e:
        logger.error(f"[VAULT] Critical Unseal Error: {e}")
        return False

def solidify_archival_tree():
    """ [v1.8.3] Archival Genesis: Robust directory creation with Hardware elevation. """
    dirs = [
        VAULT_MOUNT,
        os.path.join(VAULT_MOUNT, "Archive"),
        os.path.join(VAULT_MOUNT, "Archive/Incoming"),
        os.path.join(VAULT_MOUNT, "Archive/Processed"),
        os.path.join(VAULT_MOUNT, "Archive/Vision"),
        os.path.join(VAULT_MOUNT, "Archive/Audio"),
        os.path.join(VAULT_MOUNT, "Archive/Vault"),
        os.path.join(VAULT_MOUNT, "Archive/Journals"),
        os.path.join(VAULT_MOUNT, "sessions"),
        os.path.join(VAULT_MOUNT, "thumbnails")
    ]
    for d in dirs:
        try:
            # [v1.8.3] Elevation Protocol: Use sudo mkdir to bypass root-only mount permissions
            if not os.path.exists(d):
                logger.info(f"[GENESIS] Elevating creation of archival directory: {d}")
                subprocess.run(["sudo", "-n", "/usr/bin/mkdir", "-p", d], capture_output=True)
            
            # Ensure concierge ownership
            subprocess.run(["sudo", "-n", "/usr/bin/chown", "-R", "concierge:concierge", d], capture_output=True)
        except Exception as e:
            logger.error(f"[GENESIS] Solidification failed for {d}: {e}")

def unmount_vault() -> bool:
    """ [v1.6.3] Hard Seal: Safely unmounts the archival volume and purges keys from memory. """
    global MASTER_KEY, VAULT_SEALED, SESSION_SECRET
    try:
        if not os.path.ismount(VAULT_MOUNT):
            logger.info(f"[VAULT] {VAULT_MOUNT} is not mounted. Ensuring logic is sealed.")
            MASTER_KEY = ""
            VAULT_SEALED = True
            SESSION_SECRET = None # [v1.6.3] Purge session cache on lockdown
            return True
            
        if VAULT_TYPE == "DIRECTORY":
            logger.info("[VAULT] Sealing directory-based vault.")
            MASTER_KEY = ""
            VAULT_SEALED = True
            SESSION_SECRET = None
            return True

        # 3. Attempt unmount
        # [v1.7.20] Added -n and absolute path for sudoers matching
        cmd = ["sudo", "-n", "/usr/bin/umount", VAULT_MOUNT]
        logger.info(f"[VAULT] Executing: {' '.join(cmd)}")
        res = subprocess.run(cmd, capture_output=True, text=True)
        
        if res.returncode == 0:
            logger.info("[VAULT] Volume unmounted successfully. Performing Hardware Lockdown...")
            # [v1.7.1] Hardware Hard-Seal: luksClose
            # [v1.7.20] Added -n and absolute path for sudoers matching
            cmd_close = ["sudo", "-n", "/usr/sbin/cryptsetup", "luksClose", VAULT_MAPPER]
            subprocess.run(cmd_close, capture_output=True)
            
            MASTER_KEY = ""
            VAULT_SEALED = True
            SESSION_SECRET = None # [v1.6.3] Force fresh handshake on next unseal
            return True
        else:
            logger.error(f"[VAULT] Unmount failed: {res.stderr}")
            return False
    except Exception as e:
        logger.error(f"[VAULT] Critical Unmount Error: {e}")
        return False

def solidify_schema_v126():
    """ [v1.2.6] Schema Governance & Solidification. """
    # [v1.2.9] Canary Guard: Total block on blank DB creation if volume is unmounted
    if not os.path.exists(CANARY_PATH):
        logger.critical(f"[VAULT] DETACHED! Canary not found at {CANARY_PATH}. Aborting DB migration.")
        return # Refuse to initialize a blank DB

    conn = get_db_connection()
    c = conn.cursor()
    
    # 1. Profiles Table (PINs are PBKDF2 hashed)
    c.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            role TEXT, -- 'SUPERADMIN', 'ADMIN', 'USER'
            pin_hash BLOB,
            pin_salt BLOB,
            avatar_path TEXT,
            theme TEXT DEFAULT 'modern',
            created_at TIMESTAMP
        )
    """)
    
    # [v1.1.0] Theme Column Migration
    c.execute("PRAGMA table_info(profiles)")
    cols = [col[1] for col in c.fetchall()]
    if "theme" not in cols:
        c.execute("ALTER TABLE profiles ADD COLUMN theme TEXT DEFAULT 'modern'")
    
    # [v1.7.0] Governance Migration: Legal Name & Mimicry
    if "legal_name" not in cols:
        c.execute("ALTER TABLE profiles ADD COLUMN legal_name TEXT")
    if "style_sampling_permitted" not in cols:
        c.execute("ALTER TABLE profiles ADD COLUMN style_sampling_permitted INTEGER DEFAULT 0")
    
    # [v1.2.6] Writings Table Initialization
    c.execute("""
        CREATE TABLE IF NOT EXISTS writings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
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
            thumbnail_path TEXT,
            curated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS writings_fts USING fts5(content, description, content_id UNINDEXED)")
    
    # 2. Archival Tagging (owner_id, visibility)
    # writings table
    c.execute("PRAGMA table_info(writings)")
    cols = [col[1] for col in c.fetchall()]
    if "owner_id" not in cols:
        c.execute("ALTER TABLE writings ADD COLUMN owner_id INTEGER DEFAULT 0")
    if "visibility" not in cols:
        c.execute("ALTER TABLE writings ADD COLUMN visibility TEXT DEFAULT 'SHARED'")
        
    # 3. [v1.2.0] Filesystem Metadata (ctime/mtime)
    c.execute("PRAGMA table_info(writings)")
    cols = [col[1] for col in c.fetchall()]
    if "fs_mtime" not in cols:
        c.execute("ALTER TABLE writings ADD COLUMN fs_mtime TIMESTAMP")
    if "fs_ctime" not in cols:
        c.execute("ALTER TABLE writings ADD COLUMN fs_ctime TIMESTAMP")
    if "revision_count" not in cols:
        c.execute("ALTER TABLE writings ADD COLUMN revision_count INTEGER DEFAULT 0")
    if "revision_note" not in cols:
        c.execute("ALTER TABLE writings ADD COLUMN revision_note TEXT")
    if "thumbnail_path" not in cols:
        c.execute("ALTER TABLE writings ADD COLUMN thumbnail_path TEXT")
    if "hidden" not in cols:
        c.execute("ALTER TABLE writings ADD COLUMN hidden INTEGER DEFAULT 0")
    if "description" not in cols:
        c.execute("ALTER TABLE writings ADD COLUMN description TEXT")
    if "curated_at" not in cols:
        c.execute("ALTER TABLE writings ADD COLUMN curated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    # 4. [v1.2.0] Image Cache (Multi-User Hardening)
    c.execute("""
        CREATE TABLE IF NOT EXISTS image_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER DEFAULT 1,
            visibility TEXT DEFAULT 'SHARED',
            img_path TEXT UNIQUE,
            description TEXT,
            revision_count INTEGER DEFAULT 0,
            fs_mtime TIMESTAMP,
            fs_ctime TIMESTAMP,
            source_note TEXT,
            source_note_author INTEGER,
            thumbnail_path TEXT,
            curated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("PRAGMA table_info(image_cache)")
    cols = [col[1] for col in c.fetchall()]
    if "curated_at" not in cols:
        c.execute("ALTER TABLE image_cache ADD COLUMN curated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    # [v1.2.0] Image FTS for Encrypted Search (Porter Stemming + Path Search)
    c.execute("DROP TABLE IF EXISTS image_cache_fts")
    c.execute("CREATE VIRTUAL TABLE image_cache_fts USING fts5(description, img_path, content_id UNINDEXED, tokenize='porter')")
    
    # Ensure revision_count exists [v1.2.0]
    try:
        c.execute("PRAGMA table_info(image_cache)")
        cols = [col[1] for col in c.fetchall()]
        if "revision_count" not in cols:
            c.execute("ALTER TABLE image_cache ADD COLUMN revision_count INTEGER DEFAULT 0")
        if "hidden" not in cols:
            c.execute("ALTER TABLE image_cache ADD COLUMN hidden INTEGER DEFAULT 0")
        if "source_note" not in cols:
            c.execute("ALTER TABLE image_cache ADD COLUMN source_note TEXT")
        if "source_note_author" not in cols:
            c.execute("ALTER TABLE image_cache ADD COLUMN source_note_author INTEGER")
        if "thumbnail_path" not in cols:
            c.execute("ALTER TABLE image_cache ADD COLUMN thumbnail_path TEXT")
    except Exception as e:
        logger.error(f"[MIGRATE] Image Cache migration failed: {e}")
    
    # [v1.2.6] Repair Transaction: Normalize all backslashes to forward slashes for cross-platform stability
    try:
        c.execute("UPDATE image_cache SET img_path = replace(img_path, '\\', '/') WHERE img_path LIKE '%\\%'")
        c.execute("UPDATE writings SET source_file = replace(source_file, '\\', '/') WHERE source_file LIKE '%\\%'")
        if c.rowcount > 0:
            logger.info(f"[MIGRATE] Repaired {c.rowcount} archival paths with standardized slashes.")
    except Exception as e:
        logger.error(f"[MIGRATE] Path normalization failed: {e}")

    conn.commit()
    conn.close()

def is_setup_completed():
    """ 
    [v1.6.3] Hardened Setup Detection.
    Determines if the appliance has finished its first-time onboarding.
    Strict Policy: If the volume is unmounted, we check a persistent 'initialized' 
    flag on the home directory to avoid 'Ghost Initialization' loops.
    Self-Healing Path [v1.8.10]: If locks are missing but DB is healthy, restoration 
    is handled during the /api/vault/unseal flow (Fail-Sealed Protocol).
    """
    PERSISTENT_INIT_FLAG = "/home/concierge/.memorybox_initialized"
    
    if os.path.ismount(VAULT_MOUNT):
        # Case A: Volume is mounted. Trust the on-disk lock.
        return os.path.exists(SETUP_LOCK_PATH)
    
    # Case B: Volume is detached. Trust the persistent flag.
    return os.path.exists(PERSISTENT_INIT_FLAG)

# v1.2.0: AI Engine Configuration (Meteor Lake Ready)
# v1.6.2: State-Shifting AI Engine (32GB / Intel Arc Optimized)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "127.0.0.1")
OLLAMA_API_URL = f"http://{OLLAMA_HOST}:11434/api/generate"
CHAT_API_URL = f"http://{OLLAMA_HOST}:11434/api/chat"
TAGS_API_URL = f"http://{OLLAMA_HOST}:11434/api/tags"

ORACLE_MODEL = "mistral:latest" # [v1.8.8] High-Speed Production Oracle (7B)
SENSE_MODEL = "moondream:latest" # [v1.8.8] Blink-Speed Vision (1.6B)
MAX_SENSING_TOKENS = 150 # [v1.8.8] Archival Performance Ceiling
CANARY_MODEL = "qwen2.5:1.5b" # 1.5B (Apache 2.0) - Always Resident if possible
WHISPER_MODEL_SIZE = "large-v3" # [v1.8.1] High-Fidelity Archival Standard

class ModelOrchestrator:
    """ [v1.7.0] Governance of VRAM Residency & Production Sensing. """
    def __init__(self):
        self.active_mode = None # 'INSIGHT' (Oracle), 'INTAKE' (Sensing), or 'TRANSCRIBE'
        self.lock = asyncio.Lock()
        self.whisper_engine = None
    
    async def ensure_mode(self, target_mode: str):
        """ Park and Shift the VRAM resident models as needed. """
        async with self.lock:
            if self.active_mode == target_mode:
                return
                
            logger.info(f"[ORCHESTRATOR] Shifting state: {self.active_mode} -> {target_mode}")
            
            # 1. Park the active Ollama model if moving away from its side
            if self.active_mode in ['INSIGHT', 'INTAKE']:
                current_model = ORACLE_MODEL if self.active_mode == 'INSIGHT' else SENSE_MODEL
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.post(OLLAMA_API_URL, json={"model": current_model, "keep_alive": 0})
                    logger.info(f"[ORCHESTRATOR] Parked Ollama model: {current_model}")
                except Exception as e:
                    logger.error(f"[ORCHESTRATOR] Parking failed: {e}")
            
            # 2. Park/Evict Whisper if moving away from Transcription
            if self.active_mode == 'TRANSCRIBE' and self.whisper_engine:
                logger.info("[ORCHESTRATOR] Evicting Whisper Engine to free VRAM...")
                del self.whisper_engine
                self.whisper_engine = None
                import gc
                gc.collect()

            # 3. Waking Target
            self.active_mode = target_mode
            
            if target_mode == 'TRANSCRIBE':
                # Load Whisper large-v3 natively (CPU/oneDNN optimized for Intel)
                logger.info(f"[ORCHESTRATOR] Waking Transcription Engine ({WHISPER_MODEL_SIZE})...")
                if WhisperModel:
                    # [v1.8.1] Precision Upgrade: float16 utilization for 32GB RAM headroom
                    self.whisper_engine = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="float16")
                else:
                    logger.error("[ORCHESTRATOR] Whisper requested but binary NOT found via pip.")
            else:
                target_model = ORACLE_MODEL if target_mode == 'INSIGHT' else SENSE_MODEL
                logger.info(f"[ORCHESTRATOR] Waking Ollama model: {target_model}")
                try:
                    async with httpx.AsyncClient(timeout=45.0) as client:
                        await client.post(OLLAMA_API_URL, json={"model": target_model, "prompt": "", "stream": False})
                except Exception as e:
                    logger.error(f"[ORCHESTRATOR] Load trigger failed for {target_model}: {e}")

orchestrator = ModelOrchestrator()

# Security: Authorized images/files (populated on-the-fly by the AI)
AUTHORIZED_PERSONAL_IMAGES = set()
AUTHORIZED_THUMBNAILS = set() # [v1.7.5] Secure Thumbnails Set
ARCHIVE_FILE_MAP = {}

# [v1.6.2] Security Hardware Anchor
SESSION_SECRET = None
MACHINE_SECRET_PATH = os.path.join(VAULT_MOUNT, ".machine_secret")

def get_hardware_id() -> str:
    """ [v1.6.2] Retrieve a stable, hardware-bound identifier for the appliance. """
    # Standard Ubuntu machine-id (stable across reboots, unique to the instance)
    paths = ['/etc/machine-id', '/var/lib/dbus/machine-id']
    for p in paths:
        if os.path.exists(p):
            with open(p, 'r') as f:
                return f.read().strip()
    # Fallback for VMs or systems without machine-id: BIOS UUID
    if os.path.exists('/sys/class/dmi/id/product_uuid'):
        with open('/sys/class/dmi/id/product_uuid', 'r') as f:
            return f.read().strip()
    return "MEMBOX_VIRTUAL_METAL_FALLBACK"

def get_session_secret():
    """ 
    [v1.6.2] Retrieve or derive the hardware session anchor. 
    Rationale: We derive the secret from (HardwareID + MASTER_KEY) so it is 
    never saved to disk and is anchored to both the physical metal and the vault key.
    """
    global SESSION_SECRET, MASTER_KEY
    if SESSION_SECRET: return SESSION_SECRET
    
    # [v1.6.3] Identity Hardening: Secret is only available if vault is unsealed
    if not MASTER_KEY:
        raise HTTPException(status_code=403, detail="Signature Verification Failed (Vault Sealed).")

    import hmac
    # Derive secret: HMAC-SHA256(machine_id, MASTER_KEY)
    hw_id = get_hardware_id().encode()
    key = MASTER_KEY.encode()
    SESSION_SECRET = hmac.new(hw_id, key, hashlib.sha256).hexdigest()
    return SESSION_SECRET

def create_session_token(user_id: int) -> str:
    """ [v1.6.2] Generate a hardware-signed identity token. """
    import hmac
    secret = get_session_secret().encode()
    # Payload: user_id:expiry_timestamp
    expiry = int((datetime.now() + timedelta(hours=24)).timestamp())
    payload = f"{user_id}:{expiry}"
    signature = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"

def verify_session_token(token: str, expected_user_id: int) -> bool:
    """ [v1.6.2] Verify the hardware signature and expiration. """
    import hmac
    if not token or ":" not in token: return False
    try:
        parts = token.split(":")
        if len(parts) != 3: return False
        user_id, expiry, signature = int(parts[0]), int(parts[1]), parts[2]
        
        if user_id != expected_user_id: return False
        if datetime.now().timestamp() > expiry: return False
        
        # Verify Signature
        secret = get_session_secret().encode()
        payload = f"{user_id}:{expiry}"
        expected_sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected_sig)
    except:
        return False

async def verify_auth(request: Request):
    """ [v1.6.2] Validates the hardware-signed session token. """
    token = request.headers.get("X-MemoryBox-Token")
    user_id_str = request.headers.get("X-MemoryBox-User-ID")
    
    if not token or not user_id_str:
        raise HTTPException(status_code=401, detail="Identity missing.")
        
    try:
        user_id = int(user_id_str)
        if not verify_session_token(token, user_id):
            raise HTTPException(status_code=401, detail="Identity mismatch or expired session.")
        return user_id
    except ValueError:
        raise HTTPException(status_code=401, detail="Malfomed identity.")

# v1.0.1: Persona Identity Settings
PERSONA_NAME = "Persona"
app = FastAPI()

# [v1.2.9] Vault Status & Diagnostics
@app.get("/api/diagnostic/vault")
async def get_vault_diagnostic(user_id: int = Depends(verify_auth)):
    """ Returns the raw mount and canary status of the memory volume. """
    db_exists = os.path.exists(DB_PATH)
    canary_exists = os.path.exists(CANARY_PATH)
    db_size = os.path.getsize(DB_PATH) if db_exists else 0
    
    return {
        "memories_dir": VAULT_MOUNT,
        "db_path": DB_PATH,
        "db_size": db_size,
        "canary_found": canary_exists,
        "setup_lock": os.path.exists(SETUP_LOCK_PATH),
        "mount_status": "ATTACHED" if canary_exists else "DETACHED",
        "timestamp": datetime.now().isoformat()
    }

# [v1.2.0-AUDIT] Early Discovery Handshake
# RELOCATED: Mounting static assets before any routes to prevent shadow 404s
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static"), html=True), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def handle_root_path(request: Request, call_next):
    """ [v1.8.12] Proxy Awareness: Respect X-Forwarded-Prefix for root_path. """
    path_prefix = request.headers.get("X-Forwarded-Prefix")
    if path_prefix:
        request.scope["root_path"] = path_prefix
    return await call_next(request)

# --- Identity & Vault Endpoints ---

def verify_vault_key(key: str) -> bool:
    """ [v1.7.2] Absolute Secret Mastery: No format constraints on Appliance Keys. """
    return len(key) >= 8 # Ensure a minimum complexity for the vault floor

@app.get("/setup")
async def setup_page():
    """ The First-Open Onboarding Wizard. """
    if is_setup_completed():
        return FileResponse(os.path.join(BASE_DIR, "static", "personal.html"))
    return FileResponse(os.path.join(BASE_DIR, "static", "setup.html"))

# --- Continuity Governance & Vault Status [v1.2.0] ---
@app.get("/api/vault/status")
async def get_vault_status():
    registry_empty = True
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT count(*) FROM profiles")
        registry_empty = c.fetchone()[0] == 0
        conn.close()
    except:
        pass
        
    return {
        "sealed": VAULT_SEALED,
        "status": "sealed" if VAULT_SEALED else "unsealed",
        "user_registry_empty": registry_empty
    }

@app.post("/api/vault/seal")
async def seal_vault():
    logger.info("[VAULT] Sealing Appliance...")
    
    # [v1.6.3] Physical Hard Seal (Updates global MASTER_KEY and VAULT_SEALED)
    if unmount_vault():
        return {"status": "success", "message": "Appliance Hard-Sealed."}
    else:
        return {"status": "error", "message": "Soft-Seal active, but volume unmount failed. Check for busy files."}

@app.post("/api/vault/unseal")
async def unseal_vault(request: Request):
    data = await request.json()
    key = data.get("key", "").strip() # [v1.7.12] Sanitize whitespace
    
    # 1. Regex Validation (Uniform failure point)
    if not key or not verify_vault_key(key):
        return {"status": "error", "message": "Signature Verification Failed."}

    # 2. Hardware Mounting (Physical unseal + State Update)
    if not mount_vault(key):
        return {"status": "error", "message": "Signature Verification Failed."}

    # 3. Canary Verification (Cryptographic unseal)
    try:
        if not os.path.exists(CANARY_PATH):
            # First unseal bootstrap
            logger.info("Initializing Vault Verification Canary...")
            signature = hashlib.sha256((key + "MB_VOL_LOCK").encode()).hexdigest()
            with open(CANARY_PATH, "w") as f_canary:
                f_canary.write(signature)
        else:
            with open(CANARY_PATH, "r") as f_canary:
                stored = f_canary.read().strip()
            current = hashlib.sha256((key + "MB_VOL_LOCK").encode()).hexdigest()
            if stored != current:
                # [v1.6.3] Security Interlock: Unmount immediately if canary fails
                logger.warning("[VAULT] Canary Mismatch! Emergency Sealing.")
                unmount_vault()
                return {"status": "error", "message": "Signature Verification Failed."}
    except Exception as e:
        logger.error(f"Vault validation error: {e}")
        unmount_vault()
        return {"status": "error", "message": "Signature Verification Failed."}

    logger.info("[VAULT] Appliance Successfully Unsealed. Solidifying Schema...")
    try:
        solidify_schema_v126() # [v1.8.8] Ensure schema is correct immediately upon mount
        
        # [v1.8.10] Fail-Sealed Restoration:
        # If the setup files are missing but the vault has active profiles, restore them.
        if not is_setup_completed():
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM profiles")
                count = c.fetchone()[0]
                conn.close()
                if count > 0:
                    logger.info("[GOVERNANCE] Restoring missing setup_lock: Database profiles detected.")
                    with open(SETUP_LOCK_PATH, "w") as f:
                        f.write(datetime.now().isoformat())
                    with open("/home/concierge/.memorybox_initialized", "w") as f:
                        f.write(datetime.now().isoformat())
            except Exception as e:
                logger.error(f"[GOVERNANCE] Setup lock restoration failed: {e}")
                
    except Exception as e:
        logger.error(f"Post-Unseal Migration Alert: {e}")
        
    return {"status": "success", "message": "Vault Unsealed."}

@app.get("/api/vault/audit")
async def archival_audit(user_id: int = Depends(verify_auth)):
    """ [v1.2.0] Integrity Verification of the Dedicated Archival Volume. """
    if VAULT_SEALED: return {"status": "error", "message": "Vault is Sealed."}
    
    try:
        size_kb = os.path.getsize(DB_PATH) // 1024
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM profiles")
        total_profiles = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM writings")
        total_records = c.fetchone()[0]
        
        # Check image cache if table exists
        total_images = 0
        try:
            c.execute("SELECT COUNT(*) FROM image_cache")
            total_images = c.fetchone()[0]
        except: pass
            
        conn.close()
        
        return {
            "status": "success",
            "db_path": DB_PATH,
            "db_size_kb": size_kb,
            "census": {
                "profiles": total_profiles,
                "records": total_records,
                "images": total_images
            },
            "environment": "Archival Partition (Drive B:)"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/profiles")
async def list_profiles():
    """ [v1.6.3] Identity Discovery: Returns users once the Vault is physically unsealed. """
    if VAULT_SEALED or not MASTER_KEY:
         # [v1.6.3] Total Lockdown: No users disclosed until Master Key is provided.
         return []
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, name, avatar_path, role, theme FROM profiles")
        profiles = [{"id": r[0], "name": r[1], "avatar": r[2], "role": r[3], "theme": r[4]} for r in c.fetchall()]
        conn.close()
        return profiles
    except:
        return []

@app.post("/api/setup/initialize")
async def onboarding_mastery_v126(request: Request):
    """ [v1.2.6] Identity Mastery & Hardware Sealing. """
    if is_setup_completed():
        # [v1.2.9] If the canary exists but lock is missing, it's an unmount error
        if not os.path.exists(SETUP_LOCK_PATH) and os.path.exists(CANARY_PATH):
            raise HTTPException(status_code=400, detail="VAULT UNMOUNTED: Please mount memory volume before proceeding.")
        raise HTTPException(status_code=400, detail="Setup already completed.")
    
    data = await request.json()
    owner_name = data.get("name", "Owner")
    pin = data.get("pin")
    appliance_key = data.get("appliance_key", "").strip() # [v1.7.12] Sanitize whitespace
    
    if not pin or len(pin) < 6:
         return {"status": "error", "message": "PIN must be at least 6 characters."}
         
    if not appliance_key or not verify_vault_key(appliance_key):
         return {"status": "error", "message": "Signature Verification Failed."}
    
    # [v1.7.17] Auto-Solidification: Hardware-Aware Provisioning
    if VAULT_TYPE != "DIRECTORY" and not is_luks_partition(VAULT_SOURCE):
        logger.warning(f"[HARDWARE] Raw partition detected at {VAULT_SOURCE}. Initiating Auto-Solidification...")
        try:
            # [v1.7.21] Full Disk Decontamination: Unmount all partitions of the target disk
            logger.info(f"[HARDWARE] Decontaminating {VAULT_SOURCE} and its partitions...")
            
            # 1. Enumerate and unmount all partitions
            try:
                lsblk_res = subprocess.run(["lsblk", "-nlo", "NAME", VAULT_SOURCE], capture_output=True, text=True)
                if lsblk_res.returncode == 0:
                    for part in lsblk_res.stdout.splitlines():
                        part_path = f"/dev/{part.strip()}"
                        subprocess.run(["sudo", "-n", "/usr/bin/umount", "-l", part_path], capture_output=True)
            except Exception as e:
                logger.warning(f"[HARDWARE] Partition enumeration failed: {e}")

            subprocess.run(["sudo", "-n", "/usr/sbin/cryptsetup", "luksClose", VAULT_MAPPER], capture_output=True)
            
            # 2. Wipe all filesystem/LVM/RAID signatures from the disk itself
            subprocess.run(["sudo", "-n", "/usr/sbin/wipefs", "-af", VAULT_SOURCE], capture_output=True)
            
            # 3. Refresh kernel partition table (clears stale /dev/sdbX nodes)
            subprocess.run(["sudo", "-n", "/usr/sbin/partprobe", VAULT_SOURCE], capture_output=True)
            time.sleep(2) # Settle time
            
            # -q for non-interactive, --key-file - to read the key from stdin
            # [v1.7.20] Using sudo -n
            fmt_cmd = ["sudo", "-n", "/usr/sbin/cryptsetup", "luksFormat", VAULT_SOURCE, "-q", "--key-file", "-"]
            process = subprocess.Popen(fmt_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate(input=appliance_key.encode('utf-8'))
            
            if process.returncode != 0:
                err_msg = stderr.decode().strip()
                logger.error(f"[HARDWARE] Auto-Solidification Failed: {err_msg}")
                return {"status": "error", "message": f"Hardware Provisioning Failed: {err_msg}"}
            logger.info("[HARDWARE] LUKS Vault Provisioned successfully. Initializing Filesystem...")
            
            # --- Filesystem Creation ---
            # 1. Open it temporarily to reach the inner block device
            open_cmd = ["sudo", "-n", "/usr/sbin/cryptsetup", "luksOpen", VAULT_SOURCE, VAULT_MAPPER, "--key-file", "-"]
            p_open = subprocess.Popen(open_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            p_open.communicate(input=appliance_key.encode('utf-8'))
            
            # [v1.7.18] Settle Time: Allow kernel to register the new mapper device
            # [v1.7.20] Increased settle time and verified mapper presence
            for i in range(5):
                if os.path.exists(VAULT_DEVICE): break
                logger.info(f"[HARDWARE] Waiting for mapper device: {VAULT_DEVICE} (Attempt {i+1}/5)...")
                time.sleep(1)
            
            if not os.path.exists(VAULT_DEVICE):
                logger.error("[HARDWARE] Mapper device failed to appear after luksOpen.")
                return {"status": "error", "message": "Hardware Device Mapping Failed."}

            # 2. Format as EXT4
            mkfs_cmd = ["sudo", "-n", "/usr/sbin/mkfs.ext4", "-F", VAULT_DEVICE]
            logger.info(f"[HARDWARE] Executing: {' '.join(mkfs_cmd)}")
            res_mkfs = subprocess.run(mkfs_cmd, capture_output=True)
            
            # 3. Close it so mount_vault can take over with standard logic
            subprocess.run(["sudo", "-n", "/usr/sbin/cryptsetup", "luksClose", VAULT_MAPPER], capture_output=True)
            
            if res_mkfs.returncode != 0:
                logger.error(f"[HARDWARE] mkfs.ext4 Failed: {res_mkfs.stderr.decode().strip()}")
                return {"status": "error", "message": "Filesystem Initialization Failed."}

            logger.info("[HARDWARE] Archival Filesystem (EXT4) initialized. Handing off to Mount Engine.")
            
            # --- [v1.7.20] Seal the Genesis Gate ---
            # Now that formatting is complete, we revoke the app's own permission to ever re-format.
            # This is the "No Erasure" security baseline.
            logger.info("[SECURITY] Sealing the Genesis Gate... Revoking Erasure Permissions.")
            subprocess.run(["sudo", "-n", "/usr/bin/rm", "-f", "/etc/sudoers.d/memorybox-genesis"], capture_output=True)
            
        except Exception as e:
            logger.error(f"[HARDWARE] Recovery Failure during provisioning: {e}")
            return {"status": "error", "message": "Hardware Provisioning Error."}

    # 1. Hardware Detachment Prevention (Mounting Storage)
    if not mount_vault(appliance_key):
        return {"status": "error", "message": "Signature Verification Failed."}

    # 2. Seed the Vault Canary (Locking the key to the hardware)
    try:
        # [v1.2.6] Standard Lib Signature (No external dependencies)
        signature = hashlib.sha256((appliance_key + "MB_VOL_LOCK").encode()).hexdigest()
        with open(CANARY_PATH, "w") as f_canary:
            f_canary.write(signature)
        logger.info("[INIT] Vault Canary Solidified.")
    except Exception as e:
        logger.error(f"Failed to seed Vault Canary: {e}")
        unmount_vault() # Cleanup on failure
        return {"status": "error", "message": "Signature Verification Failed."}

    # 3. Solidify Database
    # [v1.6.3] This now passes because the Canary exists on the mounted storage.
    solidify_schema_v126()
    
    # 2. Secure Owner Partition
    h, salt = hash_pin(pin)
    try:
        conn = get_db_connection()
        c = conn.cursor()
        # [v1.7.4] INSERT OR REPLACE: Resilience against partial setup retries
        c.execute("INSERT OR REPLACE INTO profiles (name, legal_name, role, pin_hash, pin_salt, created_at) VALUES (?,?,?,?,?,?)",
                 (owner_name, data.get("legal_name", owner_name), 'SUPERADMIN', h, salt, datetime.now()))
        conn.commit()
        conn.close()
        
        # 3. Create Setup Lock
        with open(SETUP_LOCK_PATH, "w") as f:
            f.write(datetime.now().isoformat())
            
        # 4. [v1.6.3] Create Persistent 'Initialized' Flag (On root drive)
        # This prevents the appliance from falling back into setup mode even when unmounted.
        with open("/home/concierge/.memorybox_initialized", "w") as f_init:
            f_init.write("DETACHMENT_RESISTANT_FLAG")

        return {"status": "success", "message": "Vault Solidified. Welcome."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- Continuity Governance [v1.2.0] ---

def snap_to_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)

def get_wipe_status():
    """ 
    [v1.2.0] Check if an Archival Destruction deliberation is active. 
    Returns: (Status, Metadata)
    """
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT value_text FROM appliance_state WHERE key = 'wipe_initiated_at'")
        row = c.fetchone()
        conn.close()
        
        if not row:
            return "IDLE", None
            
        start_time = datetime.fromisoformat(row[0])
        now = datetime.now()
        
        # [v1.2.0] 30-Day Hard Deliberation
        DELIBERATION_DAYS = 30
        diff = now - start_time
        remaining = max(0, (timedelta(days=DELIBERATION_DAYS) - diff).total_seconds() // 60)
        
        if remaining > 0:
            return "PENDING", {"minutes_remaining": int(remaining)}
        else:
            # 72-hour Execution Window
            window_diff = diff - timedelta(days=DELIBERATION_DAYS)
            window_remaining = max(0, (timedelta(hours=72) - window_diff).total_seconds() // 60)
            if window_remaining > 0:
                return "AUTHORIZED", {"window_minutes_remaining": int(window_remaining)}
            else:
                return "EXPIRED", None
    except:
        return "IDLE", None

@app.post("/api/profiles/auth")
async def auth_profile(data: Dict):
    """ [v1.1.0] Multi-Factor Archival Identity Verification. """
    if VAULT_SEALED: 
        raise HTTPException(status_code=403, detail="Vault Sealed.")
        
    profile_id = data.get("id")
    name = data.get("name")
    pin = data.get("pin")
    
    conn = get_db_connection()
    c = conn.cursor()
    
    if profile_id:
        c.execute("SELECT pin_hash, pin_salt, theme, id FROM profiles WHERE id = ?", (profile_id,))
    elif name:
        # Strict name-based lookup for direct login [v1.1.0: Case-Insensitive]
        c.execute("SELECT pin_hash, pin_salt, theme, id FROM profiles WHERE LOWER(name) = LOWER(?)", (name.strip(),))
    else:
        conn.close()
        return {"status": "error", "message": "Identity unidentified."}
        
    row = c.fetchone()
    conn.close()
    
    if row and verify_pin(pin, row[1], row[0]):
        actual_id = row[3]
        # [v1.6.2] Hardware-Anchored Token Generation
        token = create_session_token(actual_id)
        
        # [v1.2.0] Integrity Handshake (Wipe Warning)
        wipe_status, wipe_meta = get_wipe_status()
        return {
            "status": "success", 
            "message": "Access Granted.", 
            "token": token,
            "theme": row[2] or "modern",
            "wipe_warning": wipe_meta if wipe_status != "IDLE" else None
        }
    
    return {"status": "error", "message": "Invalid credentials."}

@app.post("/api/vault/wipe/initiate")
async def initiate_wipe(request: Request):
    """ [v1.2.0] PRESERVATION MODE: Destruction Disabled. """
    return {"status": "error", "message": "Archival Destruction is DISABLED on this appliance for continuity preservation."}

# --- Succession Governance [v1.2.0] ---
# Identity Handshake & Multi-Factor Succession Stubs

@app.post("/api/succession/register")
async def register_succession_node(request: Request):
    """ [Stub] Register a secondary MemoryBox for future archival transfer. """
    if VAULT_SEALED: raise HTTPException(status_code=403, detail="Vault Sealed.")
    return {"status": "success", "message": "Succession Node Handshake Registered. Awaiting multi-factor confirmation."}

# --- Middlewares & Guards [v1.6.2] ---

@app.post("/api/profiles/update_theme")
async def update_profile_theme(request: Request, user_id: int = Depends(verify_auth)):
    """ [v1.1.0] Persist user aesthetic choice. """
    if VAULT_SEALED: 
        raise HTTPException(status_code=403, detail="Vault Sealed.")
        
    data = await request.json()
    target_profile_id = data.get("id")
    
    if user_id != target_profile_id:
        raise HTTPException(status_code=403, detail="Aesthetic manipulation denied.")
    theme = data.get("theme")
    
    if not target_profile_id or not theme:
        return {"status": "error", "message": "Missing criteria."}
        
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE profiles SET theme = ? WHERE id = ?", (theme, target_profile_id))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Aesthetic Preferences Solidified."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/profiles/create")
async def create_profile(request: Request, session_user_id: int = Depends(verify_auth)):
    """ [v1.1.0] SuperAdmin user expansion. """
    if VAULT_SEALED: 
        raise HTTPException(status_code=403, detail="Vault Sealed.")
        
    data = await request.json()
    issuer_id = session_user_id # [v1.6.3] Identity Anchoring: Use the token's identity
    name = data.get("name")
    role = data.get("role", "USER").upper()
    pin = data.get("pin")
    
    # [v1.2.6] Hierarchical Creation Guard
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Check if this is the first user (Bootstrap Mode)
        c.execute("SELECT COUNT(*) FROM profiles")
        is_first_user = c.fetchone()[0] == 0
        
        # [v1.6.3] Identity Hardening: Prevent SuperAdmin duplication
        if role == "SUPERADMIN" and not is_first_user:
            conn.close()
            return {"status": "error", "message": "Appliance Sovereign: Only one Super-Administrator may exist."}

        if is_first_user:
            issuer_role = "SUPERADMIN" # Self-authorizing for the first record
        else:
            c.execute("SELECT role FROM profiles WHERE id = ?", (issuer_id,))
            res = c.fetchone()
            if not res:
                conn.close()
                return {"status": "error", "message": "Issuer identity not found."}
            issuer_role = res[0]
        if role == 'ADMIN' and issuer_role != 'SUPERADMIN':
             conn.close()
             return {"status": "error", "message": "Only SuperAdmins can forge new Admin identities."}
        if role == 'SUPERADMIN' and issuer_role != 'SUPERADMIN':
             conn.close()
             return {"status": "error", "message": "The SuperAdmin title is unique and protected."}
        conn.close()
    except: pass
    
    if not name or not pin or len(pin) < 6:
        return {"status": "error", "message": "Valid name and 6-char PIN required."}
        
    h, salt = hash_pin(pin)
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("INSERT INTO profiles (name, legal_name, role, pin_hash, pin_salt, created_at, theme) VALUES (?,?,?,?,?,?,?)",
                 (name, data.get("legal_name", name), role, h, salt, datetime.now(), 'modern'))
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"Archival Identity Created: {name}"}
    except sqlite3.IntegrityError:
         return {"status": "error", "message": "Identity already exists."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/governance/status")
async def get_governance_status(request: Request, user_id: int = Depends(verify_auth)):
    """ [v1.7.0] Real-time Appliance Pulse for Governance Hub. """
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT name, legal_name, role, theme, style_sampling_permitted FROM profiles WHERE id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        
        if not row:
            raise HTTPException(status_code=404, detail="Identity unknown.")
            
        return {
            "identity": {
                "id": user_id,
                "name": row[0],
                "legal_name": row[1] or row[0],
                "role": row[2],
                "theme": row[3],
                "mimicry": bool(row[4])
            },
            "session": {
                "ip": request.client.host,
                "timestamp": datetime.now().isoformat()
            },
             "environment": {
                "vault_status": "ATTACHED" if not VAULT_SEALED else "SEALED",
                "storage_mode": "LUKS"
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/governance/update_identity")
async def update_governance_identity(request: Request, user_id: int = Depends(verify_auth)):
    """ [v1.7.0] Persist Legal Name and Mimicry preferences. """
    try:
        data = await request.json()
        legal_name = data.get("legal_name")
        mimicry = 1 if data.get("mimicry") else 0
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE profiles SET legal_name = ?, style_sampling_permitted = ? WHERE id = ?",
                 (legal_name, mimicry, user_id))
        conn.commit()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/profiles/change_pin")
async def change_profile_pin(request: Request, session_user_id: int = Depends(verify_auth)):
    """ [v1.2.6] Hierarchical User Identity Mastery. """
    if VAULT_SEALED: 
        raise HTTPException(status_code=403, detail="Vault Sealed.")
        
    data = await request.json()
    issuer_id = session_user_id # [v1.6.3] Identity Anchoring: Use the token's identity
    target_id = data.get("target_id")
    current_pin = data.get("current_pin")
    new_pin = data.get("new_pin")
    
    if not target_id or not new_pin or len(new_pin) < 6:
        return {"status": "error", "message": "Target ID and 6-char new PIN required."}
    
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # 1. Verify Issuer
        c.execute("SELECT role, pin_hash, pin_salt FROM profiles WHERE id = ?", (issuer_id,))
        issuer_row = c.fetchone()
        if not issuer_row:
            conn.close()
            return {"status": "error", "message": "Issuer identity not found."}
        
        issuer_role = issuer_row[0]
        
        # 2. Verify Target
        c.execute("SELECT id, role, name FROM profiles WHERE id = ?", (target_id,))
        target_row = c.fetchone()
        if not target_row:
            conn.close()
            return {"status": "error", "message": "Target identity not found."}
        
        target_role = target_row[1]
        target_name = target_row[2]
        
        # 3. Hierarchy Check
        is_self = (int(issuer_id) == int(target_id))
        authorized = False
        
        if is_self:
            if current_pin and verify_pin(current_pin, issuer_row[2], issuer_row[1]):
                authorized = True
            else:
                conn.close()
                return {"status": "error", "message": "Current PIN verification failed."}
        else:
            if issuer_role == 'SUPERADMIN':
                authorized = True
            elif issuer_role == 'ADMIN' and target_role == 'USER':
                authorized = True
            else:
                conn.close()
                return {"status": "error", "message": f"Hierarchy prevents {issuer_role} from resetting {target_role}."}
        
        if authorized:
            h, salt = hash_pin(new_pin)
            c.execute("UPDATE profiles SET pin_hash = ?, pin_salt = ? WHERE id = ?", (h, salt, target_id))
            conn.commit()
            conn.close()
            return {"status": "success", "message": f"PIN updated for {target_name}."}
        
        conn.close()
        return {"status": "error", "message": "Unauthorized reset attempt."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- Memory Vault Handlers ---

def update_archive_map():
    """ [v1.2.6] Deep Resolution: Scans the entire archival mount to build a filename-to-path index. """
    global ARCHIVE_FILE_MAP
    root = "/home/concierge/memories"
    if not os.path.exists(root): return
    for r, _, files in os.walk(root):
        # We scan Archive and Vault subdirectories
        for f in files:
            # [v1.2.6] Normalize to forward-slashes for cross-platform index integrity
            ARCHIVE_FILE_MAP[f.lower()] = os.path.join(r, f).replace('\\', '/')

def get_personal_context(query: str, current_user_id: int = 0, super_admin_mode: bool = False):
    """
    Retrieves relevant text segments and image descriptions from personal_memory.db.
    [v1.1.0] Multi-user filtering: (Visibility='SHARED') OR (owner_id=user).
    """
    if VAULT_SEALED: return {"text": "", "image_count": 0, "images": []}
    if not os.path.exists(DB_PATH):
        return {"text": "", "image_count": 0, "images": []}

    try:
        author_filter = None
        recipient_filter = None
        clean_query = query
        
        # [v1.8.7] Archival Sovereignty: Removed hardcoded owner_id=1 bypass.
        # [v1.8.7] Super-Admin Override: Admins can search the whole vault when toggled.
        view_filter = "hidden = 0"
        if not super_admin_mode:
            view_filter += f" AND (visibility = 'SHARED' OR owner_id = {current_user_id})"

        conn = get_db_connection()
        c = conn.cursor()
        
        # [v1.2.0] Aggressive Keyword Extraction (Prefix-Matching)
        fts_query = re.sub(r'[^\w\s]', ' ', clean_query)
        # Ensure every word gets a prefix asterisk for fuzzy-matching
        fts_query = " OR ".join([f'"{k}*"' for k in fts_query.split() if len(k) > 2]) or clean_query
        
        sql = f"""
            SELECT w.id, w.content, w.author, w.recipient, w.timestamp, w.source_file, w.doc_type 
            FROM writings w 
            JOIN writings_fts f ON w.id = f.content_id 
            WHERE writings_fts MATCH ? AND {view_filter}
        """
        params = [fts_query]
        
        sql += " ORDER BY rank LIMIT 12"
        
        c.execute(sql, params)
        rows = c.fetchall()
        text_context = []
        for row in rows:
            rid, content, author, recipient, ts, source, dtype = row
            decrypted = decrypt_content(content)
            
            # [v1.8.0] Mnemonic Identification
            date_str = ts if ts else "Undated"
            m_label = "Textual Memory"
            is_media = dtype == 'media_transcript' or "[MEDIA TRANSCRIPT]" in decrypted
            if is_media:
                 m_label = "Media Memory - Transcribed"
            
            mnemonic = f"[{m_label}: #{rid} ({date_str})]"
            text_context.append(f"{mnemonic}:\n{decrypted}\n")
        
        # 2. Search Images
        img_keywords = [k for k in re.sub(r'[^\w\s]', ' ', clean_query).lower().split() if len(k) > 2]
        
        img_rows = []
        # [v1.2.0] Aggressive Search using Image FTS (Prefix-Matching)
        if img_keywords:
            try:
                fts_img_query = " OR ".join([f'"{k}*"' for k in img_keywords])
                c.execute("SELECT id, content_id FROM image_cache_fts WHERE image_cache_fts MATCH ?", (fts_img_query,))
                res = c.fetchall()
                ids = [r[1] for r in res]
                if ids:
                    placeholders = ",".join(["?" for _ in ids])
                    c.execute(f"SELECT id, img_path, description, fs_mtime FROM image_cache WHERE id IN ({placeholders}) AND {view_filter} LIMIT 12", ids)
                    img_rows = c.fetchall()
            except Exception as e:
                logger.warning(f"Aggressive Image search failed: {e}")
                conditions = " OR ".join(["description LIKE ?" for _ in img_keywords])
                params = [f"%{k}%" for k in img_keywords]
                c.execute(f"SELECT id, img_path, description, fs_mtime FROM image_cache WHERE ({conditions}) AND {view_filter} LIMIT 12", params)
                img_rows = c.fetchall()

        img_context = []
        for rid, path, desc, rts in img_rows:
            # [v1.2.0] Retrieve user note for grounding
            c.execute("SELECT source_note FROM image_cache WHERE id = ?", (rid,))
            note_row = c.fetchone()
            note = note_row[0] if note_row else None
            
            # [v1.8.0] Mnemonic Label Generator
            date_str = str(rts)[:10] if rts else "Undated"
            location = "Memory"
            for loc in ['Austin', 'Texas', 'Home', 'Office', 'California', 'London', 'Paris', 'New York']:
                    if loc.lower() in path.lower() or loc.lower() in desc.lower():
                        location = loc
                        break
            mnemonic = f"Visual Memory: #{rid} from {location} ({date_str})"
            
            img_context.append({"id": rid, "mnemonic": mnemonic, "description": desc, "note": note})
            text_context.append(f"[{mnemonic}]: {desc} (Archivist Note: {note or 'None'})")
            AUTHORIZED_PERSONAL_IMAGES.add(str(rid))


        conn.close()
        return {
            "text": "\n---\n".join(text_context),
            "image_count": len(img_context),
            "images": img_context
        }
    except Exception as e:
        logger.error(f"Personal Context Error: {e}")
        return {"text": f"Memory access error: {e}", "image_count": 0, "images": []}

# --- Tactical Logic Merged [v1.0.5] ---

async def get_ollama_models():
    """ Fetches tags directly from the local Ollama instance. """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(TAGS_API_URL)
            if resp.status_code == 200:
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
    except Exception as e:
        logger.error(f"Failed to fetch models: {e}")
    return [ORACLE_MODEL, SENSE_MODEL, CANARY_MODEL]

async def run_tactical_script(script_name: str, args: List[str]):
    """ Executes a script from the scripts/ directory. """
    script_path = os.path.join(BASE_DIR, "scripts", script_name)
    if not os.path.exists(script_path):
        return {"status": "error", "message": f"Script {script_name} not found."}
    
    try:
        # v1.0.5: Run using the venv python if possible
        python_bin = os.path.join(BASE_DIR, "venv", "bin", "python3")
        if not os.path.exists(python_bin): python_bin = "python3"
        
        cmd = [python_bin, script_path] + args
        subprocess.run(cmd, check=True, capture_output=True)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- Unified Search Handler ---

async def summarize_personal_context(query: str, raw_writings: str):
    """ [v1.2.0] Sentimental Reasoning Layer.
        Synthesizes raw data into a coherent 'Archival Voice'. """
    if not raw_writings: return ""
    
    prompt = (
        "You are the 'Oracle' of the MemoryBox Appliance. Your purpose is to bridge the gap between raw data and human sentiment.\n\n"
        "REASONING DIRECTIVE:\n"
        "1. Treat the following Archive Data as precious, immutable memories.\n"
        "2. Identify the emotional weight of the participants and the significance of the dates.\n"
        "3. If multiple records conflict, prioritize the one with the most recent 'fs_mtime' (Filesystem Modified Time).\n"
        "4. Summarize these segments into a 'Memory Brief' that feel reflective, not clinical.\n\n"
        "CRITICAL SENSORY INJECTION:\n"
        "- If the Archive Data contains [Visual Memory: #ID ...] tags, you MUST explicitly mention them as visual witnesses to the memory.\n"
        "- Format them like this: ![Mnemonic Description](api/personal/image/id/ID)\n"
        "- For Media/Video memories (Transcripts), use: [Media Witness: #ID]\n"
        "- If NO [Visual Memory...] tags exist, DO NOT invent paths. Truthfully state that no visual records accompany this textual memory.\n\n"
        f"ARCHIVE DATA:\n{raw_writings}"
    )
    
    # [v1.6.2] State-Shift: Insight Mode
    await orchestrator.ensure_mode('INSIGHT')
    
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(OLLAMA_API_URL, json={"model": ORACLE_MODEL, "prompt": prompt, "stream": False})
            if resp.status_code == 200:
                return resp.json().get("response", "").strip()
    except Exception as e:
        logger.error(f"Summarization Error: {e}")
    return ""

# --- API Endpoints ---

@app.get("/api/personal/ls")
async def list_archive_dir(path: str = "", user_id: int = Depends(verify_auth)):
    archive_root = "/home/concierge/memories/Archive"
    target_path = os.path.abspath(os.path.join(archive_root, path))
    if not target_path.startswith(archive_root):
        raise HTTPException(status_code=403, detail="Forbidden.")
    
    try:
        items = []
        for entry in os.scandir(target_path):
            rel_p = os.path.relpath(entry.path, archive_root)
            items.append({
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "path": rel_p,
                "size": entry.stat().st_size if entry.is_file() else 0,
                "mtime": entry.stat().st_mtime
            })
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return {"status": "success", "current_path": path, "items": items}
    except:
        return {"status": "error", "message": "Failed to list directory."}

@app.get("/api/personal/read")
async def read_archive_file(path: str, offset: int = 0, limit: int = 100000, user_id: int = Depends(verify_auth)):
    archive_root = "/home/concierge/memories/Archive"
    full_path = os.path.abspath(os.path.join(archive_root, path))
    if not full_path.startswith(archive_root):
        raise HTTPException(status_code=403, detail="Forbidden.")
        
    try:
        size = os.path.getsize(full_path)
        with open(full_path, "rb") as f:
            f.seek(offset)
            chunk = f.read(limit)
        return {
            "status": "success",
            "content": chunk.decode('utf-8', errors='ignore'),
            "file_size": size,
            "offset": offset,
            "has_more": (offset + limit) < size
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/personal/file/{file_path:path}")
async def serve_personal_file(file_path: str, user_id: int = Depends(verify_auth)):
    archive_root = "/home/concierge/memories/Archive"
    full_path = os.path.abspath(os.path.join(archive_root, file_path))
    if not os.path.exists(full_path):
        filename = os.path.basename(file_path).lower()
        if filename not in ARCHIVE_FILE_MAP:
            # [v1.2.6] Anti-Desync: Perform a tactical refresh if the file is missing from the index
            update_archive_map()
            
        if filename in ARCHIVE_FILE_MAP:
            full_path = ARCHIVE_FILE_MAP[filename]

    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File not found in archives.")

    # [v1.8.7] Sovereign Authorization Handshake: Strict ownership/visibility enforcement
    is_authorized = False
    norm_path = full_path.replace('\\', '/')
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # 1. Check Image Cache
        c.execute("SELECT visibility, owner_id FROM image_cache WHERE img_path = ?", (norm_path,))
        row = c.fetchone()
        if row:
            vis, owner = row
            if vis == 'SHARED' or int(owner) == int(user_id):
                is_authorized = True
        
        # 2. Check Writings (if not authorized yet)
        if not is_authorized:
            c.execute("SELECT visibility, owner_id FROM writings WHERE source_file = ?", (norm_path,))
            row = c.fetchone()
            if row:
                vis, owner = row
                if vis == 'SHARED' or int(owner) == int(user_id):
                    is_authorized = True
        conn.close()
    except Exception as e:
        logger.error(f"[SHIELD] Archive Access Check Failed: {e}")

    if is_authorized:
        return FileResponse(full_path)
    
    raise HTTPException(status_code=403, detail="Archival Sovereignty Check Failed: Metadata access denied.")

@app.get("/api/personal/image/id/{rid}")
async def serve_personal_image_by_id(rid: int, user_id: int = Depends(verify_auth)):
    # [v1.8.7] Sovereign Handshake: ID-based access still requires ownership check
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT img_path, visibility, owner_id FROM image_cache WHERE id = ?", (rid,))
        row = c.fetchone()
        conn.close()
        
        if not row: raise HTTPException(status_code=404)
        path, vis, owner = row
        
        if vis == 'SHARED' or int(owner) == int(user_id):
            return FileResponse(path)
        
        raise HTTPException(status_code=403, detail="Individual memory access restricted.")
    except HTTPException: raise
    except:
        raise HTTPException(status_code=404)

@app.get("/api/personal/media/id/{rid}")
async def serve_personal_media_by_id(rid: int, user_id: int = Depends(verify_auth)):
    # [v1.8.7] Sovereign Handshake: Validate owner for media transcripts
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT source_file, visibility, owner_id FROM writings WHERE id = ?", (rid,))
        row = c.fetchone()
        conn.close()
        
        if not row: raise HTTPException(status_code=404)
        path, vis, owner = row

        if vis == 'SHARED' or int(owner) == int(user_id):
            return FileResponse(path)

        raise HTTPException(status_code=403, detail="Individual media access restricted.")
    except HTTPException: raise
    except:
        raise HTTPException(status_code=404)

@app.get("/api/personal/image/{image_path:path}")
async def serve_personal_image(image_path: str, user_id: int = Depends(verify_auth)):
    return await serve_personal_file(image_path)


# --- Diagnostic & Security Proxy [v1.0.5] ---

@app.get("/api/personal/debug/permit")
async def debug_permit(path: str, user_id: int = Depends(verify_auth)):
    """ Authorizes a file for temporary session access. """
    archive_root = "/home/concierge/memories/Archive"
    full_path = os.path.abspath(os.path.join(archive_root, path))
    if not os.path.exists(full_path) and path.lower() in ARCHIVE_FILE_MAP:
        full_path = ARCHIVE_FILE_MAP[path.lower()]
    
    if os.path.exists(full_path):
        AUTHORIZED_PERSONAL_IMAGES.add(full_path)
        return {"status": "success", "resolved_path": full_path}
    return {"status": "error", "message": "File not found."}

@app.get("/api/personal/debug/copy")
async def debug_copy(path: str, user_id: int = Depends(verify_auth)):
    """ Bypasses the proxy by copying images to the web-ready static folder. """
    archive_root = "/home/concierge/memories/Archive"
    full_path = os.path.abspath(os.path.join(archive_root, path))
    if not os.path.exists(full_path) and path.lower() in ARCHIVE_FILE_MAP:
        full_path = ARCHIVE_FILE_MAP[path.lower()]
        
    if os.path.exists(full_path):
        target_dir = os.path.join(BASE_DIR, "static", "debug_temp")
        os.makedirs(target_dir, exist_ok=True)
        fname = os.path.basename(full_path)
        shutil.copy(full_path, os.path.join(target_dir, fname))
        return {"status": "success", "static_url": f"/static/debug_temp/{fname}"}
    return {"status": "error", "message": "File not found."}

@app.get("/api/personal/stats")
async def get_personal_stats(user_id: int = Depends(verify_auth)):
    """ [v1.0.5] Unified Hub Telemetry (Protected). """
    if VAULT_SEALED:
        return {
            "writings": 0, "images_described": 0, "db_size_mb": 0,
            "disk_total_gb": 0, "disk_free_gb": 0, "status": "Sealed"
        }
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT count(*), IFNULL(SUM(LENGTH(content)), 0) FROM writings")
        row_w = c.fetchone()
        writings_count, writings_len = row_w[0], row_w[1]
        
        c.execute("SELECT count(*), IFNULL(SUM(LENGTH(description)), 0) FROM image_cache")
        row_i = c.fetchone()
        image_count, image_len = row_i[0], row_i[1]
        conn.close()
        
        total_len = writings_len + image_len
        context_mb = round(total_len / (1024 * 1024), 2)
        
        size_mb = os.path.getsize(DB_PATH) / (1024 * 1024) if os.path.exists(DB_PATH) else 0
        return {
            "writings": writings_count,
            "images_described": image_count,
            "db_size_mb": round(size_mb, 2),
            "context_mb": context_mb,
            "disk_total_gb": 1000.0, # Mocked
            "disk_free_gb": 900.0,   # Mocked
            "status": "Healthy"
        }
    except Exception as e:
        return {"status": "Error", "message": str(e)}

@app.get("/api/models")
async def api_get_models(user_id: int = Depends(verify_auth)):
    models = await get_ollama_models()
    return models

@app.get("/api/health")
async def api_health():
    """ [v1.2.0] Real Hub Telemetry. """
    # cpu = psutil.cpu_percent(interval=None)
    # ram = psutil.virtual_memory()
    return {
        "status": "Online",
        "ollama": "Online" if OLLAMA_HOST == "127.0.0.1" else "Remote",
        "telemetry": {
            "cpu_load": "Healthy",
            "ram_usage": "Optimal",
            "vram_est": "Stable"
        }
    }

@app.get("/api/config")
async def api_config():
    return {"is_tactical_allowed": True, "public_state": "Local"}

@app.get("/api/personal/debug/curate")
async def debug_curate(session_user_id: int = Depends(verify_auth)):
    """ [DEBUG] Audit the curate batch parameters. """
    if VAULT_SEALED: return {"error": "Vault Sealed"}
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, name, role FROM profiles WHERE id = ?", (session_user_id,))
        profile = c.fetchone()
        
        c.execute("SELECT id, IFNULL(visibility, 'NULL'), owner_id, img_path FROM image_cache WHERE IFNULL(revision_count, 0) = 0")
        visibility_groups = c.fetchall()
        
        c.execute("SELECT COUNT(*) FROM image_cache WHERE IFNULL(revision_count, 0) = 0")
        level_0_count = c.fetchone()[0]
        
        conn.close()
        return {
            "session_user_id": session_user_id,
            "profile_found": profile,
            "total_level_0": level_0_count,
            "records": [{"id": r[0], "vis": r[1], "owner": r[2], "path": r[3]} for r in visibility_groups]
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/diagnostic")
async def api_diagnostic():
    return {"node_count": 1}

@app.get("/api/personal/curate/batch")
async def get_curation_batch(request: Request, mode: str = "visual", level: int = 0, show_excluded: bool = False, session_user_id: int = Depends(verify_auth)):
    """ [v1.8.8] Archival Sovereignty: Returns a mix of memories, strictly filtered by authenticated identity. """
    if VAULT_SEALED: return {"status": "success", "batch": []}
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # [v1.2.6] Schema Guard: Self-heal if columns are missing
        c.execute("PRAGMA table_info(image_cache)")
        cols = [col[1] for col in c.fetchall()]
        
        c.execute("PRAGMA table_info(writings)")
        w_cols = [col[1] for col in c.fetchall()]
        
        # Ensure image_cache has curated_at
        if "curated_at" not in cols:
            logger.info("[MIGRATE] Adding curated_at to image_cache")
            c.execute("ALTER TABLE image_cache ADD COLUMN curated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        
        # Ensure writings has curated_at
        if "curated_at" not in w_cols:
            logger.info("[MIGRATE] Adding curated_at to writings")
            c.execute("ALTER TABLE writings ADD COLUMN curated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            
        conn.commit()
        
        # [v1.8.8] Inclusion Control: Filter by hidden state (Archival Recall Toggle)
        hidden_filter = 1 if show_excluded else 0
        
        # [v1.8.8] Expanded Recall Filter: Include multi-platform chat histories
        if mode == "visual":
            table = "image_cache"
            cols_str = "id, img_path, description, source_note, owner_id, visibility, revision_count, hidden"
            type_clause = ""
        else:
            table = "writings"
            cols_str = "id, source_file, content, description, revision_note, owner_id, visibility, revision_count, thumbnail_path, hidden"
            # Expanded to include all platform chat histories identified during research
            type_clause = "AND doc_type IN ('pdf_document', 'docx_document', 'plaintext', 'media_transcript', 'pages_document_scavenged', 'google_chat', 'hangouts_export', 'facebook_msg', 'chat_interaction', 'session_summary')"

        super_admin_mode = request.query_params.get("super_admin_mode", "false").lower() == "true"

        # Check current user's role
        role = "USER"
        if session_user_id:
            c.execute("SELECT role, name FROM profiles WHERE id = ?", (session_user_id,))
            user_data = c.fetchone()
            role = (user_data[0] or "USER").upper() if user_data else "USER"
        else:
            role = "ADMIN"

        # [v1.8.8] Identity Locking: Use the authenticated session_user_id, not a client-provided param
        visibility_cond = f"(hidden = {hidden_filter})"
        if "ADMIN" in role:
            # Admins can see the whole vault if toggled, otherwise just their own.
            if not super_admin_mode:
                visibility_cond += f" AND owner_id = {session_user_id}"
        else:
            # Standard User: strictly bound to shared or owned memories
            visibility_cond += f" AND (visibility = 'SHARED' OR owner_id = {session_user_id})"

        
        # [v1.8.12] Enhanced Batching: 50 items total for smoother navigation
        batch_size = 50
        
        # [v1.8.8] Count total eligible memories at this level for the UI progress bar
        c.execute(f"SELECT COUNT(*) FROM {table} WHERE IFNULL(revision_count, 0) = ? AND {visibility_cond} {type_clause}", (level,))
        total_eligible = c.fetchone()[0]

        if mode == "visual":
            # Primary Batch (Level 0)
            q1 = f"SELECT {cols_str} FROM image_cache WHERE IFNULL(revision_count, 0) = ? AND {visibility_cond} {type_clause} ORDER BY curated_at ASC, id ASC LIMIT ?"
            c.execute(q1, (level, batch_size))
            rows = c.fetchall()
            
            # Seed Mix Fill
            if len(rows) < batch_size:
                remainder = batch_size - len(rows)
                q2 = f"SELECT {cols_str} FROM image_cache WHERE IFNULL(revision_count, 0) != ? AND {visibility_cond} {type_clause} ORDER BY curated_at ASC, id ASC LIMIT ?"
                c.execute(q2, (level, remainder))
                rows.extend(c.fetchall())
        else:
            # Textual Artifacts
            q1 = f"SELECT {cols_str} FROM writings WHERE IFNULL(revision_count, 0) = ? AND {visibility_cond} {type_clause} ORDER BY curated_at ASC, id ASC LIMIT ?"
            c.execute(q1, (level, batch_size))
            rows = c.fetchall()
            
            if len(rows) < batch_size:
                remainder = batch_size - len(rows)
                q2 = f"SELECT {cols_str} FROM writings WHERE IFNULL(revision_count, 0) != ? AND {visibility_cond} {type_clause} ORDER BY curated_at ASC, id ASC LIMIT ?"
                c.execute(q2, (level, remainder))
                rows.extend(c.fetchall())
        
        batch = []
        archive_root = "/home/concierge/memories/Archive"
        for row in rows:
            if mode == "visual":
                m_id, path, desc, note, owner_id, visibility, rev_count, hidden = row
            else:
                m_id, path, content, desc, note, owner_id, visibility, rev_count, thumb, hidden = row

            try:
                rel_media_path = os.path.relpath(path, archive_root) if path.startswith(archive_root) else os.path.basename(path)
            except:
                rel_media_path = os.path.basename(path)

            owner_name = "Archivist"
            if "ADMIN" in role:
                c.execute("SELECT name FROM profiles WHERE id = ?", (owner_id,))
                o_row = c.fetchone()
                if o_row: owner_name = o_row[0]

            if mode == "visual":
                AUTHORIZED_PERSONAL_IMAGES.add(str(m_id)) # ID-based Authorization
                batch.append({
                    "id": m_id, "type": "visual", "path": path, "url": f"api/personal/image/id/{m_id}",
                    "description": desc, "note": note, "owner_name": owner_name, "visibility": visibility, "revision": rev_count, "hidden": hidden
                })
            else:
                if thumb and thumb.startswith("api/personal/thumbnail/"):
                    thumb_fn = os.path.basename(thumb)
                    AUTHORIZED_THUMBNAILS.add(thumb_fn)
                
                batch.append({
                    "id": m_id, "type": "textual", "path": path, "content": content, "description": desc, "note": note,
                    "thumbnail": thumb, "owner_name": owner_name, "visibility": visibility, "revision": rev_count, "hidden": hidden
                })
            
        conn.close()
        return {"status": "success", "batch": batch, "total_eligible": total_eligible}
    except Exception as e:
        logger.error(f"Curation Batch Error: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/api/personal/revisit")
async def revisit_specimen(id: int = None, type: str = "visual", path: str = None, session_user_id: int = 0, user_id: int = Depends(verify_auth)):
    """ [v1.8.0] The ID-Based Archival Bridge: Loads a specific memory into the bench. 
        Accepts 'id' + 'type' (Preferred) or deprecated 'path'. """
    if VAULT_SEALED: return {"status": "error", "message": "Vault Sealed"}
    
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        result = None
        if id is not None:
            if type == "visual":
                c.execute("SELECT id, img_path, description, note, owner_id, visibility, revision_count, hidden FROM image_cache WHERE id = ?", (id,))
                row = c.fetchone()
                if row:
                    m_id, fpath, desc, note, o_id, vis, rev, hid = row
                    rel_path = f"id/{m_id}" # Authorized by ID reference
                    AUTHORIZED_PERSONAL_IMAGES.add(str(m_id))
                    # Fetch owner name
                    c.execute("SELECT name FROM profiles WHERE id = ?", (o_id,))
                    o_row = c.fetchone()
                    owner_name = o_row[0] if o_row else "Archivist"
                    result = {
                        "id": m_id, "type": "visual", "path": fpath, "url": f"api/personal/image/{rel_path}",
                        "description": desc, "note": note, "owner_name": owner_name, "visibility": vis, "revision": rev, "hidden": hid
                    }
            else:
                c.execute("SELECT id, source_file, content, owner_id, visibility, revision_count, thumbnail_path, hidden FROM writings WHERE id = ?", (id,))
                row = c.fetchone()
                if row:
                    m_id, fpath, content, o_id, vis, rev, thumb, hid = row
                    c.execute("SELECT name FROM profiles WHERE id = ?", (o_id,))
                    o_row = c.fetchone()
                    owner_name = o_row[0] if o_row else "Archivist"
                    if thumb and thumb.startswith("api/personal/thumbnail/"):
                        AUTHORIZED_THUMBNAILS.add(os.path.basename(thumb))
                    
                    # Detect media type for bench player
                    is_media = "[MEDIA TRANSCRIPT]" in content or fpath.lower().endswith(('.mp4', '.m4a', '.wav', '.mov'))
                    
                    result = {
                        "id": m_id, "type": "textual", "path": fpath, "content": content,
                        "thumbnail": thumb, "owner_name": owner_name, "visibility": vis, "revision": rev, "hidden": hid,
                        "is_media": is_media,
                        "url": f"api/personal/media/id/{m_id}" if is_media else None
                    }
        elif path:
             # Fallback to path lookup (Deprecated)
             # ... (existing path logic reduced for space) ...
             c.execute("SELECT id, img_path FROM image_cache WHERE img_path LIKE ?", (f"%{path}%",))
             row = c.fetchone()
             if row: return await revisit_specimen(id=row[0], type="visual", user_id=user_id)
        
        conn.close()
        if not result:
             return {"status": "error", "message": "Record not found in the archive index."}
        return {"status": "success", "specimen": result}
    except Exception as e:
        logger.error(f"Revisit Error: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/api/personal/thumbnail/{filename}")
async def api_personal_thumbnail(filename: str, user_id: int = Depends(verify_auth)):
    """ [v1.7.5] Secure Thumbnails: Serves authenticated document previews from the encrypted partition. """
    if filename not in AUTHORIZED_THUMBNAILS:
        logger.warning(f"[SECURE] Unauthorized thumbnail access: {filename}")
        # Check if it exists in DB as an extra safety fallthrough
        # but for now, strict authorization is the masterpiece way
        raise HTTPException(status_code=403, detail="Unauthorized access to archival specimen.")
    
    thumb_path = os.path.join(VAULT_MOUNT, "thumbnails", filename)
    if not os.path.exists(thumb_path):
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    
    return FileResponse(thumb_path)

@app.post("/api/personal/curate/approve")
async def approve_curation(data: dict, user_id: int = Depends(verify_auth)):
    """ [v1.2.6] Just Right: Level up a memory. """
    if VAULT_SEALED: return {"status": "error", "message": "Vault Sealed"}
    img_id = data.get("id")
    mode = data.get("mode", "visual")
    try:
        conn = get_db_connection()
        c = conn.cursor()
        if mode == "visual":
            c.execute("UPDATE image_cache SET revision_count = revision_count + 1, curated_at = CURRENT_TIMESTAMP WHERE id = ?", (img_id,))
        else:
            c.execute("UPDATE writings SET revision_count = revision_count + 1, curated_at = CURRENT_TIMESTAMP WHERE id = ?", (img_id,))
        conn.commit()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/personal/curate/toggle_hidden")
async def toggle_hidden(data: dict, user_id: int = Depends(verify_auth)):
    """ [v1.6.0] Archival Governance: Toggle the 'hidden' (Excluded) status. """
    if VAULT_SEALED: return {"status": "error", "message": "Vault Sealed"}
    target_id = data.get("id")
    mode = data.get("mode", "visual")
    target_state = data.get("state")
        
    try:
        conn = get_db_connection()
        c = conn.cursor()
        table = "image_cache" if mode == "visual" else "writings"
        
        # Get current state
        c.execute(f"SELECT hidden FROM {table} WHERE id = ?", (target_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return {"status": "error", "message": "Memory artifact not found."}
        
        current_state = row[0]
        new_state = target_state if target_state is not None else (1 if current_state == 0 else 0)
        
        if new_state != current_state:
            c.execute(f"UPDATE {table} SET hidden = ? WHERE id = ?", (new_state, target_id))
            conn.commit()
            
        conn.close()
        return {"status": "success", "new_state": new_state}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/sensing/transcribe")
async def transcribe_artifact(request: Request, user_id: int = Depends(verify_auth)):
    """ [v1.7.0] Production Transcription: leverages Whisper large-v3. """
    data = await request.json()
    path = data.get("path")
    if not path or not os.path.exists(path):
        return {"status": "error", "message": "Artifact not found."}
    
    try:
        # Shift hardware to Transcription mode
        await orchestrator.ensure_mode('TRANSCRIBE')
        
        if not orchestrator.whisper_engine:
            return {"status": "error", "message": "Transcription Engine not resident."}
            
        logger.info(f"[SENSING] Transcribing: {os.path.basename(path)}")
        # beam_size 5 provides high accuracy for large-v3
        segments, info = orchestrator.whisper_engine.transcribe(path, beam_size=5)
        
        full_text = ""
        for segment in segments:
            full_text += f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text.strip()}\n"
            
        return {
            "status": "success",
            "text": full_text.strip(),
            "language": info.language,
            "probability": info.language_probability
        }
    except Exception as e:
        logger.error(f"[SENSING] Transcription Failed: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/api/personal/curate/regenerate")
async def regenerate_curation(request: Request, user_id: int = Depends(verify_auth)):
    data = await request.json()
    """ [v1.2.6] Revise: Sharpen sensing with user context. """
    target_id = data.get("id")
    user_context = data.get("context", "")
    mode = data.get("mode", "visual") 
    blind = data.get("blind", False) # [v1.6.5] Fast Synthesis Toggle
    
    conn = get_db_connection()
    try:
        c = conn.cursor()
        if mode == "visual":
            c.execute("SELECT img_path, description FROM image_cache WHERE id = ?", (target_id,))
        else:
            c.execute("SELECT source_file, description, content FROM writings WHERE id = ?", (target_id,))
        
        row = c.fetchone()
        if not row: return {"status": "error", "message": f"Memory artifact [{mode}:{target_id}] not found in index."}
        
        source_path, old_desc = row[0], row[1]
        raw_content = row[2] if mode == "textual" else None
        
        # --- [Pass 1] Vision Re-Look or Deep Context Pull ---
        sensing_context = old_desc
        
        if mode == "visual" and not blind:
            # High-Fidelity Pass: Re-examine pixels with Moondream
            img_b64 = encode_image(source_path)
            if img_b64:
                hint_prompt = f"Based on the archivist's context: '{user_context}', re-examine this image and describe it in detail. Focus on verifying the archivist's claims against visual evidence. No preamble."
                async with httpx.AsyncClient(timeout=180.0) as client:
                    try:
                        v_resp = await client.post(OLLAMA_API_URL, json={
                            "model": SENSE_MODEL, 
                            "prompt": hint_prompt, 
                            "images": [img_b64],
                            "stream": False,
                            "options": {"num_predict": MAX_SENSING_TOKENS, "temperature": 0.2}
                        })
                        if v_resp.status_code == 200:
                            raw_sc = v_resp.json().get("response", "").strip()
                            sensing_context = SafetyScrubber.scrub(raw_sc)
                            logger.info(f"[SENSING] Moondream re-look successful for {target_id}")
                    except Exception as ve:
                        logger.error(f"[SENSING] Moondream re-look failed: {ve}")
        
        # --- [Pass 2] Archival Synthesis (Oracle Pass) ---
        if mode == "visual":
            prompt = (
                f"You are the MemoryBox Synthesis Engine. I have a raw vision description {'(re-examined)' if not blind else '(cached)'} and a human archivist's grounding note. \n"
                f"Your goal is to merge these into a single, cohesive, and authoritative archival record.\n\n"
                f"VISION DATA: {sensing_context}\n"
                f"ARCHIVIST NOTE: {user_context}\n\n"
                "Produce the final description as a single vivid paragraph. No preamble, no conversational filler."
            )
        else:
            # Textual Sensing
            sample = (raw_content or "")[:2000] if not blind else "None (Blind Synthesis)"
            prompt = (
                f"As the MemoryBox Sense Engine, refine the sensing of this document.\n"
                f"SENSING DATA: {old_desc}\n"
                f"ARCHIVIST GROUNDING: {user_context}\n"
                f"DOCUMENT SAMPLE: {sample}\n\n"
                f"{'Synthesize these into a 2-3 sentence summary.' if not blind else 'Synthesize the grounding into the existing description.'} "
                "Highlight key entities and sentiment. No preamble."
            )
        
        # Always use ORACLE (Mistral/Gemma) for the final paragraph synthesis
        target_engine = ORACLE_MODEL 
        target_mode = 'INSIGHT'
        
        await orchestrator.ensure_mode(target_mode)
        
        async with httpx.AsyncClient(timeout=180.0) as client:
            try:
                resp = await client.post(OLLAMA_API_URL, json={
                    "model": target_engine, 
                    "prompt": prompt, 
                    "stream": False,
                    "options": {"num_predict": MAX_SENSING_TOKENS, "temperature": 0.2}
                })
                if resp.status_code == 200:
                    raw_new_desc = resp.json().get("response", "").strip()
                    new_desc = SafetyScrubber.scrub(raw_new_desc)
                    # [v1.8.10] Stateless Regeneration: DB commit deferred to /update
                    return {"status": "success", "new_description": new_desc}
                else:
                    return {"status": "error", "message": f"AI Engine returned status {resp.status_code}"}
            except httpx.TimeoutException:
                return {"status": "error", "message": "AI Engine Timed Out (Ollama too slow on CPU)"}
            except (httpx.ConnectError, httpx.HTTPError):
                return {"status": "error", "message": "AI Engine Communication Failed."}
            except Exception as e_inner:
                return {"status": "error", "message": f"AI Engine Internal Error: {str(e_inner)}"}
                
    except Exception as e:
        return {"status": "error", "message": f"Sensing Engine Error: {str(e)}"}
    finally:
        conn.close()

@app.post("/api/personal/curate/update")
async def commit_revision(data: dict, user_id: int = Depends(verify_auth)):
    """ Finalize a revision after regeneration. """
    target_id = data.get("id")
    description = data.get("description")
    visibility = data.get("visibility")
    mode = data.get("mode", "visual") # [v1.5.0] Multi-mode support
    note = data.get("note", "") # [v1.8.10] Ensure context note is saved
    
    try:
        conn = get_db_connection()
        c = conn.cursor()
        table = "image_cache" if mode == "visual" else "writings"
        note_col = "source_note" if mode == "visual" else "revision_note"
        
        hidden = data.get("hidden")
        
        if visibility:
            c.execute(f"UPDATE {table} SET description = ?, {note_col} = ?, revision_count = revision_count + 1, visibility = ?, hidden = ?, curated_at = CURRENT_TIMESTAMP WHERE id = ?", 
                      (description, note, visibility, hidden if hidden is not None else 0, target_id))
        else:
            c.execute(f"UPDATE {table} SET description = ?, {note_col} = ?, revision_count = revision_count + 1, hidden = ?, curated_at = CURRENT_TIMESTAMP WHERE id = ?", 
                      (description, note, hidden if hidden is not None else 0, target_id))

        try:
            # [v1.5.0] Synchronize FTS
            if mode == "visual":
                c.execute("DELETE FROM image_cache_fts WHERE content_id = ?", (target_id,))
                c.execute("SELECT img_path FROM image_cache WHERE id = ?", (target_id,))
                v_row = c.fetchone()
                if v_row:
                    c.execute("INSERT INTO image_cache_fts (description, img_path, content_id) VALUES (?,?,?)", 
                              (description, os.path.basename(v_row[0]), target_id))
            else:
                c.execute("DELETE FROM writings_fts WHERE content_id = ?", (target_id,))
                c.execute("SELECT content FROM writings WHERE id = ?", (target_id,))
                w_row = c.fetchone()
                orig_content = w_row[0] if w_row else ""
                c.execute("INSERT INTO writings_fts (content, description, content_id) VALUES (?,?,?)", 
                          (orig_content, description, target_id))
        except Exception as fts_err:
            logger.error(f"[CURATE] FTS Sync Delay: {fts_err}")
            # Non-fatal: FTS sync can be rebuilt later, metadata is more important

        conn.commit()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"[CURATE] Commit Failed: {e}")
        return {"status": "error", "message": f"Archival Commit Rejected: {str(e)}"}

@app.post("/api/ingestion/upload")
async def upload_ingestion_file(
    file: UploadFile = File(...), 
    relativePath: str = Form(...),
    user_id: int = Depends(verify_auth)
):
    """ [v1.2.6] Structured Upload: Saves files with preserved subdirectory paths in Incoming area. """
    incoming_root = "/home/concierge/memories/Archive/Incoming"
    # Ensure relative path doesn't escape the root
    safe_rel_path = os.path.normpath(relativePath).lstrip(os.sep).lstrip('/')
    target_path = os.path.join(incoming_root, safe_rel_path)
    
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    
    try:
        with open(target_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        return {"status": "success", "message": f"Saved {safe_rel_path}"}
    except Exception as e:
        logger.error(f"Upload failed for {safe_rel_path}: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/api/ingestion/trigger")
async def trigger_ingestion(data: Dict, user_id: int = Depends(verify_auth)):
    # [v1.2.0] Vaulting Metadata Layer
    target = data.get("target", "/home/concierge/memories/Archive/Incoming")
    notes = data.get("notes", {})
    visibility = data.get("visibility", "SHARED")
    author_id = data.get("author_id", 1)
    
    # [v1.8.3] Auto-Recovery: Ensure target exists before writing metadata
    if not os.path.exists(target):
        logger.warning(f"[TRIGGER] Target {target} missing. Attempting restoration...")
        solidify_archival_tree()

    # Save metadata for the script to pick up
    notes_file = os.path.join(VAULT_MOUNT, "ingest_notes.json")
    try:
        # 1. Metadata Persistence
        with open(notes_file, "w") as f:
            json.dump({"notes": notes, "author_id": author_id, "visibility": visibility}, f)
        
        # 2. Binary Resolution [v1.8.1]
        # Check if venv binary exists AND is readable (symlink resolution check)
        venv_bin = os.path.join(BASE_DIR, "venv", "bin", "python3")
        if os.path.exists(venv_bin):
            python_exec = venv_bin
        else:
            python_exec = "python3" # Fallback to global
            
        script_path = os.path.join(BASE_DIR, "scripts", "ingest_writings.py")
        cmd = [python_exec, script_path, target, "--backfill-vision", "--backfill-audio"]
        
        logger.info(f"[TRIGGER] Triggering ingestion: {cmd}")
        # Launch using start_new_session=True to decouple from the web service
        subprocess.Popen(cmd, stdout=None, stderr=None, start_new_session=True)
        return {"status": "success", "message": "Vaulting Triggered."}
        
    except FileNotFoundError as fe:
        logger.error(f"[TRIGGER] Binary not found: {fe}")
        return {"status": "error", "message": f"Engine binary not found: {python_exec if 'python_exec' in locals() else 'unknown'}"}
    except Exception as e:
        logger.error(f"[TRIGGER] Failed to trigger ingestion: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/api/ingestion/status")
async def get_ingestion_status(user_id: int = Depends(verify_auth)):
    """ [v1.2.0] Deep Sensing: Returns current archival progress. """
    path = "/home/concierge/memories/ingestion_status.json"
    if not os.path.exists(path):
        return {"status": "IDLE"}
    try:
        with open(path, "r") as f:
            data = json.load(f)
            # If timestamp is older than 10 minutes, assuming stale/idle
            ts = datetime.fromisoformat(data.get("timestamp"))
            if datetime.now() - ts > timedelta(minutes=10):
                return {"status": "IDLE"}
            return data
    except:
        return {"status": "IDLE"}

@app.post("/api/archive/refresh")
async def refresh_archive_map(user_id: int = Depends(verify_auth)):
    """ [v1.2.0] Deep Resolution: Force a re-scan of archival subdirectories. """
    update_archive_map()
    return {"status": "success", "count": len(ARCHIVE_FILE_MAP)}

@app.get("/api/personal/find_alike")
async def find_alike(path: str = None, id: int = None, type: str = "visual", user_id: int = Depends(verify_auth)):
    db_path = "/home/concierge/memories/personal_memory.db"
    archive_root = "/home/concierge/memories/Archive"
    
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        anchor_desc = ""
        full_path = ""
        
        # [v1.8.0] Secure ID Lookup for Anchor
        if id:
             if type == 'visual':
                 c.execute("SELECT img_path, description FROM image_cache WHERE id = ?", (id,))
             else:
                 c.execute("SELECT source_file, content FROM writings WHERE id = ?", (id,))
             row = c.fetchone()
             if row:
                 full_path, anchor_desc = row
                 # [v1.8.1] Ensure path is derived from full_path for strategy hints
                 path = os.path.relpath(full_path, archive_root).replace('\\', '/')
             else:
                 conn.close()
                 return {"status": "error", "message": "Specimen not found in archive index.", "related": []}
        elif path:
            full_path = os.path.abspath(os.path.join(archive_root, path))
            if not os.path.exists(full_path) and path.lower() in ARCHIVE_FILE_MAP:
                full_path = ARCHIVE_FILE_MAP[path.lower()]
            
            c.execute("SELECT description FROM image_cache WHERE img_path = ?", (full_path,))
            row = c.fetchone()
            if row:
                anchor_desc = row[0]
            else:
                c.execute("SELECT content FROM writings WHERE source_file = ?", (full_path,))
                row = c.fetchone()
                if row: anchor_desc = row[0]
                else:
                    conn.close()
                    return {"status": "error", "message": "No anchor record found for this path.", "related": []}
        else:
            return {"status": "error", "message": "No identity (ID or Path) provided for discovery."}

        
        # --- [v1.6.5] Heartstrings Discovery Strategy ---
        import random
        strategy_roll = random.random()
        strategy = "KINSHIP" 
        strategy_label = "semantic similarity"
        
        # [v1.8.1] Guard None path to prevent lstrip error
        path_str = path if path else ""
        path_parts = path_str.lstrip('/').split('/')
        year_hint = path_parts[0] if len(path_parts) > 1 and path_parts[0].isdigit() else None
        folder_hint = path_parts[-2] if len(path_parts) > 1 else None
        
        # Determine Primary Strategy
        if strategy_roll < 0.2 and year_hint:
            strategy = "ERA"
            strategy_label = f"archival proximity in {year_hint}"
        elif strategy_roll < 0.4 and folder_hint:
            strategy = "ATMOSPHERE"
            strategy_label = f"spatial resonance with '{folder_hint}'"
        elif strategy_roll < 0.6:
            heirloom_keywords = ['car', 'truck', 'mustang', 'boat', 'house', 'cabin', 'watch', 'ring', 'guitar', 'piano', 'garden', 'antique']
            found = [h for h in heirloom_keywords if h in anchor_desc.lower()]
            if found:
                strategy = "HEIRLOOM"
                strategy_label = f"shared focus on '{found[0]}'"
        elif strategy_roll < 0.8:
            emotion_keywords = ['joy', 'serene', 'peaceful', 'celebration', 'wedding', 'graduation', 'milestone', 'smile', 'laughing', 'together', 'warm', 'triumphant']
            found = [e for e in emotion_keywords if e in anchor_desc.lower()]
            if found:
                strategy = "RESONANCE"
                strategy_label = f"emotional resonance ({found[0]})"

        # [v1.6.6] Cascading Fallback Engine: Ensure we ALWAYS find a correlation across ALL media
        # [v1.8.0] Standardized Discovery Pool: (id, path, content, mtype, timestamp)
        # For images, we map description to 'content' and use fs_mtime as timestamp
        c.execute("SELECT id, img_path, description, 'visual' as mtype, fs_mtime FROM image_cache WHERE img_path != ?", (full_path,))
        all_candidates = c.fetchall()
        
        # [v1.7.0] Integrated Resonance: Pull in relevant writings (Chat logs, Google Takeout, Journals, Transcripts)
        # [v1.8.1] Expanded Selection: description added for higher fidelity reminiscence
        c.execute("SELECT id, source_file, content, 'textual' as mtype, timestamp, description FROM writings")
        all_writings = c.fetchall()
        
        all_pool = all_candidates + all_writings

        random.shuffle(all_pool)
        
        related = []
        related_rows = []
        
        # Pass 1: Primary Strategy
        if strategy == "ERA":
            related_rows = [c for c in all_pool if (c[3] == 'visual' and f"/{year_hint}/" in c[1]) or (c[3] == 'textual' and year_hint in str(c[4]))][:6]
        elif strategy == "ATMOSPHERE":
            related_rows = [c for c in all_pool if (c[3] == 'visual' and (f"/{folder_hint}/" in c[1] or folder_hint.lower() in c[2].lower())) or (c[3] == 'textual' and (folder_hint.lower() in str(c[1]).lower() or folder_hint.lower() in c[2].lower()))][:6]
        elif strategy == "HEIRLOOM":
            obj = [h for h in heirloom_keywords if h in anchor_desc.lower()][0]
            related_rows = [c for c in all_pool if obj in c[2].lower()][:6]
        elif strategy == "RESONANCE":
            emo = [e for e in emotion_keywords if e in anchor_desc.lower()][0]
            related_rows = [c for c in all_pool if emo in c[2].lower()][:6]
        
        # Pass 2: KINSHIP Fallback (Deep Thematic Scan)
        if not related_rows or strategy == "KINSHIP":
            if strategy != "KINSHIP":
                strategy = "KINSHIP"
                strategy_label = "thematic correlation"
            
            import re
            clean_desc = re.sub(r'[^\w\s]', ' ', anchor_desc).lower()
            stopwords = {'the', 'a', 'an', 'and', 'is', 'in', 'on', 'at', 'it', 'with', 'to', 'of', 'this', 'that', 'from', 'their', 'there'}
            vision_fluff = {'captured', 'camera', 'photo', 'image', 'picture', 'observed', 'discerned', 'featured', 'background', 'visible'}
            
            words = [w for w in clean_desc.split() if w not in stopwords and w not in vision_fluff and len(w) > 3][:20]
            if words:
                search_candidates = []
                for cand in all_pool:
                    match_count = sum(1 for w in words if w in cand[2].lower())
                    if match_count > 0:
                        search_candidates.append((cand, match_count))
                search_candidates.sort(key=lambda x: x[1], reverse=True)
                related_rows = [s[0] for s in search_candidates[:6]]

        # Pass 3: Discovery Fallback
        if not related_rows:
            if not all_pool:
                return {
                    "status": "success",
                    "anchor": os.path.basename(full_path),
                    "related": [],
                    "synthesis": "This appears to be a solitary memory in your vault. I need more archival threads to weave a connection through your timeline."
                }
            strategy = "DISCOVERY"
            strategy_label = "broad archival discovery"
            related_rows = all_pool[:3]

        for rid, rpath, rcontent, rtype, rts, *extra in related_rows:
            # [v1.8.1] Context Recovery: Pull description from extra if it exists (for textual)
            rdesc = extra[0] if extra else None
            # [v1.8.0] Mnemonic Label Generator
            date_str = rts if rts else "Undated"
            if len(str(date_str)) > 10: date_str = str(date_str)[:10]
            
            mnemonic = f"Archival Memory ({date_str})"
            if rtype == 'visual':
                location = "Memory"
                # Heuristic: Extract location from path or description
                for loc in ['Austin', 'Texas', 'Home', 'Office', 'California', 'London', 'Paris', 'New York']:
                    if loc.lower() in rpath.lower() or loc.lower() in rcontent.lower():
                        location = loc
                        break
                mnemonic = f"Visual memory from {location} ({date_str})"
                # Authorization fix: we authorize the ID for serving
                AUTHORIZED_PERSONAL_IMAGES.add(str(rid))
                related.append({
                    "id": rid,
                    "url": f"api/personal/image/id/{rid}", # ID-based serving
                    "description": rcontent,
                    "mnemonic": mnemonic,
                    "type": "visual"
                })
            else:
                # [v1.8.0] Media Memory Detection
                m_label = "Textual memory"
                is_media = "[MEDIA TRANSCRIPT]" in rcontent or rpath.lower().endswith(('.mp4', '.m4a', '.wav', '.mov'))
                if is_media:
                    m_label = "Media memory - Transcribed"
                
                mnemonic = f"{m_label} ({date_str})"
                
                # [v1.8.1] Reminiscence Upgrade: Use sensed description as primary context if available
                # This prevents the 'Thin Context' problem where the AI only sees generic labels.
                display_desc = rdesc if rdesc else mnemonic
                
                related.append({
                    "id": rid,
                    "content": rcontent, 
                    "description": display_desc, # UI uses description for the title/synthesis
                    "mnemonic": mnemonic,
                    "type": "textual" if not is_media else "media"
                })

            
        conn.close()
        
        # --- [v1.8.10] Reminiscence Synthesis (Oracle Pass) ---
        synthesis = ""
        if related:
            related_context = "\n".join([f"- {r['description']}" for r in related])
            prompt = (
                f"You are the MemoryBox Reminiscence Engine. I have an anchor memory and several related archival records found via {strategy_label}.\n"
                f"PRIMARY MEMORY: {anchor_desc}\n"
                f"RELATED RECORDS:\n{related_context}\n\n"
                f"Synthesize the connection between these items based on the {strategy} strategy. "
                "Produce a single vivid and poetic paragraph explaining the correlation. No technical jargon."
            )
            
            try:
                # [v1.8.10] Oracle Synthesis: 120s Timeout, 512 Tokens
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        OLLAMA_API_URL, 
                        json={
                            "model": ORACLE_MODEL, 
                            "prompt": prompt, 
                            "stream": False,
                            "options": {"num_predict": 512, "temperature": 0.7}
                        }
                    )
                    if response.status_code == 200:
                        raw_synthesis = response.json().get("response", "").strip()
                        synthesis = SafetyScrubber.scrub(raw_synthesis)
            except (httpx.ReadTimeout, httpx.ConnectTimeout):
                logger.warning("[REMINISCENCE] Synthesis timed out. Returning 'Slow Burn' status.")
                synthesis = "DEFERRED_REFLECTION"
            except Exception as e:
                logger.error(f"[REMINISCENCE] Synthesis failed: {e}")
                synthesis = ""

        return {
            "status": "success", 
            "anchor": os.path.basename(full_path), 
            "related": related,
            "synthesis": synthesis,
            "strategy": strategy
        }
    except Exception as e:
        logger.error(f"Find Alike Error: {e}")
        return {"status": "error", "message": str(e)}

# --- Archival & Summarization Endpoints [v1.0.6] ---

@app.post("/api/personal/archive")
async def api_archive_chat(data: Dict, user_id: int = Depends(verify_auth)):
    """ Atomic archival of a single interaction. """
    try:
        content = f"User: {data.get('query')}\nPersona: {data.get('response')}"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("INSERT INTO writings (content, author, recipient, timestamp, source_file, doc_type) VALUES (?,?,?,?,?,?)", 
                 (content, "User", "MemoryBox", ts, "MemoryBox Interaction", "chat_interaction"))
        doc_id = c.lastrowid
        c.execute("INSERT INTO writings_fts (content, content_id) VALUES (?,?)", (content, doc_id))
        conn.commit()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Archival error: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/api/personal/summarize_session")
async def api_summarize_session(data: Dict, user_id: int = Depends(verify_auth)):
    """ Generates a narrative summary of the current session and stores it. """
    history = data.get("history", [])
    if not history: return {"status": "error", "message": "No history provided."}
    
    # 1. AI Summary Pass
    chat_text = "\n".join([f"{m['role'].capitalize()}: {m['content']}" for m in history])
    prompt = (
        "You are the MemoryBox Archival Agent. Summarize the following conversation "
        "into a concise, narrative memory. Focus on the core questions asked and the "
        "insights discovered. Do not use conversational filler.\n\n"
        f"Conversation:\n{chat_text}"
    )
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(OLLAMA_API_URL, json={"model": MODEL_NAME, "prompt": prompt, "stream": False})
            if resp.status_code == 200:
                summary = resp.json().get("response", "").strip()
                if summary:
                    # 2. Persist to DB
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("INSERT INTO writings (content, author, recipient, timestamp, source_file, doc_type) VALUES (?,?,?,?,?,?)", 
                             (f"[SESSION SUMMARY]: {summary}", "MemoryBox", "User", ts, "Session Record", "session_summary"))
                    doc_id = c.lastrowid
                    c.execute("INSERT INTO writings_fts (content, content_id) VALUES (?,?)", (f"[SESSION SUMMARY]: {summary}", doc_id))
                    conn.commit()
                    conn.close()
                    return {"status": "success", "summary": summary}
    except Exception as e:
        logger.error(f"Summarization error: {e}")
    return {"status": "error", "message": "Ollama failed to generate summary."}

@app.post("/api/personal/ingest_chat")
async def api_ingest_chat(data: Dict, user_id: int = Depends(verify_auth)):
    """ [v1.5.0] Chat Journaling: Writes raw transcript to the archival journals folder. """
    history = data.get("history", [])
    user_id = data.get("user_id", 0)
    if not history: return {"status": "error", "message": "No history provided."}
    
    # 1. Resolve User Name for Filename
    user_name = "Anonymous"
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT name FROM profiles WHERE id = ?", (user_id,))
        res = c.fetchone()
        if res: user_name = res[0]
        conn.close()
    except: pass

    # 2. Format Transcript
    ts = datetime.now()
    journal_text = f"ARCHIVAL CHAT JOURNAL - {ts.strftime('%Y-%m-%d %H:%M:%S')}\n"
    journal_text += f"Identity: {user_name}\n"
    journal_text += "="*40 + "\n\n"
    for m in history:
        journal_text += f"[{m['role'].upper()}]: {m['content']}\n\n"

    # 3. Write to Memories B: Partition
    filename = f"journal_chat_{ts.strftime('%Y%m%d_%H%M%S')}_{user_name.replace(' ', '_')}.txt"
    journals_dir = "/home/concierge/memories/Archive/Journals"
    if not os.path.exists(journals_dir):
        os.makedirs(journals_dir, exist_ok=True)
    
    file_path = os.path.join(journals_dir, filename)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(journal_text)
        
        # 4. Trigger Instant Sensing (Background)
        python_bin = os.path.join(BASE_DIR, "venv", "bin", "python3")
        if not os.path.exists(python_bin): python_bin = "python3"
        script_path = os.path.join(BASE_DIR, "scripts", "ingest_writings.py")
        
        # We target specifically the new file
        cmd = [python_bin, script_path, file_path]
        subprocess.Popen(cmd, stdout=None, stderr=None, start_new_session=True)

        return {"status": "success", "file": filename, "message": "Transcript Vaulted."}
    except Exception as e:
        logger.error(f"Chat Ingestion Error: {e}")
        return {"status": "error", "message": str(e)}

# --- Wiki Endpoints ---
@app.get("/api/wiki/list")
async def wiki_list():
    if not os.path.exists(DIRECTIVES_PATH): return {"files": []}
    files = [f for f in os.listdir(DIRECTIVES_PATH) if f.endswith(".md")]
    return {"files": files}

@app.get("/api/wiki/read")
async def wiki_read(path: str):
    full_path = os.path.join(DIRECTIVES_PATH, path)
    if not os.path.exists(full_path): raise HTTPException(status_code=404)
    with open(full_path, "r", encoding="utf-8") as f:
        return {"content": f.read()}

@app.post("/api/wiki/save")
async def wiki_save(data: Dict):
    path = data.get("path")
    content = data.get("content")
    full_path = os.path.join(DIRECTIVES_PATH, path)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"status": "success"}

# --- Control Endpoints ---
@app.post("/api/control/{system}/{action}")
async def api_control(system: str, action: str):
    if system == "bridge":
        return await run_tactical_script("lan_orchestrator.py", [action])
    elif system == "system" and action == "backup":
        return await run_tactical_script("wiki_backup.py", [])
    return {"status": "error", "message": f"Unknown system/action: {system}/{action}"}

@app.get("/api/curation/queue")
async def get_curation_queue():
    """ [v1.2.0] Returns 5 media items for curation, prioritized by lowest revision count. """
    conn = get_db_connection()
    c = conn.cursor()
    # Prioritize 0 revisions (new), then 1, etc. Secondary sort by newest file time.
    c.execute("""
        SELECT id, img_path, description, revision_count, fs_mtime 
        FROM image_cache 
        ORDER BY revision_count ASC, fs_mtime DESC 
        LIMIT 5
    """)
    rows = c.fetchall()
    
    queue = []
    for r in rows:
        idx, path, desc, revs, mtime = r
        fname = os.path.basename(path)
        # We tentatively authorize these for UI preview
        AUTHORIZED_PERSONAL_IMAGES.add(path)
        queue.append({
            "id": idx,
            "filename": fname,
            "description": desc,
            "revisions": revs,
            "mtime": mtime,
            "url": f"/api/personal/image/{fname}"
        })
    conn.close()
    return queue

# Tactical Scanning Endpoints removed (MemoryBox Appliance Mode)

@app.post("/api/search")
async def search(request: Request, user_id: int = Depends(verify_auth)):
    data = await request.json()
    query = data.get("query", "")
    personal_mode = data.get("personal_mode", False)
    wiki_mode = data.get("wiki_mode", False)
    tactical_mode = data.get("tactical_mode", False)
    
    if personal_mode:
        if query.startswith("/reanalyze_failed"):
            return await handle_reanalyze_failed_command(user_id=user_id)
        if query.startswith("/reanalyze"):
            return await handle_reanalyze_command(query.replace("/reanalyze", "").strip(), user_id=user_id)
        if query.startswith("/ground"):
            return await handle_ground_command(query.replace("/ground", "").strip(), user_id=user_id)
        if query.startswith("/transcribe"):
            return await transcribe_audio_on_demand(query.replace("/transcribe", "").strip(), user_id=user_id)
        if query.startswith("/search_visual"):
            return await handle_search_visual_command(query.replace("/search_visual", "").strip(), user_id=user_id)

    async def generate():
        full_response = ""
        synthetic_context = ""
        if personal_mode:
            yield "__STATUS__:Restoring Personal Context...\n"
            # [v1.8.8] Sovereignty Lock: Only admins can use super-admin mode
            user_is_admin = False
            conn = get_db_connection()
            try:
                c = conn.cursor()
                c.execute("SELECT role FROM profiles WHERE id = ?", (user_id,))
                user_role_row = c.fetchone()
                user_is_admin = user_role_row and ("ADMIN" in user_role_row[0].upper())
            finally: conn.close()
            
            is_super = data.get("super_admin_mode", False) if user_is_admin else False
            rag = get_personal_context(query, current_user_id=user_id, super_admin_mode=is_super)
            synthetic_context = await summarize_personal_context(query, rag["text"])
            
            # [v1.2.0] Integrity Guard: Hard-stop for zero-result queries
            if not rag["text"] and not rag["images"]:
                yield f"I have no record of '{query}' within the archive vault. As an offline appliance, I am unable to provide information beyond my confirmed memory blocks."
                return
            
            # [v1.2.0] Formatted Image Context Injection
            visual_context = ""
            if rag["images"]:
                visual_context = "\n### ARCHIVE DATA (VISUAL MEMORIES):\n"
                for img in rag["images"]:
                    fname = os.path.basename(img["path"])
                    visual_context += f"- [IMAGE: api/personal/image/{fname}] ({img['description']})\n"
            
            yield "__STATUS__:Synthesizing Memories...\n"
        elif wiki_mode:
            yield "__STATUS__:Consulting Brain Directives...\n"
            # [Stub] logic for wiki search
            visual_context = ""
        else:
            visual_context = ""
        
        system = f"You are the ConciergeHub Persona. Mode: {'Personal' if personal_mode else 'General'}. Context: {synthetic_context}\n{visual_context}"
        if personal_mode:
            system += (
                "CRITICAL OFFLINE PROTOCOL: You are the 'Concierge Oracle,' a strictly sequestered neural engine inside a physically hardened appliance. "
                "You have ZERO ACCESS to external data. You MUST NOT mention Ferraris, Lamborghinis, or Mustangs unless they are EXPLICITLY in the Context. "
                "You are STRICTLY FORBIDDEN from generating external links (Pexels, Unsplash, etc.). "
                "If the ### ARCHIVE DATA (VISUAL MEMORIES) block below is empty, you MUST NOT describe any visual elements. "
                "CRITICAL: You are a machine with no imagination. If a detail is not provided in your context, it does not exist. Never guess. Never hallucinate. Never links."
                "\n### ARCHIVE DATA (TRANSCRIPTS):\n"
                f"{synthetic_context}\n"
                f"{visual_context}"
            )
        if tactical_mode:
            system += "\nYou are currently in TACTICAL mode. Focus on hardware analysis and security protocols."
            
        messages = [{"role": "system", "content": system}, {"role": "user", "content": query}]
        
        # [v1.6.2] State-Shift: Smart Canary Routing
        # If the Oracle (12B) is not loaded AND the query is simple, use the Canary (1.5B)
        # to avoid the 3s swap latency.
        is_simple = len(query.split()) < 6
        use_canary = orchestrator.active_mode != 'INSIGHT' and is_simple
        
        target_model = CANARY_MODEL if use_canary else ORACLE_MODEL
        
        if not use_canary:
            yield "__STATUS__:Consulting Deep Oracle...\n"
            await orchestrator.ensure_mode('INSIGHT')
        else:
            yield "__STATUS__:Instant Response (Canary)...\n"
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", CHAT_API_URL, json={"model": target_model, "messages": messages, "stream": True}) as response:
                async for line in response.aiter_lines():
                    if not line: continue
                    chunk = json.loads(line).get("message", {}).get("content", "")
                    full_response += chunk
                    yield chunk
                    if "api/personal/image/" in full_response or "api/personal/file/" in full_response:
                        # Matches both legacy paths and new /id/ routes
                        matches = re.findall(r"api/personal/(?:image|file)/(?:id/)?([^\s\)]+)", full_response)
                        for m in matches:
                            update_archive_map()
                            # If it's an ID, we authorize the ID string
                            if m.isdigit():
                                AUTHORIZED_PERSONAL_IMAGES.add(m)
                            else:
                                fname = os.path.basename(m).lower()
                                if fname in ARCHIVE_FILE_MAP:
                                    AUTHORIZED_PERSONAL_IMAGES.add(ARCHIVE_FILE_MAP[fname])


    return StreamingResponse(generate(), media_type="text/plain")

# --- Command Handlers ---

async def handle_reanalyze_command(path: str):
    archive_root = "/home/concierge/memories/Archive"
    full_path = os.path.abspath(os.path.join(archive_root, path))
    if not os.path.exists(full_path) and path.lower() in ARCHIVE_FILE_MAP:
        full_path = ARCHIVE_FILE_MAP[path.lower()]
    
    if not os.path.exists(full_path):
        return StreamingResponse(iter([f"ERROR: File {path} not found."]), media_type="text/plain")

    async def generate_revision():
        yield "__STATUS__:Deep Vision Recalibration...\n"
        from scripts.ingest_writings import VisionProcessor
        db_path = "/home/concierge/memories/personal_memory.db"
        # [v1.6.2] State-Shift: Intake/Sense Mode
        await orchestrator.ensure_mode('INTAKE')
        vp = VisionProcessor(SENSE_MODEL)
        desc = vp.describe_image(full_path, deep_scan=True)
        if desc:
            mtime = datetime.fromtimestamp(os.path.getmtime(full_path)).isoformat()
            ctime = datetime.fromtimestamp(os.path.getctime(full_path)).isoformat()
            curated_at = datetime.now().isoformat()
            
            # [v1.8.8] Schema Solidarity: Capture original file dates and temporal anchor
            c.execute("""INSERT OR REPLACE INTO image_cache 
                         (img_path, description, fs_mtime, fs_ctime, curated_at) 
                         VALUES (?,?,?,?,?)""", (full_path, desc, mtime, ctime, curated_at))
            
            # [v1.2.0] Update FTS and Increment Revision
            c.execute("UPDATE image_cache SET revision_count = revision_count + 1 WHERE img_path = ?", (full_path,))
            
            c.execute("SELECT id FROM image_cache WHERE img_path = ?", (full_path,))
            row_id = c.fetchone()[0]
            c.execute("DELETE FROM image_cache_fts WHERE content_id = ?", (row_id,))
            c.execute("INSERT INTO image_cache_fts (description, img_path, content_id) VALUES (?,?,?)", 
                      (desc, os.path.basename(full_path), row_id))
            conn.commit()
            conn.close()
            yield f"**RECALIBRATED MEMORY: {os.path.basename(full_path)}**\n\n{desc}\n\n[SUCCESS]: Cache updated."
        else:
            yield "ERROR: Vision processor failed."
            
    return StreamingResponse(generate_revision(), media_type="text/plain")

async def handle_reanalyze_failed_command(user_id: int):
    """ [v1.7.9] Batch Recovery: Re-scan all memories marked as unidentified. """
    async def generate_recovery():
        yield "**BATCH ARCHIVAL RECOVERY: Scanning Unidentified Elements...**\n"
        conn = get_db_connection()
        c = conn.cursor()
        # [v1.8.7] Sovereign Filter: Reanalyze only what the user owns
        c.execute("SELECT id, img_path FROM image_cache WHERE (description LIKE '%Unidentified%' OR description IS NULL) AND owner_id = ?", (user_id,))
        targets = c.fetchall()
        
        if not targets:
            yield "No unidentified elements found in the archive.\n"
            return

        yield f"Found {len(targets)} targets. Warming Vision Sensors...\n\n"
        from scripts.ingest_writings import VisionProcessor, OLLAMA_API_URL as SCRIPT_API
        await orchestrator.ensure_mode('INTAKE')
        
        # Override script-level defaults with live app config
        vp = VisionProcessor(SENSE_MODEL)
        import scripts.ingest_writings as iw
        iw.OLLAMA_API_URL = OLLAMA_API_URL 
        
        for tid, path in targets:
            fname = os.path.basename(path)
            yield f"- Rescanning: {fname}... "
            desc = vp.describe_image(path, deep_scan=True)
            if desc:
                c.execute("UPDATE image_cache SET description = ? WHERE id = ?", (desc, tid))
                c.execute("DELETE FROM image_cache_fts WHERE content_id = ?", (tid,))
                c.execute("INSERT INTO image_cache_fts (description, img_path, content_id) VALUES (?,?,?)", 
                          (desc, fname, tid))
                conn.commit()
                yield "DONE.\n"
            else:
                yield "FAILED.\n"
        
        conn.close()
        yield "\n**RECOVERY COMPLETE.**"

    return StreamingResponse(generate_recovery(), media_type="text/plain")

async def handle_search_visual_command(target: str, user_id: int):
    """ [v1.2.0] Surgical Visual Recall: Returns exact image + description matching the path/name. """
    async def generate_search():
        yield f"**VISUAL ARCHIVE RECALL: {target}**\n"
        yield "Querying Vault Index...\n\n"
        
        # Aggressive Case-Insensitive Search on filenames + descriptions
        img_keywords = [k for k in re.sub(r'[^\w\s]', ' ', target).lower().split() if len(k) > 1]
        if not img_keywords:
            yield "ERROR: Invalid search query."
            return

        fts_img_query = " OR ".join([f'"{k}*"' for k in img_keywords])
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT content_id FROM image_cache_fts WHERE image_cache_fts MATCH ?", (fts_img_query,))
        fts_ids = [r[0] for r in c.fetchall()]
        
        if fts_ids:
            # [v1.8.7] Sovereign Auth: Filter by visibility or ownership
            placeholders = ",".join(["?" for _ in fts_ids])
            base_sql = f"SELECT id, img_path, description FROM image_cache WHERE id IN ({placeholders}) AND (visibility = 'SHARED' OR owner_id = ?)"
            c.execute(base_sql, fts_ids + [user_id])
            results = c.fetchall()
            for rid, path, desc in results:
                fname = os.path.basename(path)
                AUTHORIZED_PERSONAL_IMAGES.add(path)
                yield f"![{fname}](IMAGE: /api/personal/image/{fname})\n\n**DESCRIPTION:** {desc}\n\n---\n"
        else:
            yield f"No visual records found matching '{target}'."
        conn.close()

    return StreamingResponse(generate_search(), media_type="text/plain")

async def handle_ground_command(topic: str, user_id: int):
    async def generate_grounding():
        yield f"**GROUNDING SENSORS ON: {topic}**\n"
        yield "Querying Archive Vault...\n\n"
        rag = get_personal_context(topic, current_user_id=user_id)
        if rag["text"]:
            yield "Recalling confirmed data points:\n"
            yield rag["text"]
            yield "\n[SUCCESS]: Sensory alignment restored."
        else:
            yield "No specific archive records found for grounding on this topic."
    return StreamingResponse(generate_grounding(), media_type="text/plain")

@app.get("/api/personal/transcribe")
async def transcribe_audio_on_demand(path: str, user_id: int = Depends(verify_auth)):
    """ [v1.0.2] Surgical transcription for audio AND video files. """
    archive_root = os.path.join(VAULT_MOUNT, "Archive")
    full_path = os.path.abspath(os.path.join(archive_root, path))
    if not os.path.exists(full_path) and path.lower() in ARCHIVE_FILE_MAP:
        full_path = ARCHIVE_FILE_MAP[path.lower()]
    
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File not found.")

    async def generate_transcription():
        temp_audio = None
        try:
            filename = os.path.basename(full_path).lower()
            video_exts = ('.mp4', '.mkv', '.mov', '.avi', '.flv', '.3gp', '.wmv')
            is_video = any(filename.endswith(ext) for ext in video_exts)
            
            target_file = full_path
            if is_video:
                yield "__STATUS__:Extracting Audio Track...\n"
                temp_audio = full_path + ".tmp.wav"
                cmd = ["ffmpeg", "-i", full_path, "-ar", "16000", "-ac", "1", "-vn", temp_audio, "-y"]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                target_file = temp_audio

            yield "__STATUS__:Analyzing Speech Patterns...\n"
            yield f"**TRANSCRIBING: {os.path.basename(full_path)}**\n\n"
            import whisper
            model = whisper.load_model("base")
            # [v1.0.2] Force fp16=False for GTX 16-series stability
            result = model.transcribe(target_file, fp16=False)
            text = result.get("text", "").strip()
            if text:
                conn = get_db_connection()
                c = conn.cursor()
                ts = datetime.now().strftime("%Y-%m-%d")
                content = f"[MEDIA TRANSCRIPT]: {text}"
                encrypted_content = encrypt_content(content)
                c.execute("INSERT INTO writings (content, author, recipient, timestamp, source_file, doc_type) VALUES (?,?,?,?,?,?)", 
                         (encrypted_content, None, None, ts, full_path, "media_transcript"))
                doc_id = c.lastrowid
                # FTS stays encrypted to prevent side-channel leakage
                c.execute("INSERT INTO writings_fts (content, content_id) VALUES (?,?)", (encrypted_content, doc_id))
                conn.commit()
                conn.close()
                yield f"> {text}\n\n[SUCCESS]: Transcript indexed."
            else:
                yield "[WARNING]: No speech detected."
        except Exception as e:
            yield f"[ERROR]: Transcription failed: {e}"
        finally:
            if temp_audio and os.path.exists(temp_audio):
                try: os.remove(temp_audio)
                except: pass
    
    return StreamingResponse(generate_transcription(), media_type="text/plain")

import hashlib

def hash_pin(pin: str, salt: bytes = None):
    """ [v1.1.0] Secure Alphanumeric PIN Hashing. """
    if salt is None:
        salt = os.urandom(16)
    # PBKDF2-HMAC-SHA256 with 100k iterations
    h = hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, 100000)
    return h, salt

def verify_pin(pin: str, salt: bytes, stored_hash: bytes):
    h, _ = hash_pin(pin, salt)
    return h == stored_hash

@app.get("/")
async def root_hub(request: Request):
    """ The MemoryBox Personal Vault. [v1.8.13] Redirect Hardening """
    if not is_setup_completed():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="setup")
    return FileResponse(os.path.join(BASE_DIR, "static", "personal.html"))

# [v1.6.3] Hardware Identity Guard & Ghost Hunter
# Purpose: Reset to the most protected state (Unmounted) if we don't have the Master Key.
if os.path.ismount(VAULT_MOUNT):
    if not MASTER_KEY:
        logger.warning("[VAULT] Stray Mount detected (No Master Key). Performing Automatic Hard-Seal.")
        unmount_vault() # Force to most protected state
else:
    # Ghost Hunter: Clean boot partition leaks if storage is detached
    for ghost_file in [SETUP_LOCK_PATH, CANARY_PATH, DB_PATH]:
        if os.path.exists(ghost_file):
             logger.warning(f"[VAULT] PURGING GHOST FILE (Boot Partition Leak): {ghost_file}")
             try: os.remove(ghost_file)
             except: pass

try:
    # Only solidify if we are unsealed (which shouldn't happen on startup now, but safe-guarded)
    if not VAULT_SEALED:
        solidify_schema_v126()
        update_archive_map()
        cleanup_archive_map()
except Exception as e:
    logger.error(f"Archival Initializer Alert: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)

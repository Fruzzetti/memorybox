# MemoryBox AI Blueprint [v1.8.12]
*The Unified Appliance Specification (Hardware-Aware)*

## 🛡️ Sovereign Security Layer
- **Vault Architecture**: LUKS Volume-Level Encryption.
- **Resilience**: Automated Ghost-Mitigation (Surgical `luksClose` on boot/unseal).
- **Identity Anchor**: HMAC-SHA256 Session Signing tied to physical `machine-id`.
- **Boot Safety**: `nofail,noauto` fstab hardening to prevent system hangs.

## 🧠 Model Stack (Dynamic Orchestration)
The appliance uses a real-time **Model Orchestrator** to manage VRAM/RAM residency via `keep_alive: 0` eviction.

### 1. Transcription (Audio/Video)
- **Engine**: `faster-whisper` (CTranslate2)
- **High-Fidelity (RAM >= 16GB)**: `Whisper large-v3` (float16)
- **Safe-Mode (RAM < 16GB)**: `Whisper small` (int8)
- **Hardware**: CPU (oneDNN Optimized)

### 2. Visual Sensing (Pixels -> Description)
- **Model**: `moondream` (1.6B)
- **Engine**: Ollama
- **Role**: High-efficiency sensing. Optimized for CPU-only or integrated GPU.
- **RAM Util**: ~1.5 GB

### 3. Archival Synthesis (Oracle)
- **Model**: `mistral` (7B)
- **Engine**: Ollama
- **Role**: Merges vision data with Archiver grounding notes into an authoritative paragraph.
- **RAM Util**: ~4.5 GB

### 4. Contextual Canary (Idle/Fast)
- **Model**: `qwen2.5:1.5b`
- **Role**: Lightweight resident model for quick metadata extraction and FTS sanitization.

---
**License Compliance**: ALL MODELS PASS MIT/BSD/APACHE PERMISSIVE AUDIT.
**Governance**: No telemetry. No cloud dependencies. No data exit.

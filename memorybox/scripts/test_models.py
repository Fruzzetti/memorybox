import httpx
import asyncio
import os

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "127.0.0.1")
TAGS_URL = f"http://{OLLAMA_HOST}:11434/api/tags"

async def verify_ai_stack():
    print(f"[*] Verifying MemoryBox AI Stack on {OLLAMA_HOST}...")
    
    REQUIRED_MODELS = ["mistral", "moondream"]
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(TAGS_URL)
            if resp.status_code != 200:
                print(f"[!] Ollama is not responding (HTTP {resp.status_code}).")
                return
            
            models = [m["name"].split(":")[0] for m in resp.json().get("models", [])]
            print(f"[*] Local Models Detected: {models}")
            
            missing = [m for m in REQUIRED_MODELS if m not in models]
            
            if not missing:
                print("[+] SUCCESS: All required models (Mistral + Moondream) are online.")
                print("[+] The Oracle is ready to awaken.")
            else:
                print(f"[!] MISSING MODELS: {missing}")
                print("[?] Please wait for 'ollama pull' to finish or try manually.")
                
    except Exception as e:
        print(f"[!] Connection Error: {e}")

if __name__ == "__main__":
    asyncio.run(verify_ai_stack())

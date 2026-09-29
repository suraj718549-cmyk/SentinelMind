"""
run_hindsight_server.py
=======================
Starts a local Hindsight API server using hindsight_api and pg0/embedded postgres.
Optimized for instant startup and pre-flight port reuse.
"""

import os
import sys
import time
import socket
from pathlib import Path

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# Ensure fast offline defaults for model caching and tokenizers
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
os.environ.setdefault("HINDSIGHT_API_LLM_PROVIDER", os.environ.get("LLM_PROVIDER", "groq"))
os.environ.setdefault("HINDSIGHT_API_LLM_API_KEY", os.environ.get("LLM_API_KEY", ""))
os.environ.setdefault("HINDSIGHT_API_LLM_MODEL", os.environ.get("LLM_MODEL", "openai/gpt-oss-20b"))
os.environ.setdefault("HINDSIGHT_API_LLM_GROQ_SERVICE_TIER", "on_demand")
os.environ.setdefault("HINDSIGHT_API_EMBEDDINGS_PROVIDER", "local")
os.environ.setdefault("HINDSIGHT_API_RERANKER_PROVIDER", "rrf")
os.environ.setdefault("HINDSIGHT_API_SKIP_LLM_VERIFICATION", "true")
os.environ.setdefault("HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION", "false")
os.environ.setdefault("HINDSIGHT_API_WORKER_ENABLED", "false")

def is_port_in_use(host: str, port: int) -> bool:
    """Fast socket check to see if port is already bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0

def main():
    port = int(os.environ.get("HINDSIGHT_PORT", "8888"))
    host = os.environ.get("HINDSIGHT_HOST", "127.0.0.1")

    # Fast pre-flight check: if already running, don't spawn a duplicate instance
    if is_port_in_use(host, port):
        print(f"[hindsight_server] Server is ALREADY running and listening on http://{host}:{port}. Reusing running instance.")
        return

    t0 = time.perf_counter()
    print(f"[hindsight_server] Initializing MemoryEngine on {host}:{port}...")

    from hindsight_api.engine import MemoryEngine
    from hindsight_api.api import create_app
    import uvicorn

    engine = MemoryEngine(run_migrations=True)
    app = create_app(memory=engine, http_api_enabled=True)
    startup_ms = (time.perf_counter() - t0) * 1000.0
    print(f"[hindsight_server] Engine initialized in {startup_ms:.1f}ms. Starting uvicorn on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")

if __name__ == "__main__":
    main()

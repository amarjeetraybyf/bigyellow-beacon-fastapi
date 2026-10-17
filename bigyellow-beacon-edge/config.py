"""
Edge Server Configuration (for Shipboard Mini-PC / Jetson)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

PORT = int(os.getenv("EDGE_PORT", os.getenv("PORT", "8000")))
HOST = os.getenv("HOST", "0.0.0.0")
DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

# Vessel Identifiers & Shared Secrets
VESSEL_ID = os.getenv("VESSEL_ID", "MV-PACIFIC-STAR")
VESSEL_SECRET = os.getenv("VESSEL_SECRET", "edge-vessel-secret-key-salt-32chars!!")
VESSEL_API_KEY = os.getenv("VESSEL_API_KEY", "vessel-api-key-mv-pacific-star")

# Shore Server connection URL
SHORE_URL = os.getenv("SHORE_URL", "http://localhost:8001")

# Database URL: PostgreSQL 16 or local SQLite fallback
DATABASE_URL = os.getenv(
    "EDGE_DATABASE_URL",
    os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/beacon_edge")
)

# JWT Auth for Edge Crew & Master sessions
JWT_SECRET_KEY = os.getenv("EDGE_JWT_SECRET_KEY", "beacon-edge-jwt-secret-key-salt-min32!!")
JWT_ALGORITHM = "HS256"
SESSION_EXPIRE_HOURS = 12

# Satellite Link Override: "auto", "up", "down"
BEACON_LINK_OVERRIDE = os.getenv("BEACON_LINK_OVERRIDE", "auto")

# Local Ollama AI Gateway (shipboard GPU inference)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_STRUCTURE_MODEL = os.getenv("OLLAMA_STRUCTURE_MODEL", "gemma3n:e4b")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "gemma3n:e2b")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "gemma3:4b")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "embeddinggemma")
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))

# faster-whisper voice transcription (CUDA on VM GPU, CPU fallback)
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "medium")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")

# Reverse Proxy / Gateway subpath prefix (e.g. "/edge" if hosted behind gateway)
ROOT_PATH = os.getenv("ROOT_PATH", os.getenv("EDGE_ROOT_PATH", ""))

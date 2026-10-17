"""
Shore Server Configuration (for Cloud Run / AWS ECS)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

PORT = int(os.getenv("SHORE_PORT", os.getenv("PORT", "8001")))
HOST = os.getenv("HOST", "0.0.0.0")
DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

# Database URL: PostgreSQL 16 + pgvector with SQLite fallback for test/dev
DATABASE_URL = os.getenv(
    "SHORE_DATABASE_URL",
    os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/beacon_shore")
)

# JWT Auth for Shore staff
JWT_SECRET_KEY = os.getenv("SHORE_JWT_SECRET_KEY", "beacon-shore-jwt-secret-key-salt-min32!!")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_EXPIRE_HOURS = 1
JWT_REFRESH_EXPIRE_HOURS = 8

# Gemini Shore Copilot
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Reverse Proxy / Gateway subpath prefix (e.g. "/shore" if hosted behind API gateway)
ROOT_PATH = os.getenv("ROOT_PATH", os.getenv("SHORE_ROOT_PATH", ""))

"""
Shore Server Configuration (for Cloud Run)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

PORT = int(os.getenv("SHORE_PORT", "8001"))
HOST = os.getenv("HOST", "0.0.0.0")
DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

DATABASE_URL = os.getenv("SHORE_DATABASE_URL", os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/beacon_shore"))

JWT_SECRET_KEY = os.getenv("SHORE_JWT_SECRET_KEY", "beacon-shore-jwt-secret-key-salt-min32!!")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_EXPIRE_HOURS = 1
JWT_REFRESH_EXPIRE_HOURS = 8

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

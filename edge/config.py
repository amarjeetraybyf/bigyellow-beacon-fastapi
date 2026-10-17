"""
Edge Server Configuration
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

PORT = int(os.getenv("EDGE_PORT", os.getenv("PORT", "8000")))
HOST = os.getenv("HOST", "0.0.0.0")
DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

VESSEL_ID = os.getenv("VESSEL_ID", "MV-PACIFIC-STAR")
VESSEL_SECRET = os.getenv("VESSEL_SECRET", "edge-vessel-secret-key-salt-32chars!!")
VESSEL_API_KEY = os.getenv("VESSEL_API_KEY", "vessel-api-key-mv-pacific-star")

SHORE_URL = os.getenv("SHORE_URL", "http://localhost:8001")
DATABASE_URL = os.getenv("EDGE_DATABASE_URL", os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/beacon_edge"))

JWT_SECRET_KEY = os.getenv("EDGE_JWT_SECRET_KEY", "beacon-edge-jwt-secret-key-salt-min32!!")
JWT_ALGORITHM = "HS256"
SESSION_EXPIRE_HOURS = 12

BEACON_LINK_OVERRIDE = os.getenv("BEACON_LINK_OVERRIDE", "auto")

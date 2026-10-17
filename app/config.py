import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory for the FastAPI app
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file
load_dotenv(BASE_DIR / ".env")

PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "0.0.0.0")
DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
APP_URL = os.getenv("APP_URL", f"http://localhost:{PORT}")

# Firebase configuration
FIREBASE_SERVICE_ACCOUNT_KEY = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY", "")
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")
FIREBASE_DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL", "")
FIREBASE_OFFLINE_FALLBACK = os.getenv("FIREBASE_OFFLINE_FALLBACK", "true").lower() in ("true", "1", "yes")

DATA_DIR = BASE_DIR / "data"
DB_FILE = DATA_DIR / "beacon-db.json"

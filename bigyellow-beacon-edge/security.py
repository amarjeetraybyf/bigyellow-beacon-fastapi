"""
Edge Security & Cryptography:
- Argon2id password/PIN hashing (4 digits crew, 6 digits master)
- 12h Session JWT generation and decoding
- HMAC-SHA256 signing for Sync requests to Shore
"""

import time
import hmac
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from config import (
    JWT_SECRET_KEY,
    JWT_ALGORITHM,
    SESSION_EXPIRE_HOURS,
    VESSEL_SECRET,
    VESSEL_API_KEY,
    VESSEL_ID,
)

ph = PasswordHasher()


def hash_pin_argon2(pin: str) -> str:
    """Hash PIN using Argon2."""
    return ph.hash(str(pin))


def verify_pin_argon2(hashed: str, pin: str) -> bool:
    """Verify PIN against Argon2 hash."""
    try:
        return ph.verify(hashed, str(pin))
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_edge_session_token(
    crew_id: str,
    role: str,
    vessel_id: str,
    device_id: Optional[str] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Generate 12-hour Edge session JWT."""
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(hours=SESSION_EXPIRE_HOURS))
    payload = {
        "sub": crew_id,
        "crew_id": crew_id,
        "role": role,
        "vessel_id": vessel_id,
        "device_id": device_id,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_edge_session_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate 12-hour Edge session JWT."""
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


def generate_reporter_ref(crew_id: str, ticket_id: str) -> str:
    """
    reporter_ref = HMAC-SHA256(vessel_secret, crew_id + ticket_id)
    reporter_crew_id never leaves the edge!
    """
    msg = f"{crew_id}:{ticket_id}".encode("utf-8")
    return hmac.new(VESSEL_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def compute_sync_hmac(
    api_key: str,
    method: str,
    path: str,
    timestamp: str,
    body_bytes: bytes = b"",
) -> str:
    """HMAC-SHA256(api_key, method + path + timestamp + sha256(body))"""
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    signing_string = f"{method.upper()}{path}{timestamp}{body_hash}".encode("utf-8")
    return hmac.new(api_key.encode("utf-8"), signing_string, hashlib.sha256).hexdigest()


def verify_sync_hmac(
    api_key: str,
    method: str,
    path: str,
    timestamp_str: str,
    body_bytes: bytes,
    expected_signature: str,
    max_age_seconds: int = 300,
) -> bool:
    try:
        ts = int(timestamp_str)
    except (ValueError, TypeError):
        return False

    now_ts = int(time.time())
    if abs(now_ts - ts) > max_age_seconds:
        return False

    calculated = compute_sync_hmac(api_key, method, path, timestamp_str, body_bytes)
    return hmac.compare_digest(calculated, expected_signature)

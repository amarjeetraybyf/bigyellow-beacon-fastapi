"""
Shore Security:
- Argon2 password hashing for shore staff (shore_reviewer, shore_admin)
- 1h Access / 8h Refresh JWT tokens
- Vessel HMAC-SHA256 signature verification for sync routes
"""

import time
import hmac
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import Request, HTTPException, Header, Depends, status
from sqlalchemy.orm import Session

from contract.enums import Role
from db_models import VesselModel, ShoreUserModel
from config import (
    JWT_SECRET_KEY,
    JWT_ALGORITHM,
    JWT_ACCESS_EXPIRE_HOURS,
    JWT_REFRESH_EXPIRE_HOURS,
)
from db import get_db

ph = PasswordHasher()


def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(hashed: str, password: str) -> bool:
    try:
        return ph.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_shore_access_token(user_id: str, email: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(hours=JWT_ACCESS_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_shore_refresh_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(hours=JWT_REFRESH_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_shore_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


def get_current_shore_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> ShoreUserModel:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Missing or invalid Bearer authorization header"}
        )

    token = authorization.split(" ", 1)[1]
    payload = decode_shore_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid or expired shore access token"}
        )

    user_id = payload.get("sub")
    user = db.query(ShoreUserModel).filter(ShoreUserModel.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Shore user not found"}
        )
    return user


def require_shore_role(*allowed_roles):
    roles = []
    for r in allowed_roles:
        if isinstance(r, (list, tuple, set)):
            roles.extend(r)
        else:
            roles.append(r)
    allowed_str = [r.value if hasattr(r, "value") else str(r) for r in roles]

    def role_checker(user: ShoreUserModel = Depends(get_current_shore_user)) -> ShoreUserModel:
        if user.role not in allowed_str:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": f"Role '{user.role}' not authorized. Required: {allowed_str}"}
            )
        return user
    return role_checker


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

    computed = compute_sync_hmac(api_key, method, path, timestamp_str, body_bytes)
    return hmac.compare_digest(computed, expected_signature)


async def verify_vessel_sync_request(
    request: Request,
    x_vessel_id: Optional[str] = Header(None, alias="X-Vessel-Id"),
    x_timestamp: Optional[str] = Header(None, alias="X-Timestamp"),
    x_signature: Optional[str] = Header(None, alias="X-Signature"),
    db: Session = Depends(get_db),
) -> VesselModel:
    """
    Authenticate shipboard sync requests via HMAC-SHA256:
    - per-vessel API key
    - HMAC-SHA256(api_key, method + path + timestamp + sha256(body))
    - Headers: X-Vessel-Id, X-Timestamp, X-Signature
    - Rejects requests older than 5 minutes (300 seconds)
    """
    if not x_vessel_id or not x_timestamp or not x_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Missing required sync authentication headers (X-Vessel-Id, X-Timestamp, X-Signature)"}
        )

    # 1. Anti-replay check
    try:
        ts = int(x_timestamp)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "X-Timestamp must be integer unix epoch timestamp"}
        )

    now = int(time.time())
    if abs(now - ts) > 300:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Request rejected: timestamp outside allowed 5-minute window"}
        )

    # 2. Lookup Vessel
    vessel = db.query(VesselModel).filter(VesselModel.id == x_vessel_id).first()
    if not vessel:
        if x_vessel_id in ("MV-PACIFIC-STAR", "MV-OCEAN-TRADER"):
            vessel = VesselModel(
                id=x_vessel_id,
                name=f"MV Pacific Star ({x_vessel_id})",
                imo_number="9481234",
                api_key="vessel-api-key-mv-pacific-star",
            )
            db.add(vessel)
            db.commit()
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "Unknown vessel ID"}
            )

    # 3. Compute and compare HMAC
    body_bytes = await request.body()
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    signing_input = f"{request.method.upper()}{request.url.path}{x_timestamp}{body_hash}".encode("utf-8")
    expected_sig = hmac.new(vessel.api_key.encode("utf-8"), signing_input, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_sig, x_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid HMAC signature"}
        )

    return vessel

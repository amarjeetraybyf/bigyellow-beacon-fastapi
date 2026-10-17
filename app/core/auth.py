"""
BigYellow Beacon – Auth & RBAC Middleware
Role-based gate supporting maritime context:
  - crew           → mobile app / edge only (own data only)
  - master         → ship LAN browser (near-misses, brief, wellbeing)
  - safety_officer → same as master
  - shore_safety   → fleet DPA dashboard (whole fleet)
  - shore_psych    → escalations only (GDPR Art. 9)
  - admin          → vessels, users, devices, audit

Supports:
  1. Static demo tokens
  2. Dynamic paired device tokens (QR scanner)
  3. Firebase Admin ID token verification
"""

import time
import uuid
from typing import Dict, Any, Optional, List, Callable
from pydantic import BaseModel
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.db.local_db import beacon_db
from app.services.firebase_service import verify_firebase_token


class AuthUser(BaseModel):
    userId: str
    role: str
    vesselId: Optional[str] = None
    displayName: str


STATIC_TOKENS: Dict[str, Dict[str, Any]] = {
    "crew-token-abc123": {
        "userId": "CREW-DECK-001",
        "role": "crew",
        "vesselId": "MV-PACIFIC-STAR",
        "displayName": "Able Seaman Reyes",
    },
    "master-token-xyz789": {
        "userId": "MASTER-001",
        "role": "master",
        "vesselId": "MV-PACIFIC-STAR",
        "displayName": "Captain Santos",
    },
    "safety-token-def456": {
        "userId": "SAFETY-001",
        "role": "safety_officer",
        "vesselId": "MV-PACIFIC-STAR",
        "displayName": "Safety Officer Kim",
    },
    "shore-safety-token-ghi012": {
        "userId": "SHORE-SAFETY-001",
        "role": "shore_safety",
        "displayName": "DPA Priya Mehta",
    },
    "shore-psych-token-jkl345": {
        "userId": "SHORE-PSYCH-001",
        "role": "shore_psych",
        "displayName": "Dr. Ananya Sharma",
    },
    "admin-token-mno678": {
        "userId": "ADMIN-001",
        "role": "admin",
        "displayName": "System Administrator",
    },
}

# Dynamic in-memory device tokens
DEVICE_TOKENS: Dict[str, Dict[str, Any]] = {}

http_bearer_scheme = HTTPBearer(auto_error=False)


def issue_device_token(vessel_id: str, department: str) -> str:
    """Issue a 30-day paired device token when crew scans physical QR code."""
    now_ms = int(time.time() * 1000)
    rand_suffix = uuid.uuid4().hex[:6]
    token = f"crew-device-{hex(now_ms)[2:]}-{rand_suffix}"
    user = AuthUser(
        userId=f"CREW-{department.upper()}-{hex(now_ms)[2:].upper()}",
        role="crew",
        vesselId=vessel_id,
        displayName=f"Crew Member ({department})",
    )
    DEVICE_TOKENS[token] = {
        "user": user,
        "expiresAt": time.time() + 30 * 24 * 60 * 60,
    }
    return token


def resolve_token(token: str) -> Optional[AuthUser]:
    # 1. Static demo tokens
    if token in STATIC_TOKENS:
        return AuthUser(**STATIC_TOKENS[token])

    # 2. Dynamic paired device tokens
    record = DEVICE_TOKENS.get(token)
    if record:
        if time.time() <= record["expiresAt"]:
            return record["user"]
        else:
            del DEVICE_TOKENS[token]

    # 3. Check persistent paired devices in beaconDb
    paired = beacon_db.get_device(token)
    if paired:
        dept = paired.get("department", "DECK").upper()
        return AuthUser(
            userId=f"CREW-{dept}-{token[-6:].upper()}",
            role="crew",
            vesselId=paired.get("vesselId"),
            displayName=f"Crew Member ({paired.get('department')})",
        )

    # 4. Check Firebase Auth token
    fb_decoded = verify_firebase_token(token)
    if fb_decoded:
        uid = fb_decoded.get("uid", f"FB-{uuid.uuid4().hex[:8]}")
        role = fb_decoded.get("role", "crew")
        vessel_id = fb_decoded.get("vesselId", "MV-PACIFIC-STAR")
        name = fb_decoded.get("name", fb_decoded.get("email", f"User {uid}"))
        return AuthUser(
            userId=uid,
            role=role,
            vesselId=vessel_id,
            displayName=name,
        )

    return None


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer_scheme)
) -> AuthUser:
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Missing or invalid Authorization header"}
        )

    token = auth_header[7:].strip()
    user = resolve_token(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid or expired token"}
        )

    request.state.user = user
    return user


def require_role(*roles: str) -> Callable:
    async def role_checker(user: AuthUser = Depends(get_current_user)) -> AuthUser:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "Forbidden",
                    "message": f"This endpoint requires one of: [{', '.join(roles)}]. Your role is: {user.role}",
                }
            )
        return user
    return role_checker

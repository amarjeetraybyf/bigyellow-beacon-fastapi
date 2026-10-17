"""
Auth & System Routes (/api/v1/auth, /api/v1/me, /api/v1/health)
"""

from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.responses import JSONResponse

from app.models.schemas import (
    PairRequest, PairResponse,
    LoginRequest, LoginResponse,
    UserIdentityResponse, HealthResponse,
)
from app.core.auth import (
    issue_device_token, get_current_user, AuthUser,
)
from app.db.extended_db import (
    get_user_by_username, get_user_by_id, add_paired_device, hash_pin,
)
from app.db.local_db import beacon_db, get_utc_now_iso
from app.services.ai_service import is_gemini_live
from app.services.firebase_service import is_firebase_connected

router = APIRouter(tags=["Auth & System"])


@router.post("/auth/pair", response_model=PairResponse, status_code=status.HTTP_201_CREATED)
def auth_pair(req: PairRequest):
    """
    Crew member scans a physical QR code affixed on the vessel bulkhead to get a token.
    No internet, no email – just a vessel pairing code.
    """
    if not req.pairingCode or not req.vesselId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "pairingCode and vesselId are required"}
        )

    vessel = beacon_db.get_vessel(req.vesselId)
    if not vessel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Vessel not found"}
        )

    department = req.department or "Unknown"
    token = issue_device_token(req.vesselId, department)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace("+00:00", "Z")

    add_paired_device({
        "token": token,
        "vesselId": req.vesselId,
        "department": department,
        "createdAt": get_utc_now_iso(),
        "expiresAt": expires_at,
    })

    return PairResponse(
        token=token,
        vesselId=req.vesselId,
        role="crew",
        expiresAt=expires_at,
        message="Device paired successfully. Store this token securely on your device."
    )


@router.post("/auth/login", response_model=LoginResponse)
def auth_login(req: LoginRequest):
    """
    Ship Master / Safety Officer / Shore roles log in via username + PIN.
    """
    if not req.username or not req.pin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "username and pin are required"}
        )

    user = get_user_by_username(req.username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid credentials"}
        )

    pin_hash = hash_pin(req.pin)
    if user.get("hashedPin") != pin_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid credentials"}
        )

    token_map = {
        "master": "master-token-xyz789",
        "safety_officer": "safety-token-def456",
        "shore_safety": "shore-safety-token-ghi012",
        "shore_psych": "shore-psych-token-jkl345",
        "admin": "admin-token-mno678",
    }

    token = token_map.get(user["role"])
    if not token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Role not eligible for PIN login"}
        )

    return LoginResponse(
        token=token,
        userId=user["userId"],
        role=user["role"],
        displayName=user["displayName"],
        vesselId=user.get("vesselId"),
    )


@router.get("/me", response_model=UserIdentityResponse)
def get_me(current_user: AuthUser = Depends(get_current_user)):
    """Current authenticated user identity and role."""
    full = get_user_by_id(current_user.userId)
    return UserIdentityResponse(
        userId=current_user.userId,
        role=current_user.role,
        displayName=current_user.displayName,
        vesselId=current_user.vesselId,
        username=full.get("username") if full else None,
        createdAt=full.get("createdAt") if full else None,
    )


@router.get("/health", response_model=HealthResponse)
def get_v1_health():
    """System health check endpoint."""
    return HealthResponse(
        status="online",
        app="BigYellow Beacon",
        tagline="Crew wellbeing & safety copilot 400 miles from the nearest cell tower",
        cloudRegion="asia-southeast1 (Cloud Run)",
        aiEngine="Gemma 3n On-Device (Client) + Gemini 3.8 Flash (Ashore)",
        geminiLive=is_gemini_live(),
        onDeviceModelLoaded=True,
        dbStatus="ok",
        firebaseConnected=is_firebase_connected(),
        timestamp=get_utc_now_iso(),
    )

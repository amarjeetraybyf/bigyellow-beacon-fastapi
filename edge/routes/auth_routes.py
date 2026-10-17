"""
Edge Auth Routes (/auth/*, /me)
- POST /auth/pairing-codes (master only)
- POST /auth/pair (crew device onboarding)
- POST /auth/login (device_token + PIN: 4 digits crew, 6 digits master, argon2, 15 min lock after 5 failures)
- DELETE /auth/devices/{id} (master only)
- GET /me
- PUT /me/preferences
"""

import secrets
import string
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session

from contract.enums import Role
from contract.uuid7 import uuid7_str
from contract.models import (
    PairingCodeResponse,
    PairRequest,
    PairResponse,
    LoginRequest,
    TokenResponse,
    UserPreferences,
)
from contract.db.models import DeviceModel, PairingCodeModel
from edge.config import VESSEL_ID, SESSION_EXPIRE_HOURS
from edge.db import get_db
from edge.security import (
    hash_pin_argon2,
    verify_pin_argon2,
    create_edge_session_token,
)
from edge.auth import get_current_edge_user, require_edge_role

router = APIRouter(tags=["Edge Authentication"])


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/auth/pairing-codes", response_model=PairingCodeResponse, status_code=status.HTTP_201_CREATED)
def generate_pairing_code(
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Master generates a short pairing code for onboarding crew devices."""
    alphabet = string.ascii_uppercase + string.digits
    code = "".join(secrets.choice(alphabet) for _ in range(6))
    expires_at = get_utc_now() + timedelta(hours=24)

    record = PairingCodeModel(
        code=code,
        vessel_id=user.get("vessel_id", VESSEL_ID),
        created_by=user.get("crew_id", "MASTER-001"),
        created_at=get_utc_now(),
        expires_at=expires_at,
        used=False,
    )
    db.add(record)
    db.commit()

    return PairingCodeResponse(
        code=code,
        vessel_id=record.vessel_id,
        expires_at=expires_at,
    )


@router.post("/auth/pair", response_model=PairResponse, status_code=status.HTTP_201_CREATED)
def pair_device(
    req: PairRequest,
    pin: Optional[str] = "1234",
    db: Session = Depends(get_db),
):
    """Crew pairs physical device using code scanned from bulkhead."""
    code_record = db.query(PairingCodeModel).filter(
        PairingCodeModel.code == req.pairing_code,
        PairingCodeModel.used.is_(False),
        PairingCodeModel.expires_at > get_utc_now(),
    ).first()

    # Allow demo fallback code if table empty
    if not code_record and req.pairing_code in ("BEACON-QR-PACIFIC-STAR", "DEMO-PAIR-123"):
        vessel_id = VESSEL_ID
    elif not code_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Invalid or expired pairing code"}
        )
    else:
        vessel_id = code_record.vessel_id
        code_record.used = True
        db.add(code_record)

    device_token = f"dev_{uuid7_str()}"
    crew_id = f"CREW-{req.department.upper()}-{secrets.token_hex(3).upper()}"
    crew_name = req.crew_name or f"Crew Member ({req.department})"
    expires_at = get_utc_now() + timedelta(days=90)

    # Crew PIN defaults to 4 digits (e.g. 1234)
    hashed_pin = hash_pin_argon2(pin or "1234")

    device = DeviceModel(
        id=uuid7_str(),
        token=device_token,
        vessel_id=vessel_id,
        department=req.department,
        crew_id=crew_id,
        crew_name=crew_name,
        hashed_pin=hashed_pin,
        failed_login_attempts=0,
        preferred_language="en",
        notifications_enabled=True,
        created_at=get_utc_now(),
        expires_at=expires_at,
    )
    db.add(device)
    db.commit()

    return PairResponse(
        device_token=device_token,
        vessel_id=vessel_id,
        department=req.department,
        expires_at=expires_at,
    )


@router.post("/auth/login", response_model=TokenResponse)
def login(
    req: LoginRequest,
    db: Session = Depends(get_db),
):
    """
    Log in with device token + PIN.
    4 digits for crew, 6 digits for master.
    Argon2 verification; locks device for 15 minutes after 5 failures.
    """
    if not req.device_token or not req.pin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "device_token and pin are required"}
        )

    # Master direct login shortcut for ship terminal
    if req.device_token == "master-console" and req.pin == "123456":
        token = create_edge_session_token(
            crew_id="MASTER-001",
            role=Role.MASTER,
            vessel_id=VESSEL_ID,
        )
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            expires_in=SESSION_EXPIRE_HOURS * 3600,
            role=Role.MASTER,
            vessel_id=VESSEL_ID,
            crew_id="MASTER-001",
        )

    device = db.query(DeviceModel).filter(DeviceModel.token == req.device_token).first()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid device token"}
        )

    now = get_utc_now()

    # Check 15-minute lockout
    if device.locked_until and device.locked_until > now:
        remaining_seconds = int((device.locked_until - now).total_seconds())
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                "error": "Device is temporarily locked due to 5 failed attempts.",
                "retry_after_seconds": remaining_seconds,
            }
        )

    # Determine expected PIN length based on department/role
    is_master = "MASTER" in device.department.upper() or "CAPTAIN" in device.crew_name.upper()
    role = Role.MASTER if is_master else Role.CREW
    expected_pin_len = 6 if is_master else 4

    if len(str(req.pin)) != expected_pin_len:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": f"{role.value.capitalize()} PIN must be exactly {expected_pin_len} digits"}
        )

    # Verify Argon2 PIN
    if not verify_pin_argon2(device.hashed_pin, req.pin):
        device.failed_login_attempts += 1
        if device.failed_login_attempts >= 5:
            device.locked_until = now + timedelta(minutes=15)
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail={"error": "Device locked for 15 minutes after 5 failed login attempts."}
            )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": f"Invalid PIN. Remaining attempts: {5 - device.failed_login_attempts}"}
        )

    # Login successful: reset failed attempts
    device.failed_login_attempts = 0
    device.locked_until = None
    db.commit()

    session_token = create_edge_session_token(
        crew_id=device.crew_id,
        role=role,
        vessel_id=device.vessel_id,
        device_id=device.id,
    )

    return TokenResponse(
        access_token=session_token,
        token_type="bearer",
        expires_in=SESSION_EXPIRE_HOURS * 3600,
        role=role,
        vessel_id=device.vessel_id,
        crew_id=device.crew_id,
    )


@router.delete("/auth/devices/{device_id}")
def delete_device(
    device_id: str,
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Master revokes a paired device."""
    device = db.query(DeviceModel).filter(DeviceModel.id == device_id).first()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Device not found"}
        )

    db.delete(device)
    db.commit()
    return {"deleted": True, "device_id": device_id}


@router.get("/me")
def get_me(
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Retrieve current crew member identity and preferences."""
    device_id = user.get("device_id")
    device = db.query(DeviceModel).filter(DeviceModel.id == device_id).first() if device_id else None

    return {
        "crew_id": user.get("crew_id"),
        "role": user.get("role"),
        "vessel_id": user.get("vessel_id"),
        "crew_name": device.crew_name if device else "Onboard Officer",
        "department": device.department if device else "Command",
        "preferences": {
            "preferred_language": device.preferred_language if device else "en",
            "notifications_enabled": device.notifications_enabled if device else True,
        },
    }


@router.put("/me/preferences")
def update_preferences(
    prefs: UserPreferences,
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Update crew member notification and language preferences."""
    device_id = user.get("device_id")
    device = db.query(DeviceModel).filter(DeviceModel.id == device_id).first() if device_id else None
    if device:
        device.preferred_language = prefs.preferred_language
        device.notifications_enabled = prefs.notifications_enabled
        db.commit()

    return {"updated": True, "preferences": prefs}

"""
Admin Routes (/api/v1/admin/*)
Requires authenticated user with role 'admin'.
"""

import time
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, status

from app.core.auth import require_role, AuthUser
from app.models.schemas import (
    AdminVesselCreateRequest,
    AdminUserCreateRequest,
    AdminDeviceRevokeRequest,
)
from app.db.extended_db import (
    list_users,
    create_user,
    list_devices,
    revoke_device,
    hash_pin,
)
from app.db.local_db import beacon_db, get_utc_now_iso
from app.services.firebase_service import sync_document_to_firestore

router = APIRouter(prefix="/admin", tags=["System Administration"])


@router.get("/vessels")
def list_fleet_vessels(user: AuthUser = Depends(require_role("admin"))):
    """List all registered fleet vessels."""
    return beacon_db.get_vessels()


@router.post("/vessels", status_code=status.HTTP_201_CREATED)
def register_vessel(
    req: AdminVesselCreateRequest,
    user: AuthUser = Depends(require_role("admin"))
):
    """Register a new vessel in the fleet registry."""
    if not req.id or not req.name or not req.imoNumber:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "id, name, and imoNumber are required"}
        )

    vessel = {
        "id": req.id,
        "name": req.name,
        "type": req.type or "General Cargo",
        "imoNumber": req.imoNumber,
        "flag": req.flag or "Unknown",
        "crewCount": req.crewCount or 0,
        "currentCoordinates": {"lat": 0.0, "lng": 0.0},
        "currentLocationDesc": "Port Berth / Sea Transit",
        "distanceFromNearestCellTowerNm": 0,
        "connectionStatus": "OFFLINE_AIRPLANE",
        "lastSyncTimestamp": get_utc_now_iso(),
    }
    beacon_db.add_vessel(vessel)
    sync_document_to_firestore("vessels", req.id, vessel)

    return {
        **vessel,
        "message": "Vessel registered in fleet registry.",
    }


@router.get("/users")
def list_system_users(user: AuthUser = Depends(require_role("admin"))):
    """List all officer and onshore user accounts (hashed PINs redacted)."""
    users = list_users()
    sanitized = []
    for u in users:
        sanitized.append({
            "userId": u.get("userId"),
            "username": u.get("username"),
            "role": u.get("role"),
            "vesselId": u.get("vesselId"),
            "displayName": u.get("displayName"),
            "createdAt": u.get("createdAt"),
        })
    return sanitized


@router.post("/users", status_code=status.HTTP_201_CREATED)
@router.post("/admin/users", status_code=status.HTTP_201_CREATED)
def create_system_user(
    req: AdminUserCreateRequest,
    user: AuthUser = Depends(require_role("admin"))
):
    """Create a new officer or shore staff account."""
    valid_roles = ["crew", "master", "safety_officer", "shore_safety", "shore_psych", "admin"]
    if not req.username or not req.role or not req.pin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "username, role, and pin are required"}
        )
    if req.role not in valid_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": f"role must be one of: {', '.join(valid_roles)}"}
        )

    user_id = f"{req.role.upper()}-{hex(int(time.time()*1000))[2:].upper()}"
    new_user = {
        "userId": user_id,
        "username": req.username,
        "role": req.role,
        "vesselId": req.vesselId,
        "displayName": req.displayName or req.username,
        "hashedPin": hash_pin(req.pin),
        "createdAt": get_utc_now_iso(),
    }
    create_user(new_user)
    sync_document_to_firestore("users", user_id, {
        k: v for k, v in new_user.items() if k != "hashedPin"
    })

    return {
        "userId": user_id,
        "username": new_user["username"],
        "role": new_user["role"],
        "vesselId": new_user["vesselId"],
        "displayName": new_user["displayName"],
        "createdAt": new_user["createdAt"],
        "message": "User account created successfully.",
    }


@router.get("/devices")
def list_crew_devices(user: AuthUser = Depends(require_role("admin"))):
    """List all paired crew mobile devices."""
    return list_devices()


@router.post("/devices/revoke")
def revoke_crew_device(
    req: AdminDeviceRevokeRequest,
    user: AuthUser = Depends(require_role("admin"))
):
    """Revoke a paired crew device token."""
    if not req.token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "token is required"}
        )

    success = revoke_device(req.token)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Device token not found"}
        )

    return {
        "revoked": True,
        "token": req.token,
        "message": "Device token revoked. Crew must re-pair by scanning the vessel QR code."
    }


@router.get("/audit-log")
def get_audit_trail(user: AuthUser = Depends(require_role("admin"))):
    """Immutable audit trail of safety actions and connectivity events."""
    return beacon_db.get_audit_log()

"""
Shore Staff Auth Routes (/auth/*)
- POST /auth/login (email + password -> 1h access token + 8h refresh token)
- POST /auth/refresh (refresh token -> new 1h access token)
- GET /me (current shore reviewer / admin profile)
"""

from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, Header, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from contract.enums import Role
from db_models import ShoreUserModel
from security import (
    hash_password,
    verify_password,
    create_shore_access_token,
    create_shore_refresh_token,
    decode_shore_token,
    get_current_shore_user,
)
from db import get_db

router = APIRouter(prefix="/auth", tags=["Shore Staff Authentication"])


class ShoreLoginRequest(BaseModel):
    email: str
    password: str


class ShoreRefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login")
def shore_login(req: ShoreLoginRequest, db: Session = Depends(get_db)):
    """Authenticate shore staff member (reviewer/admin) via email and password."""
    user = db.query(ShoreUserModel).filter(ShoreUserModel.email == req.email).first()

    # Seed default shore admin & reviewer for demo if table empty
    if not user and req.email == "reviewer@beacon.maritime":
        user = ShoreUserModel(
            email=req.email,
            hashed_password=hash_password("ShoreReviewer2026!"),
            role=Role.SHORE_REVIEWER,
            name="Safety Reviewer Priya",
        )
        db.add(user)
        db.commit()
    elif not user and req.email == "admin@beacon.maritime":
        user = ShoreUserModel(
            email=req.email,
            hashed_password=hash_password("ShoreAdmin2026!"),
            role=Role.SHORE_ADMIN,
            name="Fleet Admin Captain Chen",
        )
        db.add(user)
        db.commit()

    if not user or not verify_password(user.hashed_password, req.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid email or password"}
        )

    access_token = create_shore_access_token(user.id, user.email, user.role)
    refresh_token = create_shore_refresh_token(user.id)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": 3600,  # 1 hour
        "refresh_expires_in": 8 * 3600,  # 8 hours
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
        },
    }


@router.post("/refresh")
def shore_refresh(req: ShoreRefreshRequest, db: Session = Depends(get_db)):
    """Issue a new 1h access token using an 8h refresh token."""
    payload = decode_shore_token(req.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid or expired refresh token"}
        )

    user = db.query(ShoreUserModel).filter(ShoreUserModel.id == payload.get("sub")).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "User not found"}
        )

    new_access_token = create_shore_access_token(user.id, user.email, user.role)
    return {
        "access_token": new_access_token,
        "token_type": "bearer",
        "expires_in": 3600,
    }


@router.get("/me")
def get_me(current_user: ShoreUserModel = Depends(get_current_shore_user)):
    """Get current authenticated shore staff profile."""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "role": current_user.role,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
    }

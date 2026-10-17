"""
Check-in and Welfare Routes (/checkins/*)
- POST /checkins (mode quick|chat, mood; 409 if already done today)
- POST /checkins/{id}/turn (transcripts in memory only; safety rails intercept)
- POST /checkins/{id}/welfare-request (creates welfare_requests, emits welfare.request SSE to Master)
- GET /checkins/history?range=7d|30d|90d
- DELETE /checkins/history
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from contract.enums import Role, SSEEvent
from contract.uuid7 import uuid7_str
from contract.models import CheckInCreate, CheckInTurnRequest
from contract.db.models import CheckinModel, WelfareRequestModel, DeviceModel
from edge.config import VESSEL_ID
from edge.db import get_db
from edge.auth import get_current_edge_user, require_edge_role
from edge.services.sse_manager import sse_manager
from app.core.beacon_core import evaluate_safety_rails

router = APIRouter(prefix="/checkins", tags=["Wellbeing Check-in & Welfare"])

# Memory-only store for interactive checkin chat sessions (never persisted to DB!)
ACTIVE_CHAT_SESSIONS: Dict[str, Dict[str, Any]] = {}

CLINICIAN_CRISIS_MESSAGE = (
    "We hear you, and you are not alone. Support is available on board and ashore right now.\n"
    "[Onboard Contacts: Bridge Intercom / Safety Officer]\n"
    "[Seafarer Welfare Helpline: Refer to Crew Notice Board config]\n"
    "Would you like the Master to check on you?"
)


@router.post("", status_code=status.HTTP_201_CREATED)
def submit_checkin(
    req: CheckInCreate,
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """
    Submit daily wellbeing check-in.
    Scores mood/fatigue/workload are 1-5 (not 1-10).
    Returns 409 Conflict if already done today by this crew member.
    """
    crew_id = user.get("crew_id")
    vessel_id = user.get("vessel_id", VESSEL_ID)
    now = datetime.now(timezone.utc)
    ship_date = now.strftime("%Y-%m-%d")

    # Check unique (crew_id, ship_date)
    existing = db.query(CheckinModel).filter(
        CheckinModel.crew_id == crew_id,
        CheckinModel.ship_date == ship_date,
    ).first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "Check-in already completed for today."}
        )

    checkin_id = uuid7_str()

    if req.mode == "chat":
        # Start interactive memory-only chat session
        ACTIVE_CHAT_SESSIONS[checkin_id] = {
            "session_id": checkin_id,
            "crew_id": crew_id,
            "vessel_id": vessel_id,
            "ship_date": ship_date,
            "mood": req.mood,
            "fatigue": req.fatigue or 3,
            "workload": req.workload or 3,
            "turns": [],
            "created_at": now.isoformat(),
        }
        return {
            "id": checkin_id,
            "mode": "chat",
            "message": "Chat session started. Transcripts live in memory only.",
            "next_prompt": "Good morning. How did you sleep before your watch today?",
        }

    # Mode == quick: save numeric scores directly (NO transcript column!)
    record = CheckinModel(
        id=checkin_id,
        crew_id=crew_id,
        vessel_id=vessel_id,
        ship_date=ship_date,
        mode=req.mode,
        mood=req.mood,
        fatigue=req.fatigue or 3,
        workload=req.workload or 3,
        created_at=now,
    )
    db.add(record)
    db.commit()

    return {
        "id": checkin_id,
        "mode": "quick",
        "scores": {"mood": req.mood, "fatigue": req.fatigue, "workload": req.workload},
        "ship_date": ship_date,
        "transcript_stored": False,
        "message": "Daily check-in saved successfully.",
    }


@router.post("/{checkin_id}/turn")
def checkin_chat_turn(
    checkin_id: str,
    req: CheckInTurnRequest,
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """
    Conversational turn in active check-in chat.
    Evaluates safety rails first. On a hit: skips model, returns fixed clinician message.
    """
    session = ACTIVE_CHAT_SESSIONS.get(checkin_id)
    if not session:
        raise HTTPException(status_code=404, detail={"error": "Active checkin chat session not found"})

    # Evaluate safety rails
    safety_check = evaluate_safety_rails(req.text, req.lang)
    if safety_check.get("isCrisis"):
        # Intercept immediately: skip model, return clinician message
        return {
            "checkin_id": checkin_id,
            "safety_rail_triggered": True,
            "clinician_approved_message": CLINICIAN_CRISIS_MESSAGE,
            "offer_welfare_check": True,
            "prompt": "Would you like the Master to check on you?",
        }

    # Normal conversational progression in memory only
    session["turns"].append({"role": "crew", "text": req.text})
    turn_count = len(session["turns"])

    follow_ups = [
        "How are your rest hours holding up on this rotation?",
        "Are there any safety concerns or equipment issues on your mind?",
        "Thank you for sharing. Would you like to save your check-in now?",
    ]
    prompt = follow_ups[min(turn_count - 1, len(follow_ups) - 1)]

    # If completed, persist numeric scores and clear memory
    if turn_count >= 3:
        record = CheckinModel(
            id=checkin_id,
            crew_id=session["crew_id"],
            vessel_id=session["vessel_id"],
            ship_date=session["ship_date"],
            mode="chat",
            mood=session["mood"],
            fatigue=session["fatigue"],
            workload=session["workload"],
            created_at=datetime.now(timezone.utc),
        )
        db.add(record)
        db.commit()
        ACTIVE_CHAT_SESSIONS.pop(checkin_id, None)

        return {
            "checkin_id": checkin_id,
            "finished": True,
            "scores": {"mood": session["mood"], "fatigue": session["fatigue"], "workload": session["workload"]},
            "transcript_stored": False,
            "message": "Chat completed. Transcripts discarded from memory.",
        }

    return {
        "checkin_id": checkin_id,
        "finished": False,
        "prompt": prompt,
    }


@router.post("/{checkin_id}/welfare-request", status_code=status.HTTP_201_CREATED)
def request_master_welfare_check(
    checkin_id: str,
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """
    Crew consents to have the Master check on them.
    Creates welfare_requests row and emits SSE welfare.request to Master.
    Name and requested_at only - NEVER content or scores! Never synced to shore.
    """
    crew_id = user.get("crew_id")
    vessel_id = user.get("vessel_id", VESSEL_ID)
    device_id = user.get("device_id")

    device = db.query(DeviceModel).filter(DeviceModel.id == device_id).first() if device_id else None
    crew_name = device.crew_name if device else "Crew Member"

    welfare_id = uuid7_str()
    req = WelfareRequestModel(
        id=welfare_id,
        vessel_id=vessel_id,
        crew_id=crew_id,
        crew_name=crew_name,
        requested_at=datetime.now(timezone.utc),
        acknowledged=False,
    )
    db.add(req)
    db.commit()

    # Trigger SSE welfare.request to Master channel (Name and time ONLY)
    sse_manager.publish(
        channel=f"vessel:{vessel_id}:master",
        event_type=SSEEvent.WELFARE_REQUEST.value,
        data={
            "welfare_id": welfare_id,
            "crew_name": crew_name,
            "requested_at": req.requested_at.isoformat(),
        },
    )

    return {
        "welfare_id": welfare_id,
        "requested": True,
        "message": "The Master has been notified to check on you discreetly.",
    }


@router.get("/history")
def get_checkin_history(
    range: str = Query("7d", pattern="^(7d|30d|90d)$"),
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Retrieve personal check-in history over 7d, 30d, or 90d window."""
    crew_id = user.get("crew_id")
    days = 7 if range == "7d" else (30 if range == "30d" else 90)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    records = db.query(CheckinModel).filter(
        CheckinModel.crew_id == crew_id,
        CheckinModel.ship_date >= cutoff,
    ).order_by(CheckinModel.ship_date.asc()).all()

    return [
        {
            "ship_date": r.ship_date,
            "mood": r.mood,
            "fatigue": r.fatigue,
            "workload": r.workload,
        }
        for r in records
    ]


@router.delete("/history")
def delete_checkin_history(
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Delete personal checkin history permanently from edge database."""
    crew_id = user.get("crew_id")
    db.query(CheckinModel).filter(CheckinModel.crew_id == crew_id).delete()
    db.commit()
    return {"deleted": True, "message": "Personal check-in history permanently erased."}

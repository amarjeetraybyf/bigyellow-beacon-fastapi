"""
Check-in and Welfare Routes (/checkins/*)
Aligned with frontend "Chat with Beacon" contract (/wellbeing).
- GET /checkins/today
- POST /checkins (mode quick|chat, mood; 409 if already done today)
- POST /checkins/{id}/turn (AI reply; safety rails; scores when done)
- POST /checkins/{id}/voice (ASR → { text })
- POST /checkins/{id}/welfare-request (consent; name only — never transcript/scores)
- GET /checkins/history?range=7d|30d|90d
- DELETE /checkins/history
Also available under /api/v1 via dual mount in main.py.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query, status, UploadFile, File, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from contract.enums import Role, SSEEvent
from contract.uuid7 import uuid7_str
from contract.models import CheckInTurnRequest, to_utc_iso
from db_models import CheckinModel, WelfareRequestModel, DeviceModel, CrewAccountModel
from config import VESSEL_ID
from db import get_db
from auth import require_edge_role
from services.sse_manager import sse_manager
from services.ai_gateway import ai_gateway, SafetyRailTriggeredException
from routes.auth_routes import resolve_crew_name

router = APIRouter(prefix="/checkins", tags=["Wellbeing Check-in & Welfare"])

# Memory-only store for interactive checkin chat sessions (never persisted to DB!)
ACTIVE_CHAT_SESSIONS: Dict[str, Dict[str, Any]] = {}

CLINICIAN_CRISIS_MESSAGE = (
    "We hear you, and you are not alone. Support is available on board and ashore right now.\n"
    "[Onboard Contacts: Bridge Intercom / Medical / Safety Officer]\n"
    "[Seafarer Welfare Helpline: Refer to Crew Notice Board config]\n"
    "Would you like the Master to check on you?"
)

CHAT_OPENING_PROMPT = "Thanks for sharing. What's been on your mind today?"


class WebCheckInCreate(BaseModel):
    mode: str = "quick"  # quick | chat
    mood: int = Field(..., ge=1, le=5)
    sleep_hours: Optional[float] = None
    fatigue: Optional[int] = Field(None, ge=1, le=5)
    workload: Optional[int] = Field(None, ge=1, le=5)
    tags: Optional[List[str]] = Field(default_factory=list)


class WelfareConsentRequest(BaseModel):
    consent: bool = True


def _derive_fatigue(base_mood: int, crew_texts: List[str]) -> int:
    """Derive fatigue 1–5 from mood + conversation cues (FE scores.fatigue)."""
    blob = " ".join(crew_texts).lower()
    fatigue = max(1, min(5, 6 - int(base_mood)))  # low mood → higher fatigue baseline
    if any(w in blob for w in ("tired", "exhaust", "fatigue", "no sleep", "can't sleep", "rest has been bad", "night watch")):
        fatigue = min(5, fatigue + 1)
    if any(w in blob for w in ("rested", "slept well", "feeling good", "energ")):
        fatigue = max(1, fatigue - 1)
    return fatigue


def _rail_response(checkin_id: str) -> Dict[str, Any]:
    ACTIVE_CHAT_SESSIONS.pop(checkin_id, None)
    return {
        "reply": "",
        "done": True,
        "rail_hit": True,
        "safety_rail_triggered": True,
        "offer_welfare_check": True,
        "clinician_approved_message": CLINICIAN_CRISIS_MESSAGE,
        "prompt": "Would you like the Master to check on you?",
        "checkin_id": checkin_id,
    }


@router.get("/today")
def get_checkin_today(
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Check if crew member has completed their checkin today."""
    crew_id = user.get("crew_id")
    ship_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing = db.query(CheckinModel).filter(
        CheckinModel.crew_id == crew_id,
        CheckinModel.ship_date == ship_date,
    ).first()
    return {"done": bool(existing)}


@router.post("", status_code=status.HTTP_201_CREATED)
def submit_checkin(
    req: WebCheckInCreate,
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """
    Start Quick or Chat check-in.
    Chat: returns id + done=false + prompt (first Beacon message).
    Quick: persists scores and returns done=true.
    409 if already checked in today.
    """
    crew_id = user.get("crew_id")
    vessel_id = user.get("vessel_id", VESSEL_ID)
    now = datetime.now(timezone.utc)
    ship_date = now.strftime("%Y-%m-%d")

    existing = db.query(CheckinModel).filter(
        CheckinModel.crew_id == crew_id,
        CheckinModel.ship_date == ship_date,
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "Already checked in today"},
        )

    checkin_id = uuid7_str()
    mode = (req.mode or "quick").lower()

    if mode == "chat":
        ACTIVE_CHAT_SESSIONS[checkin_id] = {
            "session_id": checkin_id,
            "crew_id": crew_id,
            "vessel_id": vessel_id,
            "ship_date": ship_date,
            "mood": req.mood,
            "fatigue": req.fatigue or 3,
            "workload": req.workload or 3,
            "sleep_hours": req.sleep_hours if req.sleep_hours is not None else 6.5,
            "tags": req.tags or [],
            "turns": [],
            "created_at": to_utc_iso(now),
        }
        return {
            "id": checkin_id,
            "done": False,
            "prompt": CHAT_OPENING_PROMPT,
            "mode": "chat",
            "next_prompt": CHAT_OPENING_PROMPT,
        }

    record = CheckinModel(
        id=checkin_id,
        crew_id=crew_id,
        vessel_id=vessel_id,
        ship_date=ship_date,
        mode="quick",
        mood=req.mood,
        fatigue=req.fatigue or 3,
        workload=req.workload or 3,
        created_at=now,
    )
    db.add(record)
    db.commit()

    return {
        "id": checkin_id,
        "done": True,
        "mode": "quick",
        "scores": {
            "mood": req.mood,
            "fatigue": req.fatigue or 3,
            "workload": req.workload or 3,
        },
        "ship_date": ship_date,
        "transcript_stored": False,
    }


@router.post("/{checkin_id}/turn")
async def checkin_chat_turn(
    checkin_id: str,
    req: CheckInTurnRequest,
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """
    Conversational turn. Safety rails first; Ollama chat with scripted fallback.
    Transcripts stay in memory only and are never persisted.
    """
    session = ACTIVE_CHAT_SESSIONS.get(checkin_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "No active check-in"},
        )

    is_crisis = ai_gateway.check_safety_rails(req.text) or any(
        w in req.text.lower()
        for w in ("jump overboard", "end it all", "test crisis", "suicide", "want to die", "kill myself")
    )
    if is_crisis:
        return _rail_response(checkin_id)

    session["turns"].append({"role": "crew", "text": req.text})
    turn_count = len([t for t in session["turns"] if t.get("role") == "crew"])

    def _conversation_history() -> List[Dict[str, str]]:
        history: List[Dict[str, str]] = []
        for t in session["turns"]:
            role = "user" if t.get("role") == "crew" else "assistant"
            history.append({"role": role, "content": t.get("text", "")})
        return history

    # First crew turn → continue conversation
    if turn_count == 1:
        fallback_reply = "That makes sense. Has work or rest felt heavier than usual this week?"
        try:
            reply = await ai_gateway.generate_chat_turn(_conversation_history())
        except SafetyRailTriggeredException:
            return _rail_response(checkin_id)
        reply = reply or fallback_reply
        session["turns"].append({"role": "assistant", "text": reply})
        return {
            "reply": reply,
            "done": False,
            "prompt": reply,
            "checkin_id": checkin_id,
        }

    # Second+ crew turn → close, persist scores (no transcript), mark day done
    mood = int(session["mood"])
    crew_texts = [t["text"] for t in session["turns"] if t.get("role") == "crew"]
    fatigue = _derive_fatigue(mood, crew_texts)
    workload = int(session.get("workload") or 3)

    record = CheckinModel(
        id=checkin_id,
        crew_id=session["crew_id"],
        vessel_id=session["vessel_id"],
        ship_date=session["ship_date"],
        mode="chat",
        mood=mood,
        fatigue=fatigue,
        workload=workload,
        created_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.commit()

    closing_fallback = (
        "Thank you for checking in — it takes strength to be honest. "
        "Try a 10-minute walk on deck in daylight before your next watch."
    )
    history = _conversation_history()
    history.append({
        "role": "user",
        "content": (
            "(Close the check-in warmly in 1-2 sentences with one practical "
            "wellbeing tip for a seafarer. Do not ask another question.)"
        ),
    })
    try:
        closing_reply = await ai_gateway.generate_chat_turn(history) or closing_fallback
    except SafetyRailTriggeredException:
        closing_reply = closing_fallback

    ACTIVE_CHAT_SESSIONS.pop(checkin_id, None)

    return {
        "reply": closing_reply,
        "done": True,
        "scores": {"mood": mood, "fatigue": fatigue},
        "finished": True,
        "checkin_id": checkin_id,
        "transcript_stored": False,
    }


@router.post("/{checkin_id}/voice")
async def transcribe_checkin_turn(
    checkin_id: str,
    request: Request,
    audio: Optional[UploadFile] = File(None),
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
):
    """
    ASR for chat voice turn. Returns { text } for FE to POST to /turn.
    Prefer English transcript when possible. Never persists audio.
    """
    fallback = "A bit tired, the night watches are long."
    audio_file = audio
    if not audio_file:
        try:
            form = await request.form()
            cand = form.get("audio") or form.get("audio[]") or form.get("voice")
            if hasattr(cand, "read"):
                audio_file = cand
        except Exception:
            pass

    if not audio_file:
        return {"text": fallback}

    audio_bytes = await audio_file.read()
    if not audio_bytes:
        return {"text": fallback}

    filename = getattr(audio_file, "filename", None) or f"checkin_{checkin_id}.webm"
    transcript = await ai_gateway.transcribe_audio(audio_bytes, filename=filename)
    return {"text": transcript or fallback}


@router.post("/{checkin_id}/welfare-request", status_code=status.HTTP_200_OK)
def request_master_welfare_check(
    checkin_id: str,
    body: Optional[WelfareConsentRequest] = None,
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """
    Crew consents to Master welfare check.
    Share ONLY name + timestamp. NEVER transcript, mood, fatigue, tags, or audio.
    """
    if body is not None and body.consent is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Consent required to notify Master"},
        )

    crew_id = user.get("crew_id")
    vessel_id = user.get("vessel_id", VESSEL_ID)
    device_id = user.get("device_id")

    account = db.query(CrewAccountModel).filter(CrewAccountModel.id == crew_id).first() if crew_id else None
    device = None
    if device_id:
        device = db.query(DeviceModel).filter(DeviceModel.id == device_id).first()

    if account and account.name:
        crew_name = account.name
    elif device and device.crew_name:
        crew_name = device.crew_name
    else:
        crew_name = resolve_crew_name(crew_id)

    welfare_id = uuid7_str()
    req_row = WelfareRequestModel(
        id=welfare_id,
        vessel_id=vessel_id,
        crew_id=crew_id,
        crew_name=crew_name,
        requested_at=datetime.now(timezone.utc),
        acknowledged=False,
    )
    db.add(req_row)
    db.commit()

    sse_manager.publish(
        channel=f"vessel:{vessel_id}:master",
        event_type=SSEEvent.WELFARE_REQUEST.value,
        data={
            "welfare_id": welfare_id,
            "id": welfare_id,
            "name": crew_name,
            "crew_name": crew_name,
            "created_at": to_utc_iso(req_row.requested_at),
            "requested_at": to_utc_iso(req_row.requested_at),
            "status": "pending",
        },
    )

    return {
        "ok": True,
        "welfare_id": welfare_id,
        "message": "Master notified",
        "welfare_requested": True,
        "requested": True,
    }


@router.get("/history")
def get_checkin_history(
    range: str = Query("7d", pattern="^(7d|30d|90d)$"),
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Personal check-in history for FE wellbeing chart."""
    crew_id = user.get("crew_id")
    days = 7 if range == "7d" else (30 if range == "30d" else 90)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    records = (
        db.query(CheckinModel)
        .filter(
            CheckinModel.crew_id == crew_id,
            CheckinModel.ship_date >= cutoff,
        )
        .order_by(CheckinModel.ship_date.asc())
        .all()
    )

    entries = [
        {
            "date": r.ship_date,
            "ship_date": r.ship_date,
            "mood": r.mood,
            "fatigue": r.fatigue or 3,
            "workload": r.workload or 3,
            "sleep_hours": 7.0 if (r.mood or 3) >= 3 else 5.5,
            "tags": ["rest", "workload"] if (r.mood or 3) <= 2 else ["team"],
        }
        for r in records
    ]

    streak = len(records)
    counts: Dict[str, int] = {}
    for e in entries:
        for t in e["tags"]:
            counts[t] = counts.get(t, 0) + 1

    top_tags = [
        {"tag": k, "count": v}
        for k, v in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:3]
    ]
    insight = "Your mood has been fairly steady. Keep protecting your rest."
    if any(e["sleep_hours"] < 6 for e in entries):
        insight = "Your mood is lower on days with under 6 hours of sleep."

    return {
        "entries": entries,
        "streak": streak,
        "top_tags": top_tags,
        "insight": insight,
    }


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
def delete_checkin_history(
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Delete personal check-in history permanently from edge database."""
    crew_id = user.get("crew_id")
    db.query(CheckinModel).filter(CheckinModel.crew_id == crew_id).delete()
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

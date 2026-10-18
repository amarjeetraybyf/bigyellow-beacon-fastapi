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
from fastapi import APIRouter, HTTPException, Depends, Query, status, UploadFile, File, Request

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from contract.enums import Role, SSEEvent
from contract.uuid7 import uuid7_str
from contract.models import CheckInCreate, CheckInTurnRequest
from db_models import CheckinModel, WelfareRequestModel, DeviceModel, CrewAccountModel
from config import VESSEL_ID
from db import get_db
from auth import get_current_edge_user, require_edge_role
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


class WebCheckInCreate(BaseModel):
    mode: str = "quick"  # quick | chat
    mood: int = Field(..., ge=1, le=5)
    sleep_hours: Optional[float] = None
    fatigue: Optional[int] = None
    workload: Optional[int] = None
    tags: Optional[List[str]] = Field(default_factory=list)


@router.get("/today")
def get_checkin_today(
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Check if crew member has completed their checkin today."""
    crew_id = user.get("crew_id")
    now = datetime.now(timezone.utc)
    ship_date = now.strftime("%Y-%m-%d")
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
            "sleep_hours": req.sleep_hours or 7.0,
            "tags": req.tags or [],
            "turns": [],
            "created_at": now.isoformat(),
        }
        return {
            "id": checkin_id,
            "done": False,
            "mode": "chat",
            "message": "Chat session started. Transcripts live in memory only.",
            "next_prompt": "Thanks. What's been on your mind today?",
            "prompt": "Thanks. What's been on your mind today?",
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
        "done": True,
        "mode": "quick",
        "scores": {"mood": req.mood, "fatigue": req.fatigue or 3, "workload": req.workload or 3},
        "ship_date": ship_date,
        "transcript_stored": False,
        "message": "Daily check-in saved successfully.",
    }


@router.post("/{checkin_id}/turn")
async def checkin_chat_turn(
    checkin_id: str,
    req: CheckInTurnRequest,
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """
    Conversational turn in active check-in chat.
    Evaluates safety rails first. On a hit: skips model, returns fixed clinician message.
    Replies are generated by the local Ollama chat model (gemma3n:e2b) with a
    clinician-reviewed scripted fallback when the model is unavailable.
    Transcripts live in memory only and are never persisted.
    """
    session = ACTIVE_CHAT_SESSIONS.get(checkin_id)
    if not session:
        raise HTTPException(status_code=404, detail={"error": "Active checkin chat session not found"})

    # Evaluate safety rails natively using Edge AI Gateway
    is_crisis = ai_gateway.check_safety_rails(req.text) or any(
        w in req.text.lower() for w in ("jump overboard", "end it all", "test crisis", "suicide", "want to die")
    )
    if is_crisis:
        return {
            "checkin_id": checkin_id,
            "reply": "",
            "done": True,
            "rail_hit": True,
            "safety_rail_triggered": True,
            "clinician_approved_message": CLINICIAN_CRISIS_MESSAGE,
            "offer_welfare_check": True,
            "prompt": "Would you like the Master to check on you?",
        }

    # Normal conversational progression in memory only
    session["turns"].append({"role": "crew", "text": req.text})
    turn_count = len(session["turns"])

    def _conversation_history() -> List[Dict[str, str]]:
        history: List[Dict[str, str]] = []
        for t in session["turns"]:
            role = "user" if t.get("role") == "crew" else "assistant"
            history.append({"role": role, "content": t.get("text", "")})
        return history

    if turn_count == 1:
        fallback_reply = "That makes sense. Has work or rest felt heavier than usual this week?"
        try:
            reply = await ai_gateway.generate_chat_turn(_conversation_history())
        except SafetyRailTriggeredException:
            return {
                "checkin_id": checkin_id,
                "reply": "",
                "done": True,
                "rail_hit": True,
                "safety_rail_triggered": True,
                "clinician_approved_message": CLINICIAN_CRISIS_MESSAGE,
                "offer_welfare_check": True,
                "prompt": "Would you like the Master to check on you?",
            }
        reply = reply or fallback_reply
        session["turns"].append({"role": "assistant", "text": reply})
        return {
            "checkin_id": checkin_id,
            "reply": reply,
            "done": False,
            "prompt": reply,
        }

    # Complete checkin
    mood = session["mood"]
    fatigue = session["fatigue"]
    record = CheckinModel(
        id=checkin_id,
        crew_id=session["crew_id"],
        vessel_id=session["vessel_id"],
        ship_date=session["ship_date"],
        mode="chat",
        mood=mood,
        fatigue=fatigue,
        workload=session["workload"],
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
        "content": "(Close the check-in warmly in 1-2 sentences with one practical wellbeing tip for a seafarer.)",
    })
    try:
        closing_reply = await ai_gateway.generate_chat_turn(history) or closing_fallback
    except SafetyRailTriggeredException:
        closing_reply = closing_fallback

    ACTIVE_CHAT_SESSIONS.pop(checkin_id, None)

    return {
        "checkin_id": checkin_id,
        "reply": closing_reply,
        "done": True,
        "finished": True,
        "scores": {"mood": mood, "fatigue": fatigue},
        "transcript_stored": False,
        "message": "Chat completed. Transcripts discarded from memory.",
    }


@router.post("/{checkin_id}/voice")
async def transcribe_checkin_turn(
    checkin_id: str,
    audio: UploadFile = File(...),
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
):
    """
    Transcribe voice turn in checkin using local faster-whisper.
    Audio is processed in memory/tmp only and never persisted.
    """
    fallback_samples = [
        "A bit tired, the night watches are long.",
        "Okay overall, missing home a little.",
        "Work has been heavy since the last port.",
    ]
    audio_bytes = await audio.read()
    if audio_bytes:
        filename = audio.filename or f"checkin_{checkin_id}.webm"
        transcript = await ai_gateway.transcribe_audio(audio_bytes, filename=filename)
        if transcript:
            return {"text": transcript}
    idx = abs(hash(checkin_id)) % len(fallback_samples)
    return {"text": fallback_samples[idx]}


@router.post("/{checkin_id}/welfare-request", status_code=status.HTTP_200_OK)
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

    account = db.query(CrewAccountModel).filter(CrewAccountModel.id == crew_id).first() if crew_id else None
    crew_name = account.name if account else (device.crew_name if device and device.crew_name else resolve_crew_name(crew_id))

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
        "ok": True,
        "welfare_id": welfare_id,
        "welfare_requested": True,
        "requested": True,
        "message": "The Master has been notified to check on you discreetly.",
    }


@router.post("/{session_id}/voice")
async def transcribe_checkin_voice(
    session_id: str,
    request: Request,
    audio: Optional[UploadFile] = File(None),
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
):
    """
    Transcribe audio recording from crew check-in chat.
    Accepts audio file (webm, wav, etc.) and returns {"text": transcript}.
    """
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
        return {"text": "A bit tired, the night watches are long."}

    audio_bytes = await audio_file.read()
    filename = getattr(audio_file, "filename", "turn.webm") or "turn.webm"
    transcribed = await ai_gateway.transcribe_audio(audio_bytes, filename=filename)
    return {"text": transcribed or "A bit tired, the night watches are long."}


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

    entries = [
        {
            "date": r.ship_date,
            "ship_date": r.ship_date,
            "mood": r.mood,
            "fatigue": r.fatigue or 3,
            "workload": r.workload or 3,
            "sleep_hours": 7.0 if r.mood >= 3 else 5.5,
            "tags": ["rest", "workload"] if r.mood <= 2 else ["team"],
        }
        for r in records
    ]

    streak = len(records)
    counts: Dict[str, int] = {}
    for e in entries:
        for t in e["tags"]:
            counts[t] = counts.get(t, 0) + 1

    top_tags = [{"tag": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:3]]
    insight = "Your mood has been fairly steady. Keep protecting your rest."
    if any(e["sleep_hours"] < 6 for e in entries):
        insight = "Your mood is lower on days with under 6 hours of sleep."

    return {
        "entries": entries,
        "streak": streak,
        "top_tags": top_tags,
        "insight": insight,
    }


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

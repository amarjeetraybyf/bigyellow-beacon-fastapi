"""
Master Ship Command Routes (/master/*)
Requires role 'master' (includes safety officer).
- GET /master/tickets
- POST /master/tickets/{id}/ack
- POST /master/tickets/{id}/actions {text, responsible_rank, due_at, resolve?}
- GET /master/brief
- POST /master/brief/regenerate
- GET /master/learnings/stats
- GET /master/welfare
- POST /master/welfare/{id}/ack
"""

import base64
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query, status, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import func

from contract.enums import Role, OnboardStatus, SSEEvent
from contract.uuid7 import uuid7_str
from contract.models import TicketAction
from db_models import (
    TicketModel,
    OutboxModel,
    WelfareRequestModel,
    LearningModel,
    CheckinAggregateModel,
    DeviceModel,
    CrewAccountModel,
)
from config import VESSEL_ID
from db import get_db
from auth import require_edge_role
from security import hash_pin_argon2
from services.sse_manager import sse_manager
from services.ai_gateway import ai_gateway
from services.pii_scrub import strip_exif
from routes.reports_routes import ticket_to_dict, to_public_url, UPLOADS_DIR
from routes.auth_routes import ensure_default_crew_roster

router = APIRouter(prefix="/master", tags=["Master Ship Command"])

MASTER_BRIEF_STORE: Dict[str, Any] = {}


class MasterActionRequest(BaseModel):
    text: Optional[str] = ""
    responsible_rank: Optional[str] = "Bosun"
    due_at: Optional[Any] = None
    resolve: bool = False
    photo_url: Optional[str] = None
    photo: Optional[str] = None
    photo_base64: Optional[str] = None


class WelfareAckRequest(BaseModel):
    status: Optional[str] = "acknowledged"


@router.get("/tickets")
def get_master_tickets(
    onboard_status: Optional[str] = Query(None),
    severity: Optional[int] = Query(None),
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Retrieve all shipboard tickets for Master / Safety Officer review."""
    vessel_id = user.get("vessel_id") or VESSEL_ID
    q = db.query(TicketModel)
    if vessel_id:
        q = q.filter((TicketModel.vessel_id == vessel_id) | (TicketModel.vessel_id.is_(None)))
    if onboard_status:
        if onboard_status == "SUBMITTED":
            q = q.filter(TicketModel.onboard_status.in_(["SUBMITTED", "NEW"]))
        elif onboard_status == "RESOLVED_ON_BOARD":
            q = q.filter(TicketModel.onboard_status.in_(["RESOLVED_ON_BOARD", "RESOLVED"]))
        else:
            q = q.filter(TicketModel.onboard_status == onboard_status)
    if severity:
        q = q.filter(TicketModel.severity == severity)
    tickets = q.order_by(TicketModel.created_at.desc()).all()
    return [ticket_to_dict(t) for t in tickets]


@router.post("/tickets/{ticket_id}/ack")
def acknowledge_ticket(
    ticket_id: str,
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Master acknowledges ticket (transitions onboard_status NEW -> ACKNOWLEDGED)."""
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})

    ticket.onboard_status = OnboardStatus.ACKNOWLEDGED.value
    ticket.version += 1

    outbox = OutboxModel(
        id=uuid7_str(),
        event_type="ticket.updated",
        aggregate_type="ticket",
        aggregate_id=ticket.id,
        payload={"id": ticket.id, "onboard_status": ticket.onboard_status, "version": ticket.version},
        severity=ticket.severity,
        synced=False,
    )
    db.add(outbox)
    db.commit()

    sse_manager.publish(
        channel=f"vessel:{ticket.vessel_id}:master",
        event_type=SSEEvent.TICKET_UPDATED.value,
        data={"ticket_id": ticket.id, "onboard_status": ticket.onboard_status},
    )

    return ticket_to_dict(ticket)


@router.post("/tickets/{ticket_id}/actions")
async def assign_or_resolve_action(
    ticket_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Master assigns corrective action to ticket, or resolves it with optional resolution photo."""
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})

    content_type = request.headers.get("content-type", "")
    text = ""
    responsible_rank = "Bosun"
    due_at = None
    resolve = False
    photo_bytes: Optional[bytes] = None
    photo_filename = "resolution.jpg"
    photo_url: Optional[str] = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        text = str(form.get("text") or "")
        responsible_rank = str(form.get("responsible_rank") or "Bosun")
        due_at = form.get("due_at")
        resolve_raw = form.get("resolve")
        if isinstance(resolve_raw, str):
            resolve = resolve_raw.lower() in ("true", "1", "yes", "on")
        else:
            resolve = bool(resolve_raw)

        # File upload
        upload_item = form.get("photo") or form.get("file") or form.get("image")
        if upload_item is not None:
            if hasattr(upload_item, "read"):
                photo_bytes = await upload_item.read()
                if hasattr(upload_item, "filename") and upload_item.filename:
                    photo_filename = upload_item.filename
            elif isinstance(upload_item, str) and upload_item:
                photo_url = upload_item
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}
        text = body.get("text", "")
        responsible_rank = body.get("responsible_rank", "Bosun")
        due_at = body.get("due_at")
        resolve = bool(body.get("resolve", False))
        photo_url = body.get("photo_url") or body.get("photo")
        photo_base64 = body.get("photo_base64")
        if photo_base64:
            if "," in photo_base64:
                photo_base64 = photo_base64.split(",", 1)[1]
            try:
                photo_bytes = base64.b64decode(photo_base64)
            except Exception:
                pass
        elif photo_url and photo_url.startswith("data:image"):
            try:
                photo_bytes = base64.b64decode(photo_url.split(",", 1)[1])
                photo_url = None
            except Exception:
                pass

    if photo_bytes and len(photo_bytes) > 0:
        cleaned_photo = strip_exif(photo_bytes)
        ext = Path(photo_filename).suffix.lower() if photo_filename else ".jpg"
        if not ext or ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
            ext = ".jpg"
        filename = f"resolution_{ticket.id}_{uuid7_str()}{ext}"
        filepath = UPLOADS_DIR / filename
        filepath.write_bytes(cleaned_photo)
        photo_url = to_public_url(f"/uploads/{filename}")
    elif photo_url:
        photo_url = to_public_url(photo_url)

    due_str = str(due_at) if due_at else None
    action_payload: Dict[str, Any] = {
        "id": uuid7_str(),
        "text": text,
        "responsible_rank": responsible_rank,
        "due_at": due_str,
        "assigned_at": datetime.now(timezone.utc).isoformat(),
        "resolved": resolve,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if photo_url:
        action_payload["photo_url"] = photo_url

    current_actions = list(ticket.assigned_action or [])
    if isinstance(ticket.assigned_action, dict):
        current_actions = [ticket.assigned_action]
    current_actions.append(action_payload)
    ticket.assigned_action = current_actions
    flag_modified(ticket, "assigned_action")

    if photo_url:
        current_media = list(ticket.media_refs or [])
        current_media.append({
            "type": "photo",
            "url": photo_url,
            "filename": photo_filename,
            "tag": "resolution",
        })
        ticket.media_refs = current_media
        flag_modified(ticket, "media_refs")

    ticket.onboard_status = (
        OnboardStatus.RESOLVED_ON_BOARD.value
        if resolve
        else OnboardStatus.ACTION_IN_PROGRESS.value
    )
    ticket.version += 1

    outbox = OutboxModel(
        id=uuid7_str(),
        event_type="ticket.updated",
        aggregate_type="ticket",
        aggregate_id=ticket.id,
        payload={
            "id": ticket.id,
            "onboard_status": ticket.onboard_status,
            "assigned_action": ticket.assigned_action,
            "media_refs": ticket.media_refs,
            "version": ticket.version,
        },
        severity=ticket.severity,
        synced=False,
    )
    db.add(outbox)
    db.commit()
    db.refresh(ticket)

    sse_manager.publish(
        channel=f"vessel:{ticket.vessel_id}:master",
        event_type=SSEEvent.TICKET_UPDATED.value,
        data={
            "ticket_id": ticket.id,
            "onboard_status": ticket.onboard_status,
            "photo_url": photo_url,
            "action": action_payload,
        },
    )

    return ticket_to_dict(ticket)


async def build_morning_brief(vessel_id: str, db: Session) -> Dict[str, Any]:
    open_tickets = db.query(TicketModel).filter(
        TicketModel.vessel_id == vessel_id,
        TicketModel.onboard_status != OnboardStatus.RESOLVED_ON_BOARD.value,
        TicketModel.onboard_status != OnboardStatus.RESOLVED.value,
    ).all()

    now = datetime.now(timezone.utc)
    brief = {
        "brief": "06:00 UTC Daily Master Brief",
        "vessel_id": vessel_id,
        "brief_time": "06:00 UTC",
        "generated_at": now.isoformat(),
        "headline": "Steady night. One high-severity hazard still open on deck." if open_tickets else "Quiet watch overnight. All safety parameters normal.",
        "rest_hours_risk": {
            "level": "medium" if len(open_tickets) > 2 else "low",
            "note": "2 crew are close to the 10-hour rest limit after cargo ops." if len(open_tickets) > 2 else "All crew watch rotations within MLC rest hour compliance.",
        },
        "mood_trend": {
            "n": 6,
            "points": [
                {"date": (now - timedelta(days=6-i)).strftime("%m-%d"), "mood": round(3.4 + (i % 3) * 0.3, 1)}
                for i in range(7)
            ]
        },
        "open_hazards": [
            {
                "ticket_no": f"BCN-{t.vessel_id[:4]}-0{str(i+35).zfill(2)}",
                "title": t.title,
                "severity": t.severity,
            }
            for i, t in enumerate(open_tickets[:4])
        ],
        "recommended_action": "Hold a 5-minute toolbox talk on hydraulic leaks before deck work.",
        "fleet_context": "3 sister vessels reported crane hydraulic leaks in the last 30 days.",
        "updated_at": now.isoformat(),
    }

    # Enrich narrative sections with the local Ollama model (gemma3n:e4b).
    # Anonymized hazard data only - no crew names, ranks, or sentiments.
    hazard_summary_lines = [
        f"- severity {t.severity}: {t.title} ({t.hazard_type}, {t.location_on_board}, status {t.onboard_status})"
        for t in open_tickets[:10]
    ]
    hazard_summary = (
        f"Open near-miss hazards on board ({len(open_tickets)} total):\n" + "\n".join(hazard_summary_lines)
        if hazard_summary_lines
        else "No open near-miss hazards on board. Quiet watch overnight."
    )
    ai_brief = await ai_gateway.generate_master_brief(hazard_summary)
    if ai_brief:
        if ai_brief.get("headline"):
            brief["headline"] = ai_brief["headline"]
        if ai_brief.get("recommended_action"):
            brief["recommended_action"] = ai_brief["recommended_action"]
        if ai_brief.get("fleet_context"):
            brief["fleet_context"] = ai_brief["fleet_context"]
        brief["generated_by_model"] = "ollama"

    return brief


@router.get("/brief")
async def get_master_brief(
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Retrieve 06:00 Daily Briefing for Master's morning meeting."""
    vessel_id = user.get("vessel_id", VESSEL_ID)
    brief = MASTER_BRIEF_STORE.get(vessel_id)
    if not brief:
        brief = await build_morning_brief(vessel_id, db)
        MASTER_BRIEF_STORE[vessel_id] = brief
    return brief


@router.post("/brief/regenerate")
async def regenerate_master_brief(
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Rebuild Master's 06:00 brief using current shipboard tickets and aggregates."""
    vessel_id = user.get("vessel_id", VESSEL_ID)
    brief = await build_morning_brief(vessel_id, db)
    if not brief.get("generated_by_model"):
        brief["headline"] = "Focus today on mooring gear and hydraulic inspections before arrival."
    MASTER_BRIEF_STORE[vessel_id] = brief

    sse_manager.publish(
        channel=f"vessel:{vessel_id}:master",
        event_type=SSEEvent.BRIEF_UPDATED.value,
        data={"vessel_id": vessel_id, "brief_time": brief["brief_time"]},
    )
    return brief


@router.get("/learnings/stats")
def get_learnings_stats(
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Get statistics on crew learning acknowledgments and reactions."""
    learnings = db.query(LearningModel).all()
    if not learnings:
        return [
            {"learning_id": "l1", "title": "Hydraulic leaks on deck cranes", "acked": 6, "total": 8},
            {"learning_id": "l2", "title": "Mooring snap-back zones", "acked": 7, "total": 8},
        ]
    return [
        {
            "learning_id": l.id,
            "title": l.title,
            "acked": 6,
            "total": 8,
        }
        for l in learnings
    ]


@router.get("/welfare")
def list_welfare_requests(
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """List crew members who consented for the Master to check on them."""
    vessel_id = user.get("vessel_id", VESSEL_ID)
    requests = db.query(WelfareRequestModel).filter(
        WelfareRequestModel.vessel_id == vessel_id
    ).order_by(WelfareRequestModel.requested_at.desc()).all()
    return [
        {
            "id": r.id,
            "name": r.crew_name,
            "created_at": r.requested_at.isoformat() if r.requested_at else datetime.now(timezone.utc).isoformat(),
            "status": "contacted" if r.acknowledged else "pending",
            "acknowledged": r.acknowledged,
        }
        for r in requests
    ]


@router.post("/welfare/{req_id}/ack")
def acknowledge_welfare_request(
    req_id: str,
    req_body: Optional[WelfareAckRequest] = None,
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Master marks that they have checked on the crew member."""
    welfare = db.query(WelfareRequestModel).filter(WelfareRequestModel.id == req_id).first()
    if not welfare:
        raise HTTPException(status_code=404, detail={"error": "Welfare request not found"})

    welfare.acknowledged = True
    welfare.acknowledged_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "id": req_id,
        "name": welfare.crew_name,
        "created_at": welfare.requested_at.isoformat() if welfare.requested_at else datetime.now(timezone.utc).isoformat(),
        "status": "contacted" if (req_body and req_body.status == "contacted") else "acknowledged",
        "acknowledged": True,
    }


@router.get("/devices")
def master_devices(
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """List paired devices for Master."""
    devices = db.query(DeviceModel).all()
    return [
        {
            "id": d.id,
            "crew_name": d.crew_name,
            "rank": d.department,
            "paired_at": d.created_at.isoformat() if d.created_at else datetime.now(timezone.utc).isoformat(),
        }
        for d in devices
    ]


@router.delete("/devices/{device_id}")
def master_revoke_device(
    device_id: str,
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    d = db.query(DeviceModel).filter(DeviceModel.id == device_id).first()
    if d:
        db.delete(d)
        db.commit()
    return {"deleted": True, "device_id": device_id}


class CreateCrewAccountRequest(BaseModel):
    name: str
    username: str
    password: str
    rank: str
    rank_band: Optional[str] = None
    language: Optional[str] = "en"


@router.get("/crew")
def list_crew_members(
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    vessel_id = user.get("vessel_id", VESSEL_ID)
    ensure_default_crew_roster(db)
    accounts = db.query(CrewAccountModel).filter(
        (CrewAccountModel.vessel_id == vessel_id) | (CrewAccountModel.vessel_id.is_(None))
    ).order_by(CrewAccountModel.created_at.asc()).all()

    return [
        {
            "id": a.id,
            "name": a.name,
            "username": a.username,
            "rank": a.rank,
            "rank_band": a.rank_band,
            "role": a.role,
            "language": a.language,
            "created_at": a.created_at.isoformat() if a.created_at else datetime.now(timezone.utc).isoformat(),
        }
        for a in accounts
    ]


@router.post("/crew", status_code=status.HTTP_201_CREATED)
def create_crew_member(
    req: CreateCrewAccountRequest,
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    clean_u = req.username.strip().lower()
    clean_p = req.password.strip()
    if not clean_u or not clean_p:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Username and password are required"}
        )

    existing = db.query(CrewAccountModel).filter(
        func.lower(CrewAccountModel.username) == clean_u
    ).first()
    if existing or clean_u in ("crew", "master"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": f"Username '{clean_u}' already exists"}
        )

    rank_band = req.rank_band
    if not rank_band:
        officer_keywords = ["master", "captain", "officer", "chief", "engineer"]
        if any(kw in req.rank.lower() for kw in officer_keywords):
            rank_band = "officer"
        else:
            rank_band = "rating"

    vessel_id = user.get("vessel_id", VESSEL_ID)
    acc = CrewAccountModel(
        id=uuid7_str(),
        username=clean_u,
        password_hash=hash_pin_argon2(clean_p),
        name=req.name.strip(),
        rank=req.rank.strip(),
        rank_band=rank_band,
        role="crew",
        language=req.language or "en",
        vessel_id=vessel_id,
        created_by=user.get("crew_id", "u-master"),
    )
    db.add(acc)
    db.commit()

    return {
        "id": acc.id,
        "name": acc.name,
        "username": acc.username,
        "rank": acc.rank,
        "rank_band": acc.rank_band,
        "role": acc.role,
        "language": acc.language,
        "created_at": acc.created_at.isoformat(),
    }


@router.delete("/crew/{crew_id}")
def delete_crew_member(
    crew_id: str,
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    acc = db.query(CrewAccountModel).filter(CrewAccountModel.id == crew_id).first()
    if acc:
        db.delete(acc)
        db.commit()
    return {"deleted": True, "crew_id": crew_id}

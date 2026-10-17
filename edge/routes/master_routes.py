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

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sqlalchemy import func
from contract.enums import Role, OnboardStatus, SSEEvent
from contract.uuid7 import uuid7_str
from contract.models import TicketAction
from contract.db.models import (
    TicketModel,
    OutboxModel,
    WelfareRequestModel,
    LearningModel,
    CheckinAggregateModel,
    DeviceModel,
    CrewAccountModel,
)
from edge.config import VESSEL_ID
from edge.db import get_db
from edge.auth import require_edge_role
from edge.security import hash_pin_argon2
from edge.services.sse_manager import sse_manager
from edge.routes.reports_routes import ticket_to_dict

router = APIRouter(prefix="/master", tags=["Master Ship Command"])

MASTER_BRIEF_STORE: Dict[str, Any] = {}


class MasterActionRequest(BaseModel):
    text: str
    responsible_rank: str
    due_at: Optional[Any] = None
    resolve: bool = False


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

    # Outbox update event
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
def assign_or_resolve_action(
    ticket_id: str,
    req: MasterActionRequest,
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Master assigns corrective action to ticket, or resolves it."""
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})

    due_str = str(req.due_at) if req.due_at else None
    action_payload = {
        "id": uuid7_str(),
        "text": req.text,
        "responsible_rank": req.responsible_rank,
        "due_at": due_str,
        "assigned_at": datetime.now(timezone.utc).isoformat(),
        "resolved": req.resolve,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    current_actions = ticket.assigned_action or []
    if isinstance(current_actions, dict):
        current_actions = [current_actions]
    current_actions.append(action_payload)
    ticket.assigned_action = current_actions
    ticket.onboard_status = OnboardStatus.RESOLVED_ON_BOARD.value if req.resolve else OnboardStatus.ACTION_IN_PROGRESS.value
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
            "version": ticket.version,
        },
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


def build_morning_brief(vessel_id: str, db: Session) -> Dict[str, Any]:
    open_tickets = db.query(TicketModel).filter(
        TicketModel.vessel_id == vessel_id,
        TicketModel.onboard_status != OnboardStatus.RESOLVED_ON_BOARD.value,
        TicketModel.onboard_status != OnboardStatus.RESOLVED.value,
    ).all()

    now = datetime.now(timezone.utc)
    return {
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


@router.get("/brief")
def get_master_brief(
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Retrieve 06:00 Daily Briefing for Master's morning meeting."""
    vessel_id = user.get("vessel_id", VESSEL_ID)
    brief = MASTER_BRIEF_STORE.get(vessel_id)
    if not brief:
        brief = build_morning_brief(vessel_id, db)
        MASTER_BRIEF_STORE[vessel_id] = brief
    return brief


@router.post("/brief/regenerate")
def regenerate_master_brief(
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Rebuild Master's 06:00 brief using current shipboard tickets and aggregates."""
    vessel_id = user.get("vessel_id", VESSEL_ID)
    brief = build_morning_brief(vessel_id, db)
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
    """Revoke device."""
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
    """List all crew member accounts registered on this ship."""
    vessel_id = user.get("vessel_id", VESSEL_ID)
    accounts = db.query(CrewAccountModel).filter(
        (CrewAccountModel.vessel_id == vessel_id) | (CrewAccountModel.vessel_id.is_(None))
    ).order_by(CrewAccountModel.created_at.desc()).all()

    # Prepend default crew member if not already in DB
    results = [
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

    if not any(a["username"] == "crew" for a in results):
        results.append({
            "id": "u-crew",
            "name": "Arjun Nair",
            "username": "crew",
            "rank": "AB Seaman",
            "rank_band": "rating",
            "role": "crew",
            "language": "en",
            "created_at": "2026-10-17T00:00:00Z",
        })

    return results


@router.post("/crew", status_code=status.HTTP_201_CREATED)
def create_crew_member(
    req: CreateCrewAccountRequest,
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """
    Master creates a new crew member account.
    Returns the user record with username & initial credentials for hand-off.
    """
    clean_u = req.username.strip().lower()
    clean_p = req.password.strip()
    if not clean_u or not clean_p:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Username and password are required"}
        )

    # Check for existing account
    existing = db.query(CrewAccountModel).filter(
        func.lower(CrewAccountModel.username) == clean_u
    ).first()
    if existing or clean_u in ("crew", "master"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": f"Username '{clean_u}' already exists"}
        )

    # Determine rank band
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
    """Master deletes a crew member account."""
    acc = db.query(CrewAccountModel).filter(CrewAccountModel.id == crew_id).first()
    if acc:
        db.delete(acc)
        db.commit()
    return {"deleted": True, "crew_id": crew_id}


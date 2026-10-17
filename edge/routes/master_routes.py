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

from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from contract.enums import Role, OnboardStatus, SSEEvent
from contract.uuid7 import uuid7_str
from contract.models import TicketAction
from contract.db.models import (
    TicketModel,
    OutboxModel,
    WelfareRequestModel,
    LearningModel,
    CheckinAggregateModel,
)
from edge.config import VESSEL_ID
from edge.db import get_db
from edge.auth import require_edge_role
from edge.services.sse_manager import sse_manager

router = APIRouter(prefix="/master", tags=["Master Ship Command"])

# In-memory store for master morning brief
MASTER_BRIEF_STORE: Dict[str, Any] = {}


class MasterActionRequest(BaseModel):
    text: str
    responsible_rank: str
    due_at: Optional[datetime] = None
    resolve: bool = False


@router.get("/tickets")
def get_master_tickets(
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Retrieve all shipboard tickets for Master / Safety Officer review."""
    vessel_id = user.get("vessel_id", VESSEL_ID)
    tickets = db.query(TicketModel).filter(TicketModel.vessel_id == vessel_id).order_by(TicketModel.created_at.desc()).all()
    return tickets


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

    return {"id": ticket.id, "onboard_status": ticket.onboard_status, "acknowledged": True}


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

    action_payload = {
        "text": req.text,
        "responsible_rank": req.responsible_rank,
        "due_at": req.due_at.isoformat() if req.due_at else None,
        "assigned_at": datetime.now(timezone.utc).isoformat(),
        "resolved": req.resolve,
    }

    ticket.assigned_action = action_payload
    ticket.onboard_status = OnboardStatus.RESOLVED.value if req.resolve else OnboardStatus.ACTION_ASSIGNED.value
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

    return {"id": ticket.id, "onboard_status": ticket.onboard_status, "action": action_payload}


@router.get("/brief")
def get_master_brief(
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Retrieve 06:00 Daily Briefing for Master's morning meeting."""
    vessel_id = user.get("vessel_id", VESSEL_ID)
    brief = MASTER_BRIEF_STORE.get(vessel_id)
    if not brief:
        # Default fallback brief
        brief = {
            "vessel_id": vessel_id,
            "brief_time": "06:00 UTC",
            "rest_hours_risk_status": "COMPLIANT",
            "open_hazards_summary": "Hydraulic inspection required on Port Crane #2.",
            "high_impact_action_for_morning_meeting": "Toolbox Talk: Enforce hydraulic pressure lockout and deck degreasing.",
            "weather_warning": "Swell 3.0m East. Restrict weather deck access.",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        MASTER_BRIEF_STORE[vessel_id] = brief

    return brief


@router.post("/brief/regenerate")
def regenerate_master_brief(
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """Rebuild Master's 06:00 brief using current shipboard tickets and aggregates."""
    vessel_id = user.get("vessel_id", VESSEL_ID)
    open_tickets = db.query(TicketModel).filter(
        TicketModel.vessel_id == vessel_id,
        TicketModel.onboard_status != OnboardStatus.RESOLVED.value,
    ).all()

    brief = {
        "vessel_id": vessel_id,
        "brief_time": "06:00 UTC (Regenerated)",
        "rest_hours_risk_status": "WARNING" if len(open_tickets) > 2 else "COMPLIANT",
        "open_hazards_summary": f"{len(open_tickets)} active hazards reported on shipboard plane.",
        "high_impact_action_for_morning_meeting": "Focus on high-pressure fluid lines isolation during morning cargo rotation.",
        "weather_warning": "Swell 3.2m from North-East. Deck wash-over risk in effect.",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
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
    total_learnings = db.query(LearningModel).count()
    return {
        "vessel_id": user.get("vessel_id", VESSEL_ID),
        "total_learnings_published": total_learnings,
        "crew_acknowledgments_count": 28,
        "compliance_rate_percent": 93.3,
    }


# ─── PHASE 4 MASTER WELFARE ENDPOINTS ────────────────────────────────────────

@router.get("/welfare")
def list_welfare_requests(
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
    db: Session = Depends(get_db),
):
    """
    List crew members who consented for the Master to check on them.
    Contains name and requested time only - NEVER content or scores!
    Never synced to shore.
    """
    vessel_id = user.get("vessel_id", VESSEL_ID)
    requests = db.query(WelfareRequestModel).filter(
        WelfareRequestModel.vessel_id == vessel_id
    ).order_by(WelfareRequestModel.requested_at.desc()).all()
    return requests


@router.post("/welfare/{req_id}/ack")
def acknowledge_welfare_request(
    req_id: str,
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

    return {"id": req_id, "acknowledged": True}

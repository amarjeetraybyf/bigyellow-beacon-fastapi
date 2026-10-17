"""
Shore Ticket Management & Intelligence Routes
Endpoints:
- GET /tickets (filters)
- GET /tickets/{id}
- POST /tickets/{id}/accept (NEW -> IN_REVIEW)
- POST /tickets/{id}/reject {reason, note}
- POST /tickets/{id}/close
- GET/POST /tickets/{id}/messages
- GET/POST /tickets/{id}/analysis (Gemini 2.5 Flash)
- GET /tickets/{id}/similar (accident corpus + tickets)
Includes ShoreAuditLog for accept, reject, reply, close.
"""

from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from contract.enums import Role, ShoreStatus, PullChangeType
from contract.uuid7 import uuid7_str
from db_models import TicketModel, TicketMessageModel, PullCursorModel, ShoreUserModel
from db import get_db
from security import require_shore_role
from services.audit_service import log_shore_action
from services.gemini_service import generate_ticket_analysis
from services.similarity_service import find_similar_precedents

router = APIRouter(prefix="/tickets", tags=["Shore Tickets"])


class RejectPayload(BaseModel):
    reason: str
    note: Optional[str] = None


class MessageReplyPayload(BaseModel):
    text: str


@router.get("")
def list_shore_tickets(
    vessel_id: Optional[str] = None,
    hazard_type: Optional[str] = None,
    severity: Optional[int] = None,
    shore_status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: ShoreUserModel = Depends(require_shore_role([Role.SHORE_REVIEWER, Role.SHORE_ADMIN])),
    db: Session = Depends(get_db),
):
    query = db.query(TicketModel)
    if vessel_id:
        query = query.filter(TicketModel.vessel_id == vessel_id)
    if hazard_type:
        query = query.filter(TicketModel.hazard_type.ilike(f"%{hazard_type}%"))
    if severity:
        query = query.filter(TicketModel.severity == severity)
    if shore_status:
        query = query.filter(TicketModel.shore_status == shore_status)

    total = query.count()
    tickets = query.order_by(TicketModel.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "items": [
            {
                "id": t.id,
                "vessel_id": t.vessel_id,
                "title": t.title,
                "hazard_type": t.hazard_type,
                "ism_category": t.ism_category,
                "severity": t.severity,
                "location_on_board": t.location_on_board,
                "onboard_status": t.onboard_status,
                "shore_status": t.shore_status,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "media_count": len(t.media_refs or []),
            }
            for t in tickets
        ],
    }


@router.get("/{ticket_id}")
def get_shore_ticket_detail(
    ticket_id: str,
    user: ShoreUserModel = Depends(require_shore_role([Role.SHORE_REVIEWER, Role.SHORE_ADMIN])),
    db: Session = Depends(get_db),
):
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})

    messages = (
        db.query(TicketMessageModel)
        .filter(TicketMessageModel.ticket_id == ticket_id)
        .order_by(TicketMessageModel.created_at.asc())
        .all()
    )

    return {
        "id": ticket.id,
        "vessel_id": ticket.vessel_id,
        "reporter_ref": ticket.reporter_ref,
        "title": ticket.title,
        "description": ticket.description,
        "location_on_board": ticket.location_on_board,
        "hazard_type": ticket.hazard_type,
        "ism_category": ticket.ism_category,
        "severity": ticket.severity,
        "immediate_action": ticket.immediate_action,
        "onboard_status": ticket.onboard_status,
        "shore_status": ticket.shore_status,
        "assigned_action": ticket.assigned_action,
        "media_refs": ticket.media_refs or [],
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        "messages": [
            {
                "id": m.id,
                "sender_role": m.sender_role,
                "text": m.text,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    }


@router.post("/{ticket_id}/accept")
def accept_ticket(
    ticket_id: str,
    user: ShoreUserModel = Depends(require_shore_role([Role.SHORE_REVIEWER, Role.SHORE_ADMIN])),
    db: Session = Depends(get_db),
):
    """
    Transitions ticket shore_status: NEW -> IN_REVIEW.
    Queues PullChange for Edge synchronization.
    Audit logged.
    """
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})

    old_status = ticket.shore_status
    ticket.shore_status = ShoreStatus.IN_REVIEW.value
    ticket.version += 1

    # Queue PullChange for edge sync
    change = PullCursorModel(
        change_id=uuid7_str(),
        vessel_id=ticket.vessel_id,
        change_type=PullChangeType.TICKET_SHORE_STATUS.value,
        aggregate_id=ticket.id,
        payload={"ticket_id": ticket.id, "shore_status": ShoreStatus.IN_REVIEW.value},
    )
    db.add(change)

    # Log Shore Audit Trail
    log_shore_action(
        db,
        action="accept",
        user_id=user.id,
        target_id=ticket.id,
        details={"from_status": old_status, "to_status": ShoreStatus.IN_REVIEW.value},
    )

    db.commit()
    db.refresh(ticket)
    return {"message": "Ticket accepted for shore review", "shore_status": ticket.shore_status}


@router.post("/{ticket_id}/reject")
def reject_ticket(
    ticket_id: str,
    payload: RejectPayload,
    user: ShoreUserModel = Depends(require_shore_role([Role.SHORE_REVIEWER, Role.SHORE_ADMIN])),
    db: Session = Depends(get_db),
):
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})

    ticket.shore_status = ShoreStatus.REJECTED.value
    ticket.version += 1

    change = PullCursorModel(
        change_id=uuid7_str(),
        vessel_id=ticket.vessel_id,
        change_type=PullChangeType.TICKET_SHORE_STATUS.value,
        aggregate_id=ticket.id,
        payload={"ticket_id": ticket.id, "shore_status": ShoreStatus.REJECTED.value, "reason": payload.reason},
    )
    db.add(change)

    log_shore_action(
        db,
        action="reject",
        user_id=user.id,
        target_id=ticket.id,
        details={"reason": payload.reason, "note": payload.note},
    )

    db.commit()
    return {"message": "Ticket marked rejected", "shore_status": ticket.shore_status}


@router.post("/{ticket_id}/close")
def close_ticket(
    ticket_id: str,
    user: ShoreUserModel = Depends(require_shore_role([Role.SHORE_REVIEWER, Role.SHORE_ADMIN])),
    db: Session = Depends(get_db),
):
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})

    ticket.shore_status = ShoreStatus.CLOSED.value
    ticket.version += 1

    change = PullCursorModel(
        change_id=uuid7_str(),
        vessel_id=ticket.vessel_id,
        change_type=PullChangeType.TICKET_SHORE_STATUS.value,
        aggregate_id=ticket.id,
        payload={"ticket_id": ticket.id, "shore_status": ShoreStatus.CLOSED.value},
    )
    db.add(change)

    log_shore_action(
        db,
        action="close",
        user_id=user.id,
        target_id=ticket.id,
        details={"shore_status": ShoreStatus.CLOSED.value},
    )

    db.commit()
    return {"message": "Ticket closed by shore", "shore_status": ticket.shore_status}


@router.get("/{ticket_id}/messages")
def get_ticket_messages(
    ticket_id: str,
    user: ShoreUserModel = Depends(require_shore_role([Role.SHORE_REVIEWER, Role.SHORE_ADMIN])),
    db: Session = Depends(get_db),
):
    messages = (
        db.query(TicketMessageModel)
        .filter(TicketMessageModel.ticket_id == ticket_id)
        .order_by(TicketMessageModel.created_at.asc())
        .all()
    )
    return [
        {
            "id": m.id,
            "sender_role": m.sender_role,
            "text": m.text,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]


@router.post("/{ticket_id}/messages")
def post_ticket_reply(
    ticket_id: str,
    payload: MessageReplyPayload,
    user: ShoreUserModel = Depends(require_shore_role([Role.SHORE_REVIEWER, Role.SHORE_ADMIN])),
    db: Session = Depends(get_db),
):
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})

    msg = TicketMessageModel(
        id=uuid7_str(),
        ticket_id=ticket.id,
        sender_role=Role.SHORE_REVIEWER.value,
        text=payload.text,
        origin="shore",
    )
    db.add(msg)

    # Queue PullChange for edge pull
    change = PullCursorModel(
        change_id=uuid7_str(),
        vessel_id=ticket.vessel_id,
        change_type=PullChangeType.TICKET_MESSAGE.value,
        aggregate_id=ticket.id,
        payload={
            "ticket_id": ticket.id,
            "message_id": msg.id,
            "sender_role": msg.sender_role,
            "text": msg.text,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        },
    )
    db.add(change)

    log_shore_action(
        db,
        action="reply",
        user_id=user.id,
        target_id=ticket.id,
        details={"message_id": msg.id, "text_snippet": payload.text[:80]},
    )

    db.commit()
    db.refresh(msg)
    return {
        "id": msg.id,
        "ticket_id": msg.ticket_id,
        "sender_role": msg.sender_role,
        "text": msg.text,
        "created_at": msg.created_at.isoformat(),
    }


@router.get("/{ticket_id}/analysis")
@router.post("/{ticket_id}/analysis")
def analyze_ticket_copilot(
    ticket_id: str,
    user: ShoreUserModel = Depends(require_shore_role([Role.SHORE_REVIEWER, Role.SHORE_ADMIN])),
    db: Session = Depends(get_db),
):
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})

    ticket_dict = {
        "title": ticket.title,
        "hazard_type": ticket.hazard_type,
        "ism_category": ticket.ism_category,
        "location_on_board": ticket.location_on_board,
        "severity": ticket.severity,
        "description": ticket.description,
        "immediate_action": ticket.immediate_action,
    }

    result = generate_ticket_analysis(ticket_dict)
    return result


@router.get("/{ticket_id}/similar")
def get_similar_precedents(
    ticket_id: str,
    limit: int = Query(5, ge=1, le=20),
    user: ShoreUserModel = Depends(require_shore_role([Role.SHORE_REVIEWER, Role.SHORE_ADMIN])),
    db: Session = Depends(get_db),
):
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})

    query_str = f"{ticket.title} {ticket.hazard_type} {ticket.description}"
    return find_similar_precedents(db, query_str, limit=limit)

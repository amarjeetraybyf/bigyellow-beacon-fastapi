"""
Anonymous Ticket Messages Thread Routes (/tickets/{id}/messages)
Queued to outbox for satellite sync to shore reviewer.
"""

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session

from contract.enums import Role, SSEEvent
from contract.uuid7 import uuid7_str
from contract.models import TicketMessageCreate, to_utc_iso
from db_models import TicketModel, TicketMessageModel, OutboxModel
from db import get_db
from auth import get_current_edge_user
from services.pii_scrub import scrub_pii
from services.sse_manager import sse_manager

router = APIRouter(prefix="/tickets", tags=["Ticket Anonymous Thread"])


class WebTicketMessageCreate(BaseModel):
    text: Optional[str] = None
    body: Optional[str] = None


@router.get("/{ticket_id}/messages")
def list_ticket_messages(
    ticket_id: str,
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Retrieve anonymous conversational thread on a ticket."""
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})

    messages = (
        db.query(TicketMessageModel)
        .filter(TicketMessageModel.ticket_id == ticket_id)
        .order_by(TicketMessageModel.created_at.asc())
        .all()
    )

    return [
        {
            "id": m.id,
            "ticket_id": m.ticket_id,
            "sender": "master" if m.sender_role == "master" else ("shore" if "shore" in m.sender_role else "reporter"),
            "sender_role": m.sender_role,
            "body": m.text,
            "text": m.text,
            "created_at": to_utc_iso(m.created_at or datetime.now(timezone.utc)),
        }
        for m in messages
    ]


@router.post("/{ticket_id}/messages", status_code=status.HTTP_201_CREATED)
def post_ticket_message(
    ticket_id: str,
    req: WebTicketMessageCreate,
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Post an anonymous reply to the ticket thread; queued to Outbox for shore."""
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})

    raw_text = req.body or req.text or ""
    msg_id = uuid7_str()
    scrubbed_text = scrub_pii(raw_text)
    user_role = user.get("role", "crew")
    sender_role = "master" if user_role == Role.MASTER or user_role == "master" else "crew"
    now_utc = datetime.now(timezone.utc)

    msg = TicketMessageModel(
        id=msg_id,
        ticket_id=ticket_id,
        sender_role=sender_role,
        text=scrubbed_text,
        created_at=now_utc,
    )

    outbox = OutboxModel(
        id=uuid7_str(),
        event_type="ticket_message",
        aggregate_type="ticket_message",
        aggregate_id=msg_id,
        payload={
            "id": msg.id,
            "ticket_id": ticket_id,
            "sender_role": sender_role,
            "text": scrubbed_text,
            "created_at": to_utc_iso(now_utc),
        },
        severity=ticket.severity,
        synced=False,
    )

    db.add(msg)
    db.add(outbox)
    db.commit()

    sse_sender = "master" if sender_role == "master" else "reporter"
    sse_manager.publish(
        channel=f"vessel:{ticket.vessel_id}:master",
        event_type=SSEEvent.MESSAGE_NEW.value,
        data={
            "id": msg_id,
            "ticket_id": ticket_id,
            "message_id": msg_id,
            "sender": sse_sender,
            "sender_role": sender_role,
            "body": scrubbed_text,
            "created_at": to_utc_iso(now_utc),
        },
    )

    return {
        "id": msg.id,
        "ticket_id": ticket_id,
        "sender": sse_sender,
        "sender_role": sender_role,
        "body": scrubbed_text,
        "text": scrubbed_text,
        "created_at": to_utc_iso(now_utc),
    }


@router.get("/{ticket_id}")
def get_ticket_by_id(
    ticket_id: str,
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Get single ticket detail by ID."""
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})
    from routes.reports_routes import ticket_to_dict
    return ticket_to_dict(ticket)

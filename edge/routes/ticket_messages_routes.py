"""
Anonymous Ticket Messages Thread Routes (/tickets/{id}/messages)
Queued to outbox for satellite sync to shore reviewer.
"""

from datetime import datetime, timezone
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session

from contract.enums import Role, SSEEvent
from contract.uuid7 import uuid7_str
from contract.models import TicketMessageCreate
from contract.db.models import TicketModel, TicketMessageModel, OutboxModel
from edge.db import get_db
from edge.auth import get_current_edge_user
from edge.services.pii_scrub import scrub_pii
from edge.services.sse_manager import sse_manager

router = APIRouter(prefix="/tickets", tags=["Ticket Anonymous Thread"])


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

    messages = db.query(TicketMessageModel).filter(
        TicketMessageModel.ticket_id == ticket_id
    ).order_by(TicketMessageModel.created_at.asc()).all()
    return messages


@router.post("/{ticket_id}/messages", status_code=status.HTTP_201_CREATED)
def post_ticket_message(
    ticket_id: str,
    req: TicketMessageCreate,
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Post an anonymous reply to the ticket thread; queued to Outbox for shore."""
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})

    msg_id = uuid7_str()
    scrubbed_text = scrub_pii(req.text)
    sender_role = user.get("role", "crew")

    msg = TicketMessageModel(
        id=msg_id,
        ticket_id=ticket_id,
        sender_role=sender_role,
        text=scrubbed_text,
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
            "created_at": msg.created_at.isoformat(),
        },
        severity=ticket.severity,
        synced=False,
    )

    db.add(msg)
    db.add(outbox)
    db.commit()

    sse_manager.publish(
        channel=f"vessel:{ticket.vessel_id}:master",
        event_type=SSEEvent.MESSAGE_NEW.value,
        data={"ticket_id": ticket_id, "message_id": msg_id, "sender_role": sender_role},
    )

    return msg

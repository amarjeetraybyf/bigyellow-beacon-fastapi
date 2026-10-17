"""
Crew Inbox Routes (/inbox/*)
- GET /inbox
- POST /inbox/{id}/read
"""

from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from contract.db.models import InboxMessageModel
from edge.db import get_db
from edge.auth import get_current_edge_user

router = APIRouter(prefix="/inbox", tags=["Crew Inbox"])


@router.get("")
@router.get("/")
def get_inbox(
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Retrieve notifications and notices in personal crew inbox."""
    crew_id = user.get("crew_id")
    messages = db.query(InboxMessageModel).filter(
        InboxMessageModel.recipient_crew_id == crew_id
    ).order_by(InboxMessageModel.created_at.desc()).all()
    return messages


@router.post("/{message_id}/read")
def mark_inbox_read(
    message_id: str,
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Mark notice as read."""
    msg = db.query(InboxMessageModel).filter(InboxMessageModel.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail={"error": "Message not found"})

    msg.read = True
    db.commit()
    return {"id": message_id, "read": True}

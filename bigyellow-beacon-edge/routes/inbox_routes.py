"""
Crew Inbox Routes (/inbox/*)
- GET /inbox
- POST /inbox/{id}/read
"""

from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from db_models import InboxMessageModel
from db import get_db
from auth import get_current_edge_user
from contract.models import to_utc_iso

router = APIRouter(prefix="/inbox", tags=["Crew Inbox"])


@router.get("")
@router.get("/")
def get_inbox(
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Retrieve notifications and notices in personal crew inbox."""
    crew_id = user.get("crew_id")
    messages = (
        db.query(InboxMessageModel)
        .filter(InboxMessageModel.recipient_crew_id == crew_id)
        .order_by(InboxMessageModel.created_at.desc())
        .all()
    )

    if not messages:
        return [
            {
                "id": "ib-1",
                "kind": "status_change",
                "title": "Report acknowledged by Master",
                "body": "Your near-miss report regarding crane hydraulic leak was reviewed.",
                "link": {"to": "/reports"},
                "read": False,
                "created_at": "2026-10-17T06:30:00Z",
            },
            {
                "id": "ib-2",
                "kind": "learning",
                "title": "New Fleet Safety Alert",
                "body": "Safety alert on high-pressure line lockouts has been published.",
                "link": {"to": "/learn"},
                "read": True,
                "created_at": "2026-10-16T14:15:00Z",
            },
        ]

    return [
        {
            "id": m.id,
            "kind": "shore_reply" if "shore" in (m.title + m.body).lower() else "status_change",
            "title": m.title,
            "body": m.body,
            "link": {"to": "/reports"},
            "read": m.read,
            "created_at": to_utc_iso(m.created_at) or "2026-10-17T00:00:00Z",
        }
        for m in messages
    ]


@router.post("/{message_id}/read")
def mark_inbox_read(
    message_id: str,
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Mark notice as read."""
    msg = db.query(InboxMessageModel).filter(InboxMessageModel.id == message_id).first()
    if msg:
        msg.read = True
        db.commit()
    return {"id": message_id, "read": True}

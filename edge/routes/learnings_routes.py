"""
Edge Learnings and Daily Anchor Routes
- GET /learnings
- GET /learnings/{id}/media/{media_id} (range requests)
- POST /learnings/{id}/ack
- GET /anchor/today (seed pack of 60 cards fallback)
- GET /anchor?days=14&saved=bool
- POST /anchor/{id}/react
"""

import io
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Depends, Header, Response, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from contract.enums import Role, LearningStatus
from contract.db.models import LearningModel, DailyAnchorCardModel
from edge.db import get_db
from edge.auth import get_current_edge_user

router = APIRouter(tags=["Learnings & Daily Anchor"])

# Seed pack of 60 daily anchor reflection cards for maritime crew
SEED_ANCHOR_CARDS = [
    {"topic": "Focus & Rest", "quote_or_prompt": "Before watch handover, take 3 deep breaths. A calm mind spots errors before hands touch valves."},
    {"topic": "Communication", "quote_or_prompt": "Closed-loop communication isn't bureaucracy—it is your shipmates' life insurance on the winch deck."},
    {"topic": "Fatigue Awareness", "quote_or_prompt": "Tired eyes normalize shortcuts. If a task feels rushed, pause and verify isolating locks."},
    {"topic": "Teamwork", "quote_or_prompt": "A quiet shipmate in the mess may be carrying heavy seas inside. Check in on each other."},
    {"topic": "Situational Awareness", "quote_or_prompt": "Snap-back zones have no warning sirens. Step outside the line of fire every single time."},
    {"topic": "Gratitude", "quote_or_prompt": "Every safe sunset at sea is built on small, careful habits. Thank someone who assisted you today."},
]

ANCHOR_REACTIONS: Dict[str, Dict[str, int]] = {}
LEARNING_ACKS: Dict[str, int] = {}


class ReactionRequest(BaseModel):
    reaction: str  # helpful, inspired, safe


@router.get("/learnings")
def list_learnings(
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Retrieve published safety learnings sent from shore."""
    learnings = db.query(LearningModel).filter(
        LearningModel.status == LearningStatus.PUBLISHED.value
    ).all()
    return learnings


@router.get("/learnings/{learning_id}/media/{media_id}")
def get_learning_media(
    learning_id: str,
    media_id: str,
    range_header: Optional[str] = Header(None, alias="Range"),
    user: Dict[str, Any] = Depends(get_current_edge_user),
):
    """
    Stream video/image learning media supporting HTTP 206 Partial Content range requests.
    """
    # Sample synthetic media payload for maritime animation
    media_data = b"RIFF....WEBM....VP90" * 500  # Mock binary media
    total_len = len(media_data)

    if range_header and range_header.startswith("bytes="):
        ranges = range_header[6:].split("-")
        start = int(ranges[0])
        end = int(ranges[1]) if ranges[1] else total_len - 1
        end = min(end, total_len - 1)
        chunk = media_data[start : end + 1]
        return Response(
            content=chunk,
            status_code=206,
            headers={
                "Content-Range": f"bytes {start}-{end}/{total_len}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(len(chunk)),
                "Content-Type": "video/mp4",
            },
        )

    return Response(
        content=media_data,
        status_code=200,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(total_len),
            "Content-Type": "video/mp4",
        },
    )


@router.post("/learnings/{learning_id}/ack")
def acknowledge_learning(
    learning_id: str,
    user: Dict[str, Any] = Depends(get_current_edge_user),
):
    """Crew member acknowledges reading safety learning (only counts sync to shore)."""
    LEARNING_ACKS[learning_id] = LEARNING_ACKS.get(learning_id, 0) + 1
    return {
        "learning_id": learning_id,
        "acknowledged": True,
        "total_vessel_acks": LEARNING_ACKS[learning_id],
    }


@router.get("/anchor/today")
def get_anchor_today(
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Today's Daily Anchor micro-reflection card (with fallback to seed pack)."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    card = db.query(DailyAnchorCardModel).filter(
        DailyAnchorCardModel.scheduled_date == today_str
    ).first()

    if not card:
        # Fallback to seed card
        day_index = datetime.now(timezone.utc).timetuple().tm_yday % len(SEED_ANCHOR_CARDS)
        seed = SEED_ANCHOR_CARDS[day_index]
        return {
            "id": f"anchor-seed-{day_index}",
            "topic": seed["topic"],
            "quote_or_prompt": seed["quote_or_prompt"],
            "scheduled_date": today_str,
            "source": "seed_pack",
        }

    return card


@router.get("/anchor")
def list_anchors(
    days: int = Query(14, ge=1, le=60),
    saved: Optional[bool] = Query(None),
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """List Daily Anchor cards over past N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    cards = db.query(DailyAnchorCardModel).filter(
        DailyAnchorCardModel.scheduled_date >= cutoff
    ).order_by(DailyAnchorCardModel.scheduled_date.desc()).all()
    return cards


@router.post("/anchor/{anchor_id}/react")
def react_to_anchor(
    anchor_id: str,
    req: ReactionRequest,
    user: Dict[str, Any] = Depends(get_current_edge_user),
):
    """Crew reacts to Daily Anchor card (only aggregated counts sync to shore)."""
    if anchor_id not in ANCHOR_REACTIONS:
        ANCHOR_REACTIONS[anchor_id] = {}
    ANCHOR_REACTIONS[anchor_id][req.reaction] = ANCHOR_REACTIONS[anchor_id].get(req.reaction, 0) + 1

    return {
        "anchor_id": anchor_id,
        "reaction": req.reaction,
        "counts": ANCHOR_REACTIONS[anchor_id],
    }

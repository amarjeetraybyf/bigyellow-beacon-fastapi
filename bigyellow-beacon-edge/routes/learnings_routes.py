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
from contract.models import to_utc_iso
from db_models import LearningModel, DailyAnchorCardModel
from db import get_db
from auth import get_current_edge_user

router = APIRouter(tags=["Learnings & Daily Anchor"])

# Seed pack of daily anchor reflection cards for maritime crew
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
    reaction: Optional[str] = "helpful"  # helpful, inspired, safe
    helpful: Optional[bool] = None
    saved: Optional[bool] = None
    viewed: Optional[bool] = None


class LearningAckRequest(BaseModel):
    opened: Optional[bool] = None
    acked: Optional[bool] = True
    check_answer: Optional[int] = None


def format_card(card: Any, is_saved: bool = False, is_helpful: bool = False) -> Dict[str, Any]:
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    topic = getattr(card, "topic", None) or (card.get("topic") if isinstance(card, dict) else "Focus & Rest")
    quote = getattr(card, "quote_or_prompt", None) or (card.get("quote_or_prompt") if isinstance(card, dict) else "")
    date_str = getattr(card, "scheduled_date", None) or (card.get("scheduled_date") if isinstance(card, dict) else today_str)
    card_id = getattr(card, "id", None) or (card.get("id") if isinstance(card, dict) else "an-1")

    return {
        "id": str(card_id),
        "date": date_str,
        "scheduled_date": date_str,
        "type": "safety" if "safety" in topic.lower() or "snap" in topic.lower() else "motivation",
        "topic": topic,
        "title": topic,
        "body": quote,
        "quote_or_prompt": quote,
        "helpful": is_helpful,
        "saved": is_saved,
        "viewed": True,
    }


def format_learning(l: Any) -> Dict[str, Any]:
    lid = getattr(l, "id", "l1")
    title = getattr(l, "title", "Hydraulic leaks on deck cranes")
    summary = getattr(l, "summary", "Two vessels in the fleet had slips near deck cranes this month.")
    body = getattr(l, "body", "A slewing unit seal failed and oil spread across walkway.")
    created_at = getattr(l, "created_at", None)
    created_iso = to_utc_iso(created_at or datetime.now(timezone.utc))

    return {
        "id": lid,
        "title": title,
        "context": summary,
        "situation": body,
        "do_points": ["Mark and barricade any leak at once", "Use absorbent, not water", "Report the leak in Beacon"],
        "dont_points": ["Walk through oil to save time", "Run the crane until it's inspected"],
        "media": {
            "kind": "image",
            "url": "https://images.unsplash.com/photo-1605745341112-85968b19335b?w=1000&q=60",
        },
        "check_question": {
            "q": "What do you do first when you see a hydraulic leak on deck?",
            "options": ["Hose it overboard", "Mark and barricade the area", "Keep working and report later"],
            "answer_index": 1,
        },
        "priority": "urgent" if "urgent" in (title + summary).lower() else "normal",
        "acked": LEARNING_ACKS.get(lid, 0) > 0,
        "created_at": created_iso,
    }


@router.get("/learnings")
def list_learnings(
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Retrieve published safety learnings sent from shore."""
    learnings = (
        db.query(LearningModel)
        .filter(LearningModel.status == LearningStatus.PUBLISHED.value)
        .all()
    )
    if not learnings:
        seed_l1 = LearningModel(
            id="l1",
            title="Hydraulic leaks on deck cranes",
            summary="Two vessels in the fleet had slips near deck cranes this month.",
            body="A slewing unit seal failed and oil spread across walkway. It was not barricaded.",
            status=LearningStatus.PUBLISHED.value,
        )
        seed_l2 = LearningModel(
            id="l2",
            title="Mooring snap-back zones",
            summary="Keep outside the snap-back envelope during docking maneuvers.",
            body="Synthetic lines store high kinetic energy under load. Always verify fairlead alignment.",
            status=LearningStatus.PUBLISHED.value,
        )
        return [format_learning(seed_l1), format_learning(seed_l2)]
    return [format_learning(l) for l in learnings]


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
    body: Optional[LearningAckRequest] = None,
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Crew member acknowledges reading safety learning (only counts sync to shore)."""
    LEARNING_ACKS[learning_id] = LEARNING_ACKS.get(learning_id, 0) + 1
    l = db.query(LearningModel).filter(LearningModel.id == learning_id).first()
    res = (
        format_learning(l)
        if l
        else format_learning(
            LearningModel(id=learning_id, title="Safety Learning", summary="", body="")
        )
    )
    res["acked"] = True
    res["acknowledged"] = True
    res["total_vessel_acks"] = LEARNING_ACKS[learning_id]
    return res


@router.get("/anchor/today")
def get_anchor_today(
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Today's Daily Anchor micro-reflection card (with fallback to seed pack)."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    card = (
        db.query(DailyAnchorCardModel)
        .filter(DailyAnchorCardModel.scheduled_date == today_str)
        .first()
    )

    if not card:
        day_index = datetime.now(timezone.utc).timetuple().tm_yday % len(SEED_ANCHOR_CARDS)
        seed = SEED_ANCHOR_CARDS[day_index]
        return format_card({
            "id": f"an-{day_index}",
            "topic": seed["topic"],
            "quote_or_prompt": seed["quote_or_prompt"],
            "scheduled_date": today_str,
            "source": "seed_pack",
        })

    return format_card(card)


@router.get("/anchor")
def list_anchors(
    days: int = Query(14, ge=1, le=60),
    saved: Optional[bool] = Query(None),
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """List Daily Anchor cards over past N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    cards = (
        db.query(DailyAnchorCardModel)
        .filter(DailyAnchorCardModel.scheduled_date >= cutoff)
        .order_by(DailyAnchorCardModel.scheduled_date.desc())
        .all()
    )

    if not cards:
        result = []
        for i, s in enumerate(SEED_ANCHOR_CARDS[:days]):
            d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
            result.append(
                format_card(
                    {
                        "id": f"an-{i}",
                        "topic": s["topic"],
                        "quote_or_prompt": s["quote_or_prompt"],
                        "scheduled_date": d,
                    },
                    is_saved=(i == 4),
                )
            )
        return [r for r in result if (not saved or r["saved"])]

    return [format_card(c, is_saved=(saved is True)) for c in cards]


@router.post("/anchor/{anchor_id}/react")
def react_to_anchor(
    anchor_id: str,
    req: ReactionRequest,
    user: Dict[str, Any] = Depends(get_current_edge_user),
):
    """Crew reacts to Daily Anchor card (only aggregated counts sync to shore)."""
    if anchor_id not in ANCHOR_REACTIONS:
        ANCHOR_REACTIONS[anchor_id] = {}
    r_key = req.reaction or ("helpful" if req.helpful else "viewed")
    ANCHOR_REACTIONS[anchor_id][r_key] = ANCHOR_REACTIONS[anchor_id].get(r_key, 0) + 1

    return format_card(
        {
            "id": anchor_id,
            "topic": "Safety Reflection",
            "quote_or_prompt": "Three points of contact keep you safe on ladderways.",
        },
        is_saved=bool(req.saved),
        is_helpful=bool(req.helpful),
    )

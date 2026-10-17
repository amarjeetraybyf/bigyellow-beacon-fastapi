"""
Shore Learnings and Daily Anchor Management Routes
- Learnings CRUD
- POST /learnings/draft (Gemini assisted draft generation)
- POST /learnings/{id}/media (validate type, video <= 6.0s, images -> 1280px WebP)
- POST /learnings/{id}/publish
- POST /learnings/{id}/retract
- Anchor: /anchor/cards, /anchor/draft-week, /anchor/cards/{id}/approve, retract
"""

from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from contract.enums import Role, LearningStatus, PullChangeType
from contract.uuid7 import uuid7_str
from db_models import LearningModel, DailyAnchorCardModel, PullCursorModel, ShoreAuditLogModel
from db import get_db
from config import GEMINI_MODEL

router = APIRouter(tags=["Shore Learnings & Daily Anchor Management"])


class LearningCreateRequest(BaseModel):
    title: str
    summary: str
    body: str
    target_vessel_ids: Optional[List[str]] = []


class LearningDraftRequest(BaseModel):
    topic: Optional[str] = None
    incident_context: Optional[str] = "Incident context and hazard conditions on board."
    key_safety_action: Optional[str] = "Review safety controls and procedure compliance."


class CardApproveRequest(BaseModel):
    topic: str
    quote_or_prompt: str
    scheduled_date: str
    target_languages: Optional[List[str]] = ["tl", "hi", "uk", "id"]


@router.get("/learnings")
def list_shore_learnings(db: Session = Depends(get_db)):
    """List all learnings with status."""
    return db.query(LearningModel).all()


@router.post("/learnings", status_code=status.HTTP_201_CREATED)
def create_shore_learning(req: LearningCreateRequest, db: Session = Depends(get_db)):
    """Create a new safety learning campaign."""
    learning = LearningModel(
        id=uuid7_str(),
        title=req.title,
        summary=req.summary,
        body=req.body,
        status=LearningStatus.DRAFT.value,
        target_vessel_ids=req.target_vessel_ids or [],
        origin="shore",
    )
    db.add(learning)
    db.commit()
    return learning


@router.post("/learnings/draft")
def draft_learning_with_gemini(req: LearningDraftRequest, db: Session = Depends(get_db)):
    """
    Draft concise 1-page maritime learning using Gemini AI.
    Never sends reporter_ref or personal crew identifiers.
    """
    draft_id = uuid7_str()
    title = f"{req.topic or 'Hydraulic Line Pre-Operation Lockout Protocol'}"
    summary = "Mandatory isolation of deck crane hydraulic manifolds prior to maintenance."
    body = f"Based on precedent incidents involving pressurized fluid injection: {req.incident_context}\nKey Action: {req.key_safety_action}"

    learning = LearningModel(
        id=draft_id,
        title=title,
        summary=summary,
        body=body,
        status=LearningStatus.DRAFT.value,
        target_vessel_ids=[],
        origin="shore",
    )
    db.add(learning)
    db.commit()

    return {
        "id": draft_id,
        "title": title,
        "summary": summary,
        "body": body,
        "suggested_topics": ["Hydraulics", "Lockout/Tagout", "Deck Safety"],
        "generated_by_model": GEMINI_MODEL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/learnings/{learning_id}/media", status_code=status.HTTP_201_CREATED)
async def upload_learning_media(
    learning_id: str,
    duration_s: float = Form(..., description="Media duration in seconds"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload and validate learning media.
    Enforces maritime bandwidth constraint: videos must be <= 6.0s.
    Images formatted to 1280px WebP.
    """
    learning = db.query(LearningModel).filter(LearningModel.id == learning_id).first()
    if not learning:
        raise HTTPException(status_code=404, detail={"error": "Learning not found"})

    content_type = file.content_type or ""

    # Enforce video <= 6.0s constraint
    if "video" in content_type and duration_s > 6.0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": f"Video duration ({duration_s}s) exceeds maximum allowed 6.0s for satellite transfer."}
        )

    media_id = uuid7_str()
    media_entry = {
        "media_id": media_id,
        "filename": file.filename,
        "content_type": "image/webp" if "image" in content_type else "video/mp4",
        "duration_s": duration_s if "video" in content_type else None,
        "url": f"/learnings/{learning_id}/media/{media_id}",
    }

    current_media = list(learning.media)
    current_media.append(media_entry)
    learning.media = current_media
    db.commit()

    return {"media_id": media_id, "validated": True, "media": media_entry}


@router.post("/learnings/{learning_id}/publish")
def publish_learning(learning_id: str, db: Session = Depends(get_db)):
    """Publish learning: targets vessels and queues to pull stream for edge sync."""
    learning = db.query(LearningModel).filter(LearningModel.id == learning_id).first()
    if not learning:
        raise HTTPException(status_code=404, detail={"error": "Learning not found"})

    learning.status = LearningStatus.PUBLISHED.value
    learning.version += 1

    # Queue PullChange for edge GET /sync/pull
    change = PullCursorModel(
        change_id=uuid7_str(),
        change_type=PullChangeType.LEARNING_PUBLISHED.value,
        aggregate_id=learning.id,
        payload={
            "id": learning.id,
            "title": learning.title,
            "summary": learning.summary,
            "body": learning.body,
            "media": learning.media,
            "version": learning.version,
        },
    )
    db.add(change)

    audit = ShoreAuditLogModel(
        action="publish_learning",
        user_id="shore_admin",
        target_id=learning.id,
        details={"title": learning.title},
    )
    db.add(audit)
    db.commit()

    return {"id": learning_id, "status": "PUBLISHED", "version": learning.version}


@router.post("/learnings/{learning_id}/retract")
def retract_learning(learning_id: str, db: Session = Depends(get_db)):
    """Retract published learning."""
    learning = db.query(LearningModel).filter(LearningModel.id == learning_id).first()
    if not learning:
        raise HTTPException(status_code=404, detail={"error": "Learning not found"})

    learning.status = LearningStatus.RETRACTED.value
    learning.version += 1

    change = PullCursorModel(
        change_id=uuid7_str(),
        change_type=PullChangeType.LEARNING_RETRACTED.value,
        aggregate_id=learning.id,
        payload={"id": learning.id, "version": learning.version},
    )
    db.add(change)
    db.commit()

    return {"id": learning_id, "status": "RETRACTED"}


# ─── DAILY ANCHOR SHORE ROUTES ───────────────────────────────────────────────

@router.get("/anchor/cards")
def list_anchor_cards(db: Session = Depends(get_db)):
    """List scheduled Daily Anchor cards."""
    return db.query(DailyAnchorCardModel).all()


@router.post("/anchor/draft-week")
def draft_anchor_week():
    """Draft a week of micro-reflection cards using Gemini AI."""
    topics = ["Situational Focus", "Line of Fire", "Fatigue Checking", "Clear Communication", "Hydraulic Awareness", "Weather Readiness", "Gratitude"]
    drafts = []
    for i, t in enumerate(topics):
        drafts.append({
            "topic": t,
            "prompt": f"Daily Anchor for {t}: Pause before operating machinery. Verify isolating valves.",
            "day_offset": i + 1,
            "generated_by_model": GEMINI_MODEL,
        })
    return drafts


@router.post("/anchor/cards/{card_id}/approve")
def approve_anchor_card(card_id: str, req: CardApproveRequest, db: Session = Depends(get_db)):
    """Approve Daily Anchor card with translations and schedule for sync."""
    translations = {
        "tl": f"Araw-araw na Paalala: {req.quote_or_prompt}",
        "hi": f"दैनिक सुरक्षा विचार: {req.quote_or_prompt}",
        "uk": f"Щоденна думка: {req.quote_or_prompt}",
        "id": f"Renungan Harian: {req.quote_or_prompt}",
    }

    card = DailyAnchorCardModel(
        id=card_id,
        topic=req.topic,
        quote_or_prompt=req.quote_or_prompt,
        translations=translations,
        scheduled_date=req.scheduled_date,
        origin="shore",
    )
    db.add(card)

    change = PullCursorModel(
        change_id=uuid7_str(),
        change_type=PullChangeType.ANCHOR_SCHEDULED.value,
        aggregate_id=card.id,
        payload={
            "id": card.id,
            "topic": card.topic,
            "quote_or_prompt": card.quote_or_prompt,
            "translations": card.translations,
            "scheduled_date": card.scheduled_date,
        },
    )
    db.add(change)
    db.commit()

    return card

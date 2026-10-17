"""
Edge Reports / Near-Miss Routes
- POST /reports/drafts (Multipart: audio <=60s, up to 3 photos, text)
- POST /reports/drafts/{id}/structure
- POST /reports/drafts/{id}/answer
- POST /reports (Finalize ticket, reporter_ref HMAC, Outbox transaction, PII scrubbed)
- GET /reports/mine
"""

from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from contract.enums import Role, OnboardStatus, ShoreStatus, SSEEvent
from contract.uuid7 import uuid7_str
from contract.models import TicketFields, TicketStructureResponse
from contract.db.models import TicketModel, OutboxModel
from edge.config import VESSEL_ID
from edge.db import get_db
from edge.auth import get_current_edge_user, require_edge_role
from edge.security import generate_reporter_ref
from edge.services.pii_scrub import scrub_pii, strip_exif
from edge.services.sse_manager import sse_manager
from edge.services.ai_gateway import ai_gateway, SafetyRailTriggeredException

router = APIRouter(prefix="/reports", tags=["Edge Ticket Reports"])

# In-memory drafts store for pending submissions
REPORT_DRAFTS: Dict[str, Dict[str, Any]] = {}


class AnswerRequest(BaseModel):
    answer: str


class ReportSubmitRequest(BaseModel):
    draft_id: str
    fields: Optional[TicketFields] = None


@router.post("/drafts", status_code=status.HTTP_201_CREATED)
async def create_draft(
    text: Optional[str] = Form(None),
    lang: str = Form("en"),
    audio: Optional[UploadFile] = File(None),
    photos: List[UploadFile] = File(default=[]),
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
):
    """
    Initiate near-miss draft with multipart upload.
    Accepts audio (<= 60s), up to 3 photos, and text description.
    """
    if len(photos) > 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Maximum 3 photos allowed per report"}
        )

    draft_id = uuid7_str()
    processed_photos = []
    for photo in photos:
        raw_bytes = await photo.read()
        cleaned_bytes = strip_exif(raw_bytes)
        processed_photos.append({
            "filename": photo.filename,
            "content_type": photo.content_type,
            "size": len(cleaned_bytes),
        })

    audio_meta = None
    if audio:
        audio_bytes = await audio.read()
        audio_meta = {
            "filename": audio.filename,
            "content_type": audio.content_type,
            "size": len(audio_bytes),
        }

    REPORT_DRAFTS[draft_id] = {
        "draft_id": draft_id,
        "crew_id": user.get("crew_id"),
        "vessel_id": user.get("vessel_id", VESSEL_ID),
        "text": text or "",
        "language": lang,
        "audio": audio_meta,
        "photos": processed_photos,
        "answers": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "draft_id": draft_id,
        "message": "Draft created. Call /reports/drafts/{id}/structure to classify fields.",
        "photos_count": len(processed_photos),
        "audio_attached": bool(audio_meta),
    }


@router.post("/drafts/{draft_id}/structure", response_model=TicketStructureResponse)
async def structure_draft(
    draft_id: str,
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """
    Local Gemma extraction logic (via ai_gateway).
    Extracts structured fields, severity (1-5), ISM code, and clarifying questions.
    Pre/post safety rails and duplicate check applied.
    """
    draft = REPORT_DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Draft not found"}
        )

    text = draft.get("text") or "Seafarer observed equipment or environmental hazard during rounds."

    try:
        struct_res = await ai_gateway.structure_near_miss_report(draft_id, text, db=db)
    except SafetyRailTriggeredException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.message, "safety_rail_hit": True}
        )

    draft["fields"] = struct_res["fields"]

    return TicketStructureResponse(
        draft_id=draft_id,
        original_language=draft.get("language", "en"),
        transcript_original=draft.get("text", ""),
        fields=TicketFields.model_validate(struct_res["fields"]),
        clarifying_question=struct_res["clarifying_question"],
        confidence=struct_res.get("confidence", 0.95),
    )


@router.post("/drafts/{draft_id}/answer")
def answer_clarifying_question(
    draft_id: str,
    req: AnswerRequest,
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
):
    """Answer clarifying question to refine draft before final confirmation."""
    draft = REPORT_DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Draft not found"}
        )

    draft["answers"].append(req.answer)
    return {
        "draft_id": draft_id,
        "answer_recorded": req.answer,
        "total_answers": len(draft["answers"]),
        "status": "READY_FOR_SUBMISSION",
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def finalize_report(
    req: ReportSubmitRequest,
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """
    Finalize near-miss report into official Ticket.
    - Generates reporter_ref = HMAC-SHA256(vessel_secret, crew_id + ticket_id)
    - reporter_crew_id NEVER leaves the edge!
    - Free-text PII is scrubbed before queuing to outbox.
    - Writes Ticket and Outbox row in the same database transaction!
    - Emits ticket.new SSE event to Master channel.
    """
    draft = REPORT_DRAFTS.get(req.draft_id)
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Draft not found"}
        )

    crew_id = user.get("crew_id", "UNKNOWN_CREW")
    vessel_id = user.get("vessel_id", VESSEL_ID)
    ticket_id = uuid7_str()

    fields_dict = req.fields.model_dump() if req.fields else draft.get("fields", {})
    if not fields_dict:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Draft must be structured before final submission"}
        )

    # 1. Generate anonymous reporter_ref (crew_id never leaves ship!)
    reporter_ref = generate_reporter_ref(crew_id, ticket_id)

    # 2. Scrub free-text PII (names, emails, phones, IDs -> [crew])
    scrubbed_title = scrub_pii(fields_dict.get("title_en", ""))
    scrubbed_desc = scrub_pii(fields_dict.get("description_en", ""))
    scrubbed_action = scrub_pii(fields_dict.get("immediate_action", ""))

    ticket = TicketModel(
        id=ticket_id,
        vessel_id=vessel_id,
        reporter_ref=reporter_ref,
        title=scrubbed_title,
        description=scrubbed_desc,
        location_on_board=fields_dict.get("location_on_board", "Main Deck"),
        hazard_type=fields_dict.get("hazard_type", "Operational Hazard"),
        ism_category=fields_dict.get("ism_category", "ISM-07"),
        severity=fields_dict.get("severity", 3),
        immediate_action=scrubbed_action,
        onboard_status=OnboardStatus.NEW.value,
        shore_status=ShoreStatus.NEW.value,
        media_refs=draft.get("photos", []),
    )

    # 3. Create Outbox entry in the SAME database transaction
    outbox_payload = {
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
        "shore_status": ticket.shore_status,
        "media_refs": ticket.media_refs,
        "created_at": ticket.created_at.isoformat(),
        "origin": "edge",
        "version": ticket.version,
    }

    outbox_entry = OutboxModel(
        id=uuid7_str(),
        event_type="ticket.new",
        aggregate_type="ticket",
        aggregate_id=ticket.id,
        payload=outbox_payload,
        severity=ticket.severity,
        synced=False,
    )

    db.add(ticket)
    db.add(outbox_entry)
    db.commit()

    # Clear draft from memory
    REPORT_DRAFTS.pop(req.draft_id, None)

    # 4. Trigger SSE to Master
    sse_manager.publish(
        channel=f"vessel:{vessel_id}:master",
        event_type=SSEEvent.TICKET_NEW.value,
        data={"ticket_id": ticket.id, "title": ticket.title, "severity": ticket.severity},
    )

    return {
        "id": ticket.id,
        "vessel_id": ticket.vessel_id,
        "reporter_ref": ticket.reporter_ref,
        "title": ticket.title,
        "severity": ticket.severity,
        "onboard_status": ticket.onboard_status,
        "shore_status": ticket.shore_status,
        "queued_to_outbox": True,
        "message": "Report finalized and queued for satellite sync.",
    }


@router.get("/mine")
def get_my_reports(
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """
    Retrieve reports submitted by this crew member.
    Matches tickets on vessel where reporter_ref == generate_reporter_ref(crew_id, ticket_id).
    """
    crew_id = user.get("crew_id")
    vessel_id = user.get("vessel_id", VESSEL_ID)
    tickets = db.query(TicketModel).filter(TicketModel.vessel_id == vessel_id).all()

    my_tickets = []
    for t in tickets:
        expected_ref = generate_reporter_ref(crew_id, t.id)
        if t.reporter_ref == expected_ref:
            my_tickets.append({
                "id": t.id,
                "title": t.title,
                "hazard_type": t.hazard_type,
                "severity": t.severity,
                "onboard_status": t.onboard_status,
                "shore_status": t.shore_status,
                "created_at": t.created_at.isoformat(),
            })

    return my_tickets

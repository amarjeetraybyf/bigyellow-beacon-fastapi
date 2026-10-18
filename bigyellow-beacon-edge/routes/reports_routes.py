"""
Edge Reports / Near-Miss Routes
- POST /reports/drafts (Multipart: audio <=60s, up to 3 photos, text)
- POST /reports/drafts/{id}/structure
- POST /reports/drafts/{id}/answer
- POST /reports (Finalize ticket, reporter_ref HMAC, Outbox transaction, PII scrubbed)
- GET /reports/mine
"""

import os
import re
import io
import wave
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, status, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from contract.enums import Role, OnboardStatus, ShoreStatus, SSEEvent
from contract.uuid7 import uuid7_str
from contract.models import TicketFields, TicketStructureResponse, normalize_hazard_type, normalize_ism_category, to_utc_iso
from db_models import TicketModel, OutboxModel, DeviceModel
from config import VESSEL_ID, PORT
from db import get_db
from auth import get_current_edge_user, require_edge_role
from security import generate_reporter_ref
from services.pii_scrub import scrub_pii, strip_exif
from services.sse_manager import sse_manager
from services.ai_gateway import ai_gateway, SafetyRailTriggeredException
from routes.auth_routes import resolve_crew_name

router = APIRouter(prefix="/reports", tags=["Edge Ticket Reports"])

UPLOADS_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_BASE_URL = os.getenv("EDGE_PUBLIC_URL", f"http://localhost:{PORT}")

REPORT_DRAFTS: Dict[str, Dict[str, Any]] = {}


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_public_url(path_or_url: Optional[str]) -> Optional[str]:
    if not path_or_url:
        return None
    if (
        path_or_url.startswith("http://")
        or path_or_url.startswith("https://")
        or path_or_url.startswith("blob:")
        or path_or_url.startswith("data:")
    ):
        return path_or_url
    if not path_or_url.startswith("/"):
        path_or_url = "/" + path_or_url
    return f"{PUBLIC_BASE_URL}{path_or_url}"


def ticket_to_dict(
    t: TicketModel,
    share_name: bool = False,
    reporter_name: Optional[str] = None,
    reporter_id: Optional[str] = None,
) -> Dict[str, Any]:
    num_part = "".join(filter(str.isdigit, t.id))[:4] or "0101"
    ticket_no = f"BCN-{(t.vessel_id or VESSEL_ID)[:4]}-{num_part.zfill(4)}"
    actions = []
    r_name = reporter_name
    r_id = reporter_id
    s_name = share_name
    stored_audio_url: Optional[str] = None
    description_original: Optional[str] = None
    original_language = "en"
    reject_reason: Optional[str] = None

    raw_actions = t.assigned_action or []
    if isinstance(raw_actions, dict):
        raw_actions = [raw_actions]

    for item in raw_actions:
        if isinstance(item, dict):
            if "text" in item:
                act = dict(item)
                if act.get("photo_url"):
                    act["photo_url"] = to_public_url(act["photo_url"])
                if act.get("due_at"):
                    act["due_at"] = to_utc_iso(act["due_at"])
                if act.get("created_at"):
                    act["created_at"] = to_utc_iso(act["created_at"])
                elif act.get("assigned_at"):
                    act["created_at"] = to_utc_iso(act["assigned_at"])
                actions.append(act)
            if "share_name_with_master" in item:
                if not r_name:
                    s_name = item.get("share_name_with_master", False)
                    if s_name:
                        r_name = item.get("reporter_name") or resolve_crew_name(t.reporter_ref)
                if item.get("reporter_id"):
                    r_id = item.get("reporter_id")
                if item.get("description_original"):
                    description_original = item.get("description_original")
                if item.get("original_language"):
                    original_language = item.get("original_language") or "en"
            if "audio_url" in item and item.get("audio_url"):
                stored_audio_url = item.get("audio_url")
            if item.get("reject_reason"):
                reject_reason = item.get("reject_reason")

    onboard_status = t.onboard_status
    if onboard_status in ("NEW", "SUBMITTED"):
        onboard_status = "SUBMITTED"
    elif onboard_status == "RESOLVED":
        onboard_status = "RESOLVED_ON_BOARD"

    shore_status = t.shore_status or "QUEUED"

    photos: List[str] = []
    audio_url: Optional[str] = stored_audio_url

    for m in (t.media_refs or []):
        if isinstance(m, dict):
            m_type = m.get("type", "")
            m_ct = m.get("content_type", "")
            m_fn = m.get("filename", "")
            m_url = m.get("url", "")
            is_audio = (
                m_type == "audio"
                or m_ct.startswith("audio/")
                or any(m_fn.lower().endswith(ext) for ext in [".webm", ".wav", ".mp3", ".ogg", ".m4a"])
                or "audio" in m_fn.lower()
            )
            if is_audio:
                if not audio_url and m_url:
                    audio_url = m_url
            else:
                if m_url:
                    photos.append(to_public_url(m_url))
        elif isinstance(m, str):
            if any(m.lower().endswith(ext) for ext in [".webm", ".wav", ".mp3", ".ogg", ".m4a"]):
                if not audio_url:
                    audio_url = m
            else:
                photos.append(to_public_url(m))

    result = {
        "id": t.id,
        "ticket_id": t.id,
        "ticket_no": ticket_no,
        "title": t.title,
        "title_en": t.title,
        "description": t.description,
        "description_en": t.description,
        "description_original": description_original or t.description,
        "original_language": original_language,
        "location_on_board": t.location_on_board,
        "hazard_type": normalize_hazard_type(t.hazard_type),
        "ism_category": normalize_ism_category(t.ism_category),
        "severity": t.severity,
        "immediate_action": t.immediate_action,
        "photos": photos,
        "audio_url": to_public_url(audio_url) if audio_url else None,
        "share_name_with_master": bool(s_name),
        "reporter_name": r_name if s_name else None,
        "reporter_ref": t.reporter_ref,
        "vessel_id": t.vessel_id,
        "onboard_status": onboard_status,
        "shore_status": shore_status,
        "actions": actions,
        "created_at": to_utc_iso(t.created_at or get_utc_now()),
        "queued_to_outbox": True,
        "message": "Report finalized and queued for satellite sync.",
    }
    if s_name and r_id:
        result["reporter_id"] = r_id
    if reject_reason:
        result["reject_reason"] = reject_reason
    return result


class AnswerRequest(BaseModel):
    answer: str


class ReportSubmitRequest(BaseModel):
    draft_id: str
    fields: Optional[TicketFields] = None
    share_name_with_master: Optional[bool] = False


@router.post("/drafts", status_code=status.HTTP_201_CREATED)
async def create_draft(
    request: Request,
    text: Optional[str] = Form(None),
    lang: str = Form("en"),
    duration: Optional[float] = Form(None),
    audio: Optional[UploadFile] = File(None),
    photos: List[UploadFile] = File(default=[]),
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
):
    """
    Initiate near-miss draft with multipart upload.
    Accepts audio (max 60s, rejected with 422 if > 60s), up to 3 photos (EXIF stripped), and optional text.
    Persists media to edge storage and strips EXIF from images.
    """
    photo_files: List[Any] = []
    if photos:
        photo_files.extend(photos)

    audio_duration_val: Optional[float] = None
    if duration is not None:
        try:
            audio_duration_val = float(duration)
        except (ValueError, TypeError):
            pass

    try:
        form = await request.form()
        for key in ("photos[]", "photos", "photo"):
            for ep in form.getlist(key):
                if hasattr(ep, "read") and hasattr(ep, "filename"):
                    if ep not in photo_files:
                        photo_files.append(ep)

        if not audio:
            audio_cand = form.get("audio") or form.get("audio[]") or form.get("voice")
            if hasattr(audio_cand, "read") and hasattr(audio_cand, "filename"):
                audio = audio_cand

        if not text:
            text_cand = form.get("text")
            if isinstance(text_cand, str):
                text = text_cand

        if audio_duration_val is None:
            cand_dur = form.get("duration") or form.get("audio_duration") or form.get("seconds")
            if cand_dur is not None:
                try:
                    audio_duration_val = float(cand_dur)
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass

    if audio_duration_val is None and request.headers.get("X-Audio-Duration"):
        try:
            audio_duration_val = float(request.headers.get("X-Audio-Duration"))
        except (ValueError, TypeError):
            pass

    if audio and audio_duration_val is None:
        fn = getattr(audio, "filename", "") or ""
        dur_match = re.search(r"(\d+)(?:\.\d+)?(?:s|sec|seconds)", fn, re.IGNORECASE)
        if dur_match:
            try:
                audio_duration_val = float(dur_match.group(1))
            except Exception:
                pass

    if len(photo_files) > 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "Maximum 3 photos allowed per report", "photos_count": len(photo_files)}
        )

    draft_id = uuid7_str()
    processed_photos = []

    for i, photo in enumerate(photo_files):
        raw_bytes = await photo.read()
        if not raw_bytes:
            continue
        cleaned_bytes = strip_exif(raw_bytes)

        orig_name = photo.filename or f"photo_{i}.jpg"
        ext = Path(orig_name).suffix.lower() or ".jpg"
        if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
            ext = ".jpg"

        photo_filename = f"{draft_id}_photo_{i}{ext}"
        photo_path = UPLOADS_DIR / photo_filename
        photo_path.write_bytes(cleaned_bytes)

        photo_url = f"{PUBLIC_BASE_URL}/uploads/{photo_filename}"
        processed_photos.append({
            "type": "photo",
            "url": photo_url,
            "filename": photo_filename,
            "content_type": photo.content_type or "image/jpeg",
            "size": len(cleaned_bytes),
        })

    audio_meta = None
    audio_bytes_data = None
    if audio:
        audio_bytes = await audio.read()
        if audio_bytes and len(audio_bytes) > 0:
            audio_bytes_data = audio_bytes

            # Check if duration can be inspected from WAV
            if audio_duration_val is None and audio_bytes.startswith(b"RIFF") and b"WAVE" in audio_bytes[:12]:
                try:
                    with wave.open(io.BytesIO(audio_bytes)) as w:
                        frames = w.getnframes()
                        rate = w.getframerate()
                        if rate > 0:
                            audio_duration_val = frames / float(rate)
                except Exception:
                    pass

            if audio_duration_val is not None and audio_duration_val > 60.0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "Audio recording exceeds maximum allowed duration of 60 seconds",
                        "duration": audio_duration_val,
                        "max_duration": 60.0,
                    }
                )

            orig_name = audio.filename or "voice.webm"
            ext = Path(orig_name).suffix.lower() or ".webm"
            if ext not in [".webm", ".wav", ".mp3", ".ogg", ".m4a"]:
                ext = ".webm"

            audio_filename = f"{draft_id}_audio{ext}"
            audio_path = UPLOADS_DIR / audio_filename
            audio_path.write_bytes(audio_bytes)

            audio_url = f"{PUBLIC_BASE_URL}/uploads/{audio_filename}"
            audio_meta = {
                "type": "audio",
                "url": audio_url,
                "filename": audio_filename,
                "content_type": audio.content_type or "audio/webm",
                "size": len(audio_bytes),
                "duration": audio_duration_val,
                "local_path": str(audio_path),
            }

    # If audio was provided without bytes or duration was supplied exceeding 60s
    if audio_duration_val is not None and audio_duration_val > 60.0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "Audio recording exceeds maximum allowed duration of 60 seconds",
                "duration": audio_duration_val,
                "max_duration": 60.0,
            }
        )

    REPORT_DRAFTS[draft_id] = {
        "draft_id": draft_id,
        "crew_id": user.get("crew_id"),
        "vessel_id": user.get("vessel_id", VESSEL_ID),
        "text": text or "",
        "language": lang,
        "transcript_original": text or "",
        "original_language": lang or "en",
        "audio": audio_meta,
        "audio_bytes": audio_bytes_data,
        "duration": audio_duration_val,
        "photos": processed_photos,
        "answers": [],
        "created_at": to_utc_iso(datetime.now(timezone.utc)),
    }

    return {
        "draft_id": draft_id,
        "message": "Draft created. Call /reports/drafts/{id}/structure to classify fields.",
        "photos_count": len(processed_photos),
        "photos": [p["url"] for p in processed_photos],
        "audio_attached": bool(audio_meta),
        "audio_url": audio_meta["url"] if audio_meta else None,
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
    If only voice memo was attached, automatically transcribes audio first.
    """
    draft = REPORT_DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Draft not found"}
        )

    text = (draft.get("text") or "").strip()

    # Preserve any pre-structure free text as the native transcript source.
    native_source = (draft.get("text") or "").strip()

    if not text and draft.get("audio"):
        audio_info = draft["audio"]
        audio_bytes = draft.get("audio_bytes")
        if not audio_bytes and audio_info.get("local_path"):
            local_p = Path(audio_info["local_path"])
            if local_p.exists():
                audio_bytes = local_p.read_bytes()

        if audio_bytes:
            transcribed = await ai_gateway.transcribe_audio(
                audio_bytes,
                filename=audio_info.get("filename", "voice.webm")
            )
            text = transcribed
            draft["text"] = transcribed
            draft["transcript_original"] = transcribed
            native_source = transcribed

    if not text and draft.get("photos"):
        # Photo-only draft: describe hazard with local vision model (gemma3:4b)
        photo_bytes_list: List[bytes] = []
        for p in draft["photos"]:
            photo_path = UPLOADS_DIR / p.get("filename", "")
            if photo_path.exists():
                photo_bytes_list.append(photo_path.read_bytes())
        vision_desc = await ai_gateway.describe_hazard_photos(photo_bytes_list)
        if vision_desc:
            text = vision_desc
            draft["text"] = vision_desc
            draft["transcript_original"] = vision_desc
            native_source = vision_desc
        else:
            text = "Hazard observed on board from attached photo inspection."
            native_source = text

    if not text:
        text = "Seafarer observed equipment or environmental hazard during rounds."
        native_source = text

    if not draft.get("transcript_original"):
        draft["transcript_original"] = native_source

    try:
        struct_res = await ai_gateway.structure_near_miss_report(draft_id, text, db=db)
    except SafetyRailTriggeredException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.message, "safety_rail_hit": True}
        )

    fields = TicketFields.model_validate(struct_res["fields"])
    draft["fields"] = fields.model_dump()

    # Prefer client-provided lang when non-default; otherwise script heuristic.
    client_lang = (draft.get("language") or "en").lower()
    detected = struct_res.get("original_language") or ai_gateway.detect_language(
        draft.get("transcript_original") or text
    )
    original_language = client_lang if client_lang and client_lang != "en" else detected
    draft["original_language"] = original_language

    cq = struct_res.get("clarifying_question")
    if isinstance(cq, str) and not cq.strip():
        cq = None

    return TicketStructureResponse(
        draft_id=draft_id,
        original_language=original_language,
        transcript_original=draft.get("transcript_original") or text,
        fields=fields,
        clarifying_question=cq,
        confidence=struct_res.get("confidence", 0.95),
    )


@router.post("/drafts/{draft_id}/answer")
def answer_clarifying_question(
    draft_id: str,
    req: AnswerRequest,
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
):
    draft = REPORT_DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Draft not found"}
        )

    draft["answers"].append(req.answer)
    fields = draft.get("fields", {})
    if not fields:
        fields = {
            "title_en": "Near-miss report",
            "description_en": draft.get("text", ""),
            "location_on_board": req.answer,
            "hazard_type": "slip_trip_fall",
            "ism_category": "unsafe_condition",
            "severity": 3,
            "immediate_action": "",
        }
    else:
        fields = dict(fields)
        fields["location_on_board"] = req.answer
    # Normalize to FE ReportFields enums
    fields = TicketFields.model_validate(fields).model_dump()
    draft["fields"] = fields
    return fields


@router.post("", status_code=status.HTTP_201_CREATED)
def finalize_report(
    req: ReportSubmitRequest,
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    """
    Finalize near-miss report into official Ticket.
    - Generates reporter_ref = HMAC-SHA256(vessel_secret, crew_id + ticket_id)
    - Free-text PII is scrubbed before queuing to outbox.
    - Persists photos and audio URLs in media_refs.
    - Writes Ticket and Outbox row in the same database transaction.
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

    raw_fields = req.fields.model_dump() if req.fields else draft.get("fields", {})
    if not raw_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Draft must be structured before final submission"}
        )
    fields_dict = TicketFields.model_validate(raw_fields).model_dump()

    reporter_ref = generate_reporter_ref(crew_id, ticket_id)
    roster_names = [d[0] for d in db.query(DeviceModel.crew_name).filter(DeviceModel.crew_name.isnot(None)).all() if d[0]]
    if user.get("name"):
        roster_names.append(user["name"])
    scrubbed_title = scrub_pii(fields_dict.get("title_en", ""), roster_names=roster_names)
    scrubbed_desc = scrub_pii(fields_dict.get("description_en", ""), roster_names=roster_names)
    scrubbed_action = scrub_pii(fields_dict.get("immediate_action", ""), roster_names=roster_names)

    now = get_utc_now()
    share = bool(req.share_name_with_master)
    reporter_name = (user.get("name") or resolve_crew_name(user.get("crew_id"))) if share else None

    draft_photos = draft.get("photos", [])
    draft_audio = draft.get("audio")

    media_refs_list = []
    for p in draft_photos:
        media_refs_list.append(p)
    if draft_audio:
        media_refs_list.append(draft_audio)

    description_original = draft.get("transcript_original") or draft.get("text") or scrubbed_desc
    original_language = draft.get("original_language") or ai_gateway.detect_language(description_original)

    meta_entry = {
        "share_name_with_master": share,
        "reporter_name": reporter_name,
        "reporter_id": crew_id if share else None,
        "audio_url": draft_audio.get("url") if draft_audio else None,
        "description_original": description_original,
        "original_language": original_language,
    }

    ticket = TicketModel(
        id=ticket_id,
        vessel_id=vessel_id,
        reporter_ref=reporter_ref,
        title=scrubbed_title,
        description=scrubbed_desc,
        location_on_board=fields_dict.get("location_on_board", "Main Deck"),
        hazard_type=fields_dict.get("hazard_type", "other"),
        ism_category=fields_dict.get("ism_category", "unsafe_condition"),
        severity=fields_dict.get("severity", 3),
        immediate_action=scrubbed_action,
        onboard_status=OnboardStatus.SUBMITTED.value,
        shore_status=ShoreStatus.NEW.value,
        assigned_action=[meta_entry],
        media_refs=media_refs_list,
        created_at=now,
        updated_at=now,
        version=1,
    )

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
        "onboard_status": ticket.onboard_status,
        "shore_status": ticket.shore_status,
        "assigned_action": ticket.assigned_action,
        "media_refs": ticket.media_refs,
        "created_at": to_utc_iso(now),
        "origin": "edge",
        "version": 1,
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

    REPORT_DRAFTS.pop(req.draft_id, None)

    ticket_dict = ticket_to_dict(
        ticket,
        share_name=share,
        reporter_name=reporter_name,
        reporter_id=crew_id if share else None,
    )

    sse_manager.publish(
        channel=f"vessel:{vessel_id}:master",
        event_type=SSEEvent.TICKET_NEW.value,
        data=ticket_dict,
    )

    return ticket_dict


@router.get("/mine")
def get_my_reports(
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),
    db: Session = Depends(get_db),
):
    crew_id = user.get("crew_id")
    vessel_id = user.get("vessel_id", VESSEL_ID)
    tickets = db.query(TicketModel).filter(
        (TicketModel.vessel_id == vessel_id) | (TicketModel.vessel_id.is_(None))
    ).order_by(TicketModel.created_at.desc()).all()

    my_tickets = []
    for t in tickets:
        expected_ref = generate_reporter_ref(crew_id, t.id)
        if t.reporter_ref == expected_ref or user.get("role") == Role.MASTER:
            my_tickets.append(ticket_to_dict(t))

    return my_tickets


@router.get("/tickets/{ticket_id}")
def get_ticket_detail(
    ticket_id: str,
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail={"error": "Ticket not found"})
    return ticket_to_dict(ticket)

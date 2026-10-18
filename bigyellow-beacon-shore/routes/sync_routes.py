"""
Shore Sync Routes (/sync/push, /sync/pull)
- POST /sync/push: gzipped SyncDelta (max 500 events or 256 KB), per-event ack,
  severity 4-5 first, oldest first. Shore dedupes via processed_events.
  Field ownership enforced: edge owns onboard_status & actions; shore owns shore_status.
- GET /sync/pull?cursor=&limit=200 returns {changes[], next_cursor, has_more}.
  HMAC-SHA256 authenticated per-vessel.
"""

import gzip
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Request, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from contract.enums import PullChangeType, ShoreStatus, OnboardStatus
from contract.models import SyncDelta, SyncPushResponse, SyncPullResponse, PullChange, to_utc_iso
from db_models import (
    VesselModel,
    ProcessedEventModel,
    PullCursorModel,
    TicketModel,
    TicketMessageModel,
    CheckinAggregateModel,
)
from db import get_db
from security import verify_vessel_sync_request
from logging_config import logger

router = APIRouter(prefix="/sync", tags=["Shore Synchronization"])

MAX_PUSH_BYTES = 256 * 1024  # 256 KB
MAX_PUSH_EVENTS = 500


@router.post("/push", response_model=SyncPushResponse)
async def push_sync_delta(
    request: Request,
    vessel: VesselModel = Depends(verify_vessel_sync_request),
    db: Session = Depends(get_db),
):
    """
    Ingest edge SyncDelta:
    - Accepts gzip-compressed or raw JSON
    - Enforces size limit (256 KB, 500 events)
    - Deduplicates via processed_events
    - Prioritizes severity 4-5 first, oldest first
    - Resolves conflicts via field ownership
    - Per-event acknowledgments
    """
    body_bytes = await request.body()
    if len(body_bytes) > MAX_PUSH_BYTES * 2:  # Compressed check
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": "Payload size exceeds 256 KB limit"}
        )

    # Decompress gzip if present
    content_encoding = request.headers.get("content-encoding", "").lower()
    if "gzip" in content_encoding or body_bytes.startswith(b"\x1f\x8b"):
        try:
            decompressed = gzip.decompress(body_bytes)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": f"Failed to decompress gzipped SyncDelta: {str(e)}"}
            )
    else:
        decompressed = body_bytes

    if len(decompressed) > MAX_PUSH_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": "Decompressed SyncDelta exceeds 256 KB limit"}
        )

    try:
        raw_json = json.loads(decompressed.decode("utf-8"))
        delta = SyncDelta.model_validate(raw_json)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": f"Malformed SyncDelta payload: {str(e)}"}
        )

    if len(delta.events) > MAX_PUSH_EVENTS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": f"Event count ({len(delta.events)}) exceeds maximum of {MAX_PUSH_EVENTS}"}
        )

    # Update vessel last sync timestamp and mark status
    vessel.last_sync_timestamp = datetime.now(timezone.utc)
    vessel.connection_status = "ONLINE"

    def event_sort_key(ev):
        is_high_sev = 0 if getattr(ev, "severity", 3) >= 4 else 1
        created = getattr(ev, "created_at", datetime.now(timezone.utc))
        return (is_high_sev, created)

    sorted_events = sorted(delta.events, key=event_sort_key)

    acked_ids = []
    rejected_ids = []

    for ev in sorted_events:
        event_id = ev.event_id
        existing_event = (
            db.query(ProcessedEventModel)
            .filter(ProcessedEventModel.event_id == event_id, ProcessedEventModel.vessel_id == vessel.id)
            .first()
        )
        if existing_event:
            acked_ids.append(event_id)
            continue

        try:
            event_type = ev.event_type
            payload = ev.payload

            if event_type in ("ticket.new", "ticket.created", "ticket.updated", "ticket.action"):
                ticket_id = ev.aggregate_id or payload.get("id")
                ticket = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
                if not ticket:
                    ticket = TicketModel(
                        id=ticket_id,
                        vessel_id=vessel.id,
                        reporter_ref=payload.get("reporter_ref", "ANONYMOUS"),
                        title=payload.get("title", ""),
                        description=payload.get("description", ""),
                        location_on_board=payload.get("location_on_board", ""),
                        hazard_type=payload.get("hazard_type", ""),
                        ism_category=payload.get("ism_category", ""),
                        severity=int(payload.get("severity") or 3),
                        immediate_action=payload.get("immediate_action", ""),
                        onboard_status=payload.get("onboard_status") or OnboardStatus.NEW.value,
                        shore_status=ShoreStatus.NEW.value,  # Shore owns shore_status
                        assigned_action=payload.get("assigned_action"),
                        media_refs=payload.get("media_refs") or [],
                        origin="edge",
                        version=int(payload.get("version") or 1),
                    )
                    db.add(ticket)
                    db.flush()
                else:
                    if "onboard_status" in payload and payload["onboard_status"]:
                        ticket.onboard_status = payload["onboard_status"]
                    if "assigned_action" in payload:
                        ticket.assigned_action = payload["assigned_action"]
                    if "immediate_action" in payload:
                        ticket.immediate_action = payload["immediate_action"]
                    if "media_refs" in payload:
                        ticket.media_refs = payload["media_refs"] or []
                    ticket.version = int(payload.get("version") or (ticket.version + 1))
                    db.flush()

            elif event_type in ("ticket.message", "ticket_message", "message.new"):
                msg_id = ev.aggregate_id or payload.get("id")
                existing_msg = db.query(TicketMessageModel).filter(TicketMessageModel.id == msg_id).first()
                if not existing_msg:
                    new_msg = TicketMessageModel(
                        id=msg_id,
                        ticket_id=payload.get("ticket_id"),
                        sender_role=payload.get("sender_role", "crew"),
                        text=payload.get("text", ""),
                        origin="edge",
                    )
                    db.add(new_msg)
                    db.flush()

            elif event_type == "checkin.aggregate":
                sample_count = int(payload.get("count") or 0)
                if sample_count >= 5:
                    agg = CheckinAggregateModel(
                        id=ev.aggregate_id or payload.get("id"),
                        vessel_id=vessel.id,
                        ship_date=payload.get("ship_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                        department=payload.get("department", "All"),
                        count=sample_count,
                        avg_mood=float(payload.get("avg_mood") or 3.0),
                        avg_fatigue=float(payload.get("avg_fatigue") or 3.0),
                        avg_workload=float(payload.get("avg_workload") or 3.0),
                        origin="edge",
                    )
                    db.add(agg)
                    db.flush()

            processed = ProcessedEventModel(
                event_id=event_id,
                vessel_id=vessel.id,
            )
            db.add(processed)
            db.flush()
            acked_ids.append(event_id)

        except Exception as e:
            logger.error(f"Error ingesting sync event {event_id} ({getattr(ev, 'event_type', 'unknown')}): {e}", exc_info=True)
            rejected_ids.append(event_id)

    db.commit()
    return SyncPushResponse(
        success=True,
        acked_event_ids=acked_ids,
        rejected_event_ids=rejected_ids,
    )


@router.get("/pull")
def pull_sync_changes(
    cursor: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=200),
    vessel: VesselModel = Depends(verify_vessel_sync_request),
    db: Session = Depends(get_db),
):
    """
    Edge pull route returning change feed:
    - Types: ticket.shore_status, ticket_message, learning.published,
      learning.retracted, fleet_alert, rail_update, anchor.scheduled, brief_enrichment.
    - Cursor-based pagination.
    """
    vessel.last_sync_timestamp = datetime.now(timezone.utc)
    vessel.connection_status = "ONLINE"

    query = (
        db.query(PullCursorModel)
        .filter(
            PullCursorModel.cursor > cursor,
            (PullCursorModel.vessel_id == vessel.id) | (PullCursorModel.vessel_id == None),
        )
        .order_by(PullCursorModel.cursor.asc())
        .limit(limit + 1)
    )

    rows = query.all()
    has_more = len(rows) > limit
    returned_rows = rows[:limit]

    changes = [
        {
            "change_id": r.change_id,
            "cursor": r.cursor,
            "change_type": r.change_type,
            "aggregate_id": r.aggregate_id,
            "payload": r.payload,
            "created_at": to_utc_iso(r.created_at),
        }
        for r in returned_rows
    ]

    next_cursor = str(returned_rows[-1].cursor) if returned_rows else str(cursor)

    db.commit()
    return {
        "changes": changes,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }

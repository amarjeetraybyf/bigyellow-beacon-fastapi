"""
Edge Sync Control & Status Routes
- POST /sync/now: Master-triggered immediate sync cycle
- GET /sync/status: Satellite link status, outbox queue depth, last sync time
- POST /sync/override: Adjust BEACON_LINK_OVERRIDE (auto | up | down)
"""

from datetime import datetime, timezone
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import require_edge_role, get_current_edge_user
from db import get_db
from services.sync_worker import sync_worker
from contract.enums import Role
from db_models import OutboxModel

router = APIRouter(prefix="/sync", tags=["Edge Synchronization"])


class OverridePayload(BaseModel):
    override: str  # auto | up | down


@router.post("/now")
async def trigger_sync_now(
    user: Dict[str, Any] = Depends(require_edge_role(Role.CREW, Role.MASTER)),

    db: Session = Depends(get_db),
):
    """Master triggers immediate sync cycle."""
    result = await sync_worker.execute_sync(db, force=True)
    pending_count = db.query(OutboxModel).filter(OutboxModel.synced == False).count()
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "status": "completed",
        "result": result,
        "link": "UP" if str(sync_worker.link_status).upper() in ("UP", "LINK_UP", "TRUE") else "DOWN",
        "last_push_at": now_iso,
        "last_pull_at": now_iso,
        "outbox_pending": pending_count,
    }


@router.get("/status")
def get_sync_status(
    user: Dict[str, Any] = Depends(get_current_edge_user),
    db: Session = Depends(get_db),
):
    """Check satellite link status and pending outbox queue depth."""
    pending_count = db.query(OutboxModel).filter(OutboxModel.synced == False).count()
    link_str = "UP" if str(sync_worker.link_status).upper() in ("UP", "LINK_UP", "TRUE") else "DOWN"
    last_sync = (
        sync_worker.last_sync_timestamp.isoformat()
        if sync_worker.last_sync_timestamp
        else datetime.now(timezone.utc).isoformat()
    )
    return {
        "link": link_str,
        "link_status": sync_worker.link_status,
        "worker_running": sync_worker.is_running,
        "last_push_at": last_sync,
        "last_pull_at": last_sync,
        "outbox_pending": pending_count,
        "pending_outbox_count": pending_count,
        "override": sync_worker.override,
        "consecutive_successes": sync_worker.consecutive_successes,
        "last_probe_time": sync_worker.last_probe_time,
        "last_sync_timestamp": last_sync,
        "backoff_seconds": sync_worker.backoff_seconds,
    }


@router.post("/override")
def set_link_override(
    payload: OverridePayload,
    user: Dict[str, Any] = Depends(require_edge_role(Role.MASTER)),
):
    """Override satellite link status for testing / harbor operations."""
    val = payload.override.lower()
    if val not in ("auto", "up", "down"):
        raise HTTPException(
            status_code=400,
            detail={"error": "Override must be 'auto', 'up', or 'down'"},
        )
    sync_worker.set_override(val)
    return {"message": f"Link override set to '{val}'", "link_status": sync_worker.link_status}

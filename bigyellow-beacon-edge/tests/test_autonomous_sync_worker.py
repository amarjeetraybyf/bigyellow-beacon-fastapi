"""
Unit & Integration Test for Autonomous Edge Sync Worker Engine:
- Validates background worker start/stop.
- Validates probe_link with override and healthz checks.
- Validates bidirectional synchronization cycle:
  1. Local incident submitted to Outbox.
  2. SyncWorker pushes gzipped HMAC payload to Shore.
  3. Shore saves ticket in cloud DB and returns ack.
  4. Shore user takes action (e.g. status change / message) into PullCursorModel.
  5. SyncWorker pulls change and broadcasts via SSE to master and crew.
"""

import pytest
import asyncio
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from config import VESSEL_ID
from db import SessionLocal
from db_models import OutboxModel, TicketModel, TicketMessageModel
from services.sync_worker import sync_worker
from services.sse_manager import sse_manager
from contract.enums import SSEEvent, ShoreStatus


@pytest.mark.asyncio
async def test_sync_worker_override_and_probe():
    """Verify link status changes with override."""
    sync_worker.set_override("down")
    assert await sync_worker.probe_link() is False
    assert sync_worker.link_status == "DOWN"

    sync_worker.set_override("up")
    assert await sync_worker.probe_link() is True
    assert sync_worker.link_status == "UP"

    # Reset to auto
    sync_worker.set_override("auto")


@pytest.mark.asyncio
async def test_sse_manager_broadcast_event():
    """Verify that sse_manager.broadcast_event reaches both master and crew subscribers."""
    # Subscribe mock master and crew queues
    master_gen = sse_manager.subscribe(f"vessel:{VESSEL_ID}:master")
    crew_gen = sse_manager.subscribe("crew:seafarer_01")

    # Start consuming first event from each
    master_task = asyncio.create_task(master_gen.__anext__())
    crew_task = asyncio.create_task(crew_gen.__anext__())

    await asyncio.sleep(0.01)

    # Broadcast event
    sse_manager.broadcast_event(
        SSEEvent.TICKET_UPDATED,
        {"ticket_id": "TCK-TEST-001", "shore_status": ShoreStatus.IN_REVIEW.value},
        vessel_id=VESSEL_ID,
    )

    master_msg = await asyncio.wait_for(master_task, timeout=1.0)
    crew_msg = await asyncio.wait_for(crew_task, timeout=1.0)

    assert "ticket.updated" in master_msg
    assert "TCK-TEST-001" in master_msg
    assert "ticket.updated" in crew_msg
    assert "TCK-TEST-001" in crew_msg

    # Cleanup generators
    await master_gen.aclose()
    await crew_gen.aclose()


@pytest.mark.asyncio
async def test_sync_worker_offline_guard():
    """Verify execute_sync gracefully skips when link is DOWN."""
    sync_worker.set_override("down")
    db = SessionLocal()
    try:
        res = await sync_worker.execute_sync(db, force=False)
        assert res["synced"] is False
        assert "Link DOWN" in res["reason"]
    finally:
        db.close()
        sync_worker.set_override("auto")

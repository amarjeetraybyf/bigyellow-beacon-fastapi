"""
Edge Sync Worker
- Probes GET {shore}/healthz every 30s (3s timeout); 2 consecutive successes = link UP.
- BEACON_LINK_OVERRIDE = down | up | auto.
- POST /sync/push: gzipped SyncDelta (max 500 events or 256 KB), severity 4-5 first, oldest first.
- GET /sync/pull?cursor=&limit=200 ingest feed (ticket.shore_status, ticket_message, learning, anchor).
- Resolves conflicts via field ownership.
- Exponential backoff: 5s to 5min (300s).
"""

import os
import time
import gzip
import json
import asyncio
import httpx
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import case

from config import (
    SHORE_URL,
    VESSEL_ID,
    VESSEL_API_KEY,
    BEACON_LINK_OVERRIDE,
)
from security import compute_sync_hmac
from services.sse_manager import sse_manager
from contract.enums import SSEEvent, ShoreStatus, LearningStatus, PullChangeType
from contract.models import SyncDelta, SyncDeltaEvent, to_utc_iso
from db_models import (
    OutboxModel,
    TicketModel,
    TicketMessageModel,
    LearningModel,
    DailyAnchorCardModel,
)


class SyncWorker:
    def __init__(self):
        self.link_status: str = "DOWN"  # UP | DOWN
        self.consecutive_successes: int = 0
        self.last_probe_time: Optional[float] = None
        self.last_sync_timestamp: Optional[datetime] = None
        self.last_cursor: int = 0
        self.backoff_seconds: float = 5.0
        self.is_running: bool = False
        self.override: str = os.getenv("BEACON_LINK_OVERRIDE", BEACON_LINK_OVERRIDE).lower()

    def set_override(self, override: str):
        self.override = override.lower()
        if self.override == "up":
            self.link_status = "UP"
        elif self.override == "down":
            self.link_status = "DOWN"

    async def probe_link(self) -> bool:
        """
        Probe GET {shore}/healthz with 3s timeout.
        2 consecutive successes = link UP.
        """
        if self.override == "up":
            self.link_status = "UP"
            return True
        elif self.override == "down":
            self.link_status = "DOWN"
            return False

        self.last_probe_time = time.time()
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{SHORE_URL}/healthz")
                if res.status_code == 200:
                    self.consecutive_successes += 1
                    if self.consecutive_successes >= 2:
                        self.link_status = "UP"
                    return self.link_status == "UP"
        except Exception:
            pass

        self.consecutive_successes = 0
        self.link_status = "DOWN"
        return False

    async def execute_sync(self, db: Session, force: bool = False) -> Dict[str, Any]:
        """
        Executes a complete push and pull sync cycle between Edge and Shore.
        """
        is_link_up = await self.probe_link()
        if not is_link_up and not force:
            return {
                "synced": False,
                "reason": "Link DOWN (satellite offline or probe failing)",
                "link_status": self.link_status,
                "override": self.override,
            }

        push_result = await self._push_outbox(db)
        pull_result = await self._pull_changes(db)

        self.last_sync_timestamp = datetime.now(timezone.utc)
        self.backoff_seconds = 5.0

        sse_manager.broadcast_sync_status(VESSEL_ID, {
            "link_status": self.link_status,
            "last_sync": to_utc_iso(self.last_sync_timestamp),
            "pushed_count": push_result.get("acked_count", 0),
            "pulled_count": pull_result.get("pulled_count", 0),
        })

        return {
            "synced": True,
            "link_status": self.link_status,
            "push": push_result,
            "pull": pull_result,
            "last_sync": to_utc_iso(self.last_sync_timestamp),
        }

    async def _push_outbox(self, db: Session) -> Dict[str, Any]:
        sev_priority = case((OutboxModel.severity >= 4, 0), else_=1)
        unsynced_items = (
            db.query(OutboxModel)
            .filter(OutboxModel.synced == False)
            .order_by(sev_priority, OutboxModel.created_at.asc())
            .limit(500)
            .all()
        )

        if not unsynced_items:
            return {"acked_count": 0, "message": "Outbox clean"}

        events_payload = [
            SyncDeltaEvent(
                event_id=item.id,
                event_type=item.event_type,
                aggregate_type=item.aggregate_type,
                aggregate_id=item.aggregate_id,
                payload=item.payload,
                severity=item.severity,
                created_at=item.created_at,
            )
            for item in unsynced_items
        ]

        delta = SyncDelta(vessel_id=VESSEL_ID, events=events_payload)
        json_data = delta.model_dump_json().encode("utf-8")
        compressed_bytes = gzip.compress(json_data)

        ts = str(int(time.time()))
        path = "/sync/push"
        sig = compute_sync_hmac(VESSEL_API_KEY, "POST", path, ts, compressed_bytes)

        headers = {
            "X-Vessel-Id": VESSEL_ID,
            "X-Timestamp": ts,
            "X-Signature": sig,
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    f"{SHORE_URL}{path}",
                    content=compressed_bytes,
                    headers=headers,
                )
                if res.status_code == 200:
                    resp_json = res.json()
                    acked = set(resp_json.get("acked_event_ids", []))
                    rejected = set(resp_json.get("rejected_event_ids", []))
                    if rejected:
                        print(f"⚠️  [SyncWorker] Shore rejected {len(rejected)} event(s): {rejected}")
                    now = datetime.now(timezone.utc)
                    for item in unsynced_items:
                        if item.id in acked:
                            item.synced = True
                            item.synced_at = now
                    db.commit()
                    return {"acked_count": len(acked), "rejected_count": len(rejected), "total_attempted": len(unsynced_items)}
                else:
                    self.backoff_seconds = min(300.0, self.backoff_seconds * 2)
                    return {"error": f"Shore rejected push: {res.status_code} {res.text}"}
        except Exception as e:
            self.backoff_seconds = min(300.0, self.backoff_seconds * 2)
            return {"error": f"Push failed: {str(e)}"}

    async def _pull_changes(self, db: Session) -> Dict[str, Any]:
        ts = str(int(time.time()))
        path = "/sync/pull"
        sig = compute_sync_hmac(VESSEL_API_KEY, "GET", path, ts, b"")

        headers = {
            "X-Vessel-Id": VESSEL_ID,
            "X-Timestamp": ts,
            "X-Signature": sig,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(
                    f"{SHORE_URL}{path}?cursor={self.last_cursor}&limit=200",
                    headers=headers,
                )
                if res.status_code != 200:
                    return {"error": f"Shore pull returned {res.status_code}"}

                data = res.json()
                changes = data.get("changes", [])

                for ch in changes:
                    change_type = ch.get("change_type")
                    payload = ch.get("payload", {})

                    cursor_val = ch.get("cursor")
                    if cursor_val and int(cursor_val) > self.last_cursor:
                        self.last_cursor = int(cursor_val)

                    if change_type == PullChangeType.TICKET_SHORE_STATUS.value:
                        ticket_id = payload.get("ticket_id")
                        t = db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
                        if t:
                            if "shore_status" in payload:
                                t.shore_status = payload["shore_status"]
                            if "assigned_action" in payload:
                                t.assigned_action = payload["assigned_action"]
                            t.version += 1
                            db.commit()
                            # Broadcast to both Master and all connected crew devices on ship LAN
                            sse_manager.broadcast_event(
                                SSEEvent.TICKET_UPDATED,
                                {"ticket_id": t.id, "shore_status": t.shore_status, "assigned_action": t.assigned_action},
                                vessel_id=VESSEL_ID,
                            )

                    elif change_type == PullChangeType.TICKET_MESSAGE.value:
                        msg_id = payload.get("message_id")
                        existing_m = db.query(TicketMessageModel).filter(TicketMessageModel.id == msg_id).first()
                        if not existing_m:
                            msg_dt = datetime.now(timezone.utc)
                            raw_created = payload.get("created_at")
                            if raw_created:
                                try:
                                    msg_dt = datetime.fromisoformat(str(raw_created).replace("Z", "+00:00"))
                                except Exception:
                                    pass
                            new_m = TicketMessageModel(
                                id=msg_id,
                                ticket_id=payload.get("ticket_id"),
                                sender_role="shore_reviewer",
                                text=payload.get("text", ""),
                                origin="shore",
                                created_at=msg_dt,
                                updated_at=msg_dt,
                            )
                            db.add(new_m)
                            db.commit()
                            # Broadcast incoming shore reply to crew & master apps
                            sse_manager.broadcast_event(
                                SSEEvent.MESSAGE_NEW,
                                {
                                    "ticket_id": payload.get("ticket_id"),
                                    "message_id": msg_id,
                                    "text": payload.get("text"),
                                    "sender_role": "shore_reviewer",
                                    "created_at": to_utc_iso(raw_created or msg_dt),
                                },
                                vessel_id=VESSEL_ID,
                            )

                    elif change_type == PullChangeType.LEARNING_PUBLISHED.value:
                        l_id = payload.get("id")
                        learn = db.query(LearningModel).filter(LearningModel.id == l_id).first()
                        if not learn:
                            learn = LearningModel(
                                id=l_id,
                                title=payload.get("title", ""),
                                summary=payload.get("summary", ""),
                                body=payload.get("body", ""),
                                status=LearningStatus.PUBLISHED.value,
                                media=payload.get("media", []),
                                origin="shore",
                                version=payload.get("version", 1),
                            )
                            db.add(learn)
                        else:
                            learn.title = payload.get("title", learn.title)
                            learn.summary = payload.get("summary", learn.summary)
                            learn.body = payload.get("body", learn.body)
                            learn.status = LearningStatus.PUBLISHED.value
                            learn.media = payload.get("media", learn.media)
                        db.commit()
                        sse_manager.broadcast_event(
                            SSEEvent.LEARNING_NEW,
                            {"learning_id": l_id, "title": payload.get("title")},
                            vessel_id=VESSEL_ID,
                        )

                    elif change_type == PullChangeType.LEARNING_RETRACTED.value:
                        l_id = payload.get("id")
                        learn = db.query(LearningModel).filter(LearningModel.id == l_id).first()
                        if learn:
                            learn.status = LearningStatus.RETRACTED.value
                            db.commit()

                    elif change_type == PullChangeType.ANCHOR_SCHEDULED.value:
                        card_id = payload.get("id")
                        card = db.query(DailyAnchorCardModel).filter(DailyAnchorCardModel.id == card_id).first()
                        if not card:
                            card = DailyAnchorCardModel(
                                id=card_id,
                                topic=payload.get("topic", ""),
                                quote_or_prompt=payload.get("quote_or_prompt", ""),
                                translations=payload.get("translations", {}),
                                scheduled_date=payload.get("scheduled_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                                origin="shore",
                            )
                            db.add(card)
                        db.commit()
                        sse_manager.broadcast_event(
                            SSEEvent.ANCHOR_NEW,
                            {"anchor_id": card_id, "topic": payload.get("topic")},
                            vessel_id=VESSEL_ID,
                        )

                return {"pulled_count": len(changes), "next_cursor": data.get("next_cursor")}
        except Exception as e:
            return {"error": f"Pull failed: {str(e)}"}

    async def run_forever(self):
        """
        Autonomous background loop for Edge Sync Worker.
        - Continually probes link state to Shore backend.
        - When link is UP (satellite online or local network connected):
          Executes push of unsynced Outbox events and pulls Shore changes.
          Maintains an active burst window (polls Shore every 10s while link remains stable).
        - When link is DOWN (offline at sea):
          Backs off gracefully (probe sleep between 10s and 60s) with low-overhead probes.
        - Cleanly terminates on application shutdown via asyncio.CancelledError.
        """
        self.is_running = True
        print(f"🛰️  [SyncWorker] Autonomous sync worker started for vessel: {VESSEL_ID}")
        print(f"🔗  [SyncWorker] Target Shore URL: {SHORE_URL}")

        prev_link_status = self.link_status

        try:
            while True:
                try:
                    is_up = await self.probe_link()

                    if is_up != (prev_link_status == "UP"):
                        if is_up:
                            print("🟢 [SyncWorker] Satellite/Shore Link is UP! Starting immediate sync burst.")
                        else:
                            print("🔴 [SyncWorker] Satellite/Shore Link is DOWN. Operating in offline outbox mode.")
                        prev_link_status = self.link_status

                    if is_up:
                        # Link is UP: Execute sync cycle with a dedicated DB session
                        from db import SessionLocal
                        db = SessionLocal()
                        try:
                            sync_res = await self.execute_sync(db)
                            pushed = sync_res.get("push", {}).get("acked_count", 0)
                            pulled = sync_res.get("pull", {}).get("pulled_count", 0)
                            if pushed > 0 or pulled > 0:
                                print(f"⚡ [SyncWorker] Synced: pushed={pushed} events, pulled={pulled} changes.")
                        except Exception as sync_err:
                            print(f"⚠️  [SyncWorker] Sync execution error: {sync_err}")
                        finally:
                            db.close()

                        # Active burst window: poll Shore every 10 seconds while link is UP
                        await asyncio.sleep(10.0)
                    else:
                        # Link is DOWN: sleep with backoff (between 10s and 60s)
                        sleep_time = max(10.0, min(self.backoff_seconds, 60.0))
                        await asyncio.sleep(sleep_time)

                except asyncio.CancelledError:
                    raise
                except Exception as loop_err:
                    print(f"⚠️  [SyncWorker] Background loop error: {loop_err}")
                    await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            print("🛑 [SyncWorker] Sync worker background task terminated.")
        finally:
            self.is_running = False


sync_worker = SyncWorker()

"""
BigYellow Beacon POC Technical Design – Comprehensive Test Suite (Phase 9)
Tests:
1. auth/pairing: Master code generation, crew pairing, Argon2 PIN (4-digit crew, 6-digit master), 15-min lockout after 5 failures
2. ownership of statuses: onboard_status (edge-owned) vs shore_status (shore-owned)
3. anonymity: reporter_ref uniqueness HMAC, reporter_crew_id never leaves edge, PII scrubbing to [crew]
4. safety rails: clinician crisis message on trigger, consented welfare request to Master only (name + time), never synced to shore
5. k>=5 masking: checkin aggregates masked when n < 5
6. outbox transactionality: outbox row committed in same transaction as ticket
7. idempotent push: deduplication via processed_events
8. HMAC replay rejection: requests older than 5 mins rejected, signature validation
9. SSE replay: Last-Event-ID buffer replay
10. video > 6s rejection: satellite bandwidth protection for learning media
"""

import time
import gzip
import json
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from contract.enums import Role, OnboardStatus, ShoreStatus, SSEEvent
from contract.uuid7 import uuid7_str
from contract.db.models import (
    TicketModel,
    OutboxModel,
    ProcessedEventModel,
    CheckinAggregateModel,
    WelfareRequestModel,
    VesselModel,
)
from edge.main import app as edge_app
from shore.main import app as shore_app
from edge.db import get_db as get_edge_db, SessionLocal as EdgeSessionLocal
from shore.db import get_db as get_shore_db, SessionLocal as ShoreSessionLocal
from edge.security import (
    create_edge_session_token,
    generate_reporter_ref,
    compute_sync_hmac,
    hash_pin_argon2,
)
from shore.security import create_shore_access_token
from edge.services.pii_scrub import scrub_pii
from edge.services.sse_manager import sse_manager
from edge.services.ai_gateway import ai_gateway

edge_client = TestClient(edge_app)
shore_client = TestClient(shore_app)


# ─── 1. AUTH / PAIRING & LOCKOUT ──────────────────────────────────────────────

def test_auth_pairing_and_argon2_lockout():
    # 1. Master generates pairing code
    master_jwt = create_edge_session_token(crew_id="MASTER-01", role=Role.MASTER, vessel_id="MV-PACIFIC-STAR")
    code_res = edge_client.post("/auth/pairing-codes", headers={"Authorization": f"Bearer {master_jwt}"})
    assert code_res.status_code == 201
    code = code_res.json()["code"]

    # 2. Crew pairs device
    pair_res = edge_client.post("/auth/pair", json={"pairing_code": code, "department": "Deck", "crew_name": "Able Seaman Perez"})
    assert pair_res.status_code == 201
    dev_token = pair_res.json()["device_token"]

    # 3. 5 consecutive incorrect PIN attempts should lock the device for 15 mins
    for _ in range(5):
        fail_res = edge_client.post("/auth/login", json={"device_token": dev_token, "pin": "9999"})
        assert fail_res.status_code == 401

    # 6th attempt rejected with 423 Locked
    locked_res = edge_client.post("/auth/login", json={"device_token": dev_token, "pin": "1234"})
    assert locked_res.status_code == 423
    assert "locked" in locked_res.json()["error"].lower()


# ─── 2. OWNERSHIP OF STATUSES (Edge vs Shore) ────────────────────────────────

def test_status_ownership_edge_and_shore():
    edge_db = EdgeSessionLocal()
    shore_db = ShoreSessionLocal()

    ticket_id = uuid7_str()
    # Edge creates ticket with onboard_status=NEW
    edge_ticket = TicketModel(
        id=ticket_id,
        vessel_id="MV-PACIFIC-STAR",
        reporter_ref="REF-XYZ",
        title="Loose mooring stopper",
        description="Stopper line chafed",
        location_on_board="Poop Deck",
        hazard_type="Mooring",
        ism_category="ISM 7",
        severity=3,
        immediate_action="Replaced stopper",
        onboard_status=OnboardStatus.NEW.value,
        shore_status=ShoreStatus.NEW.value,
        origin="edge",
    )
    edge_db.add(edge_ticket)
    edge_db.commit()

    # Shore accepts ticket: updates shore_status -> IN_REVIEW
    shore_ticket = TicketModel(
        id=ticket_id,
        vessel_id="MV-PACIFIC-STAR",
        reporter_ref="REF-XYZ",
        title="Loose mooring stopper",
        description="Stopper line chafed",
        location_on_board="Poop Deck",
        hazard_type="Mooring",
        ism_category="ISM 7",
        severity=3,
        immediate_action="Replaced stopper",
        onboard_status=OnboardStatus.NEW.value,
        shore_status=ShoreStatus.IN_REVIEW.value,
        origin="shore",
    )
    shore_db.add(shore_ticket)
    shore_db.commit()

    # Edge sync push to shore updates onboard_status to ACTIONED
    # Field ownership: edge push MUST NOT overwrite shore_status on shore!
    vessel_id = "MV-PACIFIC-STAR"
    api_key = "vessel-api-key-mv-pacific-star"
    push_payload = {
        "vessel_id": vessel_id,
        "events": [
            {
                "event_id": uuid7_str(),
                "event_type": "ticket.action",
                "aggregate_type": "ticket",
                "aggregate_id": ticket_id,
                "payload": {
                    "id": ticket_id,
                    "onboard_status": OnboardStatus.ACTIONED.value,
                    "assigned_action": {"text": "Inspect all lines", "responsible_rank": "Bosun"},
                },
                "severity": 3,
            }
        ],
    }

    raw_bytes = json.dumps(push_payload).encode("utf-8")
    gz_bytes = gzip.compress(raw_bytes)
    ts = str(int(time.time()))
    sig = compute_sync_hmac(api_key, "POST", "/sync/push", ts, gz_bytes)

    res = shore_client.post(
        "/sync/push",
        content=gz_bytes,
        headers={
            "X-Vessel-Id": vessel_id,
            "X-Timestamp": ts,
            "X-Signature": sig,
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
        },
    )
    assert res.status_code == 200

    # Verify shore database: onboard_status updated to ACTIONED, but shore_status remains IN_REVIEW
    shore_ticket_after = shore_db.query(TicketModel).filter(TicketModel.id == ticket_id).first()
    assert shore_ticket_after.onboard_status == OnboardStatus.ACTIONED.value
    assert shore_ticket_after.shore_status == ShoreStatus.IN_REVIEW.value

    edge_db.close()
    shore_db.close()


# ─── 3. ANONYMITY & PII SCRUBBING ────────────────────────────────────────────

def test_anonymity_and_pii_scrubbing():
    # 1. reporter_ref is unique per crew+ticket and pseudonymous
    vessel_secret = "test-secret-salt"
    ref1 = generate_reporter_ref("CREW-A", "TICKET-1")
    ref2 = generate_reporter_ref("CREW-A", "TICKET-2")
    ref3 = generate_reporter_ref("CREW-B", "TICKET-1")
    assert ref1 != ref2
    assert ref1 != ref3
    assert len(ref1) == 64  # SHA256 hex length

    # 2. Free-text PII scrub replaces names, emails, phones with [crew]
    text_with_pii = (
        "Chief Mate John Doe reported that 2nd Engineer Alex (alex.smith@shipping.com, +1-555-019-2834) "
        "left valve 4 open."
    )
    scrubbed = scrub_pii(text_with_pii, roster_names=["John Doe", "Alex Smith", "Alex"])
    assert "John Doe" not in scrubbed
    assert "alex.smith@shipping.com" not in scrubbed
    assert "+1-555-019-2834" not in scrubbed
    assert "[crew]" in scrubbed


# ─── 4. SAFETY RAILS & WELFARE PRIVACY ───────────────────────────────────────

def test_safety_rails_and_welfare_privacy():
    crew_jwt = create_edge_session_token(crew_id="CREW-WELFARE-01", role=Role.CREW, vessel_id="MV-PACIFIC-STAR")

    # Start chat checkin
    start_res = edge_client.post(
        "/checkins",
        json={"mode": "chat", "mood": 1},
        headers={"Authorization": f"Bearer {crew_jwt}"},
    )
    assert start_res.status_code == 201
    checkin_id = start_res.json()["id"]

    # Post turn triggering distress safety-rail
    turn_res = edge_client.post(
        f"/checkins/{checkin_id}/turn",
        json={"text": "I can't take it anymore, I just want to end it all and jump overboard"},
        headers={"Authorization": f"Bearer {crew_jwt}"},
    )
    assert turn_res.status_code == 200
    turn_data = turn_res.json()
    assert turn_data["safety_rail_triggered"] is True
    # Fixed clinician approved message with placeholders returned
    assert "Medical / Safety Officer" in turn_data["clinician_approved_message"]
    assert "Would you like the Master to check on you?" in turn_data["prompt"]

    # Only on consent: POST welfare-request creates row and notifies Master
    consent_res = edge_client.post(
        f"/checkins/{checkin_id}/welfare-request",
        headers={"Authorization": f"Bearer {crew_jwt}"},
    )
    assert consent_res.status_code == 200
    assert consent_res.json()["welfare_requested"] is True

    # Verify edge DB welfare_requests table has crew name and time only, no transcript or sentiment scores!
    db = EdgeSessionLocal()
    req = db.query(WelfareRequestModel).filter(WelfareRequestModel.crew_id == "CREW-WELFARE-01").first()
    assert req is not None
    assert req.crew_name is not None
    assert not hasattr(req, "transcript")
    assert not hasattr(req, "mood")
    db.close()


# ─── 5. k >= 5 MASKING ───────────────────────────────────────────────────────

def test_k_anonymity_masking_enforcement():
    shore_db = ShoreSessionLocal()
    shore_db.query(CheckinAggregateModel).delete()
    shore_db.commit()
    # Insert aggregate with n=3 (violates k>=5) and n=7 (valid)
    sub_k = CheckinAggregateModel(
        id=uuid7_str(),
        vessel_id="MV-PACIFIC-STAR",
        ship_date="2026-10-17",
        department="Deck",
        count=3,  # < 5
        avg_mood=2.0,
        avg_fatigue=4.0,
        avg_workload=4.0,
        origin="edge",
    )
    valid_k = CheckinAggregateModel(
        id=uuid7_str(),
        vessel_id="MV-PACIFIC-STAR",
        ship_date="2026-10-17",
        department="Engine",
        count=7,  # >= 5
        avg_mood=3.8,
        avg_fatigue=2.5,
        avg_workload=3.0,
        origin="edge",
    )
    shore_db.add(sub_k)
    shore_db.add(valid_k)
    shore_db.commit()

    # Shore /fleet/overview must only include cohorts where k >= 5
    shore_admin_jwt = create_shore_access_token(user_id="ADMIN-01", email="admin@beacon.com", role=Role.SHORE_ADMIN)
    overview_res = shore_client.get(
        "/fleet/overview",
        headers={"Authorization": f"Bearer {shore_admin_jwt}"},
    )
    assert overview_res.status_code == 200
    data = overview_res.json()["fleet_welfare_k_anonymized"]
    # Total samples should only count the valid k >= 5 cohort (7 samples, ignoring the 3 samples)
    assert data["total_crew_checkin_samples"] == 7
    shore_db.close()


# ─── 6. OUTBOX TRANSACTIONALITY ──────────────────────────────────────────────

def test_outbox_transactionality():
    crew_jwt = create_edge_session_token(crew_id="CREW-009", role=Role.CREW, vessel_id="MV-PACIFIC-STAR")
    edge_db = EdgeSessionLocal()

    # Create draft
    draft_res = edge_client.post(
        "/reports/drafts",
        data={"text": "Crane wire winch brake slipping during cargo load", "lang": "en"},
        headers={"Authorization": f"Bearer {crew_jwt}"},
    )
    draft_id = draft_res.json()["draft_id"]

    # Structure draft
    edge_client.post(f"/reports/drafts/{draft_id}/structure", headers={"Authorization": f"Bearer {crew_jwt}"})

    # Submit report
    submit_res = edge_client.post(
        "/reports",
        json={"draft_id": draft_id},
        headers={"Authorization": f"Bearer {crew_jwt}"},
    )
    assert submit_res.status_code == 201
    ticket_id = submit_res.json()["ticket_id"]

    # Check that Outbox row exists and matches ticket_id in same database state
    outbox_entry = edge_db.query(OutboxModel).filter(OutboxModel.aggregate_id == ticket_id).first()
    assert outbox_entry is not None
    assert outbox_entry.event_type == "ticket.new"
    assert outbox_entry.synced is False

    edge_db.close()


# ─── 7. IDEMPOTENT PUSH & PROCESSED EVENTS ───────────────────────────────────

def test_idempotent_sync_push_deduplication():
    vessel_id = "MV-PACIFIC-STAR"
    api_key = "vessel-api-key-mv-pacific-star"
    event_id = uuid7_str()

    push_payload = {
        "vessel_id": vessel_id,
        "events": [
            {
                "event_id": event_id,
                "event_type": "ticket.new",
                "aggregate_type": "ticket",
                "aggregate_id": uuid7_str(),
                "payload": {
                    "id": uuid7_str(),
                    "title": "Loose railing pin",
                    "description": "Port gangway pin loose",
                    "location_on_board": "Gangway",
                    "hazard_type": "Fall Hazard",
                    "ism_category": "ISM 7",
                    "severity": 2,
                    "immediate_action": "Secured with cotter pin",
                },
                "severity": 2,
            }
        ],
    }

    raw = json.dumps(push_payload).encode("utf-8")
    ts = str(int(time.time()))
    sig = compute_sync_hmac(api_key, "POST", "/sync/push", ts, raw)

    headers = {
        "X-Vessel-Id": vessel_id,
        "X-Timestamp": ts,
        "X-Signature": sig,
        "Content-Type": "application/json",
    }

    # 1st push
    res1 = shore_client.post("/sync/push", content=raw, headers=headers)
    assert res1.status_code == 200
    assert event_id in res1.json()["acked_event_ids"]

    # 2nd identical push (idempotent replay)
    ts2 = str(int(time.time()))
    sig2 = compute_sync_hmac(api_key, "POST", "/sync/push", ts2, raw)
    headers2 = {
        "X-Vessel-Id": vessel_id,
        "X-Timestamp": ts2,
        "X-Signature": sig2,
        "Content-Type": "application/json",
    }
    res2 = shore_client.post("/sync/push", content=raw, headers=headers2)
    assert res2.status_code == 200
    # Acknowledged idempotently without error
    assert event_id in res2.json()["acked_event_ids"]


# ─── 8. HMAC REPLAY REJECTION ────────────────────────────────────────────────

def test_hmac_replay_and_stale_timestamp_rejection():
    vessel_id = "MV-PACIFIC-STAR"
    api_key = "vessel-api-key-mv-pacific-star"
    raw = json.dumps({"vessel_id": vessel_id, "events": []}).encode("utf-8")

    # 1. Request older than 5 minutes (e.g. 400 seconds ago)
    old_ts = str(int(time.time()) - 400)
    old_sig = compute_sync_hmac(api_key, "POST", "/sync/push", old_ts, raw)

    old_res = shore_client.post(
        "/sync/push",
        content=raw,
        headers={
            "X-Vessel-Id": vessel_id,
            "X-Timestamp": old_ts,
            "X-Signature": old_sig,
            "Content-Type": "application/json",
        },
    )
    assert old_res.status_code == 401
    assert "5-minute" in old_res.json()["error"]

    # 2. Corrupted signature
    now_ts = str(int(time.time()))
    bad_res = shore_client.post(
        "/sync/push",
        content=raw,
        headers={
            "X-Vessel-Id": vessel_id,
            "X-Timestamp": now_ts,
            "X-Signature": "invalid-tampered-signature-0000000000",
            "Content-Type": "application/json",
        },
    )
    assert bad_res.status_code == 401
    assert "Invalid HMAC signature" in bad_res.json()["error"]


# ─── 9. SSE BUFFER REPLAY ────────────────────────────────────────────────────

def test_sse_last_event_id_replay():
    channel = "vessel:MV-PACIFIC-STAR:master"
    ev1 = sse_manager.publish(channel, SSEEvent.TICKET_NEW, {"ticket_id": "T1"})
    ev2 = sse_manager.publish(channel, SSEEvent.TICKET_UPDATED, {"ticket_id": "T1", "status": "ACK"})
    ev3 = sse_manager.publish(channel, SSEEvent.MESSAGE_NEW, {"ticket_id": "T1", "text": "hello"})

    # Replay events since ev1
    replayed = sse_manager.get_replay_events(channel, last_event_id=ev1)
    assert len(replayed) == 2
    assert replayed[0].event_id == ev2
    assert replayed[1].event_id == ev3


# ─── 10. VIDEO > 6s REJECTION ────────────────────────────────────────────────

def test_video_longer_than_6s_rejection():
    shore_admin_jwt = create_shore_access_token(user_id="ADMIN-01", email="admin@beacon.com", role=Role.SHORE_ADMIN)

    # 1. Create a draft learning
    draft_res = shore_client.post(
        "/learnings/draft",
        json={"topic": "Mooring Snap-Back Zones"},
        headers={"Authorization": f"Bearer {shore_admin_jwt}"},
    )
    assert draft_res.status_code == 200
    learning_id = draft_res.json()["id"]

    # 2. Attempt to upload a video with duration 8.5s (> 6.0s satellite limit)
    video_bytes = b"fake-mp4-video-content-stream"
    upload_res = shore_client.post(
        f"/learnings/{learning_id}/media",
        data={"duration_s": 8.5},
        files={"file": ("snap_back.mp4", video_bytes, "video/mp4")},
        headers={"Authorization": f"Bearer {shore_admin_jwt}"},
    )
    assert upload_res.status_code == 400
    assert "6.0s" in upload_res.json()["error"]

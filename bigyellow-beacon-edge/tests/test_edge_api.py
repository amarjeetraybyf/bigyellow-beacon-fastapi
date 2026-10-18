"""
Standalone Test Suite for BigYellow Beacon Edge
Verifies:
1. Health endpoint & Swagger documentation root
2. Master pairing code generation & Crew device pairing
3. Crew PIN login (Argon2), 12h session JWT, and lockout on 5 failed attempts
4. Near-miss / hazard report draft, structure & submission with PII scrubbing & Outbox queuing
5. Daily Anchor cards & Learnings offline retrieval
6. Confidential crew checkin (GDPR Art. 9 privacy boundary)
"""

import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Ensure edge repo root is in sys.path
EDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EDGE_DIR))

from main import app
from contract.enums import Role
from security import (
    create_edge_session_token,
    decode_edge_session_token,
    compute_sync_hmac,
)
from db import init_edge_db, SessionLocal
from db_models import OutboxModel, TicketModel, CheckinModel

client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    init_edge_db()


def test_edge_health_and_docs():
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert data["plane"] == "edge"

    res_docs = client.get("/docs")
    assert res_docs.status_code == 200


def test_edge_pairing_and_crew_login():
    # 1. Master generates pairing code
    master_token = create_edge_session_token(
        crew_id="MASTER-001",
        role=Role.MASTER,
        vessel_id="MV-PACIFIC-STAR",
    )
    code_res = client.post(
        "/auth/pairing-codes",
        headers={"Authorization": f"Bearer {master_token}"},
    )
    assert code_res.status_code == 201
    pairing_code = code_res.json()["code"]

    # 2. Crew pairs device
    pair_res = client.post(
        "/auth/pair",
        json={
            "pairing_code": pairing_code,
            "department": "Deck",
            "crew_name": "Seaman Reyes",
        },
    )
    assert pair_res.status_code == 201
    device_data = pair_res.json()
    device_token = device_data["device_token"]
    assert device_token.startswith("dev_")

    # 3. Crew logs in with default 4-digit PIN (1234)
    login_res = client.post(
        "/auth/login",
        json={"device_token": device_token, "pin": "1234"},
    )
    assert login_res.status_code == 200
    token_data = login_res.json()
    assert token_data["role"] == "crew"
    assert token_data["expires_in"] == 12 * 3600

    # 4. GET /me & PUT /me/preferences
    session_token = token_data["access_token"]
    me_res = client.get("/me", headers={"Authorization": f"Bearer {session_token}"})
    assert me_res.status_code == 200
    assert me_res.json()["department"] == "Deck"

    pref_res = client.put(
        "/me/preferences",
        headers={"Authorization": f"Bearer {session_token}"},
        json={"preferred_language": "tl", "notifications_enabled": False},
    )
    assert pref_res.status_code == 200
    assert pref_res.json()["preferences"]["preferred_language"] == "tl"


def test_pin_length_rules_and_lockout():
    # 1. Master generates fresh pairing code
    master_token = create_edge_session_token(
        crew_id="MASTER-001",
        role=Role.MASTER,
        vessel_id="MV-PACIFIC-STAR",
    )
    code_res = client.post(
        "/auth/pairing-codes",
        headers={"Authorization": f"Bearer {master_token}"},
    )
    assert code_res.status_code == 201
    pairing_code = code_res.json()["code"]

    # 2. Pair a test device
    pair_res = client.post(
        "/auth/pair",
        json={"pairing_code": pairing_code, "department": "Engine"},
    )
    assert pair_res.status_code == 201
    token = pair_res.json()["device_token"]

    # 3. Crew PIN must be 4 digits
    bad_len_res = client.post("/auth/login", json={"device_token": token, "pin": "123"})
    assert bad_len_res.status_code == 400
    assert "4 digits" in bad_len_res.json()["error"]

    # 4. Fail PIN 4 times
    for _ in range(4):
        fail_res = client.post("/auth/login", json={"device_token": token, "pin": "9999"})
        assert fail_res.status_code == 401

    # 5. 5th failure triggers 15-minute lock
    lock_res = client.post("/auth/login", json={"device_token": token, "pin": "9999"})
    assert lock_res.status_code == 423
    assert "locked for 15 minutes" in lock_res.json()["error"]


def test_report_submission_pii_scrub_and_outbox():
    crew_token = create_edge_session_token(
        crew_id="CREW-AB-003",
        role=Role.CREW,
        vessel_id="MV-PACIFIC-STAR",
    )

    # 1. Create report draft with PII (email, phone, seaman ID)
    draft_res = client.post(
        "/reports/drafts",
        headers={"Authorization": f"Bearer {crew_token}"},
        data={"text": "Observed loose electrical wire near cabin 402 reported by mate.reyes@shipping.com, +1-555-019-2834, ID892341."},
    )
    assert draft_res.status_code == 201
    draft_id = draft_res.json()["draft_id"]
    assert "draft_id" in draft_res.json()

    # 2. Structure draft via AI Gateway (with offline maritime fallback)
    struct_res = client.post(
        f"/reports/drafts/{draft_id}/structure",
        headers={"Authorization": f"Bearer {crew_token}"},
    )
    assert struct_res.status_code == 200
    struct = struct_res.json()
    assert "fields" in struct
    assert "transcript_original" in struct
    assert "confidence" in struct
    fields = struct["fields"]
    assert fields["hazard_type"] in {
        "slip_trip_fall", "mooring", "crane_lifting", "enclosed_space",
        "fire", "machinery", "electrical", "chemical", "other",
    }
    assert fields["ism_category"] in {
        "unsafe_act", "unsafe_condition", "equipment_failure", "procedure_gap",
    }
    assert 1 <= fields["severity"] <= 5

    # 3. Finalize ticket
    finalize_res = client.post(
        "/reports",
        headers={
            "Authorization": f"Bearer {crew_token}",
            "Idempotency-Key": "test-key-edge-report-01",
        },
        json={
            "draft_id": draft_id,
            "share_name_with_master": False,
            "fields": fields,
        },
    )
    assert finalize_res.status_code == 201
    data = finalize_res.json()
    assert "id" in data
    assert "ticket_no" in data
    assert data["title_en"]
    assert data["onboard_status"] == "SUBMITTED"
    assert data["shore_status"] in ("NEW", "QUEUED")
    assert isinstance(data["photos"], list)
    assert isinstance(data["actions"], list)
    assert data["hazard_type"] in {
        "slip_trip_fall", "mooring", "crane_lifting", "enclosed_space",
        "fire", "machinery", "electrical", "chemical", "other",
    }
    assert "description_original" in data
    assert "original_language" in data

    # Verify PII was scrubbed
    assert "mate.reyes@shipping.com" not in data["description"]
    assert "+1-555-019-2834" not in data["description"]
    assert "ID892341" not in data["description"]
    assert "[crew]" in data["description"]

    # Verify Outbox queuing for satellite sync
    db = SessionLocal()
    try:
        outbox_entry = (
            db.query(OutboxModel)
            .filter(OutboxModel.aggregate_id == data["id"])
            .first()
        )
        assert outbox_entry is not None
        assert outbox_entry.synced is False
    finally:
        db.close()


def test_report_api_v1_draft_and_answer_location():
    """FE hits /api/v1/reports/drafts then /reports/.../answer with full ReportFields."""
    crew_token = create_edge_session_token(
        crew_id="CREW-FE-001",
        role=Role.CREW,
        vessel_id="MV-PACIFIC-STAR",
    )
    draft_res = client.post(
        "/api/v1/reports/drafts",
        headers={"Authorization": f"Bearer {crew_token}", "Idempotency-Key": "fe-draft-1"},
        data={"text": "Slipped on hydraulic oil near crane 2. No injury."},
    )
    assert draft_res.status_code == 201
    draft_id = draft_res.json()["draft_id"]

    struct_res = client.post(
        f"/reports/drafts/{draft_id}/structure",
        headers={"Authorization": f"Bearer {crew_token}"},
    )
    assert struct_res.status_code == 200

    answer_res = client.post(
        f"/reports/drafts/{draft_id}/answer",
        headers={"Authorization": f"Bearer {crew_token}"},
        json={"answer": "Main deck, crane 2"},
    )
    assert answer_res.status_code == 200
    answered = answer_res.json()
    assert answered["location_on_board"] == "Main deck, crane 2"
    assert answered["hazard_type"] in {
        "slip_trip_fall", "mooring", "crane_lifting", "enclosed_space",
        "fire", "machinery", "electrical", "chemical", "other",
    }
    assert answered["ism_category"] in {
        "unsafe_act", "unsafe_condition", "equipment_failure", "procedure_gap",
    }


def test_confidential_checkin_privacy():
    import uuid
    test_crew_id = f"CREW-TEST-{uuid.uuid4().hex[:6]}"
    crew_token = create_edge_session_token(
        crew_id=test_crew_id,
        role=Role.CREW,
        vessel_id="MV-PACIFIC-STAR",
    )

    res = client.post(
        "/checkins",
        headers={
            "Authorization": f"Bearer {crew_token}",
            "Idempotency-Key": f"test-key-checkin-{test_crew_id}",
        },
        json={
            "mode": "quick",
            "mood": 4,
            "fatigue": 2,
            "workload": 3,
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["done"] is True
    assert data["transcript_stored"] is False

    # Verify checkin is stored locally with numeric scores only
    db = SessionLocal()
    try:
        chk = (
            db.query(CheckinModel)
            .filter(CheckinModel.crew_id == test_crew_id)
            .first()
        )
        assert chk is not None
        assert chk.mood == 4
    finally:
        db.close()


def test_learnings_and_daily_anchor():
    crew_token = create_edge_session_token(
        crew_id="CREW-AB-003",
        role=Role.CREW,
        vessel_id="MV-PACIFIC-STAR",
    )

    res_anchor = client.get(
        "/anchor/today",
        headers={"Authorization": f"Bearer {crew_token}"},
    )
    assert res_anchor.status_code == 200
    assert "topic" in res_anchor.json()

    res_learnings = client.get(
        "/learnings",
        headers={"Authorization": f"Bearer {crew_token}"},
    )
    assert res_learnings.status_code == 200
    assert isinstance(res_learnings.json(), list)

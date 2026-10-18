"""
Standalone Unit and Integration Tests for BigYellow Beacon Shore Server
"""

import sys
from pathlib import Path
import time
import hmac
import hashlib
import json
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

# Ensure current package is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from contract.enums import Role
from main import app
from db import init_shore_db, SessionLocal
from security import create_shore_access_token, hash_password
from db_models import VesselModel, TicketModel, ShoreUserModel

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    init_shore_db()
    with SessionLocal() as db:
        # Ensure test admin user exists
        if not db.query(ShoreUserModel).filter(ShoreUserModel.email == "admin@beacon.maritime").first():
            db.add(ShoreUserModel(
                email="admin@beacon.maritime",
                name="Marcus Lee",
                hashed_password=hash_password("ShoreAdmin2026!"),
                role=Role.SHORE_ADMIN.value,
            ))
        # Ensure test reviewer user exists
        if not db.query(ShoreUserModel).filter(ShoreUserModel.email == "reviewer@beacon.maritime").first():
            db.add(ShoreUserModel(
                email="reviewer@beacon.maritime",
                name="Anita Rao",
                hashed_password=hash_password("ShoreReviewer2026!"),
                role=Role.SHORE_REVIEWER.value,
            ))

        # Ensure test vessel exists
        if not db.query(VesselModel).filter(VesselModel.id == "MV-PACIFIC-STAR").first():
            vessel = VesselModel(
                id="MV-PACIFIC-STAR",
                name="MV Pacific Star",
                imo_number="9481234",
                api_key="vessel-api-key-mv-pacific-star",
                connection_status="ONLINE",
            )
            db.add(vessel)

        # Ensure a ticket exists for test queries
        if not db.query(TicketModel).filter(TicketModel.id == "TEST-TICK-01").first():
            ticket = TicketModel(
                id="TEST-TICK-01",
                vessel_id="MV-PACIFIC-STAR",
                reporter_ref="anon-hash-test",
                title="Hydraulic pipe seepage at winch",
                description="Minor pinhole seepage observed during mooring operation",
                location_on_board="Aft Mooring Station",
                hazard_type="Hydraulics",
                ism_category="maintenance",
                severity=3,
                immediate_action="Depressurized line and wrapped with temporary clamp",
                onboard_status="NEW",
                shore_status="NEW",
            )
            db.add(ticket)

        db.commit()


def get_auth_token(role: str = Role.SHORE_ADMIN.value) -> str:
    with SessionLocal() as db:
        user = db.query(ShoreUserModel).filter(ShoreUserModel.role == role).first()
        if not user:
            user = db.query(ShoreUserModel).first()
        return create_shore_access_token(user.id, user.email, user.role)


def test_shore_healthz():
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_shore_login():
    res = client.post(
        "/auth/login",
        json={"email": "reviewer@beacon.maritime", "password": "ShoreReviewer2026!"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["role"] in ("reviewer", "shore_reviewer")


def test_video_longer_than_6s_rejected():
    token = get_auth_token(Role.SHORE_ADMIN.value)

    # 1. Create learning draft
    draft_res = client.post(
        "/learnings/draft",
        json={"topic": "Snap-Back Hazard Training"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert draft_res.status_code == 200
    learning_id = draft_res.json()["id"]

    # 2. Upload video exceeding 6.0s (e.g. 7.5s) -> Must return 400
    files = {"file": ("demo.mp4", b"fake-video-bytes", "video/mp4")}
    data = {"duration_s": "7.5"}
    media_res = client.post(
        f"/learnings/{learning_id}/media",
        data=data,
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert media_res.status_code == 400
    assert "exceeds maximum allowed 6.0s" in media_res.json()["error"]


def test_sync_push_hmac_and_deduplication():
    vessel_id = "MV-PACIFIC-STAR"
    api_key = "vessel-api-key-mv-pacific-star"
    path = "/sync/push"
    now_ts = str(int(time.time()))

    payload = {
        "vessel_id": vessel_id,
        "events": [
            {
                "event_id": f"EVT-TEST-{int(time.time())}",
                "event_type": "ticket.new",
                "aggregate_type": "ticket",
                "aggregate_id": f"TCK-TEST-{int(time.time())}",
                "severity": 4,
                "payload": {
                    "title": "Mooring winch hydraulic pressure drop",
                    "hazard_type": "Hydraulics",
                    "ism_category": "ISM-10",
                    "location_on_board": "Aft Mooring Station",
                    "severity": 4,
                    "immediate_action": "Isolated line",
                    "onboard_status": "NEW",
                },
            }
        ],
    }

    body_bytes = json.dumps(payload).encode("utf-8")
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    signing_input = f"POST{path}{now_ts}{body_hash}".encode("utf-8")
    sig = hmac.new(api_key.encode("utf-8"), signing_input, hashlib.sha256).hexdigest()

    headers = {
        "X-Vessel-Id": vessel_id,
        "X-Timestamp": now_ts,
        "X-Signature": sig,
        "Content-Type": "application/json",
    }

    push_res = client.post(path, content=body_bytes, headers=headers)
    assert push_res.status_code == 200
    data = push_res.json()
    assert data["success"] is True
    assert len(data["acked_event_ids"]) == 1


def test_list_tickets_and_patch():
    token = get_auth_token(Role.SHORE_ADMIN.value)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Test GET /tickets with list filters
    res = client.get("/tickets?severity=3&severity=4", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert len(data["items"]) > 0

    # 2. Test PATCH /tickets/{id}
    first_ticket = data["items"][0]
    t_id = first_ticket["id"]
    patch_res = client.patch(
        f"/tickets/{t_id}",
        json={"severity": 2, "ism_category": "procedure_gap"},
        headers=headers,
    )
    assert patch_res.status_code == 200
    patched = patch_res.json()
    assert patched["severity"] == 2
    assert patched["ism_category"] == "procedure_gap"


def test_vessels_and_fleet_overview():
    token = get_auth_token(Role.SHORE_REVIEWER.value)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. GET /vessels
    res_v = client.get("/vessels", headers=headers)
    assert res_v.status_code == 200
    vessels = res_v.json()
    assert len(vessels) > 0
    assert "type" in vessels[0]
    assert "imo" in vessels[0]
    assert "flag" in vessels[0]
    assert "crew_count" in vessels[0]

    # 2. GET /fleet/overview
    res_fo = client.get("/fleet/overview", headers=headers)
    assert res_fo.status_code == 200
    overview = res_fo.json()
    assert "open_by_severity" in overview
    assert "per_vessel" in overview
    assert "trend" in overview
    assert "wellbeing" in overview

    # 3. GET /fleet/clusters
    res_c = client.get("/fleet/clusters", headers=headers)
    assert res_c.status_code == 200
    clusters = res_c.json()
    assert isinstance(clusters, list)
    if clusters:
        assert "hazard_type" in clusters[0]
        assert "ticket_ids" in clusters[0]


def test_learning_crud_with_dict_tags():
    token = get_auth_token(Role.SHORE_ADMIN.value)
    headers = {"Authorization": f"Bearer {token}"}

    # Create learning with dict tags matching client schema
    payload = {
        "title": "Crane Wire Rope Inspection Procedure",
        "context": "Routine inspection noticed wire fatigue",
        "situation": "Crane wire fatigue observed at sheave groove",
        "do_points": ["Inspect daily", "Lubricate wire"],
        "dont_points": ["Do not shock load"],
        "check_question": {"q": "Inspection frequency?", "options": ["Daily", "Yearly"], "answer_index": 0},
        "audience": {"mode": "all", "vessel_ids": []},
        "tags": {"tool": "crane", "area": "main_deck", "situation": "lifting"},
        "rank_filter": "ratings",
    }
    res = client.post("/learnings", json=payload, headers=headers)
    assert res.status_code == 201
    created = res.json()
    assert created["title"] == payload["title"]
    assert isinstance(created["tags"], dict)
    assert created["tags"]["tool"] == "crane"
    assert created["rank_filter"] == "ratings"


def test_delta_polling_and_anonymous_messaging():
    token = get_auth_token(Role.SHORE_REVIEWER.value)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Test 10s Delta Polling (GET /tickets?updated_since=...)
    past_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    res_past = client.get(f"/tickets?updated_since={past_ts}", headers=headers)
    assert res_past.status_code == 200
    past_items = res_past.json()["items"]
    assert len(past_items) > 0

    future_ts = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    res_future = client.get(f"/tickets?updated_since={future_ts}", headers=headers)
    assert res_future.status_code == 200
    assert len(res_future.json()["items"]) == 0

    # 2. Test Anonymous 3-way Crew Communication
    ticket_id = past_items[0]["id"]
    msg_text = "Confirm pressure test completed after line isolation."
    post_res = client.post(
        f"/tickets/{ticket_id}/messages",
        json={"body": msg_text},
        headers=headers,
    )
    assert post_res.status_code == 200
    sent = post_res.json()
    assert sent["sender"] == "shore"
    assert sent["body"] == msg_text
    # Verify no crew personal identifiers in message
    assert "reporter" not in sent["body"].lower()

    # Verify message thread
    list_res = client.get(f"/tickets/{ticket_id}/messages", headers=headers)
    assert list_res.status_code == 200
    thread = list_res.json()
    assert any(m["id"] == sent["id"] for m in thread)

    # 3. Test Edge Pull HMAC synchronization
    vessel_id = past_items[0]["vessel_id"]
    api_key = "vessel-api-key-mv-pacific-star"
    path = "/sync/pull"
    now_ts = str(int(time.time()))
    body_hash = hashlib.sha256(b"").hexdigest()
    signing_input = f"GET{path}{now_ts}{body_hash}".encode("utf-8")
    sig = hmac.new(api_key.encode("utf-8"), signing_input, hashlib.sha256).hexdigest()
    edge_headers = {
        "X-Vessel-Id": vessel_id,
        "X-Timestamp": now_ts,
        "X-Signature": sig,
    }

    pull_res = client.get(path, headers=edge_headers)
    assert pull_res.status_code == 200
    pull_data = pull_res.json()
    assert "changes" in pull_data
    # Confirm ticket_message change was queued for edge
    has_msg_change = any(c["change_type"] == "ticket_message" and c["aggregate_id"] == ticket_id for c in pull_data["changes"])
    assert has_msg_change is True


def test_ticket_copilot_analysis_contract():
    token = get_auth_token(Role.SHORE_REVIEWER.value)
    headers = {"Authorization": f"Bearer {token}"}

    # Get an existing ticket
    res = client.get("/tickets", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) > 0
    ticket_id = items[0]["id"]

    # Call analysis endpoint
    analysis_res = client.get(f"/tickets/{ticket_id}/analysis", headers=headers)
    assert analysis_res.status_code == 200
    data = analysis_res.json()

    # Verify all expected frontend Analysis contract fields
    assert "ticket_id" in data
    assert data["ticket_id"] == ticket_id
    assert "model" in data
    assert "created_at" in data
    assert isinstance(data["summary"], list)
    assert len(data["summary"]) > 0
    assert isinstance(data["contributing_factors"], list)
    assert len(data["contributing_factors"]) > 0

    assert "ism_check" in data
    assert "current" in data["ism_check"]
    assert "suggested" in data["ism_check"]
    assert "reason" in data["ism_check"]

    assert "severity_check" in data
    assert isinstance(data["severity_check"]["current"], int)
    assert isinstance(data["severity_check"]["suggested"], int)
    assert "reason" in data["severity_check"]

    assert isinstance(data["similar_tickets"], list)
    assert isinstance(data["corpus_matches"], list)
    assert isinstance(data["recommendations"], list)
    for rec in data["recommendations"]:
        assert rec["type"] in ["corrective", "preventive"]
        assert "text" in rec

    assert isinstance(data["suggest_learning"], bool)
    assert "analysis" in data




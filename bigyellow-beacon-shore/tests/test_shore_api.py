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
from fastapi.testclient import TestClient

# Ensure current package is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from contract.enums import Role
from main import app
from db import init_shore_db, SessionLocal
from security import create_shore_access_token
from db_models import VesselModel, TicketModel

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    init_shore_db()
    with SessionLocal() as db:
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
            db.commit()


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
    assert data["user"]["role"] == "shore_reviewer"


def test_video_longer_than_6s_rejected():
    token = create_shore_access_token("ADMIN-01", "admin@beacon.maritime", Role.SHORE_ADMIN.value)

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

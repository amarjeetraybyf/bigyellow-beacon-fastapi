"""
Extended API Tests covering 100% of routes in BigYellow Beacon FastAPI
"""

import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_crew_checkin_turn_and_finish_flow():
    # 1. Pair crew
    pair_res = client.post(
        "/api/v1/auth/pair",
        json={"pairingCode": "QR-DECK", "vesselId": "MV-PACIFIC-STAR", "department": "Deck"},
    )
    token = pair_res.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Start check-in
    start_res = client.post("/api/v1/crew/checkin/start", headers=headers, json={"lang": "en"})
    assert start_res.status_code == 201
    session_id = start_res.json()["sessionId"]

    # 3. Conversational turn
    turn_res = client.post(
        f"/api/v1/crew/checkin/{session_id}/turn",
        headers=headers,
        json={"text": "I feel around 7 today, slept okay", "lang": "en"},
    )
    assert turn_res.status_code == 200
    assert turn_res.json()["safetyFlag"] == "STANDARD"

    # 4. Finish check-in
    finish_res = client.post(f"/api/v1/crew/checkin/{session_id}/finish", headers=headers)
    assert finish_res.status_code == 200
    assert finish_res.json()["transcriptStored"] is False
    assert "moodScore" in finish_res.json()["scores"]


def test_crew_help_consent_required():
    pair_res = client.post(
        "/api/v1/auth/pair",
        json={"pairingCode": "QR-HELP", "vesselId": "MV-PACIFIC-STAR", "department": "Deck"},
    )
    token = pair_res.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Without consent: rejected
    res_no_consent = client.post(
        "/api/v1/crew/help",
        headers=headers,
        json={"message": "Feeling down", "consentGranted": False},
    )
    assert res_no_consent.status_code == 400

    # With consent: accepted
    res_consent = client.post(
        "/api/v1/crew/help",
        headers=headers,
        json={"message": "I would like to speak to someone ashore", "consentGranted": True},
    )
    assert res_consent.status_code == 201
    data = res_consent.json()
    assert data["escalationId"].startswith("ESC-")
    assert "SeafarerHelp" in data["seafarerHelpLine"]


def test_ship_officer_routes():
    master_headers = {"Authorization": "Bearer master-token-xyz789"}

    # Overview
    ov_res = client.get("/api/v1/ship/overview", headers=master_headers)
    assert ov_res.status_code == 200
    assert ov_res.json()["vesselId"] == "MV-PACIFIC-STAR"

    # Wellbeing summary
    wb_res = client.get("/api/v1/ship/wellbeing/summary", headers=master_headers)
    assert wb_res.status_code == 200
    assert wb_res.json()["kThreshold"] == 5

    # Regenerate brief
    brief_res = client.post("/api/v1/ship/brief/regenerate", headers=master_headers)
    assert brief_res.status_code == 200
    assert brief_res.json()["vesselId"] == "MV-PACIFIC-STAR"

    # Notices broadcast
    notice_res = client.post(
        "/api/v1/ship/notices",
        headers=master_headers,
        json={
            "title": "Mandatory Deck Washdown Directive",
            "body": "Wash down port side cargo crane plates with degreaser.",
            "targetLanguages": ["en", "tl"],
        },
    )
    assert notice_res.status_code == 201
    assert "en" in notice_res.json()["translations"]


def test_shore_fleet_and_corpus_routes():
    shore_headers = {"Authorization": "Bearer shore-safety-token-ghi012"}

    # Overview
    ov_res = client.get("/api/v1/fleet/overview", headers=shore_headers)
    assert ov_res.status_code == 200
    assert ov_res.json()["totalVessels"] >= 1

    # Corpus search
    search_res = client.get("/api/v1/corpus/search?q=crane", headers=shore_headers)
    assert search_res.status_code == 200
    assert search_res.json()["query"] == "crane"

    # Push fleet alert
    alert_res = client.post(
        "/api/v1/fleet/alerts",
        headers=shore_headers,
        json={
            "targetVesselIds": ["MV-PACIFIC-STAR"],
            "severity": "WARNING",
            "category": "Machinery Precursor Alert",
            "message": "Inspect crane hydraulic manifolds prior to port arrival.",
        },
    )
    assert alert_res.status_code == 201
    assert alert_res.json()["alertId"].startswith("ALERT-")


def test_admin_routes():
    admin_headers = {"Authorization": "Bearer admin-token-mno678"}

    # List vessels
    vessels_res = client.get("/api/v1/admin/vessels", headers=admin_headers)
    assert vessels_res.status_code == 200
    assert isinstance(vessels_res.json(), list)

    # List users
    users_res = client.get("/api/v1/admin/users", headers=admin_headers)
    assert users_res.status_code == 200
    assert any(u["username"] == "master" for u in users_res.json())

    # Audit log
    audit_res = client.get("/api/v1/admin/audit-log", headers=admin_headers)
    assert audit_res.status_code == 200
    assert isinstance(audit_res.json(), list)


def test_legacy_api_routes():
    # Legacy vessels
    v_res = client.get("/api/fleet/vessels")
    assert v_res.status_code == 200

    # Legacy corpus
    c_res = client.get("/api/accidents/corpus")
    assert c_res.status_code == 200

    # Legacy voice transcribe
    t_res = client.post("/api/gemini/voice-transcribe", json={"sampleId": "hindi_crane"})
    assert t_res.status_code == 200
    assert t_res.json()["language"] == "hi"

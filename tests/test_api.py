"""
API Integration Tests for BigYellow Beacon FastAPI
Matches 100% of src/server/api.test.ts from the original Express backend.
"""

import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_api_swagger_ui_and_openapi_spec_are_live():
    # Test OpenAPI JSON specification
    res = client.get("/api/v1/openapi.json")
    assert res.status_code == 200
    spec = res.json()
    assert spec["info"]["title"] == "BigYellow Beacon API"
    assert "/api/v1/auth/pair" in spec["paths"]
    assert "/api/v1/crew/near-miss" in spec["paths"]
    assert "/api/v1/ship/brief/today" in spec["paths"]
    assert "/api/v1/fleet/clusters" in spec["paths"]
    assert "/api/v1/escalations" in spec["paths"]

    # Test Swagger HTML UI
    ui_res = client.get("/docs")
    assert ui_res.status_code == 200
    assert "swagger-ui" in ui_res.text


def test_api_health_check_returns_system_status():
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert data["app"] == "BigYellow Beacon"
    assert "Gemma 3n" in data["aiEngine"]


def test_api_auth_pairing_creates_device_token_for_crew():
    res = client.post(
        "/api/v1/auth/pair",
        json={
            "pairingCode": "BEACON-QR-PACIFIC-STAR",
            "vesselId": "MV-PACIFIC-STAR",
            "department": "Deck",
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["token"].startswith("crew-device-")
    assert data["role"] == "crew"
    assert data["vesselId"] == "MV-PACIFIC-STAR"


def test_api_master_login_with_pin():
    res = client.post(
        "/api/v1/auth/login",
        json={
            "username": "master",
            "pin": "1234",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["role"] == "master"
    assert data["userId"] == "MASTER-001"
    assert data["displayName"] == "Captain Santos"


def test_api_crew_near_miss_draft_and_confirm_workflow():
    # 1. Pair crew
    pair_res = client.post(
        "/api/v1/auth/pair",
        json={
            "pairingCode": "QR-TEST",
            "vesselId": "MV-PACIFIC-STAR",
            "department": "Engine",
        },
    )
    assert pair_res.status_code == 201
    token = pair_res.json()["token"]

    # 2. Draft near-miss
    draft_res = client.post(
        "/api/v1/crew/near-miss",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "text": "Main generator fuel line weeping over hot exhaust manifold",
            "lang": "en",
        },
    )
    assert draft_res.status_code == 201
    draft_data = draft_res.json()
    assert draft_data["draftId"]
    assert draft_data["draft"]["severity"] in ["High", "Critical"]

    # 3. Confirm near-miss
    confirm_res = client.post(
        f"/api/v1/crew/near-miss/{draft_data['draftId']}/confirm",
        headers={"Authorization": f"Bearer {token}"},
        json={"draftPayload": draft_data["draft"]},
    )
    assert confirm_res.status_code == 201
    confirm_data = confirm_res.json()
    assert confirm_data["saved"] is True
    assert confirm_data["queuedForSync"] is True


def test_api_rbac_blocks_master_from_psychologist_escalations():
    res = client.get(
        "/api/v1/escalations",
        headers={"Authorization": "Bearer master-token-xyz789"},
    )
    assert res.status_code == 403
    data = res.json()
    assert data["error"] == "Forbidden"


def test_api_shore_psychologist_can_view_escalations():
    res = client.get(
        "/api/v1/escalations",
        headers={"Authorization": "Bearer shore-psych-token-jkl345"},
    )
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["consentGranted"] is True


def test_api_shore_safety_can_view_hazard_clusters():
    res = client.get(
        "/api/v1/fleet/clusters",
        headers={"Authorization": "Bearer shore-safety-token-ghi012"},
    )
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "Hydraulic" in data[0]["patternName"]


def test_api_delta_sync_status_and_burst_run():
    # Check status
    status_res = client.get(
        "/api/v1/sync/status",
        headers={"Authorization": "Bearer master-token-xyz789"},
    )
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["vesselId"] == "MV-PACIFIC-STAR"

    # Trigger burst sync
    burst_res = client.post(
        "/api/v1/sync/run",
        headers={"Authorization": "Bearer master-token-xyz789"},
        json={"vesselId": "MV-PACIFIC-STAR"},
    )
    assert burst_res.status_code == 200
    burst_data = burst_res.json()
    assert burst_data["success"] is True
    assert burst_data["vesselStatus"] == "VSAT_BURST_SYNCED"

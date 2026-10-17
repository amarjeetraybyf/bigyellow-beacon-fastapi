"""
Test Phase 2: Auth, Roles, Argon2 PINs, Lockouts, HMAC-SHA256, and Idempotency
"""

import time
import pytest
from fastapi.testclient import TestClient

from contract.enums import Role
from edge.main import app as edge_app
from shore.main import app as shore_app
from edge.security import (
    create_edge_session_token,
    decode_edge_session_token,
    compute_sync_hmac,
)

edge_client = TestClient(edge_app)
shore_client = TestClient(shore_app)


def test_roles_definition():
    valid_roles = {r.value for r in Role}
    assert "crew" in valid_roles
    assert "master" in valid_roles
    assert "shore_reviewer" in valid_roles
    assert "shore_admin" in valid_roles
    assert "edge_device" in valid_roles
    # Removed roles
    assert "shore_psych" not in valid_roles
    assert "dpa" not in valid_roles


def test_edge_master_pairing_and_crew_onboarding():
    # 1. Master generates pairing code
    master_token = create_edge_session_token(crew_id="MASTER-001", role=Role.MASTER, vessel_id="MV-PACIFIC-STAR")
    code_res = edge_client.post(
        "/auth/pairing-codes",
        headers={"Authorization": f"Bearer {master_token}"},
    )
    assert code_res.status_code == 201
    pairing_code = code_res.json()["code"]

    # 2. Crew pairs device
    pair_res = edge_client.post(
        "/auth/pair",
        json={"pairing_code": pairing_code, "department": "Deck", "crew_name": "Seaman Reyes"},
    )
    assert pair_res.status_code == 201
    device_data = pair_res.json()
    device_token = device_data["device_token"]
    assert device_token.startswith("dev_")

    # 3. Crew logs in with default 4-digit PIN (1234)
    login_res = edge_client.post(
        "/auth/login",
        json={"device_token": device_token, "pin": "1234"},
    )
    assert login_res.status_code == 200
    token_data = login_res.json()
    assert token_data["role"] == "crew"
    assert token_data["expires_in"] == 12 * 3600

    # 4. GET /me & PUT /me/preferences
    session_token = token_data["access_token"]
    me_res = edge_client.get("/me", headers={"Authorization": f"Bearer {session_token}"})
    assert me_res.status_code == 200
    assert me_res.json()["department"] == "Deck"

    pref_res = edge_client.put(
        "/me/preferences",
        headers={"Authorization": f"Bearer {session_token}"},
        json={"preferred_language": "tl", "notifications_enabled": False},
    )
    assert pref_res.status_code == 200
    assert pref_res.json()["preferences"]["preferred_language"] == "tl"


def test_pin_length_rules_and_lockout_after_5_failures():
    # 1. Pair a test device
    pair_res = edge_client.post(
        "/auth/pair",
        json={"pairing_code": "DEMO-PAIR-123", "department": "Engine"},
    )
    token = pair_res.json()["device_token"]

    # 2. Crew PIN must be 4 digits (reject 3 digits or 6 digits)
    bad_len_res = edge_client.post("/auth/login", json={"device_token": token, "pin": "123"})
    assert bad_len_res.status_code == 400
    assert "4 digits" in bad_len_res.json()["error"]

    # 3. Fail PIN 4 times
    for _ in range(4):
        fail_res = edge_client.post("/auth/login", json={"device_token": token, "pin": "9999"})
        assert fail_res.status_code == 401

    # 4. 5th failure triggers 15-minute lock
    lock_res = edge_client.post("/auth/login", json={"device_token": token, "pin": "9999"})
    assert lock_res.status_code == 423
    assert "locked for 15 minutes" in lock_res.json()["error"]

    # Subsequent attempt also locked
    retry_res = edge_client.post("/auth/login", json={"device_token": token, "pin": "1234"})
    assert retry_res.status_code == 423


def test_shore_login_and_refresh_tokens():
    res = shore_client.post(
        "/auth/login",
        json={"email": "reviewer@beacon.maritime", "password": "ShoreReviewer2026!"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["expires_in"] == 3600  # 1h access
    assert data["refresh_expires_in"] == 28800  # 8h refresh
    assert data["user"]["role"] == "shore_reviewer"

    refresh_res = shore_client.post(
        "/auth/refresh",
        json={"refresh_token": data["refresh_token"]},
    )
    assert refresh_res.status_code == 200
    assert refresh_res.json()["access_token"]


def test_idempotency_key_header():
    # Master pairing code with Idempotency-Key
    master_token = create_edge_session_token(crew_id="MASTER-001", role=Role.MASTER, vessel_id="MV-PACIFIC-STAR")
    headers = {
        "Authorization": f"Bearer {master_token}",
        "Idempotency-Key": "test-idempotent-key-001",
    }

    res1 = edge_client.post("/auth/pairing-codes", headers=headers)
    assert res1.status_code == 201
    code1 = res1.json()["code"]

    # Replay same request with same key
    res2 = edge_client.post("/auth/pairing-codes", headers=headers)
    assert res2.status_code == 201
    code2 = res2.json()["code"]
    assert code1 == code2
    assert res2.headers.get("x-cache-lookup") == "HIT-IDEMPOTENT"


def test_hmac_sha256_sync_signature_and_anti_replay():
    api_key = "vessel-api-key-mv-pacific-star"
    path = "/sync/push"
    method = "POST"
    body = b'{"vessel_id":"MV-PACIFIC-STAR","events":[]}'

    # Valid fresh timestamp
    valid_ts = str(int(time.time()))
    sig = compute_sync_hmac(api_key, method, path, valid_ts, body)

    # Older than 5 minutes timestamp (replay attack)
    stale_ts = str(int(time.time()) - 400)
    stale_sig = compute_sync_hmac(api_key, method, path, stale_ts, body)

    from shore.security import verify_sync_hmac
    assert verify_sync_hmac(api_key, method, path, valid_ts, body, sig) is True
    assert verify_sync_hmac(api_key, method, path, stale_ts, body, stale_sig) is False

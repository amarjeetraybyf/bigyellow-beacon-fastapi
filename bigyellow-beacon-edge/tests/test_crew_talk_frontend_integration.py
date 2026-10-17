"""
Comprehensive Integration test suite verifying all API contracts expected by beacon-crew-talk frontend
against bigyellow-beacon-edge.
"""

import asyncio
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

import sys
EDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EDGE_DIR))

from main import app
from db import init_edge_db

client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    init_edge_db()


def test_crew_talk_auth_flow():
    # 1. Login with crew and PIN 1234
    res = client.post("/auth/login", json={"username": "crew", "password": "1234"})
    assert res.status_code == 200
    data = res.json()
    assert "token" in data
    assert "user" in data
    user = data["user"]
    assert user["role"] == "crew"
    assert user["name"] == "Arjun Nair"
    crew_token = data["token"]

    # 2. GET /me with token
    res_me = client.get("/me", headers={"Authorization": f"Bearer {crew_token}"})
    assert res_me.status_code == 200
    assert res_me.json()["id"] == user["id"]

    # 3. PUT /me/preferences
    res_pref = client.put(
        "/me/preferences",
        json={"language": "hi", "checkin_reminder_at": "20:00"},
        headers={"Authorization": f"Bearer {crew_token}"},
    )
    assert res_pref.status_code == 200
    assert res_pref.json()["language"] == "hi"

    # 4. Login with master and PIN 1234
    res_master = client.post("/auth/login", json={"username": "master", "password": "1234"})
    assert res_master.status_code == 200
    master_data = res_master.json()
    assert master_data["user"]["role"] == "master"
    assert master_data["user"]["name"] == "Capt. R. Menon"


def test_crew_talk_checkins_flow():
    # Login as crew
    login_res = client.post("/auth/login", json={"username": "crew", "password": "1234"})
    token = login_res.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Delete history if any to start clean
    client.delete("/checkins/history", headers=headers)

    # Check today's status
    res_today = client.get("/checkins/today", headers=headers)
    assert res_today.status_code == 200
    assert "done" in res_today.json()

    # Start chat checkin
    start_res = client.post(
        "/checkins",
        json={"mode": "chat", "mood": 3},
        headers=headers,
    )
    assert start_res.status_code in (200, 201)
    start_data = start_res.json()
    assert "id" in start_data
    assert start_data["done"] is False
    checkin_id = start_data["id"]

    # Checkin turn 1
    turn1_res = client.post(
        f"/checkins/{checkin_id}/turn",
        json={"text": "Everything is running smoothly on watch."},
        headers=headers,
    )
    assert turn1_res.status_code == 200
    assert turn1_res.json()["done"] is False

    # Checkin voice transcription
    voice_res = client.post(
        f"/checkins/{checkin_id}/voice",
        files={"audio": ("turn.webm", b"\x1a\x45\xdf\xa3fake-audio", "audio/webm")},
        headers=headers,
    )
    assert voice_res.status_code == 200
    assert "text" in voice_res.json()
    assert len(voice_res.json()["text"]) > 0

    # Checkin turn 2
    turn2_res = client.post(
        f"/checkins/{checkin_id}/turn",
        json={"text": voice_res.json()["text"]},
        headers=headers,
    )
    assert turn2_res.status_code == 200
    turn_data = turn2_res.json()
    assert turn_data["done"] is True
    assert "scores" in turn_data

    # Welfare request
    welfare_res = client.post(
        f"/checkins/{checkin_id}/welfare-request",
        json={"consent": True},
        headers=headers,
    )
    assert welfare_res.status_code == 200
    assert welfare_res.json()["ok"] is True

    # Checkin history
    hist_res = client.get("/checkins/history?range=7d", headers=headers)
    assert hist_res.status_code == 200
    hist_data = hist_res.json()
    assert "entries" in hist_data
    assert "streak" in hist_data
    assert "insight" in hist_data


def test_crew_talk_reports_flow():
    # Login as crew
    login_res = client.post("/auth/login", json={"username": "crew", "password": "1234"})
    crew_token = login_res.json()["token"]
    headers = {"Authorization": f"Bearer {crew_token}"}

    # Create draft
    draft_res = client.post(
        "/reports/drafts",
        data={"text": "Oil leak observed near crane 2 on main deck."},
        headers=headers,
    )
    assert draft_res.status_code in (200, 201)
    draft_id = draft_res.json()["draft_id"]

    # Structure draft
    struct_res = client.post(f"/reports/drafts/{draft_id}/structure", headers=headers)
    assert struct_res.status_code == 200
    fields = struct_res.json()["fields"]
    assert "title_en" in fields
    assert "description_en" in fields

    # Answer clarifying question if needed
    ans_res = client.post(
        f"/reports/drafts/{draft_id}/answer",
        json={"answer": "Main deck, starboard side"},
        headers=headers,
    )
    assert ans_res.status_code == 200

    # Submit report
    submit_res = client.post(
        "/reports",
        json={
            "draft_id": draft_id,
            "fields": {
                "title_en": "Crane 2 Hydraulic Oil Leak",
                "description_en": "Oil leak observed near crane 2 on main deck.",
                "location_on_board": "Main deck, starboard side",
                "hazard_type": "slip_trip_fall",
                "ism_category": "unsafe_condition",
                "severity": 3,
                "immediate_action": "Placed drip tray and warning sign",
            },
            "share_name_with_master": True,
        },
        headers=headers,
    )
    assert submit_res.status_code in (200, 201)
    ticket = submit_res.json()
    ticket_id = ticket["id"]
    assert ticket["ticket_no"].startswith("BCN-")

    # Get my reports
    mine_res = client.get("/reports/mine", headers=headers)
    assert mine_res.status_code == 200
    assert any(t["id"] == ticket_id for t in mine_res.json())

    # Get single ticket
    single_res = client.get(f"/tickets/{ticket_id}", headers=headers)
    assert single_res.status_code == 200
    assert single_res.json()["id"] == ticket_id

    # Post message on ticket
    msg_res = client.post(
        f"/tickets/{ticket_id}/messages",
        json={"body": "Drip tray was replaced this morning."},
        headers=headers,
    )
    assert msg_res.status_code in (200, 201)
    assert msg_res.json()["body"] == "Drip tray was replaced this morning."

    # List messages
    msgs_res = client.get(f"/tickets/{ticket_id}/messages", headers=headers)
    assert msgs_res.status_code == 200
    assert len(msgs_res.json()) >= 1


def test_crew_talk_content_and_master():
    # Login as master
    master_login = client.post("/auth/login", json={"username": "master", "password": "1234"})
    master_token = master_login.json()["token"]
    master_headers = {"Authorization": f"Bearer {master_token}"}

    # Daily anchor
    anchor_res = client.get("/anchor/today", headers=master_headers)
    assert anchor_res.status_code == 200
    anchor_id = anchor_res.json()["id"]

    react_res = client.post(f"/anchor/{anchor_id}/react", json={"viewed": True, "helpful": True}, headers=master_headers)
    assert react_res.status_code == 200

    anchor_list_res = client.get("/anchor?days=14&saved=false", headers=master_headers)
    assert anchor_list_res.status_code == 200
    assert len(anchor_list_res.json()) > 0

    # Learnings
    learn_res = client.get("/learnings", headers=master_headers)
    assert learn_res.status_code == 200
    learnings = learn_res.json()
    assert len(learnings) > 0
    lid = learnings[0]["id"]

    ack_res = client.post(f"/learnings/{lid}/ack", json={"acked": True}, headers=master_headers)
    assert ack_res.status_code == 200

    # Inbox
    inbox_res = client.get("/inbox", headers=master_headers)
    assert inbox_res.status_code == 200
    inbox_items = inbox_res.json()
    if inbox_items:
        inbox_read_res = client.post(f"/inbox/{inbox_items[0]['id']}/read", headers=master_headers)
        assert inbox_read_res.status_code == 200

    # Sync
    sync_res = client.get("/sync/status", headers=master_headers)
    assert sync_res.status_code == 200
    assert "link" in sync_res.json()

    now_res = client.post("/sync/now", headers=master_headers)
    assert now_res.status_code == 200

    # Master tickets
    mt_res = client.get("/master/tickets", headers=master_headers)
    assert mt_res.status_code == 200
    tickets = mt_res.json()
    if tickets:
        tid = tickets[0]["id"]
        ack_t = client.post(f"/master/tickets/{tid}/ack", headers=master_headers)
        assert ack_t.status_code == 200

        action_t = client.post(
            f"/master/tickets/{tid}/actions",
            json={"text": "Inspect seals", "responsible_rank": "Bosun", "due_at": "2026-10-18T12:00:00Z", "resolve": False},
            headers=master_headers,
        )
        assert action_t.status_code == 200

    # Morning brief
    brief_res = client.get("/master/brief", headers=master_headers)
    assert brief_res.status_code == 200
    assert "headline" in brief_res.json()

    regen_res = client.post("/master/brief/regenerate", headers=master_headers)
    assert regen_res.status_code == 200
    assert "headline" in regen_res.json()

    # Master learning stats
    stats_res = client.get("/master/learnings/stats", headers=master_headers)
    assert stats_res.status_code == 200

    # Master welfare
    welfare_list = client.get("/master/welfare", headers=master_headers)
    assert welfare_list.status_code == 200
    w_items = welfare_list.json()
    if w_items:
        w_id = w_items[0]["id"]
        ack_w = client.post(f"/master/welfare/{w_id}/ack", json={"status": "acknowledged"}, headers=master_headers)
        assert ack_w.status_code == 200

    # Master devices
    dev_res = client.get("/master/devices", headers=master_headers)
    assert dev_res.status_code == 200
    dev_del_res = client.delete("/master/devices/fake-dev-id", headers=master_headers)
    assert dev_del_res.status_code == 200

    # Events SSE endpoint auth verification
    unauth_events = client.get("/events")
    assert unauth_events.status_code == 401

    from routes.events_routes import sse_events_stream
    res_stream = asyncio.run(sse_events_stream(last_event_id=None, user={"role": "master", "crew_id": "u-master", "vessel_id": "MV-PACIFIC-STAR"}))
    assert isinstance(res_stream, StreamingResponse)
    assert res_stream.media_type == "text/event-stream"


def test_master_creates_crew_and_crew_logins():
    # 1. Master logs in
    master_login = client.post("/auth/login", json={"username": "master", "password": "1234"})
    assert master_login.status_code == 200
    master_token = master_login.json()["token"]
    master_headers = {"Authorization": f"Bearer {master_token}"}

    # 2. Master creates new crew account
    create_res = client.post(
        "/master/crew",
        json={
            "name": "Suresh Pillai",
            "username": "suresh",
            "password": "sureshpassword",
            "rank": "Bosun",
            "language": "en",
        },
        headers=master_headers,
    )
    assert create_res.status_code in (200, 201)
    created_user = create_res.json()
    assert created_user["username"] == "suresh"
    assert created_user["name"] == "Suresh Pillai"
    created_id = created_user["id"]

    # 3. Master lists crew accounts
    crew_list_res = client.get("/master/crew", headers=master_headers)
    assert crew_list_res.status_code == 200
    all_crew = crew_list_res.json()
    assert any(c["username"] == "suresh" for c in all_crew)

    # 4. Suresh logs in using the credentials provided by Master
    login_res = client.post(
        "/auth/login",
        json={"username": "suresh", "password": "sureshpassword"},
    )
    assert login_res.status_code == 200
    login_data = login_res.json()
    assert "token" in login_data
    assert login_data["user"]["name"] == "Suresh Pillai"
    assert login_data["user"]["rank"] == "Bosun"
    assert login_data["user"]["role"] == "crew"
    suresh_token = login_data["token"]

    # 5. Suresh calls GET /me
    me_res = client.get("/me", headers={"Authorization": f"Bearer {suresh_token}"})
    assert me_res.status_code == 200
    assert me_res.json()["name"] == "Suresh Pillai"
    assert me_res.json()["rank"] == "Bosun"

    # 6. Master deletes the crew member
    del_res = client.delete(f"/master/crew/{created_id}", headers=master_headers)
    assert del_res.status_code == 200


def test_crew_talk_reports_audio_and_photos():
    """Verify that photo and audio uploads are persisted to disk and served via static URL."""
    # 1. Login as crew
    login_res = client.post("/auth/login", json={"username": "crew", "password": "1234"})
    assert login_res.status_code == 200
    crew_token = login_res.json()["token"]
    headers = {"Authorization": f"Bearer {crew_token}"}

    # 2. Upload draft with 2 photos and 1 voice memo (multipart)
    fake_photo_1 = ("photo-0.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\xff\xd9", "image/jpeg")
    fake_photo_2 = ("photo-1.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\xff\xd9", "image/jpeg")
    fake_audio = ("voice.webm", b"\x1a\x45\xdf\xa3fake-webm-audio-bytes-for-test", "audio/webm")

    files = [
        ("photos[]", fake_photo_1),
        ("photos[]", fake_photo_2),
        ("audio", fake_audio),
    ]

    draft_res = client.post(
        "/reports/drafts",
        data={"lang": "en"},
        files=files,
        headers=headers,
    )
    assert draft_res.status_code == 201
    draft_data = draft_res.json()
    assert draft_data["photos_count"] == 2
    assert draft_data["audio_attached"] is True
    assert len(draft_data["photos"]) == 2
    assert draft_data["audio_url"] is not None

    draft_id = draft_data["draft_id"]
    photo_url = draft_data["photos"][0]
    audio_url = draft_data["audio_url"]

    # 3. Verify static file serving via edge server
    # Strip base URL to get path for test client
    photo_path = photo_url.split("localhost:8000")[-1]
    audio_path = audio_url.split("localhost:8000")[-1]

    get_photo_res = client.get(photo_path)
    assert get_photo_res.status_code == 200
    assert len(get_photo_res.content) > 0

    get_audio_res = client.get(audio_path)
    assert get_audio_res.status_code == 200
    assert len(get_audio_res.content) > 0

    # 4. Structure draft (since text was omitted, audio should be automatically transcribed)
    struct_res = client.post(f"/reports/drafts/{draft_id}/structure", headers=headers)
    assert struct_res.status_code == 200
    struct_data = struct_res.json()
    assert len(struct_data["transcript_original"]) > 0
    assert "title_en" in struct_data["fields"]

    # 5. Finalize ticket
    submit_res = client.post(
        "/reports",
        json={
            "draft_id": draft_id,
            "fields": struct_data["fields"],
            "share_name_with_master": True,
        },
        headers=headers,
    )
    assert submit_res.status_code == 201
    ticket = submit_res.json()
    assert len(ticket["photos"]) == 2
    assert ticket["audio_url"] is not None
    assert "uploads" in ticket["audio_url"]
    assert ticket["reporter_name"] == "Arjun Nair"

    ticket_id = ticket["id"]

    # 6. Verify single ticket GET /tickets/{id} has photos and audio_url
    ticket_get_res = client.get(f"/tickets/{ticket_id}", headers=headers)
    assert ticket_get_res.status_code == 200
    t_data = ticket_get_res.json()
    assert len(t_data["photos"]) == 2
    assert t_data["audio_url"] == ticket["audio_url"]

    # 7. Verify Master GET /master/tickets also includes photos and audio_url
    master_login = client.post("/auth/login", json={"username": "master", "password": "1234"})
    master_token = master_login.json()["token"]
    master_headers = {"Authorization": f"Bearer {master_token}"}

    master_tickets_res = client.get("/master/tickets", headers=master_headers)
    assert master_tickets_res.status_code == 200
    m_tickets = master_tickets_res.json()
    matched_t = next((t for t in m_tickets if t["id"] == ticket_id), None)
    assert matched_t is not None
    assert len(matched_t["photos"]) == 2
    assert matched_t["audio_url"] == ticket["audio_url"]


def test_bulkhead_qr_pair_and_device_login():
    """
    Test userflow:
    1. POST /api/v1/auth/pair accepting crew_id and pairing_code (bulkhead QR)
    2. Returns device_token
    3. Login using device_token + PIN
    4. Access /me using device session token
    """
    # 1. Pair with bulkhead QR and existing crew ID (u-crew)
    pair_res = client.post(
        "/api/v1/auth/pair",
        json={
            "pairing_code": "BEACON-QR-MV-PACIFIC-STAR",
            "crew_id": "u-crew",
            "pin": "1234",
        },
    )
    assert pair_res.status_code == 201
    pair_data = pair_res.json()
    assert "device_token" in pair_data
    assert pair_data["device_token"].startswith("dev_")
    assert pair_data["crew_id"] == "u-crew"
    assert pair_data["crew_name"] == "Arjun Nair"
    device_token = pair_data["device_token"]

    # 2. Login with device_token + PIN 1234
    dev_login_res = client.post(
        "/auth/login",
        json={
            "device_token": device_token,
            "pin": "1234",
        },
    )
    assert dev_login_res.status_code == 200
    session_data = dev_login_res.json()
    assert "token" in session_data
    assert session_data["user"]["name"] == "Arjun Nair"

    # 3. Pair new crew member with custom crew_id and bulkhead QR
    new_crew_res = client.post(
        "/api/v1/auth/pair",
        json={
            "pairing_code": "BEACON-QR-PACIFIC-STAR",
            "crew_id": "CRW-ENG-402",
            "crew_name": "Elena Rostova",
            "department": "Engine",
            "pin": "5678",
        },
    )
    assert new_crew_res.status_code == 201
    new_pair_data = new_crew_res.json()
    assert new_pair_data["crew_id"] == "CRW-ENG-402"
    assert new_pair_data["crew_name"] == "Elena Rostova"
    assert new_pair_data["department"] == "Engine"
    elena_device_token = new_pair_data["device_token"]

    # 4. Login with Elena's device token
    elena_login = client.post(
        "/auth/login",
        json={
            "device_token": elena_device_token,
            "pin": "5678",
        },
    )
    assert elena_login.status_code == 200
    assert elena_login.json()["user"]["name"] == "Elena Rostova"

    # 5. Bulkhead QR with JSON payload
    json_qr_res = client.post(
        "/api/v1/auth/pair",
        json={
            "pairing_code": '{"vessel_id": "MV-PACIFIC-STAR", "location": "Engine Bulkhead C-4"}',
            "crew_id": "u-crew",
        },
    )
    assert json_qr_res.status_code == 201
    assert "device_token" in json_qr_res.json()


def test_api_v1_auth_login_spec():
    """
    Verifies userflow 2 requirements:
    1. POST /api/v1/auth/login
    2. Response includes 'token' field alias alongside 'access_token'
    3. User profile includes rank_band ('Officer' | 'Rating') and vessel
    4. Crew PIN is 4 digits, Master PIN is 6 digits
    5. Argon2 lockout after 5 failed attempts with 15-minute lock
    """
    # 1. Test POST /api/v1/auth/login for Crew (username + password)
    crew_res = client.post(
        "/api/v1/auth/login",
        json={"username": "crew", "password": "1234"},
    )
    assert crew_res.status_code == 200
    crew_data = crew_res.json()
    assert "token" in crew_data
    assert "access_token" in crew_data
    assert crew_data["token"] == crew_data["access_token"]
    assert "user" in crew_data
    user_c = crew_data["user"]
    assert user_c["rank_band"] in ("Officer", "Rating")
    assert user_c["rank_band"] == "Rating"
    assert "vessel" in user_c
    assert user_c["vessel"] == "MV-PACIFIC-STAR"

    # 2. Test POST /api/v1/auth/login for Master (username + password)
    master_res = client.post(
        "/api/v1/auth/login",
        json={"username": "master", "password": "1234"},
    )
    assert master_res.status_code == 200
    master_data = master_res.json()
    assert master_data["token"] == master_data["access_token"]
    user_m = master_data["user"]
    assert user_m["rank_band"] in ("Officer", "Rating")
    assert user_m["rank_band"] == "Officer"
    assert "vessel" in user_m
    assert user_m["vessel"] == "MV-PACIFIC-STAR"

    # 3. Test Master device pairing and 6-digit PIN rule
    master_pair = client.post(
        "/api/v1/auth/pair",
        json={
            "pairing_code": "BEACON-QR-MV-PACIFIC-STAR",
            "crew_id": "u-master",
            "department": "Bridge",
            "crew_name": "Capt. R. Menon",
            "pin": "123456",
        },
    )
    assert master_pair.status_code == 201
    m_dev_token = master_pair.json()["device_token"]

    # Master device requires exactly 6 digits
    bad_master_pin = client.post(
        "/api/v1/auth/login",
        json={"device_token": m_dev_token, "pin": "1234"},
    )
    assert bad_master_pin.status_code == 400
    assert "6 digits" in bad_master_pin.json()["error"]

    # Master login with correct 6-digit PIN
    good_master_pin = client.post(
        "/api/v1/auth/login",
        json={"device_token": m_dev_token, "pin": "123456"},
    )
    assert good_master_pin.status_code == 200
    m_profile = good_master_pin.json()["user"]
    assert m_profile["rank_band"] == "Officer"
    assert "vessel" in m_profile
    assert good_master_pin.json()["token"] is not None

    # 4. Test Crew device pairing and 4-digit PIN rule
    crew_pair = client.post(
        "/api/v1/auth/pair",
        json={
            "pairing_code": "BEACON-QR-MV-PACIFIC-STAR",
            "crew_id": "CRW-TEST-LOCKOUT",
            "department": "Deck",
            "pin": "8888",
        },
    )
    assert crew_pair.status_code == 201
    c_dev_token = crew_pair.json()["device_token"]

    # Crew device requires exactly 4 digits (fails on 6 digits)
    bad_len = client.post(
        "/api/v1/auth/login",
        json={"device_token": c_dev_token, "pin": "888888"},
    )
    assert bad_len.status_code == 400
    assert "4 digits" in bad_len.json()["error"]

    # 5. Lockout after 5 failed attempts
    for attempt in range(1, 5):
        fail_res = client.post(
            "/api/v1/auth/login",
            json={"device_token": c_dev_token, "pin": "0000"},
        )
        assert fail_res.status_code == 401
        assert f"Remaining attempts: {5 - attempt}" in fail_res.json()["error"]

    # 5th failed attempt locks for 15 minutes
    lock_res = client.post(
        "/api/v1/auth/login",
        json={"device_token": c_dev_token, "pin": "0000"},
    )
    assert lock_res.status_code == 423
    assert "locked for 15 minutes" in lock_res.json()["error"]

    # 6. Test default onboard hardware terminals (crew-tablet & master-console)
    # Crew tablet success (4 digits)
    ct_ok = client.post("/api/v1/auth/login", json={"device_token": "crew-tablet", "pin": "1234"})
    assert ct_ok.status_code == 200
    assert ct_ok.json()["user"]["name"] == "Arjun Nair"
    assert ct_ok.json()["user"]["rank_band"] == "Rating"

    # Crew tablet bad length (6 digits rejected)
    ct_bad = client.post("/api/v1/auth/login", json={"device_token": "crew-tablet", "pin": "123456"})
    assert ct_bad.status_code == 400
    assert "4 digits" in ct_bad.json()["error"]

    # Master console success (6 digits)
    mc_ok = client.post("/api/v1/auth/login", json={"device_token": "master-console", "pin": "123456"})
    assert mc_ok.status_code == 200
    assert mc_ok.json()["user"]["name"] == "Capt. R. Menon"
    assert mc_ok.json()["user"]["rank_band"] == "Officer"

    # Master console bad length (4 digits rejected)
    mc_bad = client.post("/api/v1/auth/login", json={"device_token": "master-console", "pin": "1234"})
    assert mc_bad.status_code == 400
    assert "6 digits" in mc_bad.json()["error"]

"""
Comprehensive Integration test suite verifying all API contracts expected by beacon-crew-talk frontend
against bigyellow-beacon-edge.
"""

import asyncio
import io
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
    assert user["name"] == "Amarjeet"
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
    assert master_data["user"]["name"] == "Capt. Kundan"


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

    # Morning brief — FE MorningBrief contract
    brief_res = client.get("/master/brief", headers=master_headers)
    assert brief_res.status_code == 200
    brief = brief_res.json()
    for key in (
        "generated_at",
        "headline",
        "rest_hours_risk",
        "mood_trend",
        "open_hazards",
        "recommended_action",
        "fleet_context",
    ):
        assert key in brief
    assert brief["rest_hours_risk"]["level"] in ("low", "medium", "high")
    assert "note" in brief["rest_hours_risk"]
    # Privacy: mood_trend is null unless n >= 5
    if brief["mood_trend"] is not None:
        assert brief["mood_trend"]["n"] >= 5
        assert isinstance(brief["mood_trend"]["points"], list)
    assert isinstance(brief["open_hazards"], list)
    for hz in brief["open_hazards"]:
        assert "ticket_no" in hz and hz["ticket_no"].startswith("BCN-")
        assert "title" in hz and "severity" in hz
    # Severity sorted desc when multiple hazards
    if len(brief["open_hazards"]) >= 2:
        sevs = [h["severity"] for h in brief["open_hazards"]]
        assert sevs == sorted(sevs, reverse=True)

    regen_res = client.post("/master/brief/regenerate", headers=master_headers)
    assert regen_res.status_code == 200
    regen = regen_res.json()
    assert "generated_at" in regen and "headline" in regen

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
    from db import get_db
    from db_models import CrewAccountModel
    db = next(get_db())
    db.query(CrewAccountModel).filter(CrewAccountModel.username == "suresh").delete()
    db.commit()

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
    assert ticket["reporter_name"] == "Amarjeet"

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
    assert pair_data["crew_name"] == "Amarjeet"
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
    assert session_data["user"]["name"] == "Amarjeet"

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
            "crew_name": "Capt. Kundan",
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
    assert ct_ok.json()["user"]["name"] == "Amarjeet"
    assert ct_ok.json()["user"]["rank_band"] == "Rating"

    # Crew tablet bad length (6 digits rejected)
    ct_bad = client.post("/api/v1/auth/login", json={"device_token": "crew-tablet", "pin": "123456"})
    assert ct_bad.status_code == 400
    assert "4 digits" in ct_bad.json()["error"]

    # Master console success (6 digits)
    mc_ok = client.post("/api/v1/auth/login", json={"device_token": "master-console", "pin": "123456"})
    assert mc_ok.status_code == 200
    assert mc_ok.json()["user"]["name"] == "Capt. Kundan"
    assert mc_ok.json()["user"]["rank_band"] == "Officer"

    # Master console bad length (4 digits rejected)
    mc_bad = client.post("/api/v1/auth/login", json={"device_token": "master-console", "pin": "1234"})
    assert mc_bad.status_code == 400
    assert "6 digits" in mc_bad.json()["error"]


def test_api_v1_me_and_session_role_switch():
    """
    Contract test for GET /api/v1/me and Crew & Master session / role switching.
    1. Verify unauthenticated GET /api/v1/me fails with 401.
    2. Crew PIN Pad login (4-digit PIN 1234) -> GET /api/v1/me returns crew profile & rank_band: Rating.
    3. Terminal role switch to Master (6-digit PIN 123456) -> GET /api/v1/me returns master profile & rank_band: Officer.
    4. Terminal role switch back to Crew -> GET /api/v1/me validates crew session.
    """
    # 1. Unauthenticated GET /api/v1/me returns 401
    unauth = client.get("/api/v1/me")
    assert unauth.status_code == 401

    # 2. Crew PIN Pad Login (4 digits)
    crew_login = client.post(
        "/api/v1/auth/login",
        json={"device_token": "crew-tablet", "pin": "1234"},
    )
    assert crew_login.status_code == 200
    crew_token = crew_login.json()["token"]
    assert crew_token

    # Verify GET /api/v1/me with Crew session
    crew_me = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {crew_token}"},
    )
    assert crew_me.status_code == 200
    c_data = crew_me.json()
    assert c_data["id"] == "u-crew"
    assert c_data["crew_id"] == "u-crew"
    assert c_data["name"] == "Amarjeet"
    assert c_data["role"] == "crew"
    assert c_data["rank_band"] == "Rating"
    assert c_data["vessel"] == "MV-PACIFIC-STAR"
    assert "preferences" in c_data
    assert c_data["preferences"]["preferred_language"] in ("en", "hi", "tl")

    # 3. Session / Role switch to Master (6 digits) without clearing browser/terminal
    master_switch = client.post(
        "/api/v1/auth/login",
        json={"device_token": "master-console", "pin": "123456"},
    )
    assert master_switch.status_code == 200
    master_token = master_switch.json()["token"]
    assert master_token

    # Verify GET /api/v1/me with Master session
    master_me = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {master_token}"},
    )
    assert master_me.status_code == 200
    m_data = master_me.json()
    assert m_data["id"] in ("u-master", "MASTER-001")
    assert m_data["crew_id"] in ("u-master", "MASTER-001")
    assert m_data["name"] == "Capt. Kundan"
    assert m_data["role"] == "master"
    assert m_data["rank_band"] == "Officer"
    assert m_data["vessel"] == "MV-PACIFIC-STAR"
    assert "preferences" in m_data

    # 4. Switch back to Crew watch
    crew_switch = client.post(
        "/api/v1/auth/login",
        json={"device_token": "crew-tablet", "pin": "1234"},
    )
    assert crew_switch.status_code == 200
    switched_crew_token = crew_switch.json()["token"]

    switched_crew_me = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {switched_crew_token}"},
    )
    assert switched_crew_me.status_code == 200
    assert switched_crew_me.json()["role"] == "crew"
    assert switched_crew_me.json()["rank_band"] == "Rating"


def test_api_v1_reports_drafts_multipart_spec():
    """
    Contract test for POST /api/v1/reports/drafts:
    1. Valid multipart upload: voice audio (<= 60s), up to 3 photos (EXIF stripped), optional text -> 201.
    2. Voice audio > 60s rejected with 422 Unprocessable Entity.
    3. More than 3 photos rejected with 422 Unprocessable Entity.
    4. Optional text (voice and photo only) -> 201.
    """
    # 1. Login as crew
    login_res = client.post("/api/v1/auth/login", json={"device_token": "crew-tablet", "pin": "1234"})
    assert login_res.status_code == 200
    token = login_res.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    fake_audio = ("voice.webm", b"\x1a\x45\xdf\xa3fake-voice-bytes", "audio/webm")
    fake_photo_1 = ("photo1.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIFfakephoto1", "image/jpeg")
    fake_photo_2 = ("photo2.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIFfakephoto2", "image/jpeg")
    fake_photo_3 = ("photo3.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIFfakephoto3", "image/jpeg")
    fake_photo_4 = ("photo4.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIFfakephoto4", "image/jpeg")

    # 1. Valid: voice audio (45s <= 60s), 3 photos, optional text
    valid_res = client.post(
        "/api/v1/reports/drafts",
        data={"text": "Hydraulic oil leak observed near deck crane 2", "duration": "45.0", "lang": "en"},
        files=[
            ("audio", fake_audio),
            ("photos[]", fake_photo_1),
            ("photos[]", fake_photo_2),
            ("photos[]", fake_photo_3),
        ],
        headers=headers,
    )
    assert valid_res.status_code == 201
    valid_data = valid_res.json()
    assert "draft_id" in valid_data
    assert valid_data["photos_count"] == 3
    assert valid_data["audio_attached"] is True

    # 2. Voice audio > 60s rejected with 422
    over_duration_res = client.post(
        "/api/v1/reports/drafts",
        data={"text": "Long recording", "duration": "65.5"},
        files=[("audio", fake_audio)],
        headers=headers,
    )
    assert over_duration_res.status_code == 422
    assert "60 seconds" in over_duration_res.json()["error"]

    # 3. More than 3 photos rejected with 422
    over_photos_res = client.post(
        "/api/v1/reports/drafts",
        data={"text": "Four photos"},
        files=[
            ("photos[]", fake_photo_1),
            ("photos[]", fake_photo_2),
            ("photos[]", fake_photo_3),
            ("photos[]", fake_photo_4),
        ],
        headers=headers,
    )
    assert over_photos_res.status_code == 422
    assert "Maximum 3 photos" in over_photos_res.json()["error"]

    # 4. Voice audio + photos without text (optional text)
    no_text_res = client.post(
        "/api/v1/reports/drafts",
        data={"duration": "30.0"},
        files=[
            ("audio", fake_audio),
            ("photos[]", fake_photo_1),
        ],
        headers=headers,
    )
    assert no_text_res.status_code == 201
    assert no_text_res.json()["photos_count"] == 1
    assert no_text_res.json()["audio_attached"] is True


def test_master_ticket_action_resolution_photo():
    # 1. Login as crew member to file a report
    crew_login = client.post("/auth/login", json={"username": "amarjeet", "pin": "1234"})
    if crew_login.status_code != 200:
        crew_login = client.post("/auth/login", json={"username": "crew1", "password": "1234"})
    crew_token = crew_login.json()["token"]
    crew_headers = {"Authorization": f"Bearer {crew_token}"}

    # Create draft & submit report
    draft_res = client.post("/reports/drafts", json={"text": "Loose handrail on poop deck"}, headers=crew_headers)
    assert draft_res.status_code in (200, 201)
    draft_id = draft_res.json()["draft_id"]

    submit_res = client.post(
        "/reports",
        json={
            "draft_id": draft_id,
            "fields": {
                "title_en": "Loose Handrail",
                "description_en": "Loose handrail on poop deck near stairwell.",
                "location_on_board": "Poop deck",
                "hazard_type": "slip_trip_fall",
                "ism_category": "unsafe_condition",
                "severity": 2,
                "immediate_action": "Marked with caution tape",
            },
        },
        headers=crew_headers,
    )
    assert submit_res.status_code in (200, 201)
    ticket_id = submit_res.json()["id"]

    # 2. Login as master
    master_login = client.post("/auth/login", json={"username": "master", "password": "1234"})
    assert master_login.status_code == 200
    master_token = master_login.json()["token"]
    master_headers = {"Authorization": f"Bearer {master_token}"}

    # 3. Post resolution action with photo upload (multipart/form-data)
    fake_photo = io.BytesIO(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00")
    action_res = client.post(
        f"/master/tickets/{ticket_id}/actions",
        data={
            "text": "Handrail re-welded and secured with new bracket",
            "responsible_rank": "Bosun",
            "resolve": "true",
        },
        files={"photo": ("resolution_fix.jpg", fake_photo, "image/jpeg")},
        headers=master_headers,
    )
    assert action_res.status_code == 200
    data = action_res.json()
    assert data["onboard_status"] == "RESOLVED_ON_BOARD"
    assert len(data["actions"]) >= 1
    last_action = data["actions"][-1]
    assert last_action["resolved"] is True
    assert last_action["text"] == "Handrail re-welded and secured with new bracket"
    assert "photo_url" in last_action
    assert last_action["photo_url"] is not None
    assert "/uploads/resolution_" in last_action["photo_url"]
    assert any("resolution_" in p for p in data["photos"])

    # 4. Fetch ticket as crew member and verify resolution photo is passed back to user
    crew_ticket_res = client.get(f"/tickets/{ticket_id}", headers=crew_headers)
    assert crew_ticket_res.status_code == 200
    crew_ticket = crew_ticket_res.json()
    assert crew_ticket["onboard_status"] == "RESOLVED_ON_BOARD"
    assert any(a.get("photo_url") == last_action["photo_url"] for a in crew_ticket["actions"])
    assert any("resolution_" in p for p in crew_ticket["photos"])




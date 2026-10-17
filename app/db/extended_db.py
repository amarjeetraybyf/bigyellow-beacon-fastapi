"""
Extended BeaconDatabase methods supporting all modular API routes.
Adds: Users, Devices, Notices, Checkin Sessions, Crew History, Fleet Alerts,
      Corpus ingestion, and Monthly Report storage.
"""

import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from app.db.local_db import beacon_db, get_utc_now_iso


# ---------------------------------------------------------------------------
# Default Users initialized with SHA-256 hashed PINs
# ---------------------------------------------------------------------------
def hash_pin(pin: str) -> str:
    return hashlib.sha256(str(pin).encode("utf-8")).hexdigest()


USERS_STORE: Dict[str, Dict[str, Any]] = {
    "MASTER-001": {
        "userId": "MASTER-001",
        "username": "master",
        "role": "master",
        "vesselId": "MV-PACIFIC-STAR",
        "displayName": "Captain Santos",
        "hashedPin": hash_pin("1234"),
        "createdAt": get_utc_now_iso(),
    },
    "SAFETY-001": {
        "userId": "SAFETY-001",
        "username": "safety_officer",
        "role": "safety_officer",
        "vesselId": "MV-PACIFIC-STAR",
        "displayName": "Safety Officer Kim",
        "hashedPin": hash_pin("5678"),
        "createdAt": get_utc_now_iso(),
    },
    "SHORE-SAFETY-001": {
        "userId": "SHORE-SAFETY-001",
        "username": "shore_safety",
        "role": "shore_safety",
        "displayName": "DPA Priya Mehta",
        "hashedPin": hash_pin("1111"),
        "createdAt": get_utc_now_iso(),
    },
    "SHORE-PSYCH-001": {
        "userId": "SHORE-PSYCH-001",
        "username": "shore_psych",
        "role": "shore_psych",
        "displayName": "Dr. Ananya Sharma",
        "hashedPin": hash_pin("2222"),
        "createdAt": get_utc_now_iso(),
    },
}

PAIRED_DEVICES: List[Dict[str, Any]] = []
NOTICES: Dict[str, List[Dict[str, Any]]] = {}  # vesselId -> list
FLEET_ALERTS: List[Dict[str, Any]] = []
CHECKIN_SESSIONS: Dict[str, Dict[str, Any]] = {}
CREW_HISTORY: Dict[str, List[Dict[str, Any]]] = {}
MONTHLY_REPORTS: List[Dict[str, Any]] = []
NEAR_MISS_UPDATES: Dict[str, Dict[str, Any]] = {}


# --- Users ---

def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    for u in USERS_STORE.values():
        if u.get("username") == username:
            return u
    return None


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    return USERS_STORE.get(user_id)


def list_users() -> List[Dict[str, Any]]:
    return list(USERS_STORE.values())


def create_user(user: Dict[str, Any]) -> Dict[str, Any]:
    USERS_STORE[user["userId"]] = user
    return user


# --- Devices ---

def add_paired_device(device: Dict[str, Any]) -> Dict[str, Any]:
    PAIRED_DEVICES.append(device)
    beacon_db.add_device(device)
    return device


def list_devices() -> List[Dict[str, Any]]:
    # Merge memory & db devices
    all_devs = list(PAIRED_DEVICES)
    db_devs = beacon_db.get_devices()
    existing_tokens = {d["token"] for d in all_devs}
    for d in db_devs:
        if d.get("token") not in existing_tokens:
            all_devs.append(d)
    return all_devs


def revoke_device(token: str) -> bool:
    removed = False
    for i, d in enumerate(list(PAIRED_DEVICES)):
        if d.get("token") == token:
            PAIRED_DEVICES.pop(i)
            removed = True
            break
    db_removed = beacon_db.revoke_device(token)
    return removed or db_removed


# --- Near-Miss CRUD ---

def update_near_miss(nm_id: str, update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    nm = beacon_db.get_near_miss(nm_id)
    if not nm:
        return None
    if nm_id not in NEAR_MISS_UPDATES:
        NEAR_MISS_UPDATES[nm_id] = {}
    NEAR_MISS_UPDATES[nm_id].update(update)
    beacon_db.update_near_miss(nm_id, update)
    return {**nm, **NEAR_MISS_UPDATES[nm_id]}


def get_near_miss_with_status(nm_id: str) -> Optional[Dict[str, Any]]:
    nm = beacon_db.get_near_miss(nm_id)
    if not nm:
        return None
    extra = NEAR_MISS_UPDATES.get(nm_id, {})
    return {**nm, **extra}


def get_all_near_misses_with_status() -> List[Dict[str, Any]]:
    results = []
    for nm in beacon_db.get_near_misses():
        extra = NEAR_MISS_UPDATES.get(nm.get("id"), {})
        results.append({**nm, **extra})
    return results


# --- Notices ---

def add_notice(notice: Dict[str, Any]) -> Dict[str, Any]:
    vessel_id = notice.get("vesselId", "")
    if vessel_id not in NOTICES:
        NOTICES[vessel_id] = []
    NOTICES[vessel_id].insert(0, notice)
    return notice


def get_notices(vessel_id: str) -> List[Dict[str, Any]]:
    return NOTICES.get(vessel_id, [])


# --- Fleet Alerts ---

def add_fleet_alert(alert: Dict[str, Any]) -> Dict[str, Any]:
    FLEET_ALERTS.insert(0, alert)
    return alert


def get_fleet_alerts(vessel_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if not vessel_id:
        return FLEET_ALERTS
    return [
        a for a in FLEET_ALERTS
        if not a.get("targetVesselIds") or vessel_id in a.get("targetVesselIds", [])
    ]


# --- Checkin Sessions ---

def create_session(session: Dict[str, Any]) -> Dict[str, Any]:
    CHECKIN_SESSIONS[session["sessionId"]] = session
    return session


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    return CHECKIN_SESSIONS.get(session_id)


def update_session(session_id: str, update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    s = CHECKIN_SESSIONS.get(session_id)
    if not s:
        return None
    s.update(update)
    return s


# --- Crew History ---

def append_crew_history(crew_token: str, entry: Dict[str, Any]):
    if crew_token not in CREW_HISTORY:
        CREW_HISTORY[crew_token] = []
    CREW_HISTORY[crew_token].append(entry)
    if len(CREW_HISTORY[crew_token]) > 90:
        CREW_HISTORY[crew_token] = CREW_HISTORY[crew_token][-90:]


def get_crew_history(crew_token: str) -> List[Dict[str, Any]]:
    return CREW_HISTORY.get(crew_token, [])


# --- Monthly Reports ---

def save_monthly_report(report: Dict[str, Any]) -> Dict[str, Any]:
    MONTHLY_REPORTS.insert(0, report)
    return report


def get_latest_monthly_report() -> Optional[Dict[str, Any]]:
    return MONTHLY_REPORTS[0] if MONTHLY_REPORTS else None


# --- Corpus Search ---

def search_corpus(query: str) -> List[Dict[str, Any]]:
    return beacon_db.search_corpus(query)


# --- Ship Overview ---

def get_ship_overview(vessel_id: str) -> Dict[str, Any]:
    near_misses = beacon_db.get_near_misses(vessel_id)
    open_hazards = [
        n for n in near_misses
        if NEAR_MISS_UPDATES.get(n.get("id"), {}).get("status", n.get("status", "OPEN")) == "OPEN"
    ]
    checkins = beacon_db.get_check_ins(vessel_id)
    vessel = beacon_db.get_vessel(vessel_id)

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reports_today = [
        n for n in near_misses
        if n.get("timestamp", "").startswith(today_str)
    ]

    return {
        "vesselId": vessel_id,
        "vesselName": vessel.get("name", vessel_id) if vessel else vessel_id,
        "connectionStatus": vessel.get("connectionStatus") if vessel else "OFFLINE_AIRPLANE",
        "lastSyncTimestamp": vessel.get("lastSyncTimestamp") if vessel else None,
        "totalReportsToday": len(reports_today),
        "openHazards": len(open_hazards),
        "crewCheckInsToday": len(checkins),
        "fleetAlertsPending": len(get_fleet_alerts(vessel_id)),
        "generatedAt": get_utc_now_iso(),
    }

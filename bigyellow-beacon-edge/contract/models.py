"""
Shared Pydantic v2 Models for BigYellow Beacon Contract
"""

from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, model_validator

from contract.enums import (
    Role,
    Origin,
    OnboardStatus,
    ShoreStatus,
    LearningStatus,
    PullChangeType,
    HazardType,
    IsmCategory,
)
from contract.uuid7 import uuid7_str


import re

def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc_iso(dt: Optional[Any]) -> Optional[str]:
    """
    Ensures a datetime or ISO timestamp string is formatted as an explicit UTC ISO-8601 string ending with 'Z'.
    Prevents browsers/clients in other timezones (e.g. IST UTC+05:30) from misinterpreting naive timestamps
    as local time (which causes newly created items to appear as '6 hours ago').
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        s = dt.strip()
        if not s:
            return None
        if " " in s and "T" not in s:
            s = s.replace(" ", "T")
        if s.endswith("Z"):
            return s
        if "+00:00" in s:
            return s.replace("+00:00", "Z")
        if re.search(r"[+-]\d{2}:\d{2}$", s):
            return s
        return s + "Z"
    if hasattr(dt, "tzinfo"):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    return str(dt)


def normalize_hazard_type(raw: Optional[str]) -> str:
    """Map free-text / legacy labels to frontend HazardType enum values."""
    if not raw:
        return HazardType.OTHER.value
    value = str(raw).strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")
    for ht in HazardType:
        if value == ht.value or value == ht.name.lower():
            return ht.value
    text = str(raw).lower()
    if any(w in text for w in ("slip", "trip", "fall", "wet deck")):
        return HazardType.SLIP_TRIP_FALL.value
    if any(w in text for w in ("mooring", "snap", "winch", "rope", "line parting")):
        return HazardType.MOORING.value
    if any(w in text for w in ("crane", "lifting", "hoist", "cargo gear")):
        return HazardType.CRANE_LIFTING.value
    if any(w in text for w in ("enclosed", "confined", "ballast tank", "oxygen", "atmosphere")):
        return HazardType.ENCLOSED_SPACE.value
    if any(w in text for w in ("fire", "flammable", "ignition", "smoke")):
        return HazardType.FIRE.value
    if any(w in text for w in ("electric", "wiring", "cable", "shock")):
        return HazardType.ELECTRICAL.value
    if any(w in text for w in ("chemical", "corrosive", "toxic", "hazmat")):
        return HazardType.CHEMICAL.value
    if any(w in text for w in ("machiner", "engine", "hydraulic", "pump", "leak", "oil", "fuel", "pipe")):
        return HazardType.MACHINERY.value
    return HazardType.OTHER.value


def normalize_ism_category(raw: Optional[str]) -> str:
    """Map free-text / ISM code labels to frontend IsmCategory enum values."""
    if not raw:
        return IsmCategory.UNSAFE_CONDITION.value
    value = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    for ic in IsmCategory:
        if value == ic.value or value == ic.name.lower():
            return ic.value
    text = str(raw).lower()
    if any(w in text for w in ("unsafe_act", "unsafe act", "human error", "procedure not followed")):
        return IsmCategory.UNSAFE_ACT.value
    if any(w in text for w in ("equipment", "failure", "breakdown", "defect", "ism 10", "maintenance")):
        return IsmCategory.EQUIPMENT_FAILURE.value
    if any(w in text for w in ("procedure", "gap", "permit", "checklist", "ism 7", "operations")):
        return IsmCategory.PROCEDURE_GAP.value
    if any(w in text for w in ("unsafe_condition", "unsafe condition", "condition", "hazard")):
        return IsmCategory.UNSAFE_CONDITION.value
    return IsmCategory.UNSAFE_CONDITION.value


class SyncedBaseModel(BaseModel):
    id: str = Field(default_factory=uuid7_str)
    created_at: datetime = Field(default_factory=get_utc_now)
    updated_at: datetime = Field(default_factory=get_utc_now)
    origin: Origin = Origin.EDGE
    version: int = 1


# ─── TICKETS ─────────────────────────────────────────────────────────────────

class TicketFields(BaseModel):
    title_en: str
    description_en: str
    location_on_board: str
    hazard_type: str
    ism_category: str
    severity: int = Field(..., ge=1, le=5)
    immediate_action: str

    @model_validator(mode="after")
    def _normalize_enums(self):
        self.hazard_type = normalize_hazard_type(self.hazard_type)
        self.ism_category = normalize_ism_category(self.ism_category)
        return self


class TicketStructureResponse(BaseModel):
    draft_id: str
    original_language: str
    transcript_original: str
    fields: TicketFields
    clarifying_question: Optional[str] = None
    confidence: float = 0.95


class TicketAction(BaseModel):
    text: str
    responsible_rank: str
    due_at: Optional[datetime] = None
    resolve: bool = False


class Ticket(SyncedBaseModel):
    vessel_id: str
    reporter_ref: str  # HMAC-SHA256(vessel_secret, crew_id + ticket_id)
    title: str
    description: str
    location_on_board: str
    hazard_type: str
    ism_category: str
    severity: int = Field(..., ge=1, le=5)
    immediate_action: str
    onboard_status: OnboardStatus = OnboardStatus.NEW
    shore_status: ShoreStatus = ShoreStatus.NEW
    assigned_action: Optional[Dict[str, Any]] = None
    media_refs: List[Dict[str, Any]] = Field(default_factory=list)


class TicketMessage(SyncedBaseModel):
    ticket_id: str
    sender_role: str  # crew or shore_reviewer
    text: str


class TicketMessageCreate(BaseModel):
    text: str


# ─── LEARNINGS ───────────────────────────────────────────────────────────────

class LearningMedia(BaseModel):
    media_id: str
    media_type: str  # image/webp or video/mp4
    url: str
    duration_s: Optional[float] = None


class Learning(SyncedBaseModel):
    title: str
    summary: str
    body: str
    context: Optional[str] = None
    situation: Optional[str] = None
    do_points: List[str] = Field(default_factory=list)
    dont_points: List[str] = Field(default_factory=list)
    check_question: Optional[Dict[str, Any]] = None
    audience: Optional[Dict[str, Any]] = None
    priority: str = "normal"
    status: LearningStatus = LearningStatus.DRAFT
    media: List[Dict[str, Any]] = Field(default_factory=list)
    target_vessel_ids: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    stats: Optional[Dict[str, Any]] = None
    published_at: Optional[str] = None


# ─── DAILY ANCHOR ────────────────────────────────────────────────────────────

class DailyAnchorCard(SyncedBaseModel):
    topic: str
    quote_or_prompt: str
    translations: Dict[str, str] = Field(default_factory=dict)
    scheduled_date: str  # YYYY-MM-DD


class AnchorCard(SyncedBaseModel):
    publish_date: str
    scheduled_date: Optional[str] = None
    type: str = "safety"
    title: str
    topic: Optional[str] = None
    body: str
    quote_or_prompt: Optional[str] = None
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    image_url: Optional[str] = None
    audience: str = "all"
    status: str = "draft"
    translations: Dict[str, str] = Field(default_factory=dict)
    stats: Dict[str, Any] = Field(default_factory=dict)


# ─── CHECKINS & WELFARE ──────────────────────────────────────────────────────

class CheckInCreate(BaseModel):
    mode: str = "quick"  # quick | chat
    mood: int = Field(..., ge=1, le=5)
    fatigue: Optional[int] = Field(None, ge=1, le=5)
    workload: Optional[int] = Field(None, ge=1, le=5)


class CheckInTurnRequest(BaseModel):
    text: str
    lang: str = "en"


class WelfareRequest(BaseModel):
    id: str = Field(default_factory=uuid7_str)
    vessel_id: str
    crew_id: str
    crew_name: str
    requested_at: datetime = Field(default_factory=get_utc_now)
    acknowledged: bool = False
    acknowledged_at: Optional[datetime] = None


class CheckinAggregate(SyncedBaseModel):
    vessel_id: str
    ship_date: str  # YYYY-MM-DD
    department: str
    count: int = Field(..., ge=5)  # Enforced k >= 5
    avg_mood: float
    avg_fatigue: float
    avg_workload: float


# ─── SYNC DELTA & PULL ───────────────────────────────────────────────────────

class SyncDeltaEvent(BaseModel):
    event_id: str = Field(default_factory=uuid7_str)
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: Dict[str, Any]
    severity: int = Field(default=3, ge=1, le=5)
    created_at: datetime = Field(default_factory=get_utc_now)


class SyncDelta(BaseModel):
    vessel_id: str
    events: List[SyncDeltaEvent]


class SyncPushResponse(BaseModel):
    success: bool
    acked_event_ids: List[str]
    rejected_event_ids: List[str] = Field(default_factory=list)


class PullChange(BaseModel):
    change_id: str = Field(default_factory=uuid7_str)
    change_type: PullChangeType
    aggregate_id: str
    payload: Dict[str, Any]
    timestamp: datetime = Field(default_factory=get_utc_now)


class SyncPullResponse(BaseModel):
    changes: List[PullChange]
    next_cursor: Optional[str] = None
    has_more: bool = False


# ─── AUTH & TOKENS ───────────────────────────────────────────────────────────

class PairingCodeResponse(BaseModel):
    code: str
    vessel_id: str
    expires_at: datetime
    qr_payload: Optional[str] = None


class PairRequest(BaseModel):
    pairing_code: str
    crew_id: Optional[str] = None
    department: Optional[str] = "Deck"
    crew_name: Optional[str] = None
    pin: Optional[str] = "1234"


class PairResponse(BaseModel):
    device_token: str
    device_id: Optional[str] = None
    vessel_id: str
    crew_id: Optional[str] = None
    crew_name: Optional[str] = None
    department: Optional[str] = "Deck"
    expires_at: datetime
    token: Optional[str] = None


class LoginRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    device_token: Optional[str] = None
    pin: Optional[str] = None
    email: Optional[str] = None
    crew_id: Optional[str] = None
    role: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int
    role: Role
    vessel_id: Optional[str] = None
    crew_id: Optional[str] = None
    user_id: Optional[str] = None
    user: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def populate_token_alias(self) -> "TokenResponse":
        if not self.token and self.access_token:
            self.token = self.access_token
        return self


class UserPreferences(BaseModel):
    preferred_language: str = "en"
    notifications_enabled: bool = True

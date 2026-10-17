"""
Pydantic Schemas for BigYellow Beacon API
Matches OpenAPI 3.0 specification from Express backend.
"""

from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

# ── Health & System ──────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "online"
    app: str = "BigYellow Beacon"
    tagline: str = "Crew wellbeing & safety copilot 400 miles from the nearest cell tower"
    cloudRegion: str = "asia-southeast1 (Cloud Run)"
    aiEngine: str = "Gemma 3n On-Device (Client) + Gemini 3.8 Flash (Ashore)"
    geminiLive: bool = False
    onDeviceModelLoaded: Optional[bool] = True
    dbStatus: Optional[str] = "ok"
    firebaseConnected: Optional[bool] = False
    timestamp: str

# ── Auth ─────────────────────────────────────────────────────────────────────

class PairRequest(BaseModel):
    pairingCode: str = Field(..., description="QR code payload scanned on vessel bulkhead")
    vesselId: str = Field(..., description="Vessel identifier e.g. MV-PACIFIC-STAR")
    department: Optional[str] = Field("Unknown", description="Crew department e.g. Deck, Engine, Galley")

class PairResponse(BaseModel):
    token: str
    vesselId: str
    role: str = "crew"
    expiresAt: str
    message: str

class LoginRequest(BaseModel):
    username: str
    pin: str

class LoginResponse(BaseModel):
    token: str
    userId: str
    role: str
    displayName: str
    vesselId: Optional[str] = None

class UserIdentityResponse(BaseModel):
    userId: str
    role: str
    displayName: str
    vesselId: Optional[str] = None
    username: Optional[str] = None
    createdAt: Optional[str] = None

# ── Near-Miss ────────────────────────────────────────────────────────────────

class NearMissDraftRequest(BaseModel):
    text: str
    lang: Optional[str] = "en"
    photoAttached: Optional[bool] = False

class NearMissClarifyRequest(BaseModel):
    answer: str

class NearMissConfirmRequest(BaseModel):
    draftPayload: Dict[str, Any]

class NearMissRecord(BaseModel):
    id: str
    rawText: str
    translatedText: Optional[str] = None
    language: str
    location: str
    hazardType: str
    ismCategory: str
    severity: str
    recommendedFollowUp: str
    timestamp: str
    vesselId: str
    synced: bool
    status: Optional[str] = "OPEN"
    correctiveAction: Optional[str] = None
    closedAt: Optional[str] = None

class NearMissPatchRequest(BaseModel):
    status: Optional[str] = None
    correctiveAction: Optional[str] = None

# ── Check-in ─────────────────────────────────────────────────────────────────

class CheckInStartRequest(BaseModel):
    lang: Optional[str] = "en"

class CheckInStartResponse(BaseModel):
    sessionId: str
    language: str
    question: str

class CheckInTurnRequest(BaseModel):
    text: str
    lang: Optional[str] = "en"

class CheckInTurnResponse(BaseModel):
    sessionId: str
    response: Optional[str] = None
    safetyFlag: Optional[str] = "STANDARD"
    finished: bool = False
    escalationTriggered: Optional[bool] = False

class CheckInFinishResponse(BaseModel):
    sessionId: str
    scores: Dict[str, Any]
    transcriptStored: bool = False
    message: str

# ── Help / Escalation ────────────────────────────────────────────────────────

class HelpRequest(BaseModel):
    message: Optional[str] = ""
    lang: Optional[str] = "en"
    consentGranted: bool = False

class HelpResponse(BaseModel):
    escalationId: str
    urgency: str
    response: str
    seafarerHelpLine: str
    message: str

class EscalationPatchRequest(BaseModel):
    status: str
    notes: Optional[str] = None

# ── Ship & Fleet ─────────────────────────────────────────────────────────────

class NoticeCreateRequest(BaseModel):
    title: str
    body: str
    targetLanguages: Optional[List[str]] = ["en", "tl", "hi", "id"]

class FleetAlertCreateRequest(BaseModel):
    targetVesselIds: Optional[List[str]] = []
    severity: Optional[str] = "WARNING"
    category: str
    message: str

class AskRequest(BaseModel):
    question: str
    lang: Optional[str] = "en"

class VesselConnectivityRequest(BaseModel):
    status: str

# ── Sync & Ingest ────────────────────────────────────────────────────────────

class SyncRunRequest(BaseModel):
    vesselId: Optional[str] = "MV-PACIFIC-STAR"
    nearMisses: Optional[List[Dict[str, Any]]] = []
    checkIns: Optional[List[Dict[str, Any]]] = []
    rawBytesReceived: Optional[int] = None

class IngestDeltaRequest(BaseModel):
    vesselId: str
    nearMisses: Optional[List[Dict[str, Any]]] = []
    checkIns: Optional[List[Dict[str, Any]]] = []
    escalations: Optional[List[Dict[str, Any]]] = []
    rawBytesReceived: Optional[int] = None

class IngestMediaRequest(BaseModel):
    vesselId: str
    nearMissId: str
    mediaType: Optional[str] = "photo"
    description: Optional[str] = None

class IngestHeartbeatRequest(BaseModel):
    vesselId: str
    linkQuality: Optional[str] = "STRONG"
    pendingItems: Optional[int] = 0

# ── AI Routes ────────────────────────────────────────────────────────────────

class AITranscribeRequest(BaseModel):
    audioBase64: Optional[str] = None
    lang: Optional[str] = "en"
    sampleId: Optional[str] = None

class AIExtractRequest(BaseModel):
    text: str
    lang: Optional[str] = "en"
    vesselId: Optional[str] = "MV-PACIFIC-STAR"

class AIVisionRequest(BaseModel):
    imageBase64: str
    mimeType: Optional[str] = "image/jpeg"

class AITranslateRequest(BaseModel):
    text: str
    sourceLang: Optional[str] = "auto"
    targetLang: str

class AITTSRequest(BaseModel):
    text: str
    lang: Optional[str] = "en"
    voice: Optional[str] = "neutral"

# ── Admin ────────────────────────────────────────────────────────────────────

class AdminVesselCreateRequest(BaseModel):
    id: str
    name: str
    type: Optional[str] = "General Cargo"
    imoNumber: str
    flag: Optional[str] = "Unknown"
    crewCount: Optional[int] = 0

class AdminUserCreateRequest(BaseModel):
    username: str
    role: str
    vesselId: Optional[str] = None
    displayName: Optional[str] = None
    pin: str

class AdminDeviceRevokeRequest(BaseModel):
    token: str

class CorpusCaseCreateRequest(BaseModel):
    source: str
    reportCode: str
    title: str
    year: Optional[int] = None
    vesselType: Optional[str] = None
    hazardDomain: Optional[str] = None
    precursorPattern: Optional[str] = None
    casualtyOutcome: Optional[str] = None
    keySafetyAction: Optional[str] = None
    similarityKeywords: Optional[List[str]] = []

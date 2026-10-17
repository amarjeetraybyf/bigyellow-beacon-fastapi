# 🌊 BigYellow Beacon – Python FastAPI Backend

> **Maritime Edge & Shore Safety Intelligence Backend API**  
> *Operating 400 miles from the nearest cell tower.*

This project is a high-performance, modular Python **FastAPI** backend that provides an exact 1:1 drop-in replacement for the Express.js implementation of BigYellow Beacon. It incorporates **Firebase (Firebase Admin SDK & Cloud Firestore)** as the cloud server backend with seamless edge offline fallback, **Gemini AI** for onshore safety synthesis, and on-device NLP simulation.

---

## 🌟 Key Architecture & Capabilities

1. **Onboard Edge Intelligence & Resilience**:
   - Offline multilingual near-miss reporting, severity estimation, clarifying follow-ups, and MLC 2006 rest hour compliance.
   - Operates completely offline with local persistent storage (`data/beacon-db.json`) when out at sea.
2. **Cloud Server Backend (Firebase)**:
   - Uses **Firebase Admin SDK** and **Cloud Firestore** for ashore synchronization of fleet vessels, incident clusters, near-misses, and crisis escalations.
   - Supports **Firebase Auth** ID token verification as well as maritime QR-code device pairing and static demo tokens.
   - Graceful offline fallback: if satellite connectivity is down or Firebase credentials are not yet configured, the system operates seamlessly using local outbox queues.
3. **GDPR Article 9 Privacy & k-Anonymity**:
   - **Rule ①**: Voice & chat transcripts are strictly **never** stored; only numeric sentiment scores are retained.
   - **Rule ②**: Wellbeing metrics are masked under a strict **k-anonymity threshold ($k \ge 5$)** so Masters and DPAs cannot identify individuals.
   - **Rule ③**: Psychological crisis escalations are strictly gated by explicit seafarer consent (`consentGranted === true`).
4. **Bandwidth-Optimized Satellite Delta Sync**:
   - Outbox pattern queues deltas during offline sea passages. Drains during 15-minute VSAT burst windows with simulated compression ($< 1$ KB payload, $\sim 82\%$ bandwidth savings).
5. **Ashore Fleet Safety Copilot (Gemini 3.8 Flash)**:
   - Automated Master's 06:00 morning safety brief generation.
   - Cross-vessel hazard clustering and precedent matching against historical accident investigation reports (MAIB, IMO, USCG).

---

## 🛠️ Technology Stack

- **Framework**: Python 3.10+ & [FastAPI](https://fastapi.tiangolo.com/)
- **ASGI Server**: [Uvicorn](https://www.uvicorn.org/)
- **Data Validation & Schemas**: [Pydantic v2](https://docs.pydantic.dev/)
- **Cloud Database & Auth**: [Firebase Admin SDK](https://firebase.google.com/docs/admin/setup) & [Google Cloud Firestore](https://cloud.google.com/firestore)
- **AI Engine**: Google GenAI (`google-genai` / Gemini 3.8 Flash)
- **Testing**: [pytest](https://pytest.org/) & `pytest-asyncio`

---

## 🚀 Getting Started

### 1. Prerequisites

- Python 3.10 or higher
- `pip` or virtual environment tool

### 2. Virtual Environment Setup

```bash
cd bigyellow-beacon-fastapi

# Create virtual environment
python3 -m venv .venv

# Activate virtual environment
# On macOS / Linux:
source .venv/bin/activate
# On Windows:
# .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Variables Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` to configure your settings:

```ini
# Server settings
PORT=8000
HOST="0.0.0.0"
DEBUG=true

# Gemini API Key (optional for deterministic fallback mode)
GEMINI_API_KEY="YOUR_GEMINI_API_KEY"

# Firebase Cloud Server Configuration
# Path to service account JSON key downloaded from Firebase Console
FIREBASE_SERVICE_ACCOUNT_KEY=""
FIREBASE_PROJECT_ID=""
FIREBASE_DATABASE_URL=""
FIREBASE_OFFLINE_FALLBACK=true
```

> **Note on Firebase**: If `FIREBASE_SERVICE_ACCOUNT_KEY` is not provided, the backend operates in **Offline / Local Fallback Mode** using `data/beacon-db.json` and in-memory caches, allowing instant zero-friction local development while maintaining full capability.

### 4. Running the Backend Server

```bash
# Run server using Uvicorn
python main.py

# Or directly with Uvicorn:
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Once running:
- **Interactive Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Alternative Docs**: [http://localhost:8000/api-docs](http://localhost:8000/api-docs)
- **OpenAPI 3.0 Specification**: [http://localhost:8000/api/v1/openapi.json](http://localhost:8000/api/v1/openapi.json)
- **Health Check**: [http://localhost:8000/api/health](http://localhost:8000/api/health)

### 5. Running the Automated Test Suite

```bash
PYTHONPATH=. pytest tests/ -v
```

All 24 test cases covering core safety rules, k-anonymity aggregation, RBAC security, and all API endpoints will execute and pass.

---

## 🔑 Authentication & RBAC

The API uses standard HTTP Bearer tokens. For testing and demonstration, use the following pre-configured tokens:

| Persona / Role | Demo Bearer Token | Description |
| :--- | :--- | :--- |
| **Master (Captain)** | `master-token-xyz789` | Ship command dashboard, morning safety brief, crew wellbeing matrix |
| **Crew Member** | `crew-token-abc123` | Mobile app near-miss reporting, private check-in, consented help |
| **Safety Officer** | `safety-token-def456` | Shipboard safety officer dashboard, hazard resolution, SMS queries |
| **Shore Safety (DPA)** | `shore-safety-token-ghi012` | Designated Person Ashore (fleet bird's-eye view, hazard clusters) |
| **Shore Welfare Counselor** | `shore-psych-token-jkl345` | GDPR Art. 9 clinical triage queue (only role allowed to view escalations) |
| **System Administrator** | `admin-token-mno678` | Vessel registration, account creation, paired device revocation, audit |

You can also dynamically pair crew devices via `POST /api/v1/auth/pair`, or authenticate officers with username & PIN via `POST /api/v1/auth/login`.

---

## 📡 API Endpoint Overview

### 1. Modular V1 API (`/api/v1/*`)

#### **Auth & System**
- `POST /api/v1/auth/pair` – Crew scans physical vessel QR code $\to$ paired device token.
- `POST /api/v1/auth/login` – Master/Safety Officer PIN login (e.g. `master` / `1234`).
- `GET /api/v1/me` – Current authenticated user identity and role.
- `GET /api/v1/health` – System status, model loading, Firebase connectivity status.

#### **Crew Mobile App (`/api/v1/crew/*`)**
- `POST /api/v1/crew/near-miss` – Submit multilingual near-miss (runs safety rails & Gemma local extraction).
- `POST /api/v1/crew/near-miss/{id}/clarify` – Answer clarifying question.
- `POST /api/v1/crew/near-miss/{id}/confirm` – Save to local encrypted store and queue for sync.
- `GET /api/v1/crew/near-miss` – View near-misses for user's vessel.
- `POST /api/v1/crew/checkin/start` – Start wellbeing check-in session.
- `POST /api/v1/crew/checkin/{id}/turn` – Conversational turn with safety guardrails.
- `POST /api/v1/crew/checkin/{id}/finish` – Finalise session, extract numeric scores, discard raw transcript.
- `GET /api/v1/crew/history` – Personal mood & fatigue trend (`x-crew-token` header).
- `GET /api/v1/crew/notices` – Captain safety notices auto-translated to crew language.
- `POST /api/v1/crew/help` – Consented psychological crisis escalation to shore.

#### **Ship Dashboard (`/api/v1/ship/*`)**
- `GET /api/v1/ship/overview` – Daily counts: reports, open hazards, checkin trends.
- `GET /api/v1/ship/near-misses` – Filter by location, hazard type, status, date range.
- `GET /api/v1/ship/near-misses/{id}` – Single near-miss drilldown.
- `PATCH /api/v1/ship/near-misses/{id}` – Assign corrective action, update status, close report.
- `GET /api/v1/ship/wellbeing/summary` – k-Anonymity protected wellbeing matrix (masked if $< 5$).
- `GET /api/v1/ship/brief/today` – Master's 06:00 morning safety brief.
- `POST /api/v1/ship/brief/regenerate` – Rebuild the brief immediately with Gemini synthesis.
- `POST /api/v1/ship/notices` – Broadcast captain directive with multi-language translation.
- `GET /api/v1/ship/fleet-alerts` – Safety directives received from shore DPA.
- `POST /api/v1/ship/ask` – Offline natural-language RAG over SMS manuals & precedent corpus.

#### **Shore Dashboard (`/api/v1/fleet/*`, `/api/v1/escalations/*`, `/api/v1/corpus/*`)**
- `GET /api/v1/fleet/overview` – Bird's-eye view across sister vessels.
- `GET /api/v1/fleet/vessels/{id}` – Detailed vessel drilldown.
- `GET /api/v1/fleet/near-misses` – Fleet-wide near-miss query layer.
- `GET /api/v1/fleet/clusters` – Cross-vessel hazard clusters.
- `GET /api/v1/fleet/clusters/{id}` – Cluster details with MAIB/IMO citations and AI explanation.
- `GET /api/v1/fleet/wellbeing` – Fleet-wide k-anonymity wellbeing matrix.
- `POST /api/v1/fleet/alerts` – Push directive down to specific vessels or all fleet.
- `GET /api/v1/fleet/reports/monthly` – Executive monthly fleet safety report.
- `POST /api/v1/fleet/ask` – Natural-language fleet analytics.
- `GET /api/v1/escalations` – Consented psychological triage queue (shore psychologist only).
- `PATCH /api/v1/escalations/{id}` – Update triage status (contacted / stand-down / resolved).
- `POST /api/v1/corpus/cases` – Ingest new maritime casualty investigation case.
- `GET /api/v1/corpus/search` – Semantic similarity search over casualty precedents.

#### **Sync & Ingest (`/api/v1/sync/*`, `/api/v1/ingest/*`, `/api/v1/ai/*`)**
- `GET /api/v1/sync/status` – Satellite link status, pending queue count, last sync timestamp.
- `POST /api/v1/sync/run` – Satellite burst sync trigger ("Wi-Fi on" moment).
- `POST /api/v1/ingest/delta` – Vessel uploads compressed delta payload.
- `GET /api/v1/ingest/updates` – Ship polls for new alerts, notices, and regenerated brief.
- `POST /api/v1/ingest/media` – Photo and media upload for high-bandwidth links.
- `POST /api/v1/ingest/heartbeat` – Vessel connectivity heartbeat.
- `POST /api/v1/ai/transcribe` – Speech transcription with safety rail checking.
- `POST /api/v1/ai/extract` – Near-miss information extraction and ISM Code tagging.
- `POST /api/v1/ai/vision` – Photo hazard inspection.
- `POST /api/v1/ai/translate` – Multilingual translation.

#### **Administration (`/api/v1/admin/*`)**
- `GET /api/v1/admin/vessels` – List all registered fleet vessels.
- `POST /api/v1/admin/vessels` – Register a new vessel.
- `GET /api/v1/admin/users` – List system users (pins redacted).
- `POST /api/v1/admin/users` – Create new officer or shore account.
- `GET /api/v1/admin/devices` – List paired crew devices.
- `POST /api/v1/admin/devices/revoke` – Revoke paired device token.
- `GET /api/v1/admin/audit-log` – Immutable safety compliance audit trail.

---

### 2. Legacy API Endpoints (`/api/*`)

All legacy endpoints from the Express backend are fully supported:
- `GET /api/health`
- `GET /api/fleet/vessels`
- `POST /api/fleet/vessels/{id}/connectivity`
- `GET /api/accidents/corpus`
- `GET /api/fleet/clusters`
- `GET /api/near-misses`
- `GET /api/fleet/k-anonymity/{vesselId}`
- `GET /api/psychology/escalations`
- `POST /api/psychology/escalations/{id}/status`
- `GET /api/master-brief/{vesselId}`
- `POST /api/sync/delta`
- `POST /api/gemini/generate-master-brief`
- `POST /api/gemini/voice-transcribe`
- `GET /api/audit-log`

---

## 🔒 Safety & Regulatory Compliance

- **MLC 2006 Standard A2.3**: Monitored via `evaluate_mlc_compliance` (maximum 14h work in 24h, minimum 77h rest in 7 days).
- **ISM Code**: Automatic classification into ISM-06 (Fatigue/Crew), ISM-07 (Shipboard Operations), ISM-08 (Emergency Preparedness), ISM-10 (Maintenance & Equipment).
- **GDPR Article 9**: Explicit consent gating and redaction of personal health/psychological information.
- **k-Anonymity**: Aggregation enforcement ($k=5$) ensuring zero re-identification of crew members in small watchkeeper sections.

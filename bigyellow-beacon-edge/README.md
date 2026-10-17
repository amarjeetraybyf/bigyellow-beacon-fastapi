# BigYellow Beacon – Edge Server (`bigyellow-beacon-edge`)

Autonomous Shipboard Safety & Wellbeing Intelligence Server operating on vessel mini-PCs or Jetson devices with zero persistent internet requirement.

## Features
- **Offline-First Resilience**: Full local CRUD for near-miss reports, hazard drafts, daily wellbeing check-ins, and safety learnings even during prolonged satellite blackouts.
- **Store-and-Forward Outbox**: Critical updates are queued in `outbox` table and synced over satellite links using exponential backoff, burst-sync detection, and HMAC-SHA256 signature verification.
- **Privacy & GDPR Art. 9 Enforcement**: Personal check-in transcripts live in volatile memory only and are never stored in the database. Near-miss submissions undergo client-side and server-side PII scrubbing (`[crew]` replacements and EXIF GPS stripping).
- **Crew Device Pairing & Argon2 PINs**: Onboard device pairing via Master code, Argon2 PIN authentication (4 digits for crew, 6 digits for Master), and automatic 15-minute lockouts after 5 failed attempts.
- **Real-Time Bridge SSE Stream**: Bridge Officers & Masters receive live SSE notifications for near-miss reports, welfare alerts, and sync status without browser polling.

## Quick Start

### 1. Environment Configuration
```bash
cp .env.example .env
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Seed Database
```bash
python scripts/seed_edge_db.py
```

### 4. Run Development Server
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
Swagger UI documentation will be live at: **http://127.0.0.1:8000/docs**

### 5. Run Tests
```bash
pytest -v tests/
```

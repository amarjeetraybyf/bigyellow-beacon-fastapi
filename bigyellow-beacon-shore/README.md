# BigYellow Beacon – Shore Server (`bigyellow-beacon-shore`)

Centralized Shore Intelligence Hub and Fleet Safety Management Server for BigYellow Beacon. Deployed on Google Cloud Run or AWS ECS.

## Features
- **Fleet Sync Hub**: Receives encrypted, HMAC-SHA256 authenticated outbox batches pushed from vessels; handles pull synchronization for fleet safety alerts and learning updates.
- **Shore Staff Authentication**: Argon2 password hashing, short-lived 1h access JWTs, and 8h refresh tokens for Safety Reviewers, Admins, and DPAs.
- **Ticket Investigation & Gemini Copilot**: AI-assisted root-cause analysis, corrective action recommendations, and similarity search against maritime accident precedent corpus (MAIB, NTSB, ATSB).
- **Safety Campaign Engine**: Draft, approve, and publish micro-learnings (videos/images) and Daily Anchor cards pushed out to vessels.
- **Privacy-by-Design**: Strict GDPR Art. 9 boundary. Contains no crew check-in transcripts, PINs, or device credentials.

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
python scripts/seed_shore_db.py
```

### 4. Run Development Server
```bash
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```
Swagger UI documentation will be live at: **http://127.0.0.1:8001/docs**

### 5. Run Tests
```bash
pytest -v tests/
```

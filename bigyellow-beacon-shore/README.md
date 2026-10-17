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

## AI Setup

The shore copilot is **provider-aware**: it prefers the local Ollama daemon (offline Gemma),
and can fall back to Vertex AI Gemini or a Google AI Studio API key when configured.

### Backend selection (`SHORE_AI_BACKEND`)
| Value | Behavior |
|---|---|
| `auto` (default) | Ollama first; then Vertex/Gemini if configured; deterministic fallback last |
| `ollama` | Local Ollama only (`gemma3n:e4b` + `embeddinggemma`) |
| `vertex` | Vertex AI Gemini (`GOOGLE_CLOUD_PROJECT` + ADC / service account) |
| `gemini` | Google AI Studio (`GEMINI_API_KEY`) |

### Required Ollama models (when using `auto`/`ollama`)
```bash
ollama pull gemma3n:e4b      # ticket analysis, learning drafts, anchor week
ollama pull embeddinggemma   # precedent similarity search
```

### AI environment variables (`.env`)
| Variable | Default | Purpose |
|---|---|---|
| `SHORE_AI_BACKEND` | `auto` | Backend selection (see above) |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Local Ollama daemon |
| `OLLAMA_STRUCTURE_MODEL` | `gemma3n:e4b` | Analysis + drafts |
| `OLLAMA_EMBED_MODEL` | `embeddinggemma` | Similarity embeddings |
| `OLLAMA_TIMEOUT_SECONDS` | `60` | Per-call timeout |
| `GOOGLE_CLOUD_PROJECT` | (empty) | Enables Vertex fallback |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | Vertex region |
| `GEMINI_API_KEY` | (empty) | Enables AI Studio fallback |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Cloud model name |

### Smoke checks
```bash
# 1. Ticket analysis: POST /tickets/{id}/analysis
#    -> "model" should be "ollama/gemma3n:e4b" (or vertex/gemini), not "deterministic-fallback"
# 2. Learning draft: POST /learnings/draft {"topic": "Mooring safety"}
#    -> "generated_by_model" shows the live backend
# 3. Anchor week: POST /anchor/draft-week -> 7 unique LLM-written cards
# 4. Similarity: GET /tickets/{id}/similar -> results include "method": "embeddinggemma"
```
With no backend available, all endpoints still answer with labeled deterministic fallbacks
(`"model": "deterministic-fallback"`, `"method": "keyword_overlap"`).

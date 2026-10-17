"""
BigYellow Beacon Full-Stack FastAPI Server
Python FastAPI intelligence plane with Firebase Cloud Backend & Onboard Resilience.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import PORT, HOST, DEBUG, APP_URL
from app.services.firebase_service import init_firebase, is_firebase_connected
from app.services.ai_service import is_gemini_live

# Route modules
from app.routes.auth_routes import router as auth_router
from app.routes.crew_routes import router as crew_router
from app.routes.ship_routes import router as ship_router
from app.routes.shore_routes import router as shore_router
from app.routes.sync_routes import router as sync_router
from app.routes.admin_routes import router as admin_router
from app.routes.legacy_routes import router as legacy_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize Firebase and check connections
    fb_connected = init_firebase()
    print("================================================================")
    print("🌊 BigYellow Beacon Maritime FastAPI Backend Running")
    print(f"📡 Local Host & Port:   http://localhost:{PORT}")
    print(f"📖 Interactive Swagger:  http://localhost:{PORT}/docs")
    print(f"📄 OpenAPI 3.0 Spec:    http://localhost:{PORT}/api/v1/openapi.json")
    print(f"☁️ Firebase Cloud:      {'Connected to Firestore' if fb_connected else 'Offline / Local Fallback Active'}")
    print(f"🤖 Gemini Live Ashore:  {'Active' if is_gemini_live() else 'Deterministic Heuristics Mode'}")
    print("================================================================")
    yield
    print("[Shutdown] Maritime backend server stopped.")


DESCRIPTION = """
**Crew Wellbeing & Safety Copilot operating 400 miles from the nearest cell tower.**

This Python FastAPI backend provides:
- **Onboard Edge Intelligence**: Offline multilingual near-miss reporting, clarifying follow-ups, and MLC 2006 rest hour compliance.
- **GDPR Article 9 Privacy & k-Anonymity**: Transcripts never stored; wellbeing statistics masked for groups < 5; clinical escalations strictly gated by explicit crew consent.
- **Satellite Delta Sync**: Outbox queueing with payload compression (< 1 KB) for sporadic 15-minute VSAT windows.
- **Ashore Fleet Copilot**: Cross-vessel hazard clustering, precedent matching against MAIB/IMO/USCG reports, and Master's 06:00 daily brief.
- **Cloud Server Backend (Firebase)**: Firebase Admin SDK, Cloud Firestore persistence & sync, and Firebase Auth verification.

### Demo Authentication Tokens
Click **Authorize** (top right) and enter one of the following Bearer tokens:
- **Master (Captain)**: `master-token-xyz789`
- **Crew Member**: `crew-token-abc123` (or generate one via `POST /api/v1/auth/pair`)
- **Safety Officer**: `safety-token-def456`
- **Shore Safety / DPA**: `shore-safety-token-ghi012`
- **Shore Welfare Counselor**: `shore-psych-token-jkl345`
- **System Admin**: `admin-token-mno678`
"""

app = FastAPI(
    title="BigYellow Beacon API",
    version="1.0.0",
    description=DESCRIPTION,
    docs_url=None,       # Custom Swagger UI handler below
    redoc_url=None,
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)

# Custom exception handler to match Express error response structure: { "error": "...", ... }
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})

# Enable CORS for all frontends & mobile clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Modular V1 Routers (/api/v1/*)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(crew_router, prefix="/api/v1")
app.include_router(ship_router, prefix="/api/v1")
app.include_router(shore_router, prefix="/api/v1")
app.include_router(sync_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")

# Mount Legacy Routers (/api/*)
app.include_router(legacy_router)


# ─── SWAGGER UI WITH MARITIME BRANDING ───────────────────────────────────────

SWAGGER_CUSTOM_CSS = """
.swagger-ui .topbar { background-color: #0b1329; border-bottom: 2px solid #38bdf8; }
.swagger-ui .topbar .download-url-wrapper { display: none; }
.swagger-ui .info { margin: 24px 0; }
.swagger-ui .info .title { font-family: system-ui, -apple-system, sans-serif; font-weight: 800; color: #0284c7; }
.swagger-ui .btn.authorize { background-color: #0284c7; border-color: #0284c7; color: #fff; }
.swagger-ui .btn.authorize svg { fill: #fff; }
"""


@app.get("/docs", include_in_schema=False)
@app.get("/api-docs", include_in_schema=False)
@app.get("/", include_in_schema=False)
async def custom_swagger_ui_html():
    html_res = get_swagger_ui_html(
        openapi_url="/api/v1/openapi.json",
        title="BigYellow Beacon – Crew Safety & Wellbeing API Explorer",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )
    custom_style = f"<style>{SWAGGER_CUSTOM_CSS}</style></head>"
    modified = html_res.body.decode("utf-8").replace("</head>", custom_style)
    return HTMLResponse(content=modified)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, reload=DEBUG)

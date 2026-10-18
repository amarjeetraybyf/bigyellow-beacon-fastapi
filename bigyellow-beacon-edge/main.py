"""
BigYellow Beacon – Edge Server (FastAPI)
Runs shipboard on mini-PC / Jetson LAN.
Zero persistent internet dependency.
"""

from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import PORT, HOST, DEBUG, VESSEL_ID, ROOT_PATH
from db import init_edge_db
from middleware.idempotency import IdempotencyMiddleware
from routes.auth_routes import router as auth_router
from routes.reports_routes import router as reports_router
from routes.master_routes import router as master_router
from routes.ticket_messages_routes import router as ticket_messages_router
from routes.inbox_routes import router as inbox_router
from routes.events_routes import router as events_router
from routes.checkin_routes import router as checkin_router
import asyncio
from routes.learnings_routes import router as learnings_router
from routes.sync_routes import router as sync_router
from services.sync_worker import sync_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_edge_db()
    print("================================================================")
    print(f"⚓ BigYellow Beacon Edge Server Running for Vessel: {VESSEL_ID}")
    print(f"📡 Local Port: http://{HOST}:{PORT}")
    print(f"📄 Swagger UI: http://{HOST}:{PORT}{ROOT_PATH}/docs")
    print("================================================================")
    # Launch autonomous Edge-to-Shore sync engine in background
    sync_task = asyncio.create_task(sync_worker.run_forever())
    yield
    sync_task.cancel()
    try:
        await sync_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title=f"BigYellow Beacon Edge ({VESSEL_ID})",
    version="2.0.0",
    description="Shipboard edge safety intelligence server operating with 0 persistent internet.",
    root_path=ROOT_PATH,
    lifespan=lifespan,
)


# Exception handler returning uniform { "error": "...", ... }
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})


# Idempotency Middleware for all edge writes
app.add_middleware(IdempotencyMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Static Files for Uploads and Media
UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
app.mount("/media", StaticFiles(directory=str(UPLOADS_DIR)), name="media")

# Mount Routes both at root and with /api/v1 prefix for direct Lovable SDK compatibility
edge_routers = (
    auth_router,
    reports_router,
    master_router,
    ticket_messages_router,
    inbox_router,
    events_router,
    checkin_router,
    learnings_router,
    sync_router,
)
for r in edge_routers:
    app.include_router(r)
    app.include_router(r, prefix="/api/v1")


@app.get("/edge/docs", include_in_schema=False)
@app.get("/edge", include_in_schema=False)
def edge_docs_redirect():
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["System"])
@app.get("/healthz", tags=["System"])
def edge_health():
    return {
        "status": "online",
        "plane": "edge",
        "vessel_id": VESSEL_ID,
        "engine": "FastAPI + SQLite/Postgres (SQLAlchemy 2)",
    }

"""
BigYellow Beacon – Edge Server (FastAPI)
Runs shipboard on mini-PC / Jetson LAN.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from edge.config import PORT, HOST, DEBUG, VESSEL_ID
from edge.db import init_edge_db
from edge.middleware.idempotency import IdempotencyMiddleware
from edge.routes.auth_routes import router as auth_router
from edge.routes.reports_routes import router as reports_router
from edge.routes.master_routes import router as master_router
from edge.routes.ticket_messages_routes import router as ticket_messages_router
from edge.routes.inbox_routes import router as inbox_router
from edge.routes.events_routes import router as events_router
from edge.routes.checkin_routes import router as checkin_router
from edge.routes.learnings_routes import router as learnings_router
from edge.routes.sync_routes import router as sync_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_edge_db()
    print("================================================================")
    print(f"⚓ BigYellow Beacon Edge Server Running for Vessel: {VESSEL_ID}")
    print(f"📡 Local Port: http://{HOST}:{PORT}")
    print("================================================================")
    yield


app = FastAPI(
    title=f"BigYellow Beacon Edge ({VESSEL_ID})",
    version="2.0.0",
    description="Shipboard edge safety intelligence server operating with 0 persistent internet.",
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

# Mount Routes
app.include_router(auth_router)
app.include_router(reports_router)
app.include_router(master_router)
app.include_router(ticket_messages_router)
app.include_router(inbox_router)
app.include_router(events_router)
app.include_router(checkin_router)
app.include_router(learnings_router)
app.include_router(sync_router)


@app.get("/health", tags=["System"])
def edge_health():
    return {
        "status": "online",
        "plane": "edge",
        "vessel_id": VESSEL_ID,
        "engine": "FastAPI + SQLite/Postgres (SQLAlchemy 2)",
    }

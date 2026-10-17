"""
BigYellow Beacon – Shore Server (FastAPI for Cloud Run)
Ashore intelligence plane connecting fleet vessels and safety management.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from shore.config import PORT, HOST, DEBUG
from shore.db import init_shore_db
from shore.routes.auth_routes import router as auth_router
from shore.routes.learnings_routes import router as learnings_router
from shore.routes.tickets_routes import router as tickets_router
from shore.routes.fleet_routes import router as fleet_router
from shore.routes.sync_routes import router as sync_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_shore_db()
    print("================================================================")
    print("🏢 BigYellow Beacon Shore Server Running (Cloud Run)")
    print(f"📡 Local Port: http://{HOST}:{PORT}")
    print("================================================================")
    yield


app = FastAPI(
    title="BigYellow Beacon Shore API",
    version="2.0.0",
    description="Ashore safety intelligence copilot & fleet management server.",
    lifespan=lifespan,
)

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(tickets_router)
app.include_router(learnings_router)
app.include_router(fleet_router)
app.include_router(sync_router)


@app.get("/healthz", tags=["System"])
def shore_healthz():
    """Health check endpoint probed by edge sync worker."""
    return {"status": "ok", "plane": "shore"}

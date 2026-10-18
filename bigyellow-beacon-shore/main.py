"""
BigYellow Beacon – Shore Server (FastAPI for Cloud Run / AWS ECS)
Ashore intelligence plane connecting fleet vessels and safety management.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import PORT, HOST, DEBUG, ROOT_PATH
from db import init_shore_db
from logging_config import logger, RequestLoggingMiddleware
from routes.auth_routes import router as auth_router
from routes.learnings_routes import router as learnings_router
from routes.tickets_routes import router as tickets_router
from routes.fleet_routes import router as fleet_router
from routes.sync_routes import router as sync_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_shore_db()
    logger.info("================================================================")
    logger.info("🏢 BigYellow Beacon Shore Server Running (Cloud Run)")
    logger.info(f"📡 Port: http://{HOST}:{PORT}")
    logger.info(f"📖 Swagger Docs: http://{HOST}:{PORT}{ROOT_PATH}/docs")
    logger.info("================================================================")
    yield


app = FastAPI(
    title="BigYellow Beacon Shore API",
    version="2.0.0",
    description="Ashore safety intelligence copilot & fleet management server.",
    root_path=ROOT_PATH,
    lifespan=lifespan,
)


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    logger.warning(f"HTTP {exc.status_code} on {request.method} {request.url.path}: {exc.detail}")
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})


# Middleware execution order: RequestLoggingMiddleware logs processed requests inside CORS
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8080",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:[0-9]+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers both at root and with /api/v1 prefix for direct Lovable SDK compatibility
for r in (auth_router, tickets_router, learnings_router, fleet_router, sync_router):
    app.include_router(r)
    app.include_router(r, prefix="/api/v1")


@app.get("/shore/docs", include_in_schema=False)
@app.get("/shore", include_in_schema=False)
def shore_docs_redirect():
    return RedirectResponse(url="/docs")


@app.get("/healthz", tags=["System"])
def shore_healthz():
    """Health check endpoint probed by edge sync worker and load balancers."""
    return {"status": "ok", "plane": "shore"}


@app.get("/", tags=["System"])
def shore_root():
    return {
        "service": "BigYellow Beacon Shore API",
        "plane": "shore",
        "version": "2.0.0",
        "docs": f"{ROOT_PATH}/docs",
    }

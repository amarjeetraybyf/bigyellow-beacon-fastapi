"""
BigYellow Beacon – Monorepo Unified Gateway
Mounts both Edge server (vessel local) and Shore API (Cloud Run).
Run independently:
  - Edge server:  uvicorn edge.main:app --port 8000 --reload
  - Shore server: uvicorn shore.main:app --port 8001 --reload
"""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from edge.main import app as edge_app
from shore.main import app as shore_app

app = FastAPI(
    title="BigYellow Beacon Monorepo Gateway",
    version="2.0.0",
    description="Unified gateway for shipboard Edge and Cloud Run Shore plane.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Edge and Shore applications under subpaths for single-process local evaluation
app.mount("/edge", edge_app)
app.mount("/shore", shore_app)


@app.get("/")
def gateway_root():
    return {
        "title": "BigYellow Beacon – Crew Safety & Wellbeing Intelligence",
        "version": "2.0.0",
        "architecture": "Monorepo (contract/, edge/, shore/)",
        "servers": {
            "edge": {
                "description": "Shipboard edge safety server (offline, zero-internet resilient)",
                "subpath": "/edge",
                "docs": "/edge/docs",
                "standalone_command": "uvicorn edge.main:app --port 8000 --reload",
            },
            "shore": {
                "description": "Shore-api Cloud Run server (fleet management & Gemini copilot)",
                "subpath": "/shore",
                "docs": "/shore/docs",
                "standalone_command": "uvicorn shore.main:app --port 8001 --reload",
            },
        },
    }

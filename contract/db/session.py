"""
Database Session and Engine Factory for SQLAlchemy 2.
Supports PostgreSQL 16 (with pgvector) and SQLite fallback.
"""

import os
from pathlib import Path
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from contract.db.models import Base


def get_database_url(default_db_name: str = "beacon_edge") -> str:
    # Check per-plane environment variables first
    clean_name = default_db_name.replace(".db", "")
    if "shore" in clean_name:
        url = os.getenv("SHORE_DATABASE_URL")
        if url:
            return url.replace("postgres://", "postgresql://", 1)
    elif "edge" in clean_name:
        url = os.getenv("EDGE_DATABASE_URL")
        if url:
            return url.replace("postgres://", "postgresql://", 1)

    db_url = os.getenv("DATABASE_URL")
    if db_url:
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        return db_url

    # Default to PostgreSQL 16
    return f"postgresql://postgres:postgres@localhost:5432/{clean_name}"


def create_db_engine(database_url: str):
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(database_url, connect_args=connect_args, echo=False)


def init_db(engine):
    """Create all tables if they don't already exist."""
    Base.metadata.create_all(bind=engine)

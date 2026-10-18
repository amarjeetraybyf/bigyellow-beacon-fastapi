"""
Shore Database Engine and Session Dependency
Supports PostgreSQL 16 (with pgvector) and SQLite fallback for local test/dev.
"""

import os
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from db_models import Base
from config import DATABASE_URL, BASE_DIR


def create_shore_engine(db_url: str):
    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if db_url.startswith("sqlite:///") and not db_url.startswith("sqlite:////") and db_url != "sqlite:///:memory:":
            rel_name = db_url.replace("sqlite:///", "")
            db_url = f"sqlite:///{BASE_DIR / rel_name}"
    elif db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    try:
        eng = create_engine(db_url, connect_args=connect_args, echo=False)
        # Probe connection
        with eng.connect() as conn:
            pass
        return eng
    except Exception:
        # Fallback to local SQLite if PostgreSQL cannot connect
        fallback_url = f"sqlite:///{BASE_DIR / 'shore_local.db'}"
        return create_engine(fallback_url, connect_args={"check_same_thread": False}, echo=False)


engine = create_shore_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_shore_db():
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

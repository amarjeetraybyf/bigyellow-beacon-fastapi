"""
SQLAlchemy 2 Models for BigYellow Beacon Shore Server (Cloud Run / AWS)
Contains Shore-only entities + Synced fleet entities.
Confidential edge data (private checkins, PIN hashes, crew devices) NEVER exist in this schema.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Text,
    JSON,
    Index,
)
from sqlalchemy.orm import declarative_base

from contract.uuid7 import uuid7_str


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


Base = declarative_base()


class SyncedMixin:
    id = Column(String(36), primary_key=True, default=uuid7_str)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)
    origin = Column(String(10), default="edge", nullable=False)
    version = Column(Integer, default=1, nullable=False)


# ─── SYNCED ENTITIES ──────────────────────────────────────────────────────────

class TicketModel(Base, SyncedMixin):
    __tablename__ = "tickets"

    vessel_id = Column(String(64), nullable=False, index=True)
    reporter_ref = Column(String(64), nullable=False, index=True)  # HMAC-SHA256
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    location_on_board = Column(String(255), nullable=False)
    hazard_type = Column(String(255), nullable=False)
    ism_category = Column(String(255), nullable=False)
    severity = Column(Integer, nullable=False)  # 1 - 5
    immediate_action = Column(Text, nullable=False)
    onboard_status = Column(String(32), default="NEW", nullable=False)  # Edge-owned
    shore_status = Column(String(32), default="NEW", nullable=False)    # Shore-owned
    assigned_action = Column(JSON, nullable=True)
    media_refs = Column(JSON, default=list, nullable=False)


class TicketMessageModel(Base, SyncedMixin):
    __tablename__ = "ticket_messages"

    ticket_id = Column(String(36), nullable=False, index=True)
    sender_role = Column(String(32), nullable=False)  # crew | shore_reviewer
    text = Column(Text, nullable=False)


class LearningModel(Base, SyncedMixin):
    __tablename__ = "learnings"

    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    status = Column(String(32), default="DRAFT", nullable=False)  # DRAFT, PUBLISHED, RETRACTED
    media = Column(JSON, default=list, nullable=False)
    target_vessel_ids = Column(JSON, default=list, nullable=False)


class DailyAnchorCardModel(Base, SyncedMixin):
    __tablename__ = "daily_anchor_cards"

    topic = Column(String(255), nullable=False)
    quote_or_prompt = Column(Text, nullable=False)
    translations = Column(JSON, default=dict, nullable=False)
    scheduled_date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD


class CheckinAggregateModel(Base, SyncedMixin):
    __tablename__ = "checkin_aggregates"

    vessel_id = Column(String(64), nullable=False, index=True)
    ship_date = Column(String(10), nullable=False, index=True)
    department = Column(String(64), nullable=False)
    count = Column(Integer, nullable=False)  # Enforce count >= 5
    avg_mood = Column(Float, nullable=False)
    avg_fatigue = Column(Float, nullable=False)
    avg_workload = Column(Float, nullable=False)


class VesselModel(Base):
    __tablename__ = "vessels"

    id = Column(String(64), primary_key=True)  # e.g. MV-PACIFIC-STAR
    name = Column(String(255), nullable=False)
    imo_number = Column(String(32), nullable=False)
    api_key = Column(String(128), nullable=False)  # HMAC shared secret
    connection_status = Column(String(32), default="OFFLINE_AIRPLANE", nullable=False)
    last_sync_timestamp = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


# ─── SHORE-SPECIFIC ENTITIES ─────────────────────────────────────────────────

class ShoreUserModel(Base):
    """
    Shore staff accounts (DPA, shore_reviewer, shore_admin).
    """
    __tablename__ = "shore_users"

    id = Column(String(36), primary_key=True, default=uuid7_str)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(32), default="shore_reviewer", nullable=False)  # shore_reviewer | shore_admin
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class ProcessedEventModel(Base):
    """
    Shore deduplication log for SyncDelta events pushed from vessels.
    """
    __tablename__ = "processed_events"

    event_id = Column(String(36), primary_key=True)
    vessel_id = Column(String(64), nullable=False, index=True)
    processed_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class PullCursorModel(Base):
    """
    Shore change log stream for vessel GET /sync/pull.
    """
    __tablename__ = "pull_changes"

    cursor = Column(Integer, primary_key=True, autoincrement=True)
    change_id = Column(String(36), default=uuid7_str, nullable=False)
    vessel_id = Column(String(64), nullable=True, index=True)  # Null = broadcast to all fleet
    change_type = Column(String(64), nullable=False)
    aggregate_id = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class AccidentCorpusModel(Base):
    """
    Shore precedent accident investigation corpus (MAIB, IMO, USCG, BEA-MER).
    """
    __tablename__ = "accident_corpus"

    id = Column(String(64), primary_key=True)
    source = Column(String(32), nullable=False)  # MAIB, IMO, USCG, BEA-MER
    report_code = Column(String(64), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    year = Column(Integer, nullable=False)
    vessel_type = Column(String(64), nullable=False)
    hazard_domain = Column(String(128), nullable=False)
    precursor_pattern = Column(Text, nullable=False)
    casualty_outcome = Column(Text, nullable=False)
    key_safety_action = Column(Text, nullable=False)
    similarity_keywords = Column(JSON, default=list, nullable=False)
    embedding = Column(JSON, nullable=True)  # List[float] vector or JSON


class ShoreAuditLogModel(Base):
    """
    Shore audit trail for accept, reject, reply, publish, retract.
    """
    __tablename__ = "shore_audit_log"

    id = Column(String(36), primary_key=True, default=uuid7_str)
    action = Column(String(64), nullable=False)
    user_id = Column(String(36), nullable=False)
    target_id = Column(String(64), nullable=False)
    details = Column(JSON, default=dict, nullable=False)
    timestamp = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

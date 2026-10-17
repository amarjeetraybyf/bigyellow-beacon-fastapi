"""
SQLAlchemy 2 Models for BigYellow Beacon Edge Server (Shipboard Mini-PC / Jetson)
Contains Edge-only confidential entities + Synced fleet entities.
Shore-only entities (staff credentials, accident corpus, pull cursors) DO NOT exist on edge.
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
    UniqueConstraint,
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


# ─── EDGE-SPECIFIC CONFIDENTIAL ENTITIES ─────────────────────────────────────

class CheckinModel(Base):
    """
    Edge-only strictly confidential checkins.
    Transcripts NEVER leave memory and are NOT stored here.
    Unique constraint on (crew_id, ship_date).
    """
    __tablename__ = "checkins"

    id = Column(String(36), primary_key=True, default=uuid7_str)
    crew_id = Column(String(64), nullable=False, index=True)
    vessel_id = Column(String(64), nullable=False, index=True)
    ship_date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    mode = Column(String(16), default="quick", nullable=False)  # quick | chat
    mood = Column(Integer, nullable=False)      # 1 - 5
    fatigue = Column(Integer, nullable=True)   # 1 - 5
    workload = Column(Integer, nullable=True)  # 1 - 5
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("crew_id", "ship_date", name="uq_checkin_crew_date"),
    )


class WelfareRequestModel(Base):
    """
    Edge-only consented welfare request to the Master.
    Never synced to shore.
    """
    __tablename__ = "welfare_requests"

    id = Column(String(36), primary_key=True, default=uuid7_str)
    vessel_id = Column(String(64), nullable=False, index=True)
    crew_id = Column(String(64), nullable=False)
    crew_name = Column(String(255), nullable=False)
    requested_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    acknowledged = Column(Boolean, default=False, nullable=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)


class OutboxModel(Base):
    """
    Edge-only outbox queue for SyncDelta push to Shore.
    Written in the same database transaction as the business entity.
    """
    __tablename__ = "outbox"

    id = Column(String(36), primary_key=True, default=uuid7_str)
    event_type = Column(String(64), nullable=False)
    aggregate_type = Column(String(64), nullable=False)
    aggregate_id = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    severity = Column(Integer, default=3, nullable=False)  # 1-5, priority order
    synced = Column(Boolean, default=False, nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    synced_at = Column(DateTime(timezone=True), nullable=True)


class DeviceModel(Base):
    """
    Edge-only paired crew devices with Argon2 hashed PINs and lockout tracking.
    """
    __tablename__ = "devices"

    id = Column(String(36), primary_key=True, default=uuid7_str)
    token = Column(String(128), unique=True, nullable=False, index=True)
    vessel_id = Column(String(64), nullable=False)
    department = Column(String(64), nullable=False)
    crew_id = Column(String(64), nullable=False, index=True)
    crew_name = Column(String(255), nullable=False)
    hashed_pin = Column(String(255), nullable=False)  # Argon2 hash
    failed_login_attempts = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    preferred_language = Column(String(10), default="en", nullable=False)
    notifications_enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class PairingCodeModel(Base):
    """
    Edge-only temporary pairing codes generated by Master.
    """
    __tablename__ = "pairing_codes"

    code = Column(String(32), primary_key=True)
    vessel_id = Column(String(64), nullable=False)
    created_by = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, default=False, nullable=False)


class CrewAccountModel(Base):
    """
    Crew member accounts created by Master or seeded on edge.
    Supports username + password / PIN login.
    """
    __tablename__ = "crew_accounts"

    id = Column(String(36), primary_key=True, default=uuid7_str)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(128), nullable=False)
    rank = Column(String(64), nullable=False)
    rank_band = Column(String(32), default="rating", nullable=False)  # officer | rating
    role = Column(String(32), default="crew", nullable=False)          # crew | master
    language = Column(String(10), default="en", nullable=False)
    vessel_id = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    created_by = Column(String(64), nullable=True)


class InboxMessageModel(Base):
    """
    Edge-only crew inbox notices and notifications.
    """
    __tablename__ = "inbox_messages"

    id = Column(String(36), primary_key=True, default=uuid7_str)
    recipient_crew_id = Column(String(64), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

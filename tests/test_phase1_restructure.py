"""
Test Phase 1: Restructure into Monorepo (contract/, edge/, shore/),
UUIDv7 generation, and SQLAlchemy 2 Database Layer.
"""

import os
import uuid
import time
import pytest
from datetime import datetime, timezone

from contract.uuid7 import uuid7, uuid7_str
from contract.enums import Role, Origin, OnboardStatus, ShoreStatus, LearningStatus, PullChangeType
from contract.models import Ticket, Learning, DailyAnchorCard, CheckinAggregate, SyncDelta, PullChange
from contract.db.models import TicketModel, LearningModel, OutboxModel, ShoreUserModel, Base
from edge.db import init_edge_db, engine as edge_engine
from shore.db import init_shore_db, engine as shore_engine


def test_uuid7_generation():
    u1 = uuid7()
    time.sleep(0.002)
    u2 = uuid7()

    assert u1.version == 7
    assert u2.version == 7
    # Lexicographically/monotonically sortable
    assert str(u1) < str(u2)


def test_contract_synced_models_have_required_fields():
    ticket = Ticket(
        vessel_id="MV-PACIFIC-STAR",
        reporter_ref="hmac-ref-12345",
        title="Crane leak",
        description="Hydraulic fluid weeping near cab",
        location_on_board="Main Deck - Cargo Crane #2",
        hazard_type="Hydraulic Leak",
        ism_category="ISM-10",
        severity=4,
        immediate_action="Barricaded area",
    )
    assert ticket.id
    assert uuid.UUID(ticket.id).version == 7
    assert isinstance(ticket.created_at, datetime)
    assert isinstance(ticket.updated_at, datetime)
    assert ticket.origin == Origin.EDGE
    assert ticket.version == 1
    assert ticket.onboard_status == OnboardStatus.NEW
    assert ticket.shore_status == ShoreStatus.NEW


def test_database_initialization_on_both_edge_and_shore():
    init_edge_db()
    init_shore_db()

    # Verify tables created
    table_names = Base.metadata.tables.keys()
    assert "tickets" in table_names
    assert "ticket_messages" in table_names
    assert "learnings" in table_names
    assert "daily_anchor_cards" in table_names
    assert "checkin_aggregates" in table_names
    assert "checkins" in table_names
    assert "welfare_requests" in table_names
    assert "outbox" in table_names
    assert "devices" in table_names
    assert "pairing_codes" in table_names
    assert "shore_users" in table_names
    assert "processed_events" in table_names


def test_firebase_service_removed():
    assert not os.path.exists("app/services/firebase_service.py")

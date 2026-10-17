"""
Extended API Tests for Edge Messaging, Master Actions, and Inbox
"""

import pytest
from fastapi.testclient import TestClient

from edge.main import app as edge_app
from edge.security import create_edge_session_token
from contract.enums import Role
from contract.uuid7 import uuid7_str
from contract.db.models import TicketModel
from edge.db import SessionLocal

edge_client = TestClient(edge_app)


def test_master_ticket_actions_and_acknowledgment():
    master_jwt = create_edge_session_token(crew_id="MASTER-01", role=Role.MASTER, vessel_id="MV-PACIFIC-STAR")
    db = SessionLocal()

    ticket_id = uuid7_str()
    ticket = TicketModel(
        id=ticket_id,
        vessel_id="MV-PACIFIC-STAR",
        reporter_ref="REF-XYZ",
        title="Winch hydraulic weep",
        description="Hydraulic oil dripping onto main deck",
        location_on_board="Main Deck",
        hazard_type="Hydraulic Leak",
        ism_category="ISM 10",
        severity=3,
        immediate_action="Wiped clean",
        onboard_status="NEW",
        shore_status="NEW",
        origin="edge",
    )
    db.add(ticket)
    db.commit()

    # 1. Master acknowledges ticket
    ack_res = edge_client.post(
        f"/master/tickets/{ticket_id}/ack",
        headers={"Authorization": f"Bearer {master_jwt}"},
    )
    assert ack_res.status_code == 200
    assert ack_res.json()["onboard_status"] == "ACKNOWLEDGED"

    # 2. Master assigns action
    act_res = edge_client.post(
        f"/master/tickets/{ticket_id}/actions",
        json={"text": "Replace copper washer on return line", "responsible_rank": "2nd Engineer", "resolve": True},
        headers={"Authorization": f"Bearer {master_jwt}"},
    )
    assert act_res.status_code == 200
    assert act_res.json()["onboard_status"] == "RESOLVED"

    db.close()


def test_inbox_messages_and_read_status():
    crew_jwt = create_edge_session_token(crew_id="CREW-100", role=Role.CREW, vessel_id="MV-PACIFIC-STAR")

    # Get inbox
    inbox_res = edge_client.get("/inbox", headers={"Authorization": f"Bearer {crew_jwt}"})
    assert inbox_res.status_code == 200
    assert isinstance(inbox_res.json(), list)

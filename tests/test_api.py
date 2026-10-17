"""
API End-to-End Tests for BigYellow Beacon Refactored API
Verifies Edge and Shore endpoints conform to BigYellow Beacon POC Technical Design.
Legacy /api/* routes have been removed in favor of modular edge and shore planes.
"""

import pytest
from fastapi.testclient import TestClient

from edge.main import app as edge_app
from shore.main import app as shore_app
from edge.security import create_edge_session_token
from shore.security import create_shore_access_token
from contract.enums import Role

edge_client = TestClient(edge_app)
shore_client = TestClient(shore_app)


def test_edge_and_shore_health_endpoints():
    edge_res = edge_client.get("/health")
    assert edge_res.status_code == 200
    assert edge_res.json()["plane"] == "edge"

    shore_res = shore_client.get("/healthz")
    assert shore_res.status_code == 200
    assert shore_res.json()["plane"] == "shore"


def test_edge_master_routes_require_master_role():
    crew_jwt = create_edge_session_token(crew_id="CREW-01", role=Role.CREW, vessel_id="MV-PACIFIC-STAR")
    master_jwt = create_edge_session_token(crew_id="MASTER-01", role=Role.MASTER, vessel_id="MV-PACIFIC-STAR")

    # Crew forbidden from /master/brief
    res_crew = edge_client.get("/master/brief", headers={"Authorization": f"Bearer {crew_jwt}"})
    assert res_crew.status_code == 403

    # Master authorized
    res_master = edge_client.get("/master/brief", headers={"Authorization": f"Bearer {master_jwt}"})
    assert res_master.status_code == 200
    assert "brief" in res_master.json()


def test_edge_daily_anchor_today():
    crew_jwt = create_edge_session_token(crew_id="CREW-01", role=Role.CREW, vessel_id="MV-PACIFIC-STAR")
    res = edge_client.get("/anchor/today", headers={"Authorization": f"Bearer {crew_jwt}"})
    assert res.status_code == 200
    card = res.json()
    assert "topic" in card
    assert "quote_or_prompt" in card


def test_shore_vessels_and_clusters():
    admin_jwt = create_shore_access_token(user_id="ADMIN-01", email="admin@beacon.com", role=Role.SHORE_ADMIN)
    vessels_res = shore_client.get("/vessels", headers={"Authorization": f"Bearer {admin_jwt}"})
    assert vessels_res.status_code == 200
    assert isinstance(vessels_res.json(), list)

    clusters_res = shore_client.get("/fleet/clusters", headers={"Authorization": f"Bearer {admin_jwt}"})
    assert clusters_res.status_code == 200
    assert "clusters" in clusters_res.json()

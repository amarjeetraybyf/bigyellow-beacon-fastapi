"""
Shore Fleet Intelligence Routes
Endpoints:
- GET /vessels (Fleet vessel directory)
- GET /fleet/overview (Fleet-wide KPI, severity breakdown, k>=5 aggregated welfare)
- GET /fleet/clusters (Recurring hazard clusters across fleet)
- GET /similar (Global similarity search over accident corpus & tickets)
"""

from typing import List, Dict, Any, Optional
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from contract.enums import Role
from contract.db.models import (
    VesselModel,
    TicketModel,
    CheckinAggregateModel,
)
from shore.db import get_db
from shore.security import require_shore_role
from shore.services.similarity_service import find_similar_precedents

router = APIRouter(tags=["Fleet Intelligence"])


@router.get("/vessels")
def list_vessels(
    user: Dict[str, Any] = Depends(require_shore_role(Role.SHORE_REVIEWER, Role.SHORE_ADMIN)),
    db: Session = Depends(get_db),
):
    vessels = db.query(VesselModel).all()
    return [
        {
            "id": v.id,
            "name": v.name,
            "imo_number": v.imo_number,
            "connection_status": v.connection_status,
            "last_sync_timestamp": v.last_sync_timestamp.isoformat() if v.last_sync_timestamp else None,
        }
        for v in vessels
    ]


@router.get("/fleet/overview")
def get_fleet_overview(
    user: Dict[str, Any] = Depends(require_shore_role(Role.SHORE_REVIEWER, Role.SHORE_ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Fleet-wide KPI overview:
    - Tickets breakdown by severity and status
    - Welfare aggregates strictly enforcing k>=5 privacy
    """
    total_tickets = db.query(TicketModel).count()
    tickets = db.query(TicketModel).all()

    sev_breakdown = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    hazard_breakdown = defaultdict(int)
    shore_status_breakdown = defaultdict(int)

    for t in tickets:
        if t.severity in sev_breakdown:
            sev_breakdown[t.severity] += 1
        hazard_breakdown[t.hazard_type or "Unspecified"] += 1
        shore_status_breakdown[t.shore_status or "NEW"] += 1

    # Aggregated welfare stats strictly requiring count >= 5
    welfare_query = db.query(CheckinAggregateModel).filter(CheckinAggregateModel.count >= 5).all()
    
    total_welfare_reports = sum(w.count for w in welfare_query)
    avg_mood = (
        round(sum(w.avg_mood * w.count for w in welfare_query) / total_welfare_reports, 2)
        if total_welfare_reports > 0
        else None
    )
    avg_fatigue = (
        round(sum(w.avg_fatigue * w.count for w in welfare_query) / total_welfare_reports, 2)
        if total_welfare_reports > 0
        else None
    )
    avg_workload = (
        round(sum(w.avg_workload * w.count for w in welfare_query) / total_welfare_reports, 2)
        if total_welfare_reports > 0
        else None
    )

    return {
        "fleet_kpis": {
            "total_incidents_reported": total_tickets,
            "severity_distribution": sev_breakdown,
            "top_hazards": sorted(hazard_breakdown.items(), key=lambda x: x[1], reverse=True)[:5],
            "shore_review_queue": dict(shore_status_breakdown),
        },
        "fleet_welfare_k_anonymized": {
            "eligible_cohorts_k_gte_5": len(welfare_query),
            "total_crew_checkin_samples": total_welfare_reports,
            "average_mood": avg_mood,
            "average_fatigue": avg_fatigue,
            "average_workload": avg_workload,
        },
    }


@router.get("/fleet/clusters")
def get_fleet_clusters(
    user: Dict[str, Any] = Depends(require_shore_role(Role.SHORE_REVIEWER, Role.SHORE_ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Identifies systemic clusters and recurring risk patterns across ships.
    """
    tickets = db.query(TicketModel).all()
    clusters = defaultdict(list)

    for t in tickets:
        key = (t.hazard_type or "General").strip().title()
        clusters[key].append({
            "ticket_id": t.id,
            "vessel_id": t.vessel_id,
            "severity": t.severity,
            "title": t.title,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })

    cluster_list = []
    for hazard, items in clusters.items():
        high_sev_count = sum(1 for x in items if x["severity"] >= 4)
        cluster_list.append({
            "hazard_cluster": hazard,
            "incident_count": len(items),
            "critical_or_high_severity_count": high_sev_count,
            "vessels_affected": list(set(x["vessel_id"] for x in items)),
            "recent_incidents": items[:3],
        })

    cluster_list.sort(key=lambda c: (c["critical_or_high_severity_count"], c["incident_count"]), reverse=True)
    return {"clusters": cluster_list}


@router.get("/similar")
def search_similar(
    query: str = Query(..., min_length=2),
    limit: int = Query(5, ge=1, le=20),
    user: Dict[str, Any] = Depends(require_shore_role(Role.SHORE_REVIEWER, Role.SHORE_ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Precedent search over historical investigation corpus (MAIB, IMO, USCG, BEA-MER) + Tickets.
    """
    return find_similar_precedents(db, query, limit=limit)

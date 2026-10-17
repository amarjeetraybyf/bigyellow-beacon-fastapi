"""
Shore Management Dashboard Routes
  - /fleet/*        (role: shore_safety, admin)
  - /escalations/*  (role: shore_psych, admin)
  - /corpus/*       (role: shore_safety, admin)
"""

import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query, status

from app.core.auth import require_role, AuthUser
from app.models.schemas import (
    FleetAlertCreateRequest,
    EscalationPatchRequest,
    CorpusCaseCreateRequest,
    AskRequest,
)
from app.core.beacon_core import aggregate_with_k_anonymity
from app.db.extended_db import (
    get_all_near_misses_with_status,
    add_fleet_alert,
    get_fleet_alerts,
    search_corpus,
    save_monthly_report,
    get_latest_monthly_report,
)
from app.db.local_db import beacon_db, get_utc_now_iso
from app.services.ai_service import get_ai_client, is_gemini_live
from app.services.firebase_service import sync_document_to_firestore

router = APIRouter(tags=["Shore Management (Fleet DPA & Welfare)"])


# ─── FLEET SUB-ROUTER (/fleet/*) ─────────────────────────────────────────────

fleet_router = APIRouter(prefix="/fleet")


@fleet_router.get("/overview")
def get_fleet_overview(user: AuthUser = Depends(require_role("shore_safety", "admin"))):
    """Fleet bird's-eye view with risk levels and active cluster counts."""
    vessels = beacon_db.get_vessels()
    clusters = beacon_db.get_clusters()
    all_near_misses = get_all_near_misses_with_status()

    summary = []
    for v in vessels:
        vid = v.get("id")
        open_count = len([
            n for n in all_near_misses
            if n.get("vesselId") == vid and n.get("status") != "CLOSED"
        ])
        is_high_risk = any(
            vid in c.get("affectedVesselIds", []) and c.get("severity") == "High"
            for c in clusters
        )
        summary.append({
            "vesselId": vid,
            "vesselName": v.get("name"),
            "flag": v.get("flag"),
            "crewCount": v.get("crewCount"),
            "connectionStatus": v.get("connectionStatus"),
            "lastSyncTimestamp": v.get("lastSyncTimestamp"),
            "currentLocationDesc": v.get("currentLocationDesc"),
            "distanceFromNearestCellTowerNm": v.get("distanceFromNearestCellTowerNm"),
            "openNearMisses": open_count,
            "riskLevel": "HIGH" if is_high_risk else "MODERATE",
        })

    return {
        "totalVessels": len(vessels),
        "totalActiveClusters": len(clusters),
        "totalOpenNearMisses": len([n for n in all_near_misses if n.get("status") != "CLOSED"]),
        "vessels": summary,
        "generatedAt": get_utc_now_iso(),
    }


@fleet_router.get("/vessels/{vessel_id}")
def get_fleet_vessel_drilldown(
    vessel_id: str,
    user: AuthUser = Depends(require_role("shore_safety", "admin"))
):
    """Single vessel drilldown with wellbeing statistics and master brief."""
    vessel = beacon_db.get_vessel(vessel_id)
    if not vessel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Vessel not found"}
        )

    near_misses = beacon_db.get_near_misses(vessel_id)
    checkins = beacon_db.get_check_ins(vessel_id)
    brief = beacon_db.get_master_brief(vessel_id)
    aggregated = aggregate_with_k_anonymity(checkins, 5)

    return {
        **vessel,
        "nearMissCount": len(near_misses),
        "wellbeingOverview": aggregated.get("vesselOverall"),
        "brief": brief,
    }


@fleet_router.get("/near-misses")
def list_fleet_near_misses(
    vesselId: Optional[str] = Query(None),
    hazard_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    user: AuthUser = Depends(require_role("shore_safety", "admin"))
):
    """Fleet-wide near-misses with multi-vessel filters."""
    results = get_all_near_misses_with_status()
    if vesselId:
        results = [n for n in results if n.get("vesselId") == vesselId]
    if hazard_type:
        results = [n for n in results if hazard_type.lower() in n.get("hazardType", "").lower()]
    if severity:
        results = [n for n in results if n.get("severity") == severity]
    if from_date:
        results = [n for n in results if n.get("timestamp", "") >= from_date]
    if to_date:
        results = [n for n in results if n.get("timestamp", "") <= to_date]

    return {"count": len(results), "results": results}


@fleet_router.get("/clusters")
def list_clusters(user: AuthUser = Depends(require_role("shore_safety", "admin"))):
    """Cross-vessel hazard clusters matched against precedent corpus."""
    return beacon_db.get_clusters()


@fleet_router.get("/clusters/{cluster_id}")
async def get_cluster_detail(
    cluster_id: str,
    user: AuthUser = Depends(require_role("shore_safety", "admin"))
):
    """Cluster detail with AI explanation and formal investigation citations."""
    cluster = beacon_db.get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Cluster not found"}
        )

    ai_client = get_ai_client()
    gemini_explanation = None
    if ai_client:
        try:
            matched = cluster.get("matchedCorpus", {})
            prompt = f"""You are a maritime safety analyst. Explain this cluster of incidents across sister vessels and cite the matched casualty report.
Cluster: {cluster.get('patternName')}
Severity: {cluster.get('severity')}
Affected vessels: {', '.join(cluster.get('affectedVesselIds', []))}
Matched report: {matched.get('reportCode')} – {matched.get('title')}
Precursor: {matched.get('precursorPattern')}
Risk synthesis: {cluster.get('riskSynthesis')}
Respond with 3 concise paragraphs: (1) what happened, (2) why it matters, (3) recommended fleet directive."""
            res = ai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            if res.text:
                gemini_explanation = res.text.strip()
        except Exception as e:
            print(f"[Shore] Cluster explanation AI error: {e}")

    matched_corpus = cluster.get("matchedCorpus", {})
    return {
        **cluster,
        "geminiExplanation": gemini_explanation or cluster.get("riskSynthesis"),
        "citations": [
            {
                "reportCode": matched_corpus.get("reportCode"),
                "source": matched_corpus.get("source"),
                "title": matched_corpus.get("title"),
                "year": matched_corpus.get("year"),
                "keySafetyAction": matched_corpus.get("keySafetyAction"),
            }
        ],
    }


@fleet_router.get("/wellbeing")
def get_fleet_wellbeing_matrix(user: AuthUser = Depends(require_role("shore_safety", "admin"))):
    """Per-vessel k-anonymity aggregate matrix across the fleet."""
    vessels = beacon_db.get_vessels()
    matrix = []
    for v in vessels:
        checkins = beacon_db.get_check_ins(v.get("id"))
        aggregated = aggregate_with_k_anonymity(checkins, 5)
        matrix.append({
            "vesselId": v.get("id"),
            "vesselName": v.get("name"),
            **aggregated,
        })

    return {
        "kThreshold": 5,
        "privacyNote": "Groups with fewer than 5 respondents are masked (k-anonymity enforced).",
        "fleet": matrix,
    }


@fleet_router.post("/alerts", status_code=status.HTTP_201_CREATED)
def push_fleet_alert(
    req: FleetAlertCreateRequest,
    user: AuthUser = Depends(require_role("shore_safety", "admin"))
):
    """Push safety alert down to specific vessels or entire fleet."""
    if not req.category or not req.message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "category and message are required"}
        )

    alert_id = f"ALERT-{hex(int(time.time()*1000))[2:].upper()}"
    alert = {
        "id": alert_id,
        "fromVesselId": None,
        "targetVesselIds": req.targetVesselIds or [],
        "severity": req.severity or "WARNING",
        "category": req.category,
        "message": req.message,
        "createdAt": get_utc_now_iso(),
    }
    add_fleet_alert(alert)
    sync_document_to_firestore("fleetAlerts", alert_id, alert)

    scope = ", ".join(req.targetVesselIds) if req.targetVesselIds else "ALL VESSELS"
    return {
        "alertId": alert_id,
        "scope": scope,
        "message": f"Alert dispatched to {scope}."
    }


@fleet_router.get("/reports/monthly")
async def get_monthly_executive_report(user: AuthUser = Depends(require_role("shore_safety", "admin"))):
    """Gemini-written monthly executive safety report with caching."""
    cached = get_latest_monthly_report()
    now = datetime.now(timezone.utc)
    current_month = now.strftime("%Y-%m")

    if cached and cached.get("month") == current_month:
        return cached

    ai_client = get_ai_client()
    if ai_client:
        try:
            vessels = beacon_db.get_vessels()
            clusters = beacon_db.get_clusters()
            all_near_misses = beacon_db.get_near_misses()
            cluster_summaries = "; ".join(c.get("patternName", "") for c in clusters[:3])

            prompt = f"""You are a senior maritime safety manager preparing an executive monthly fleet safety report for {current_month}.
Fleet: {len(vessels)} vessels, {len(all_near_misses)} near-miss reports, {len(clusters)} active hazard clusters.
Cluster summaries: {cluster_summaries}.
Write a professional 4-paragraph executive summary covering: (1) overall fleet safety status, (2) top hazard trends, (3) regulatory compliance posture (MLC 2006 / ISM Code), (4) recommended actions for next month."""

            res = ai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            content = res.text.strip() if res.text else ""
            report = {
                "id": f"RPT-{hex(int(time.time()*1000))[2:].upper()}",
                "month": current_month,
                "generatedAt": get_utc_now_iso(),
                "content": content,
                "model": "Gemini 3.8 Flash",
            }
            save_monthly_report(report)
            return report
        except Exception as e:
            print(f"[Shore] Monthly report AI error: {e}")

    # Deterministic fallback
    fallback_report = {
        "id": "RPT-FALLBACK",
        "month": current_month,
        "generatedAt": get_utc_now_iso(),
        "content": f"Fleet safety report for {current_month}: All vessels operating within MLC 2006 parameters. {len(beacon_db.get_clusters())} active cross-vessel hazard clusters require monitoring. Recommend monthly maintenance inspection of cargo crane hydraulics across all bulk carriers.",
        "model": "Beacon Maritime Engine (Fallback)",
    }
    return fallback_report


@fleet_router.post("/ask")
async def ask_fleet_analytics(
    req: AskRequest,
    user: AuthUser = Depends(require_role("shore_safety", "admin"))
):
    """Natural-language question answering across the entire fleet."""
    if not req.question:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "question is required"}
        )

    ai_client = get_ai_client()
    if ai_client:
        try:
            vessels = beacon_db.get_vessels()
            all_misses = beacon_db.get_near_misses()
            clusters = beacon_db.get_clusters()

            vessel_breakdown = ", ".join(f"{v.get('name')}: {len(beacon_db.get_near_misses(v.get('id')))}" for v in vessels)
            cluster_names = ", ".join(c.get("patternName", "") for c in clusters)

            context = f"""Fleet: {len(vessels)} vessels.
Near-misses: {len(all_misses)} total. By vessel: {vessel_breakdown}.
Active clusters: {cluster_names}."""

            prompt = f"""You are a maritime fleet safety analyst. Answer the following question using the fleet data below.
{context}
Question: {req.question}
Answer concisely and cite specific vessels or metrics where possible."""

            res = ai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            return {
                "question": req.question,
                "answer": res.text.strip() if res.text else "No response generated.",
                "model": "Gemini 3.8 Flash",
            }
        except Exception as e:
            print(f"[Shore] Fleet ask AI error: {e}")

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error": "Fleet analytics requires Gemini API key. Set GEMINI_API_KEY in .env."}
    )


# ─── ESCALATION SUB-ROUTER (/escalations/*) ──────────────────────────────────

escalation_router = APIRouter(prefix="/escalations")


@escalation_router.get("")
@escalation_router.get("/")
def list_escalations(user: AuthUser = Depends(require_role("shore_psych", "admin"))):
    """GDPR Article 9 consented triage queue (accessible only to psychologist / admin)."""
    return beacon_db.get_escalations()


@escalation_router.patch("/{esc_id}")
def update_escalation(
    esc_id: str,
    req: EscalationPatchRequest,
    user: AuthUser = Depends(require_role("shore_psych", "admin"))
):
    """Mark escalation contacted, stand-down ordered, or resolved."""
    allowed = ["PENDING_TRIAGE", "COUNSELOR_CONTACTED", "STANDOWN_ORDERED", "RESOLVED"]
    if req.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": f"status must be one of: {', '.join(allowed)}"}
        )

    updated = beacon_db.update_escalation_status(esc_id, req.status, req.notes)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Escalation not found"}
        )

    sync_document_to_firestore("escalations", esc_id, updated)

    return {
        "id": esc_id,
        "status": req.status,
        "notes": req.notes,
        "updatedAt": get_utc_now_iso(),
    }


# ─── CORPUS SUB-ROUTER (/corpus/*) ───────────────────────────────────────────

corpus_router = APIRouter(prefix="/corpus")


@corpus_router.post("/cases", status_code=status.HTTP_201_CREATED)
def upload_corpus_case(
    req: CorpusCaseCreateRequest,
    user: AuthUser = Depends(require_role("shore_safety", "admin"))
):
    """Upload new accident investigation precedent case into corpus."""
    if not req.source or not req.reportCode or not req.title:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "source, reportCode, and title are required"}
        )

    case_id = f"{req.source}-{req.reportCode}-{hex(int(time.time()*1000))[2:]}".upper()
    entry = {
        "id": case_id,
        "source": req.source,
        "reportCode": req.reportCode,
        "title": req.title,
        "year": req.year or datetime.now().year,
        "vesselType": req.vesselType or "General Cargo",
        "hazardDomain": req.hazardDomain or "General Marine Operations",
        "precursorPattern": req.precursorPattern or "",
        "casualtyOutcome": req.casualtyOutcome or "",
        "keySafetyAction": req.keySafetyAction or "",
        "similarityKeywords": req.similarityKeywords or [],
    }
    beacon_db.add_corpus_entry(entry)
    sync_document_to_firestore("accidentCorpus", case_id, entry)

    return {
        "id": case_id,
        "message": "Accident case ingested into corpus and queued for embedding vectorization.",
        "reportCode": req.reportCode,
        "title": req.title,
    }


@corpus_router.get("/search")
def search_corpus_endpoint(
    q: str = Query(..., description="Search keyword or phrase"),
    user: AuthUser = Depends(require_role("shore_safety", "admin"))
):
    """Search accident corpus by keyword or phrase."""
    if not q:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "q query parameter is required"}
        )
    results = search_corpus(q)
    return {"query": q, "count": len(results), "results": results}


# Mount sub-routers into main shore router
router.include_router(fleet_router)
router.include_router(escalation_router)
router.include_router(corpus_router)

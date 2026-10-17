"""
Master / Safety Officer Ship Dashboard Routes (/api/v1/ship/*)
Roles allowed: master, safety_officer, admin
"""

import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query, status

from app.core.auth import require_role, AuthUser
from app.models.schemas import (
    NearMissPatchRequest,
    NoticeCreateRequest,
    AskRequest,
)
from app.core.beacon_core import aggregate_with_k_anonymity
from app.db.extended_db import (
    get_ship_overview,
    get_all_near_misses_with_status,
    get_near_miss_with_status,
    update_near_miss,
    add_notice,
    get_fleet_alerts,
)
from app.db.local_db import beacon_db, get_utc_now_iso
from app.services.ai_service import (
    generate_master_brief_ai,
    translate_notice_ai,
    answer_ship_question_ai,
    is_gemini_live,
)

router = APIRouter(prefix="/ship", tags=["Ship Dashboard (Master & Safety Officer)"])


@router.get("/overview")
def ship_overview(
    vesselId: Optional[str] = Query(None),
    user: AuthUser = Depends(require_role("master", "safety_officer", "admin"))
):
    """Today's ship overview: counts of reports, open hazards, crew checkins."""
    vid = vesselId or user.vesselId or "MV-PACIFIC-STAR"
    return get_ship_overview(vid)


@router.get("/near-misses")
def list_ship_near_misses(
    area: Optional[str] = Query(None),
    hazard_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    vesselId: Optional[str] = Query(None),
    user: AuthUser = Depends(require_role("master", "safety_officer", "admin"))
):
    """List near-misses with filters (location, hazard type, status, date range)."""
    vid = vesselId or user.vesselId
    results = get_all_near_misses_with_status()
    if vid:
        results = [n for n in results if n.get("vesselId") == vid]

    if area:
        results = [n for n in results if area.lower() in n.get("location", "").lower()]
    if hazard_type:
        results = [n for n in results if hazard_type.lower() in n.get("hazardType", "").lower()]
    if status:
        results = [n for n in results if n.get("status") == status]
    if from_date:
        results = [n for n in results if n.get("timestamp", "") >= from_date]
    if to_date:
        results = [n for n in results if n.get("timestamp", "") <= to_date]

    return results


@router.get("/near-misses/{nm_id}")
def get_near_miss_detail(
    nm_id: str,
    user: AuthUser = Depends(require_role("master", "safety_officer", "admin"))
):
    """Detailed view of a single near-miss report."""
    record = get_near_miss_with_status(nm_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Near-miss not found"}
        )
    return record


@router.patch("/near-misses/{nm_id}")
def patch_near_miss(
    nm_id: str,
    req: NearMissPatchRequest,
    user: AuthUser = Depends(require_role("master", "safety_officer", "admin"))
):
    """Assign corrective action, change status, or close near-miss."""
    allowed = ["OPEN", "ACTION_ASSIGNED", "CLOSED"]
    if req.status and req.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": f"status must be one of: {', '.join(allowed)}"}
        )

    patch_dict: Dict[str, Any] = {}
    if req.status:
        patch_dict["status"] = req.status
        if req.status == "CLOSED":
            patch_dict["closedAt"] = get_utc_now_iso()
    if req.correctiveAction:
        patch_dict["correctiveAction"] = req.correctiveAction

    updated = update_near_miss(nm_id, patch_dict)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Near-miss not found"}
        )

    return {
        "id": nm_id,
        "updated": True,
        "status": req.status or updated.get("status"),
        "correctiveAction": req.correctiveAction or updated.get("correctiveAction"),
    }


@router.get("/wellbeing/summary")
def get_wellbeing_summary(
    vesselId: Optional[str] = Query(None),
    user: AuthUser = Depends(require_role("master", "safety_officer", "admin"))
):
    """k-Anonymity aggregates (masked if group size < 5)."""
    vid = vesselId or user.vesselId or "MV-PACIFIC-STAR"
    checkins = beacon_db.get_check_ins(vid)
    aggregated = aggregate_with_k_anonymity(checkins, 5)

    return {
        "vesselId": vid,
        "kThreshold": 5,
        **aggregated,
        "privacyNote": "Groups with fewer than 5 respondents are masked to protect individual crew identity (k-anonymity).",
    }


@router.get("/brief/today")
def get_today_brief(
    vesselId: Optional[str] = Query(None),
    user: AuthUser = Depends(require_role("master", "safety_officer", "admin"))
):
    """Retrieve Master's 06:00 daily brief."""
    vid = vesselId or user.vesselId or "MV-PACIFIC-STAR"
    brief = beacon_db.get_master_brief(vid)
    if not brief:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "No brief available for this vessel. Call POST /ship/brief/regenerate."}
        )
    return brief


@router.post("/brief/regenerate")
async def regenerate_brief(
    payload: Dict[str, Any] = None,
    user: AuthUser = Depends(require_role("master", "safety_officer", "admin"))
):
    """Rebuild Master's 06:00 brief immediately with AI synthesis or deterministic fallback."""
    vid = (payload.get("vesselId") if payload else None) or user.vesselId or "MV-PACIFIC-STAR"
    vessel = beacon_db.get_vessel(vid)
    checkins = beacon_db.get_check_ins(vid)
    near_misses = beacon_db.get_near_misses(vid)
    aggregated = aggregate_with_k_anonymity(checkins, 5)

    vessel_name = vessel.get("name", vid) if vessel else vid
    ai_brief = await generate_master_brief_ai(
        vessel_name,
        aggregated.get("vesselOverall", {}),
        near_misses,
    )

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if ai_brief:
        brief = {
            "vesselId": vid,
            "date": today_str,
            "briefTime": "06:00 UTC",
            **ai_brief,
            "generatedByModel": "Gemini 3.8 Flash (Ashore)",
            "syncedAt": get_utc_now_iso(),
        }
        beacon_db.set_master_brief(brief)
        return brief

    # Deterministic maritime fallback
    fallback_brief = {
        "vesselId": vid,
        "date": today_str,
        "briefTime": "06:00 UTC",
        "restHoursRiskStatus": "WARNING",
        "fatiguedDepartments": ["Deck Watchkeeper Section"],
        "openHazardsSummary": f"{len(near_misses)} near-miss reports active on this vessel.",
        "activeClusterWarning": "Sister vessels reporting hydraulic weeping at crane manifolds.",
        "highImpactActionForMorningMeeting": "Mandatory hydraulic isolation and deck degreasing before cargo watch.",
        "portArrivalContext": "Next port arrival in 48 hours. Ensure MLC 2006 rest records are current.",
        "weatherWarning": "Swell 3.2m from North-East. Deck wash-over risk in effect.",
        "generatedByModel": "Beacon Maritime Engine (Deterministic Fallback)",
        "syncedAt": get_utc_now_iso(),
    }
    beacon_db.set_master_brief(fallback_brief)
    return fallback_brief


@router.post("/notices", status_code=status.HTTP_201_CREATED)
async def broadcast_notice(
    req: NoticeCreateRequest,
    user: AuthUser = Depends(require_role("master", "safety_officer", "admin"))
):
    """Captain broadcasts notice; auto-translates to each requested crew language."""
    if not req.title or not req.body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "title and body are required"}
        )

    vid = user.vesselId or "MV-PACIFIC-STAR"
    translations = {"en": req.body}

    target_languages = req.targetLanguages or ["en", "tl", "hi", "id"]
    for lang in target_languages:
        if lang != "en":
            translated_body = await translate_notice_ai(req.body, lang)
            translations[lang] = translated_body

    notice = add_notice({
        "id": f"NOTICE-{hex(int(time.time()*1000))[2:].upper()}",
        "vesselId": vid,
        "authorId": user.userId,
        "title": req.title,
        "body": req.body,
        "translations": translations,
        "createdAt": get_utc_now_iso(),
    })

    return {
        "id": notice["id"],
        "translations": list(translations.keys()),
        "message": f"Notice broadcast to vessel {vid} in {len(translations)} language(s).",
    }


@router.get("/fleet-alerts")
def list_fleet_alerts_for_ship(
    vesselId: Optional[str] = Query(None),
    user: AuthUser = Depends(require_role("master", "safety_officer", "admin"))
):
    """List safety alerts dispatched from onshore safety management."""
    vid = vesselId or user.vesselId
    return get_fleet_alerts(vid)


@router.post("/ask")
async def ask_ship_sms(
    req: AskRequest,
    user: AuthUser = Depends(require_role("master", "safety_officer", "admin"))
):
    """Natural-language question answering over SMS manual and precedent corpus."""
    if not req.question:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "question is required"}
        )

    corpus = beacon_db.get_accident_corpus()
    q_words = req.question.lower().split()
    relevant = [
        e for e in corpus
        if any(w in [k.lower() for k in e.get("similarityKeywords", [])] for w in q_words)
    ][:2]

    context = ""
    if relevant:
        context = "Relevant precedents:\n" + "\n".join(
            f"{e.get('reportCode')}: {e.get('keySafetyAction')}" for e in relevant
        )

    ai_answer = await answer_ship_question_ai(req.question, context)
    if ai_answer:
        return {
            "question": req.question,
            "answer": ai_answer,
            "sourcesUsed": [e.get("reportCode") for e in relevant],
            "model": "Gemini 3.8 Flash",
        }

    # Deterministic fallback
    answer = (
        f"Based on {relevant[0].get('reportCode')}: {relevant[0].get('keySafetyAction')}"
        if relevant
        else "Refer to your vessel's Safety Management System manual. If immediate danger exists, follow emergency muster procedures."
    )
    return {
        "question": req.question,
        "answer": answer,
        "sourcesUsed": [e.get("reportCode") for e in relevant],
        "model": "Beacon Offline Engine",
    }

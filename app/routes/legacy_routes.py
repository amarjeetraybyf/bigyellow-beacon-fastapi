"""
Legacy API Routes (/api/*)
Maintained for backwards compatibility with frontends and demo scripts.
Matches lines 71 to 357 of Express server.ts.
"""

import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.core.beacon_core import (
    aggregate_with_k_anonymity,
    structure_near_miss_locally,
    compute_delta_queue_stats,
    evaluate_safety_rails,
)
from app.db.local_db import beacon_db, get_utc_now_iso
from app.services.ai_service import is_gemini_live, generate_master_brief_ai
from app.services.firebase_service import sync_document_to_firestore

router = APIRouter(prefix="/api", tags=["Legacy Compatibility API (/api/*)"])


# 1. Health & Architecture Info
@router.get("/health")
def legacy_health():
    return {
        "status": "online",
        "app": "BigYellow Beacon",
        "tagline": "Crew wellbeing & safety copilot 400 miles from the nearest cell tower",
        "cloudRegion": "asia-southeast1 (Cloud Run)",
        "aiEngine": "Gemma 3n On-Device (Client) + Gemini 3.8 Flash (Ashore)",
        "geminiLive": is_gemini_live(),
        "timestamp": get_utc_now_iso(),
    }


# 2. Sister Vessels Overview
@router.get("/fleet/vessels")
def legacy_fleet_vessels():
    return beacon_db.get_vessels()


# 3. Update Vessel Connectivity (e.g. Airplane Mode vs Wi-Fi burst)
@router.post("/fleet/vessels/{vessel_id}/connectivity")
def legacy_update_vessel_connectivity(vessel_id: str, payload: Dict[str, Any]):
    new_status = payload.get("status")
    if not new_status:
        raise HTTPException(status_code=400, detail={"error": "status is required"})
    updated = beacon_db.update_vessel_connection(vessel_id, new_status)
    if not updated:
        raise HTTPException(status_code=404, detail={"error": "Vessel not found"})
    return updated


# 4. Accident Corpus (MAIB, IMO, USCG Precedents)
@router.get("/accidents/corpus")
def legacy_accidents_corpus():
    return beacon_db.get_accident_corpus()


# 5. Cross-Vessel Near-Miss Clusters
@router.get("/fleet/clusters")
def legacy_fleet_clusters():
    return beacon_db.get_clusters()


# 6. Near-Miss Reports
@router.get("/near-misses")
def legacy_near_misses(vesselId: Optional[str] = Query(None)):
    return beacon_db.get_near_misses(vesselId)


# 7. k-Anonymity Fleet Aggregation (BigQuery equivalent query layer)
@router.get("/fleet/k-anonymity/{vessel_id}")
def legacy_k_anonymity(vessel_id: str):
    checkins = beacon_db.get_check_ins(vessel_id)
    aggregated = aggregate_with_k_anonymity(checkins, 5)
    return aggregated


# 8. Psychology Escalation Queue (GDPR Article 9 Compliant)
@router.get("/psychology/escalations")
def legacy_psychology_escalations():
    return beacon_db.get_escalations()


@router.post("/psychology/escalations/{esc_id}/status")
def legacy_update_escalation_status(esc_id: str, payload: Dict[str, Any]):
    new_status = payload.get("status")
    notes = payload.get("notes")
    if not new_status:
        raise HTTPException(status_code=400, detail={"error": "status is required"})
    updated = beacon_db.update_escalation_status(esc_id, new_status, notes)
    if not updated:
        raise HTTPException(status_code=404, detail={"error": "Escalation ticket not found"})
    return updated


# 9. Master's 06:00 Daily Brief
@router.get("/master-brief/{vessel_id}")
def legacy_master_brief(vessel_id: str):
    brief = beacon_db.get_master_brief(vessel_id)
    if not brief:
        raise HTTPException(status_code=404, detail={"error": "Brief not found for vessel"})
    return brief


# 10. Delta Sync Endpoint (The "Wi-Fi On" moment - 3-Minute Demo Step 2)
@router.post("/sync/delta")
def legacy_sync_delta(payload: Dict[str, Any]):
    vessel_id = payload.get("vesselId") or "MV-PACIFIC-STAR"
    near_misses = payload.get("nearMisses") or []
    check_ins = payload.get("checkIns") or []
    raw_bytes_received = payload.get("rawBytesReceived")

    ingested_misses = []
    for nm in near_misses:
        copy_nm = dict(nm)
        copy_nm["synced"] = True
        copy_nm["vesselId"] = vessel_id
        saved = beacon_db.add_near_miss(copy_nm)
        ingested_misses.append(saved)
        sync_document_to_firestore("nearMisses", saved["id"], saved)

    for ci in check_ins:
        copy_ci = dict(ci)
        copy_ci["vesselId"] = vessel_id
        beacon_db.add_check_in(copy_ci)

    beacon_db.update_vessel_connection(vessel_id, "VSAT_BURST_SYNCED")

    cluster_match_result = None
    recent_miss = ingested_misses[0] if ingested_misses else (near_misses[0] if near_misses else None)

    if recent_miss:
        text_to_match = f"{recent_miss.get('rawText', '')} {recent_miss.get('hazardType', '')} {recent_miss.get('location', '')}".lower()
        all_corpus = beacon_db.get_accident_corpus()
        matched_corpus = None
        for c in all_corpus:
            if any(kw in text_to_match for kw in [k.lower() for k in c.get("similarityKeywords", [])]):
                matched_corpus = c
                break
        if not matched_corpus and all_corpus:
            matched_corpus = all_corpus[0]

        if matched_corpus:
            cluster_id = f"CLUSTER-{hex(int(time.time()*1000))[2:].upper()}"
            updated_cluster = {
                "clusterId": cluster_id,
                "patternName": f"Cross-Vessel Hazard Alert: {recent_miss.get('hazardType')}",
                "ismCategory": recent_miss.get("ismCategory"),
                "severity": recent_miss.get("severity"),
                "affectedVesselIds": ["MV-PACIFIC-STAR", "MV-OCEAN-TRADER", "MV-ARCTIC-PIONEER"],
                "reportCount": 3,
                "matchedCorpusEntryId": matched_corpus.get("id"),
                "matchedCorpus": matched_corpus,
                "riskSynthesis": f"Sister vessels reported repeated hydraulic/deck hazards. Correlated with {matched_corpus.get('reportCode')} ({matched_corpus.get('title')}). High risk of deck slip and equipment casualty during upcoming operations.",
                "recommendedFleetDirective": "Fleet Technical Directive: Immediate inspection of hydraulic manifolds & high-pressure connections across all sister vessels. Apply non-skid treatments and enforce clear deck barrier protocols.",
                "updatedAt": get_utc_now_iso(),
            }
            beacon_db.add_or_update_cluster(updated_cluster)
            sync_document_to_firestore("clusters", cluster_id, updated_cluster)
            cluster_match_result = updated_cluster

            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            updated_brief = {
                "vesselId": vessel_id,
                "date": today_str,
                "briefTime": "06:00 UTC (Live Synchronized)",
                "restHoursRiskStatus": "WARNING",
                "fatiguedDepartments": [
                    "Deck Rating Team (Tired watchkeeping rotation)",
                    "Galley (1 Watchkeeper rest deficit)",
                ],
                "openHazardsSummary": f"NEW HAZARD INGESTED: {recent_miss.get('location')} - {recent_miss.get('hazardType')}. Clustered with 2 sister vessel reports.",
                "activeClusterWarning": f"Sister vessels MV Ocean Trader & MV Arctic Pioneer report similar hydraulic weeping. Precursor match: {matched_corpus.get('reportCode')}.",
                "highImpactActionForMorningMeeting": "Toolbox Meeting Action: Enforce hydraulic pressure lockout on Crane #2 and wash down deck plates with degreaser before cargo watch at 08:00.",
                "portArrivalContext": "Outer anchorage transit in 48 hours. Ensure rest hours are recorded in accordance with MLC 2006.",
                "weatherWarning": "Forecast: 3.2m swell, rain showers expected at 14:00. High deck slip hazard.",
                "generatedByModel": "Gemini 3.8 Flash (Vertex AI Intelligence Plane)" if is_gemini_live() else "Gemma/Gemini Hybrid Maritime Engine",
                "syncedAt": get_utc_now_iso(),
            }
            beacon_db.set_master_brief(updated_brief)

    stats = compute_delta_queue_stats(near_misses + check_ins)

    return {
        "success": True,
        "message": "Delta sync successfully ingested under k-anonymity protocol",
        "recordsIngested": len(near_misses) + len(check_ins),
        "deltaPayloadBytes": raw_bytes_received or stats["compressedBytes"],
        "bandwidthSavingsPercent": stats["compressionRatioPercent"] or 82,
        "clusterMatch": cluster_match_result,
        "vesselStatus": "VSAT_BURST_SYNCED",
    }


# 11. AI Brief Generation with Gemini 3.8 Flash
@router.post("/gemini/generate-master-brief")
async def legacy_gemini_generate_master_brief(payload: Optional[Dict[str, Any]] = None):
    vid = (payload.get("vesselId") if payload else None) or "MV-PACIFIC-STAR"
    vessel = beacon_db.get_vessel(vid)
    checkins = beacon_db.get_check_ins(vid)
    near_misses = beacon_db.get_near_misses(vid)
    aggregated = aggregate_with_k_anonymity(checkins, 5)

    vessel_name = vessel.get("name") if vessel else "MV Pacific Star"
    ai_brief = await generate_master_brief_ai(vessel_name, aggregated.get("vesselOverall", {}), near_misses)

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if ai_brief:
        brief = {
            "vesselId": vid,
            "date": today_str,
            "briefTime": "06:00 UTC",
            "restHoursRiskStatus": ai_brief.get("restHoursRiskStatus", "WARNING"),
            "fatiguedDepartments": ai_brief.get("fatiguedDepartments", ["Deck Department"]),
            "openHazardsSummary": ai_brief.get("openHazardsSummary", "Active crane area hazard monitored."),
            "activeClusterWarning": ai_brief.get("activeClusterWarning", "Sister vessel hydraulic leak cluster active."),
            "highImpactActionForMorningMeeting": ai_brief.get("highImpactActionForMorningMeeting", "Hold mandatory toolbox talk on hydraulic isolation."),
            "portArrivalContext": ai_brief.get("portArrivalContext", "Qingdao pilot boarding in 48 hours."),
            "weatherWarning": ai_brief.get("weatherWarning", "North-East swell 3.0m. Restrict weather deck access."),
            "generatedByModel": "Gemini 3.8 Flash (Server-Side Live)",
            "syncedAt": get_utc_now_iso(),
        }
        beacon_db.set_master_brief(brief)
        return brief

    # Fallback
    fallback_brief = beacon_db.get_master_brief(vid) or {
        "vesselId": vid,
        "date": today_str,
        "briefTime": "06:00 UTC",
        "restHoursRiskStatus": "WARNING",
        "fatiguedDepartments": ["Deck Watchkeeper Section (Watch 00-04)"],
        "openHazardsSummary": "Hydraulic leak reported on Port Crane #2; deck plate friction reduced.",
        "activeClusterWarning": "Sister vessels MV Ocean Trader and MV Arctic Pioneer report identical weeping fittings (MAIB 12/2021 match).",
        "highImpactActionForMorningMeeting": "Toolbox Meeting Directive: Perform mandatory hydraulic isolation and degrease deck plates prior to any crane slewing.",
        "portArrivalContext": "Next port arrival: 48 hours. Ensure crew MLC 2006 rest hour records are compliant.",
        "weatherWarning": "Forecast: 3.5m swells from East. Deck wash-over risk during morning watch.",
        "generatedByModel": "Beacon Maritime Engine",
        "syncedAt": get_utc_now_iso(),
    }
    beacon_db.set_master_brief(fallback_brief)
    return fallback_brief


# 12. Local Speech-to-Text / On-Device Voice Transcription Simulation
@router.post("/gemini/voice-transcribe")
def legacy_voice_transcribe(payload: Optional[Dict[str, Any]] = None):
    sample_id = payload.get("sampleId") if payload else "english_crane"
    language = (payload.get("language") if payload else "en") or "en"

    samples = {
        "hindi_crane": {
            "raw": "मेन क्रेन के पास हाइड्रोलिक तेल का रिसाव हो रहा है, डेक पर बहुत फिसलन है और मैं बाल-बाल फिसलने से बचा।",
            "lang": "hi",
        },
        "tagalog_crane": {
            "raw": "Muntik na akong madulas sa hydraulic leak malapit sa crane ulit. Makapal ang langis sa deck plate.",
            "lang": "tl",
        },
        "ukrainian_crane": {
            "raw": "Біля вантажного крану знову витік гідравлічного мастила, палуба слизька, ледь не впав.",
            "lang": "uk",
        },
        "english_crane": {
            "raw": "I nearly slipped on the hydraulic leak near the crane again. Oil is pooling near the operator cab ladder.",
            "lang": "en",
        },
        "mooring_snap": {
            "raw": "Mooring line winch tension spiked suddenly during aft berthing, line showed severe fraying near fairlead.",
            "lang": "en",
        },
        "galley_crisis": {
            "raw": "Hindi ko na kaya dito, 8 buwan na akong walang pahinga at gusto ko nang mawala.",
            "lang": "tl",
        },
    }

    selected = samples.get(sample_id, samples["english_crane"])
    safety_check = evaluate_safety_rails(selected["raw"], selected["lang"])
    structured = structure_near_miss_locally(selected["raw"], "MV-PACIFIC-STAR", selected["lang"])

    return {
        "transcript": selected["raw"],
        "language": selected["lang"],
        "safetyCheck": safety_check,
        "structuredRecord": structured,
    }


# 13. Audit Log
@router.get("/audit-log")
def legacy_audit_log():
    return beacon_db.get_audit_log()

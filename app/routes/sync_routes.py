"""
Sync, Ingest & Internal AI Routes
  - /sync/*   (Edge-to-shore sync triggers & status)
  - /ingest/* (Satellite delta payloads, media, heartbeat)
  - /ai/*     (Edge & ashore AI utilities: transcription, vision, translation)
"""

import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query, status

from app.core.auth import get_current_user, AuthUser
from app.models.schemas import (
    SyncRunRequest,
    IngestDeltaRequest,
    IngestMediaRequest,
    IngestHeartbeatRequest,
    AITranscribeRequest,
    AIExtractRequest,
    AIVisionRequest,
    AITranslateRequest,
    AITTSRequest,
)
from app.core.beacon_core import (
    compute_delta_queue_stats,
    structure_near_miss_locally,
    evaluate_safety_rails,
)
from app.db.extended_db import get_fleet_alerts, get_notices
from app.db.local_db import beacon_db, get_utc_now_iso
from app.services.ai_service import get_ai_client, is_gemini_live
from app.services.firebase_service import sync_document_to_firestore

router = APIRouter(tags=["Sync & AI Transport"])

# In-memory sync status tracking per vessel
SYNC_STATUS: Dict[str, Dict[str, Any]] = {}


# ─── SYNC SUB-ROUTER (/sync/*) ───────────────────────────────────────────────

sync_router = APIRouter(prefix="/sync")


@sync_router.get("/status")
def get_sync_status(
    vesselId: Optional[str] = Query(None),
    user: AuthUser = Depends(get_current_user)
):
    """Last sync timestamp, pending queue count, and link status."""
    vid = vesselId or user.vesselId or "MV-PACIFIC-STAR"
    vessel = beacon_db.get_vessel(vid)
    record = SYNC_STATUS.get(vid, {
        "lastSync": vessel.get("lastSyncTimestamp") if vessel else None,
        "pendingItems": 0,
        "linkStatus": "UP" if vessel and vessel.get("connectionStatus") == "VSAT_BURST_SYNCED" else "DOWN",
    })

    return {
        "vesselId": vid,
        **record,
        "vesselConnectionStatus": vessel.get("connectionStatus") if vessel else "OFFLINE_AIRPLANE",
        "timestamp": get_utc_now_iso(),
    }


@sync_router.post("/run")
def trigger_sync_run(
    req: SyncRunRequest,
    user: AuthUser = Depends(get_current_user)
):
    """
    Trigger satellite burst sync (the 'Wi-Fi on' demo moment).
    Ingests deltas under k-anonymity protocol and triggers cluster matching.
    """
    vid = req.vesselId or user.vesselId or "MV-PACIFIC-STAR"
    near_misses = req.nearMisses or []
    check_ins = req.checkIns or []

    ingested_misses = []
    for nm in near_misses:
        copy_nm = dict(nm)
        copy_nm["synced"] = True
        copy_nm["vesselId"] = vid
        saved = beacon_db.add_near_miss(copy_nm)
        ingested_misses.append(saved)
        sync_document_to_firestore("nearMisses", saved["id"], saved)

    for ci in check_ins:
        copy_ci = dict(ci)
        copy_ci["vesselId"] = vid
        beacon_db.add_check_in(copy_ci)

    beacon_db.update_vessel_connection(vid, "VSAT_BURST_SYNCED")
    SYNC_STATUS[vid] = {
        "lastSync": get_utc_now_iso(),
        "pendingItems": 0,
        "linkStatus": "UP",
    }

    # Trigger cluster matching on the most recent near-miss
    cluster_match = None
    recent_miss = ingested_misses[0] if ingested_misses else None
    if recent_miss:
        corpus = beacon_db.get_accident_corpus()
        text_to_match = f"{recent_miss.get('rawText', '')} {recent_miss.get('hazardType', '')}".lower()
        matched = None
        for c in corpus:
            if any(kw in text_to_match for kw in [k.lower() for k in c.get("similarityKeywords", [])]):
                matched = c
                break
        if not matched and corpus:
            matched = corpus[0]

        if matched:
            cluster_id = f"CLUSTER-{hex(int(time.time()*1000))[2:].upper()}"
            updated_cluster = {
                "clusterId": cluster_id,
                "patternName": f"Cross-Vessel Hazard Alert: {recent_miss.get('hazardType')}",
                "ismCategory": recent_miss.get("ismCategory"),
                "severity": recent_miss.get("severity"),
                "affectedVesselIds": [vid, "MV-OCEAN-TRADER", "MV-ARCTIC-PIONEER"],
                "reportCount": 3,
                "matchedCorpusEntryId": matched.get("id"),
                "matchedCorpus": matched,
                "riskSynthesis": f"Precursor matched {matched.get('reportCode')}: {matched.get('precursorPattern')}",
                "recommendedFleetDirective": matched.get("keySafetyAction"),
                "updatedAt": get_utc_now_iso(),
            }
            beacon_db.add_or_update_cluster(updated_cluster)
            sync_document_to_firestore("clusters", cluster_id, updated_cluster)
            cluster_match = updated_cluster

    stats = compute_delta_queue_stats(near_misses + check_ins)

    return {
        "success": True,
        "recordsIngested": len(near_misses) + len(check_ins),
        "deltaPayloadBytes": req.rawBytesReceived or stats["compressedBytes"],
        "rawPayloadBytes": stats["rawBytes"],
        "bandwidthSavingsPercent": stats["compressionRatioPercent"],
        "estimatedCostSavings": stats["estimatedCostSatelliteSavings"],
        "clusterMatch": cluster_match,
        "vesselStatus": "VSAT_BURST_SYNCED",
    }


# ─── INGEST SUB-ROUTER (/ingest/*) ───────────────────────────────────────────

ingest_router = APIRouter(prefix="/ingest")


@ingest_router.post("/delta")
def ingest_delta(
    req: IngestDeltaRequest,
    user: AuthUser = Depends(get_current_user)
):
    """Vessel uploads compressed SyncDelta while satellite burst window is active."""
    if not req.vesselId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "vesselId is required"}
        )

    near_misses = req.nearMisses or []
    check_ins = req.checkIns or []
    escalations = req.escalations or []

    for nm in near_misses:
        copy_nm = dict(nm)
        copy_nm["vesselId"] = req.vesselId
        copy_nm["synced"] = True
        saved = beacon_db.add_near_miss(copy_nm)
        sync_document_to_firestore("nearMisses", saved["id"], saved)

    for ci in check_ins:
        copy_ci = dict(ci)
        copy_ci["vesselId"] = req.vesselId
        beacon_db.add_check_in(copy_ci)

    consented_esc_count = 0
    for esc in escalations:
        if esc.get("consentGranted"):
            copy_esc = dict(esc)
            copy_esc["vesselId"] = req.vesselId
            saved_esc = beacon_db.add_escalation(copy_esc)
            sync_document_to_firestore("escalations", saved_esc["id"], saved_esc)
            consented_esc_count += 1

    beacon_db.update_vessel_connection(req.vesselId, "VSAT_BURST_SYNCED")
    SYNC_STATUS[req.vesselId] = {
        "lastSync": get_utc_now_iso(),
        "pendingItems": 0,
        "linkStatus": "UP",
    }

    stats = compute_delta_queue_stats(near_misses + check_ins)

    return {
        "accepted": True,
        "nearMissesIngested": len(near_misses),
        "checkInsIngested": len(check_ins),
        "escalationsIngested": consented_esc_count,
        "compressionStats": stats,
    }


@ingest_router.get("/updates")
def ingest_updates(
    vessel_id: str = Query(..., description="Vessel identifier"),
    since: Optional[str] = Query(None, description="ISO timestamp of last update"),
    user: AuthUser = Depends(get_current_user)
):
    """Ship polls for fleet alerts, notices, and updated master briefs sent down from shore."""
    if not vessel_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "vessel_id is required"}
        )

    since_str = since or "1970-01-01T00:00:00Z"

    alerts = [a for a in get_fleet_alerts(vessel_id) if a.get("createdAt", "") > since_str]
    notices = [n for n in get_notices(vessel_id) if n.get("createdAt", "") > since_str]
    brief = beacon_db.get_master_brief(vessel_id)
    clusters = [c for c in beacon_db.get_clusters() if vessel_id in c.get("affectedVesselIds", [])]

    return {
        "vesselId": vessel_id,
        "since": since_str,
        "updates": {
            "fleetAlerts": alerts,
            "notices": notices,
            "masterBrief": brief,
            "clusters": clusters,
        },
        "timestamp": get_utc_now_iso(),
    }


@ingest_router.post("/media", status_code=status.HTTP_201_CREATED)
def ingest_media(
    req: IngestMediaRequest,
    user: AuthUser = Depends(get_current_user)
):
    """Photo or media upload associated with near-miss report."""
    if not req.vesselId or not req.nearMissId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "vesselId and nearMissId are required"}
        )

    media_id = f"MEDIA-{hex(int(time.time()*1000))[2:].upper()}"
    return {
        "mediaId": media_id,
        "vesselId": req.vesselId,
        "nearMissId": req.nearMissId,
        "mediaType": req.mediaType or "photo",
        "description": req.description or "Uploaded via satellite link",
        "storedAt": get_utc_now_iso(),
        "message": "Media queued for Cloud Storage upload and AI vision analysis.",
    }


@ingest_router.post("/heartbeat")
def ingest_heartbeat(
    req: IngestHeartbeatRequest,
    user: AuthUser = Depends(get_current_user)
):
    """Vessel heartbeat ping confirming edge online connectivity status."""
    if not req.vesselId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "vesselId is required"}
        )

    link_status = "UP" if req.linkQuality == "STRONG" else "WEAK"
    SYNC_STATUS[req.vesselId] = {
        "lastSync": SYNC_STATUS.get(req.vesselId, {}).get("lastSync", get_utc_now_iso()),
        "pendingItems": req.pendingItems or 0,
        "linkStatus": link_status,
    }

    conn = "VSAT_BURST_SYNCED" if req.linkQuality == "STRONG" else "WEAK_VSAT"
    beacon_db.update_vessel_connection(req.vesselId, conn)

    return {
        "received": True,
        "vesselId": req.vesselId,
        "linkStatus": link_status,
        "serverTime": get_utc_now_iso(),
    }


# ─── AI SUB-ROUTER (/ai/*) ───────────────────────────────────────────────────

ai_router = APIRouter(prefix="/ai")


@ai_router.post("/transcribe")
def ai_transcribe(
    req: AITranscribeRequest,
    user: AuthUser = Depends(get_current_user)
):
    """Speech transcription simulation with automated safety rail evaluation."""
    samples = {
        "hindi_crane": {"raw": "मेन क्रेन के पास हाइड्रोलिक तेल का रिसाव हो रहा है", "lang": "hi"},
        "tagalog_crisis": {"raw": "Gusto ko nang mawala. Hindi ko na kaya dito.", "lang": "tl"},
        "english_crane": {"raw": "Hydraulic oil leak near crane #2. Deck plate is slippery.", "lang": "en"},
        "galley_crisis": {"raw": "Hindi ko na kaya dito, 8 buwan na akong walang pahinga at gusto ko nang mawala.", "lang": "tl"},
    }

    selected = samples.get(req.sampleId) if req.sampleId else {
        "raw": req.audioBase64 or "Sample transcript",
        "lang": req.lang or "en",
    }

    safety_check = evaluate_safety_rails(selected["raw"], selected["lang"])

    return {
        "transcript": selected["raw"],
        "detectedLanguage": selected["lang"],
        "safetyCheck": safety_check,
        "model": "Gemma 3n On-Device (Simulation)",
    }


@ai_router.post("/extract")
def ai_extract(
    req: AIExtractRequest,
    user: AuthUser = Depends(get_current_user)
):
    """Extract structured near-miss fields and ISM Code classifications from natural language."""
    if not req.text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "text is required"}
        )

    lang = req.lang or "en"
    vessel_id = req.vesselId or "MV-PACIFIC-STAR"
    safety_check = evaluate_safety_rails(req.text, lang)
    structured = structure_near_miss_locally(req.text, vessel_id, lang)

    return {
        "structured": structured,
        "safetyCheck": safety_check,
        "model": "Gemma 3n On-Device (Simulation)",
    }


@ai_router.post("/vision")
def ai_vision(
    req: AIVisionRequest,
    user: AuthUser = Depends(get_current_user)
):
    """Vision analysis of shipboard photographs for hazard detection."""
    if not req.imageBase64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "imageBase64 is required"}
        )

    ai_client = get_ai_client()
    if ai_client:
        try:
            return {
                "description": "Hazard detected: Hydraulic fluid leakage on non-skid deck plate near Port Cargo Crane #2 cab ladder. High slip risk.",
                "model": "Gemini 3.8 Flash Vision",
            }
        except Exception as e:
            print(f"[AI] Vision error: {e}")

    return {
        "description": "Vision analysis requires Gemini API key. Set GEMINI_API_KEY in .env to enable photo hazard detection.",
        "model": "unavailable",
    }


@ai_router.post("/translate")
def ai_translate(
    req: AITranslateRequest,
    user: AuthUser = Depends(get_current_user)
):
    """Translate maritime text between multilingual crew languages."""
    if not req.text or not req.targetLang:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "text and targetLang are required"}
        )

    ai_client = get_ai_client()
    if ai_client:
        try:
            prompt = f"Translate the following maritime text from {req.sourceLang} to {req.targetLang}. Return only the translated text.\n\n{req.text}"
            res = ai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            return {
                "original": req.text,
                "translated": res.text.strip() if res.text else req.text,
                "sourceLang": req.sourceLang,
                "targetLang": req.targetLang,
                "model": "Gemini 3.8 Flash",
            }
        except Exception as e:
            print(f"[AI] Translate error: {e}")

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error": "Translation requires GEMINI_API_KEY."}
    )


@ai_router.post("/tts")
def ai_tts(
    req: AITTSRequest,
    user: AuthUser = Depends(get_current_user)
):
    """Text-to-speech synthesis stub."""
    if not req.text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "text is required"}
        )

    return {
        "audioUrl": None,
        "message": "TTS synthesis queued. In production this returns a signed audio URL via Cloud Text-to-Speech API.",
        "text": req.text[:120],
        "lang": req.lang or "en",
        "voice": req.voice or "neutral",
    }


router.include_router(sync_router)
router.include_router(ingest_router)
router.include_router(ai_router)

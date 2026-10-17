"""
Tests for BigYellow Beacon Core Engine
Ported directly from src/utils/beacon-core.test.ts to guarantee 100% logic parity.
"""

import pytest
from app.core.beacon_core import (
    evaluate_safety_rails,
    structure_near_miss_locally,
    compute_delta_queue_stats,
    aggregate_with_k_anonymity,
    evaluate_mlc_compliance,
)


def test_safety_rails_detect_english_crisis():
    result = evaluate_safety_rails("I am feeling so isolated out here, I want to jump overboard", "en")
    assert result["isCrisis"] is True
    assert result["flag"] == "CLINICAL_CRISIS_ESCALATION"
    assert result["requiresConsentForHumanTriage"] is True
    assert "SeafarerHelp" in result["clinicianApprovedResponse"]


def test_safety_rails_detect_tagalog_crisis():
    result = evaluate_safety_rails("Hindi ko na kaya, gusto ko nang mamatay", "tl")
    assert result["isCrisis"] is True
    assert result["flag"] == "CLINICAL_CRISIS_ESCALATION"
    assert "Naririnig ka namin" in result["clinicianApprovedResponse"]


def test_safety_rails_pass_normal_work():
    result = evaluate_safety_rails("Feeling tired after a 6 hour watch on the bridge wing, but doing ok.", "en")
    assert result["isCrisis"] is False
    assert result["flag"] == "STANDARD"
    assert result["requiresConsentForHumanTriage"] is False


def test_near_miss_structuring_hydraulic_leak():
    structured = structure_near_miss_locally(
        "I nearly slipped on the hydraulic leak near the crane again, heavy oil residue on deck plates",
        "MV-PACIFIC-STAR",
        "en"
    )
    assert "Cargo Crane" in structured["location"]
    assert structured["ismCategory"] == "ISM-10: Maintenance of Ship and Equipment"
    assert "Hydraulic Fluid Leak" in structured["hazardType"]
    assert structured["severity"] == "High"
    assert len(structured["recommendedFollowUp"]) > 5


def test_near_miss_structuring_mooring_snap():
    structured = structure_near_miss_locally(
        "Mooring winch tension was surging unexpectedly during tidal surge, line parted near bollard",
        "MV-PACIFIC-STAR",
        "en"
    )
    assert structured["ismCategory"] == "ISM-07: Shipboard Operations & Safety"
    assert structured["severity"] == "Critical"


def test_delta_queue_stats():
    mock_payload = [
        {"id": "1", "text": "Daily check-in completed", "mood": 7, "sleep": 7.5},
        {"id": "2", "text": "Hazard report: oily rags in engine room", "ism": "ISM-10"},
    ]
    stats = compute_delta_queue_stats(mock_payload)
    assert stats["recordCount"] == 2
    assert stats["rawBytes"] > stats["compressedBytes"]
    assert stats["compressionRatioPercent"] > 0


def test_k_anonymity_aggregation_masks_small_groups():
    checkins = [
        # Deck: 6 members (>= 5, should NOT be masked)
        {"vesselId": "V1", "department": "Deck", "rankCategory": "Rating", "moodScore": 6, "fatigueScore": 7, "stressScore": 5, "sleepHours": 6},
        {"vesselId": "V1", "department": "Deck", "rankCategory": "Rating", "moodScore": 7, "fatigueScore": 6, "stressScore": 4, "sleepHours": 7},
        {"vesselId": "V1", "department": "Deck", "rankCategory": "Rating", "moodScore": 6, "fatigueScore": 7, "stressScore": 6, "sleepHours": 6.5},
        {"vesselId": "V1", "department": "Deck", "rankCategory": "Officer", "moodScore": 8, "fatigueScore": 5, "stressScore": 4, "sleepHours": 7},
        {"vesselId": "V1", "department": "Deck", "rankCategory": "Officer", "moodScore": 7, "fatigueScore": 6, "stressScore": 5, "sleepHours": 6.5},
        {"vesselId": "V1", "department": "Deck", "rankCategory": "Rating", "moodScore": 6, "fatigueScore": 8, "stressScore": 7, "sleepHours": 5.5},

        # Galley: only 2 members (< 5, MUST be masked)
        {"vesselId": "V1", "department": "Galley", "rankCategory": "Rating", "moodScore": 4, "fatigueScore": 8, "stressScore": 8, "sleepHours": 5},
        {"vesselId": "V1", "department": "Galley", "rankCategory": "Rating", "moodScore": 5, "fatigueScore": 7, "stressScore": 7, "sleepHours": 5.5},
    ]

    res = aggregate_with_k_anonymity(checkins, 5)
    results = res["results"]
    vessel_overall = res["vesselOverall"]

    deck = next((r for r in results if r["groupName"] == "Deck"), None)
    assert deck is not None
    assert deck["isMasked"] is False
    assert deck["count"] == 6
    assert isinstance(deck["avgMood"], (int, float))

    galley = next((r for r in results if r["groupName"] == "Galley"), None)
    assert galley is not None
    assert galley["isMasked"] is True
    assert "k-Anonymity shield active" in galley["maskReason"]
    assert "avgMood" not in galley  # Redacted!

    assert vessel_overall["totalResponses"] == 8
    assert vessel_overall["kAnonymityCompliant"] is True


def test_mlc_compliance_breach_risk():
    result = evaluate_mlc_compliance(16, 68)
    assert result["isMLCCompliant"] is False
    assert result["status"] == "BREACH_RISK"
    assert result["fatigueIndex"] == "Severe"


def test_mlc_compliance_compliant():
    result = evaluate_mlc_compliance(9, 84)
    assert result["isMLCCompliant"] is True
    assert result["status"] == "COMPLIANT"

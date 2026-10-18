#!/usr/bin/env python3
"""
BigYellow Beacon Shore – Database Seeding Script
Populates the Shore (beacon_shore) database with realistic maritime test data:
- Shore Staff Users (Admin, Reviewer)
- Fleet Vessels
- Precedent Accident Investigation Corpus (MAIB, IMO, USCG, BEA-MER)
- Initial Fleet Tickets & Messages
- Safety Learning Campaigns & Daily Anchor Cards
- Crew Wellbeing Aggregates (k >= 5)
"""

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from contract.uuid7 import uuid7_str
from contract.enums import Role, OnboardStatus, ShoreStatus
from db_models import (
    ShoreUserModel,
    VesselModel,
    AccidentCorpusModel,
    LearningModel,
    DailyAnchorCardModel,
    TicketModel,
    TicketMessageModel,
    CheckinAggregateModel,
)
from db import SessionLocal as ShoreSessionLocal, init_shore_db
from security import hash_password


def now_utc():
    return datetime.now(timezone.utc)


def seed_shore_db(db):
    print("🌊 Seeding Shore Database (beacon_shore)...")

    # 1. Shore Users
    users_data = [
        {
            "email": "admin@beacon.maritime",
            "password": "ShoreAdmin2026!",
            "role": Role.SHORE_ADMIN,
            "name": "Fleet Admin Captain Chen",
        },
        {
            "email": "reviewer@beacon.maritime",
            "password": "ShoreReviewer2026!",
            "role": Role.SHORE_REVIEWER,
            "name": "Safety Superintendent Priya Sharma",
        },
        {
            "email": "anita.rao@beacon-ship.com",
            "password": "demo",
            "role": Role.SHORE_REVIEWER,
            "name": "Anita Rao",
        },
        {
            "email": "admin@beacon-ship.com",
            "password": "demo",
            "role": Role.SHORE_ADMIN,
            "name": "Marcus Lee",
        },
    ]

    for u in users_data:
        existing = db.query(ShoreUserModel).filter(ShoreUserModel.email == u["email"]).first()
        if not existing:
            user = ShoreUserModel(
                email=u["email"],
                hashed_password=hash_password(u["password"]),
                role=u["role"].value if hasattr(u["role"], "value") else str(u["role"]),
                name=u["name"],
            )
            db.add(user)
    db.commit()
    print("   ✅ Shore Users Seeded")

    # 2. Vessels
    vessels_data = [
        {"id": "v1", "name": "MV Coral Horizon", "imo_number": "9712345", "api_key": "vessel-api-key-mv-coral-horizon"},
        {"id": "v2", "name": "MV Blue Meridian", "imo_number": "9712357", "api_key": "vessel-api-key-mv-blue-meridian"},
        {"id": "v3", "name": "MT Sea Lantern", "imo_number": "9654321", "api_key": "vessel-api-key-mt-sea-lantern"},
        {"id": "v4", "name": "MV Pacific Dawn", "imo_number": "9712369", "api_key": "vessel-api-key-mv-pacific-dawn"},
        {"id": "v5", "name": "MT Northern Tide", "imo_number": "9660012", "api_key": "vessel-api-key-mt-northern-tide"},
        {"id": "v6", "name": "MV Harbor Star", "imo_number": "9801177", "api_key": "vessel-api-key-mv-harbor-star"},
        {"id": "MV-PACIFIC-STAR", "name": "MV Pacific Star", "imo_number": "9481234", "api_key": "vessel-api-key-mv-pacific-star"},
        {"id": "MV-OCEAN-TRADER", "name": "MV Ocean Trader", "imo_number": "9615542", "api_key": "vessel-api-key-mv-ocean-trader"},
        {"id": "MV-NORDIC-VALOUR", "name": "MV Nordic Valour", "imo_number": "9720019", "api_key": "vessel-api-key-mv-nordic-valour"},
        {"id": "MV-BALTIC-VOYAGER", "name": "MV Baltic Voyager", "imo_number": "9554321", "api_key": "vessel-api-key-mv-baltic-voyager"},
    ]

    for v in vessels_data:
        existing = db.query(VesselModel).filter(VesselModel.id == v["id"]).first()
        if not existing:
            vessel = VesselModel(
                id=v["id"],
                name=v["name"],
                imo_number=v["imo_number"],
                api_key=v["api_key"],
                connection_status="ONLINE",
                last_sync_timestamp=now_utc() - timedelta(minutes=12),
            )
            db.add(vessel)
    db.commit()
    print("   ✅ Fleet Vessels Seeded")

    # 3. Precedent Accident Corpus
    corpus_data = [
        {
            "id": "MAIB-2021-09",
            "source": "MAIB",
            "report_code": "MAIB-09/2021",
            "title": "Catastrophic Engine Room Fuel Line Rupture and Flash Fire",
            "year": 2021,
            "vessel_type": "Container Ship",
            "hazard_domain": "Machinery Space Fire",
            "precursor_pattern": "Unscreened low-pressure return line vibration fatigue, missed during routine walkarounds",
            "casualty_outcome": "Total loss of propulsion, blackout, severe burns to 2nd Engineer",
            "key_safety_action": "Mandatory anti-spray tape verification and thermal imaging audits during every watch",
            "similarity_keywords": ["engine", "fuel", "leak", "fire", "pipe", "spray", "machinery", "vibration"],
        },
        {
            "id": "USCG-2020-04",
            "source": "USCG",
            "report_code": "USCG-MISLE-77182",
            "title": "Mooring Snap-Back Line Failure During High Current Operations",
            "year": 2020,
            "vessel_type": "Oil Tanker",
            "hazard_domain": "Mooring Operations",
            "precursor_pattern": "Synthetic line parted at fairlead due to hidden core abrasion and uneven winch tension",
            "casualty_outcome": "Critical trauma to ordinary seaman standing inside snap-back danger zone",
            "key_safety_action": "Strict deck boundary markings repainted and line retirement after shock load events",
            "similarity_keywords": ["mooring", "snap-back", "line", "rope", "winch", "deck", "fairlead", "parted"],
        },
    ]

    for c in corpus_data:
        existing = db.query(AccidentCorpusModel).filter(AccidentCorpusModel.id == c["id"]).first()
        if not existing:
            record = AccidentCorpusModel(
                id=c["id"],
                source=c["source"],
                report_code=c["report_code"],
                title=c["title"],
                year=c["year"],
                vessel_type=c["vessel_type"],
                hazard_domain=c["hazard_domain"],
                precursor_pattern=c["precursor_pattern"],
                casualty_outcome=c["casualty_outcome"],
                key_safety_action=c["key_safety_action"],
                similarity_keywords=c["similarity_keywords"],
                embedding=[0.0] * 384,
            )
            db.add(record)
    db.commit()
    print("   ✅ Accident Precedent Corpus Seeded")

    # 4. Sample Learnings
    sample_learning = db.query(LearningModel).filter(LearningModel.id == "LRN-DECK-001").first()
    if not sample_learning:
        lrn = LearningModel(
            id="LRN-DECK-001",
            title="Mooring Line Snap-Back Zone Protocol",
            summary="Essential review of deck danger zones during high-load mooring maneuvers.",
            body="Always maintain situational awareness during spring line tensioning. Never step across bights.",
            status="PUBLISHED",
            media=[{
                "media_id": "MED-001",
                "filename": "snap_back_diagram.webp",
                "content_type": "image/webp",
                "url": "/media/snap_back_diagram.webp",
            }],
            target_vessel_ids=["v1", "v2", "MV-PACIFIC-STAR", "MV-OCEAN-TRADER"],
            origin="shore",
        )
        db.add(lrn)
    db.commit()
    print("   ✅ Micro-Learnings Seeded")

    # 5. Checkin Aggregates (k >= 5)
    existing_agg = db.query(CheckinAggregateModel).first()
    if not existing_agg:
        today_str = now_utc().strftime("%Y-%m-%d")
        for v_id in ["v1", "v2", "v3", "MV-PACIFIC-STAR"]:
            agg = CheckinAggregateModel(
                id=uuid7_str(),
                vessel_id=v_id,
                ship_date=today_str,
                department="Deck",
                count=7,
                avg_mood=3.9,
                avg_fatigue=2.1,
                avg_workload=3.0,
                origin="edge",
            )
            db.add(agg)
        db.commit()
    print("   ✅ Checkin Aggregates (k>=5) Seeded")

    # 6. Fleet Near-Miss Tickets & Messages
    existing_t = db.query(TicketModel).filter(TicketModel.id == "t1").first()
    if not existing_t:
        tickets_seed = [
            {
                "id": "t1",
                "ticket_no": "BCN-CORA-0001",
                "vessel_id": "v1",
                "title": "Hydraulic oil leaking from No.2 crane slewing hose",
                "description": "During cargo ops a steady drip of hydraulic oil was noticed at the slewing motor hose fitting of crane No.2. Oil pooled on deck around the pedestal. Operator continued lifting for 20 minutes before stopping.",
                "hazard_type": "crane_lifting",
                "ism_category": "equipment_failure",
                "severity": 4,
                "location_on_board": "Main deck, crane No.2 pedestal",
                "immediate_action": "Crane stopped, area cordoned, absorbent applied.",
                "onboard_status": "ACTION_IN_PROGRESS",
                "shore_status": "NEW",
                "rank_band": "Rating",
                "hours_ago": 3,
                "messages": [
                    {"sender_role": "reporter", "text": "Oil is still dripping slowly after stopping. Absorbent pads are getting soaked."},
                    {"sender_role": "master", "text": "Chief Engineer notified. Hydraulic circuit isolated."},
                ]
            },
            {
                "id": "t2",
                "ticket_no": "BCN-BLUE-0002",
                "vessel_id": "v2",
                "title": "Crane No.3 hydraulic hose weeping at luffing cylinder",
                "description": "Small leak at luffing cylinder hose. Fitting looks corroded. Reported to bosun, crane still in use with monitoring.",
                "hazard_type": "crane_lifting",
                "ism_category": "equipment_failure",
                "severity": 3,
                "location_on_board": "Main deck, crane No.3",
                "immediate_action": "Drip tray placed, hourly checks.",
                "onboard_status": "ACKNOWLEDGED",
                "shore_status": "NEW",
                "rank_band": "Officer",
                "hours_ago": 9,
                "messages": []
            },
            {
                "id": "t3",
                "ticket_no": "BCN-PACI-0003",
                "vessel_id": "v4",
                "title": "क्रेन नंबर 1 की हाइड्रोलिक पाइप से तेल रिसाव",
                "description": "Oil leaking from hydraulic pipe of crane No.1 while lifting hatch cover. Deck became slippery and one AB almost slipped near pedestal.",
                "hazard_type": "crane_lifting",
                "ism_category": "equipment_failure",
                "severity": 4,
                "location_on_board": "Main deck, crane No.1",
                "immediate_action": "Crane isolated. Oil cleaned. Spare hose requested.",
                "onboard_status": "ACTION_IN_PROGRESS",
                "shore_status": "IN_REVIEW",
                "rank_band": "Rating",
                "hours_ago": 30,
                "messages": [
                    {"sender_role": "reporter", "text": "कृपया जल्द से जल्द नई पाइपलाइन की आपूर्ति करें।"},
                    {"sender_role": "shore", "text": "Superintendent reviewing replacement part requisition with technical department."}
                ]
            },
            {
                "id": "t4",
                "ticket_no": "BCN-SEAL-0004",
                "vessel_id": "v3",
                "title": "Gas detector found out of calibration before tank entry",
                "description": "Portable 4-gas meter showed calibration expired 2 weeks ago, noticed during pre-entry checks for cargo tank 3P.",
                "hazard_type": "enclosed_space",
                "ism_category": "procedure_gap",
                "severity": 5,
                "location_on_board": "Cargo tank 3P",
                "immediate_action": "Entry postponed, spare calibrated meter used.",
                "onboard_status": "SUBMITTED",
                "shore_status": "NEW",
                "rank_band": "Officer",
                "hours_ago": 1.5,
                "messages": []
            },
            {
                "id": "t5",
                "ticket_no": "BCN-CORA-0005",
                "vessel_id": "v1",
                "title": "Mooring line chafed through outer strands",
                "description": "Aft spring line found with outer strands chafed through at fairlead. Line had been in service beyond its recorded retirement date.",
                "hazard_type": "mooring",
                "ism_category": "unsafe_condition",
                "severity": 4,
                "location_on_board": "Aft mooring deck",
                "immediate_action": "Line replaced with spare.",
                "onboard_status": "ACTION_IN_PROGRESS",
                "shore_status": "IN_REVIEW",
                "rank_band": "Rating",
                "hours_ago": 50,
                "messages": []
            },
            {
                "id": "t6",
                "ticket_no": "BCN-NORT-0006",
                "vessel_id": "v5",
                "title": "Oil on engine room stairs after purifier overflow",
                "description": "Purifier overflow left oil on the stairs from upper platform. Oiler slipped two steps but held the handrail.",
                "hazard_type": "slip_trip_fall",
                "ism_category": "unsafe_condition",
                "severity": 3,
                "location_on_board": "Engine room, upper platform stairs",
                "immediate_action": "Stairs cleaned, purifier alarm tested.",
                "onboard_status": "RESOLVED_ON_BOARD",
                "shore_status": "NEW",
                "rank_band": "Rating",
                "hours_ago": 6,
                "messages": []
            },
            {
                "id": "t7",
                "ticket_no": "BCN-HARB-0007",
                "vessel_id": "v6",
                "title": "Lashing rod dropped from container bay 18",
                "description": "A lashing rod slipped from a stevedore's hands and fell from tier 3, landing 1 m from a crew member.",
                "hazard_type": "crane_lifting",
                "ism_category": "unsafe_act",
                "severity": 4,
                "location_on_board": "Bay 18, deck",
                "immediate_action": "Ops stopped, stevedore foreman informed.",
                "onboard_status": "ACKNOWLEDGED",
                "shore_status": "NEW",
                "rank_band": "Cadet",
                "hours_ago": 12,
                "messages": []
            },
            {
                "id": "t8",
                "ticket_no": "BCN-BLUE-0008",
                "vessel_id": "v2",
                "title": "Missing guard on workshop grinder",
                "description": "Bench grinder in workshop found without eye shield. Used by fitter without goggles.",
                "hazard_type": "machinery",
                "ism_category": "unsafe_condition",
                "severity": 2,
                "location_on_board": "Engine workshop",
                "immediate_action": "Shield refitted, toolbox talk held.",
                "onboard_status": "RESOLVED_ON_BOARD",
                "shore_status": "CLOSED",
                "rank_band": "Officer",
                "hours_ago": 200,
                "messages": []
            },
            {
                "id": "t9",
                "ticket_no": "BCN-SEAL-0009",
                "vessel_id": "v3",
                "title": "Chemical splash during sampling",
                "description": "During cargo sampling a small splash reached the forearm of the pumpman; gloves were short-cuff.",
                "hazard_type": "chemical",
                "ism_category": "unsafe_act",
                "severity": 3,
                "location_on_board": "Manifold area",
                "immediate_action": "Eyewash used as precaution, long-cuff gloves issued.",
                "onboard_status": "RESOLVED_ON_BOARD",
                "shore_status": "IN_REVIEW",
                "rank_band": "Rating",
                "hours_ago": 72,
                "messages": []
            },
            {
                "id": "t10",
                "ticket_no": "BCN-PACI-0010",
                "vessel_id": "v4",
                "title": "Electrical panel left open in steering gear room",
                "description": "Panel door found open with live terminals exposed after maintenance.",
                "hazard_type": "electrical",
                "ism_category": "unsafe_condition",
                "severity": 3,
                "location_on_board": "Steering gear room",
                "immediate_action": "Panel closed and locked.",
                "onboard_status": "SUBMITTED",
                "shore_status": "NEW",
                "rank_band": "Crew",
                "hours_ago": 20,
                "messages": []
            },
        ]

        for s in tickets_seed:
            t_created = now_utc() - timedelta(hours=s["hours_ago"])
            action_data = {
                "ticket_no": s["ticket_no"],
                "rank_band": s["rank_band"],
                "description_original": s["description"],
                "original_language": "hi" if "क्रेन" in s["title"] else "en",
                "similar_count": 3,
                "actions": [
                    {
                        "text": s["immediate_action"],
                        "responsible_rank": "Chief Officer" if s["severity"] >= 4 else "2nd Engineer",
                        "due_at": (t_created + timedelta(hours=24)).isoformat(),
                        "created_at": t_created.isoformat(),
                    }
                ]
            }

            ticket = TicketModel(
                id=s["id"],
                vessel_id=s["vessel_id"],
                reporter_ref=f"hmac_sha256_anon_{s['id']}",
                title=s["title"],
                description=s["description"],
                location_on_board=s["location_on_board"],
                hazard_type=s["hazard_type"],
                ism_category=s["ism_category"],
                severity=s["severity"],
                immediate_action=s["immediate_action"],
                onboard_status=s["onboard_status"],
                shore_status=s["shore_status"],
                assigned_action=action_data,
                media_refs=[],
                created_at=t_created,
                updated_at=t_created,
                origin="edge",
            )
            db.add(ticket)

            for m in s.get("messages", []):
                msg = TicketMessageModel(
                    id=uuid7_str(),
                    ticket_id=s["id"],
                    sender_role=m["sender_role"],
                    text=m["text"],
                    created_at=t_created + timedelta(minutes=15),
                    origin="edge" if m["sender_role"] != "shore" else "shore",
                )
                db.add(msg)

        db.commit()
        print("   ✅ Fleet Near-Miss Tickets & Messages Seeded")

    print("✨ Shore Database Seeding Complete!")


if __name__ == "__main__":
    init_shore_db()
    with ShoreSessionLocal() as session:
        seed_shore_db(session)

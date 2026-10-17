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
            target_vessel_ids=["MV-PACIFIC-STAR", "MV-OCEAN-TRADER"],
            origin="shore",
        )
        db.add(lrn)
    db.commit()
    print("   ✅ Micro-Learnings Seeded")

    # 5. Checkin Aggregates (k >= 5)
    existing_agg = db.query(CheckinAggregateModel).first()
    if not existing_agg:
        today_str = now_utc().strftime("%Y-%m-%d")
        agg = CheckinAggregateModel(
            id=uuid7_str(),
            vessel_id="MV-PACIFIC-STAR",
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
    print("✨ Shore Database Seeding Complete!")


if __name__ == "__main__":
    init_shore_db()
    with ShoreSessionLocal() as session:
        seed_shore_db(session)

#!/usr/bin/env python3
"""
BigYellow Beacon – Database Seeding Script
Populates both Shore (beacon_shore) and Edge (beacon_edge) PostgreSQL databases
with realistic, comprehensive maritime dummy data for testing and demoing:
- Shore Staff Users (Admin, Reviewer, DPA)
- Fleet Vessels
- Precedent Accident Investigation Corpus
- Near-Miss / Hazard Tickets & Messages
- Safety Learning Campaigns & Daily Anchor Cards
- Crew Wellbeing Aggregates (k >= 5)
- Edge Paired Devices, PINs, Checkins, and Active Pairing Code
"""

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Ensure workspace root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from contract.uuid7 import uuid7_str
from contract.enums import Role, OnboardStatus, ShoreStatus
from contract.db.models import (
    ShoreUserModel,
    VesselModel,
    AccidentCorpusModel,
    LearningModel,
    DailyAnchorCardModel,
    TicketModel,
    TicketMessageModel,
    CheckinAggregateModel,
    DeviceModel,
    PairingCodeModel,
    CheckinModel,
    WelfareRequestModel,
    InboxMessageModel,
)
from shore.db import SessionLocal as ShoreSessionLocal, init_shore_db
from edge.db import SessionLocal as EdgeSessionLocal, init_edge_db
from shore.security import hash_password
from edge.security import hash_pin_argon2


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
            "name": "Safety Reviewer Priya Patel",
        },
        {
            "email": "dpa@beacon.maritime",
            "password": "ShoreDPA2026!",
            "role": Role.SHORE_REVIEWER,
            "name": "Capt. Henrik Larsen (DPA)",
        },
    ]

    for u in users_data:
        existing = db.query(ShoreUserModel).filter_by(email=u["email"]).first()
        if not existing:
            user = ShoreUserModel(
                id=uuid7_str(),
                email=u["email"],
                hashed_password=hash_password(u["password"]),
                role=u["role"],
                name=u["name"],
                created_at=now_utc() - timedelta(days=30),
            )
            db.add(user)
    db.commit()
    print("  ✓ Shore users seeded (admin@beacon.maritime, reviewer@beacon.maritime, dpa@beacon.maritime)")

    # 2. Fleet Vessels
    vessels_data = [
        {
            "id": "MV-PACIFIC-STAR",
            "name": "MV Pacific Star",
            "imo_number": "9481234",
            "api_key": "vessel-api-key-mv-pacific-star",
            "connection_status": "ONLINE",
            "last_sync": now_utc() - timedelta(minutes=12),
        },
        {
            "id": "MV-OCEAN-TRADER",
            "name": "MV Ocean Trader",
            "imo_number": "9652190",
            "api_key": "vessel-api-key-mv-ocean-trader",
            "connection_status": "ONLINE",
            "last_sync": now_utc() - timedelta(minutes=45),
        },
        {
            "id": "MV-ARCTIC-PIONEER",
            "name": "MV Arctic Pioneer",
            "imo_number": "9814421",
            "api_key": "vessel-api-key-mv-arctic-pioneer",
            "connection_status": "VSAT_BURST_SYNCED",
            "last_sync": now_utc() - timedelta(hours=2),
        },
        {
            "id": "MV-INDIAN-OPAL",
            "name": "MV Indian Opal",
            "imo_number": "9518872",
            "api_key": "vessel-api-key-mv-indian-opal",
            "connection_status": "VSAT_BURST_SYNCED",
            "last_sync": now_utc() - timedelta(hours=4),
        },
    ]

    for v in vessels_data:
        existing = db.query(VesselModel).filter_by(id=v["id"]).first()
        if not existing:
            vessel = VesselModel(
                id=v["id"],
                name=v["name"],
                imo_number=v["imo_number"],
                api_key=v["api_key"],
                connection_status=v["connection_status"],
                last_sync_timestamp=v["last_sync"],
                created_at=now_utc() - timedelta(days=120),
            )
            db.add(vessel)
    db.commit()
    print(f"  ✓ {len(vessels_data)} fleet vessels seeded")

    # 3. Accident Corpus
    accidents = [
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
        {
            "id": "IMO-2022-14",
            "source": "IMO",
            "report_code": "IMO-MSC-MEPC.7/Circ.11",
            "title": "Enclosed Space Entry Hypoxia Fatality in Ballast Tank",
            "year": 2022,
            "vessel_type": "Bulk Carrier",
            "hazard_domain": "Enclosed Space Entry",
            "precursor_pattern": "Oxygen meter calibration expired, permit-to-work signed without multi-level ventilation check",
            "casualty_outcome": "Two fatalities due to rapid oxygen depletion (11% O2)",
            "key_safety_action": "Lock-out tag-out on manholes and secondary gas check by Chief Officer required",
            "similarity_keywords": ["enclosed space", "ballast", "oxygen", "tank", "gas", "ventilation", "permit", "entry"],
        },
        {
            "id": "BEA-MER-2023-02",
            "source": "BEA-MER",
            "report_code": "BEA-MER-RPT-2023-02",
            "title": "Pilot Ladder Rung Failure During Night Embarkation in Sea State 4",
            "year": 2023,
            "vessel_type": "Car Carrier (PCTC)",
            "hazard_domain": "Working Aloft / Boarding",
            "precursor_pattern": "Chafed manila side ropes and non-compliant replacement chocks stored without UV protection",
            "casualty_outcome": "Pilot fell into water between pilot boat and hull, rescued with hypothermia",
            "key_safety_action": "ISO 799 ladder certification check and retirement after 30 months mandatory",
            "similarity_keywords": ["pilot", "ladder", "embarkation", "rigging", "fall", "manila", "deck", "gangway"],
        },
        {
            "id": "MAIB-2021-12",
            "source": "MAIB",
            "report_code": "MAIB Report 12/2021",
            "title": "Hydraulic High-Pressure Hose Rupture and Slip/Fall Fatality on Deck",
            "year": 2021,
            "vessel_type": "Bulk Carrier",
            "hazard_domain": "Deck Machinery & Pressurized Hydraulics",
            "precursor_pattern": "Repeated minor hydraulic weeping at crane manifold ignored; oil film covered non-skid surface",
            "casualty_outcome": "1 Crew Member fatally injured due to slip under swinging hatch cover during sea swells",
            "key_safety_action": "Mandatory lockout and zero-tolerance barrier isolation for all crane hydraulic weeping; 100% degreasing",
            "similarity_keywords": ["hydraulic", "leak", "crane", "oil", "slip", "pressure", "deck plate"],
        },
    ]

    for acc in accidents:
        existing = db.query(AccidentCorpusModel).filter_by(id=acc["id"]).first()
        if not existing:
            item = AccidentCorpusModel(
                id=acc["id"],
                source=acc["source"],
                report_code=acc["report_code"],
                title=acc["title"],
                year=acc["year"],
                vessel_type=acc["vessel_type"],
                hazard_domain=acc["hazard_domain"],
                precursor_pattern=acc["precursor_pattern"],
                casualty_outcome=acc["casualty_outcome"],
                key_safety_action=acc["key_safety_action"],
                similarity_keywords=acc["similarity_keywords"],
                embedding=[0.0] * 384,
            )
            db.add(item)
    db.commit()
    print(f"  ✓ {len(accidents)} accident corpus precedent records seeded")

    # 4. Safety Learning Campaigns
    learnings = [
        {
            "id": "LRN-SNAPBACK-001",
            "title": "Mooring Snap-Back Danger Zones & Line Inspection",
            "summary": "Mandatory refresher on mooring snap-back zones and high-modulus line degradation warnings.",
            "body": "### Mooring Snap-Back Safety Alert\n\nWhen a mooring line under high tension parts, the sudden release of energy causes the two severed ends to whip back toward their anchor points with fatal velocity.\n\n#### Key Rules:\n1. Never stand inside the painted snap-back safety boundary or within the bight of any rope.\n2. Regularly inspect synthetic tails for internal friction powdered fibers and glazed jackets.\n3. Stop the winch immediately if uneven tension is detected.",
            "status": "PUBLISHED",
            "media": [
                {"type": "image", "url": "/media/snapback_zone_diagram.webp", "duration_seconds": 0}
            ],
            "target_vessel_ids": ["MV-PACIFIC-STAR", "MV-OCEAN-TRADER", "MV-ARCTIC-PIONEER"],
        },
        {
            "id": "LRN-HYDRAULIC-002",
            "title": "High-Pressure Hydraulic Systems: Weeping vs Pinholes",
            "summary": "Protocol for immediate isolation of weeping hydraulic lines before pressure-induced flash fires occur.",
            "body": "### Hydraulic Safety Briefing\n\nHigh-pressure hydraulic leaks at pinhole size can atomize mineral oil into a highly explosive mist, or cause catastrophic skin penetration injuries.\n\n#### Action Checklist:\n- Always run cardboard or wood along pressurized fittings to detect leaks — NEVER use bare or gloved hands.\n- Report and isolate any manifold weep, no matter how minor.",
            "status": "PUBLISHED",
            "media": [
                {"type": "image", "url": "/media/hydraulic_manifold_safety.webp", "duration_seconds": 0}
            ],
            "target_vessel_ids": ["MV-PACIFIC-STAR", "MV-INDIAN-OPAL"],
        },
        {
            "id": "LRN-ENCLOSED-003",
            "title": "Enclosed Space Entry Multi-Gas Calibration Verification",
            "summary": "Mandatory procedures for atmospheric checks and permit validation prior to entering ballast tanks.",
            "body": "### Enclosed Space Entry Protocols\n\nAtmospheres in enclosed spaces can become lethal due to oxygen depletion, rust oxidation, or toxic vapor accumulation.\n\n#### Mandatory Verification:\n1. O2 >= 20.9%\n2. LEL < 1%\n3. CO < 25 ppm, H2S < 5 ppm\n4. Dedicated standby watch with emergency rescue equipment pre-rigged.",
            "status": "PUBLISHED",
            "media": [],
            "target_vessel_ids": ["MV-PACIFIC-STAR", "MV-OCEAN-TRADER", "MV-ARCTIC-PIONEER", "MV-INDIAN-OPAL"],
        },
        {
            "id": "LRN-FATIGUE-004",
            "title": "Managing Watch Fatigue During High-Density Traffic Transits",
            "summary": "Strategies for sleep hygiene and circadian disruption prevention during canal and strait transits.",
            "body": "Draft guidance on staggered watch schedules and fatigue mitigation under MLC 2006 compliance.",
            "status": "DRAFT",
            "media": [],
            "target_vessel_ids": [],
        },
    ]

    for lrn in learnings:
        existing = db.query(LearningModel).filter_by(id=lrn["id"]).first()
        if not existing:
            l_model = LearningModel(
                id=lrn["id"],
                title=lrn["title"],
                summary=lrn["summary"],
                body=lrn["body"],
                status=lrn["status"],
                media=lrn["media"],
                target_vessel_ids=lrn["target_vessel_ids"],
                origin="shore",
                version=1,
                created_at=now_utc() - timedelta(days=5),
                updated_at=now_utc() - timedelta(days=2),
            )
            db.add(l_model)
    db.commit()
    print(f"  ✓ {len(learnings)} safety learning campaigns seeded")

    # 5. Daily Anchor Cards
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow_str = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

    anchor_cards = [
        {
            "id": "CARD-YESTERDAY",
            "topic": "Clear the Bight",
            "quote_or_prompt": "Always check your footing and ensure neither foot is inside a line bight when tensioning winches.",
            "scheduled_date": yesterday_str,
            "translations": {
                "tl": "Tiyakin na wala ang iyong paa sa loob ng bight ng lubid kapag humihila.",
                "hi": "रस्सी खींचते समय हमेशा सुनिश्चित करें कि आपका पैर रस्सी के फंदे में न हो।",
            },
        },
        {
            "id": "CARD-TODAY",
            "topic": "Two-Person Verification",
            "quote_or_prompt": "Never enter an enclosed space without an active topside watch standing by with an EEBD ready.",
            "scheduled_date": today_str,
            "translations": {
                "tl": "Huwag pumasok sa saradong espasyo nang walang bantay sa itaas na may EEBD.",
                "hi": "बिना टॉपसाइड वॉच और ईईबीडी के कभी भी बंद कमरे में प्रवेश न करें।",
            },
        },
        {
            "id": "CARD-TOMORROW",
            "topic": "Report the Weep",
            "quote_or_prompt": "A minor oil weep today is tomorrow's flash fire or deck slip. Tag and report it immediately.",
            "scheduled_date": tomorrow_str,
            "translations": {
                "tl": "Ang maliit na tulo ng langis ngayon ay maaaring magdulot ng sunog bukas. Iulat agad.",
                "hi": "आज का छोटा तेल रिसाव कल की बड़ी आग बन सकता है। तुरंत रिपोर्ट करें।",
            },
        },
    ]

    for card in anchor_cards:
        existing = db.query(DailyAnchorCardModel).filter_by(id=card["id"]).first()
        if not existing:
            c_model = DailyAnchorCardModel(
                id=card["id"],
                topic=card["topic"],
                quote_or_prompt=card["quote_or_prompt"],
                translations=card["translations"],
                scheduled_date=card["scheduled_date"],
                origin="shore",
                version=1,
            )
            db.add(c_model)
    db.commit()
    print(f"  ✓ {len(anchor_cards)} daily anchor cards seeded (including today: {today_str})")

    # 6. Checkin Aggregates (k >= 5)
    aggregates = [
        {
            "id": "AGG-DECK-TODAY",
            "vessel_id": "MV-PACIFIC-STAR",
            "ship_date": today_str,
            "department": "Deck",
            "count": 7,
            "avg_mood": 4.1,
            "avg_fatigue": 2.3,
            "avg_workload": 3.4,
        },
        {
            "id": "AGG-ENG-TODAY",
            "vessel_id": "MV-PACIFIC-STAR",
            "ship_date": today_str,
            "department": "Engine",
            "count": 6,
            "avg_mood": 3.8,
            "avg_fatigue": 2.7,
            "avg_workload": 3.9,
        },
        {
            "id": "AGG-CAT-TODAY",
            "vessel_id": "MV-PACIFIC-STAR",
            "ship_date": today_str,
            "department": "Catering",
            "count": 5,
            "avg_mood": 4.4,
            "avg_fatigue": 1.9,
            "avg_workload": 2.8,
        },
    ]

    for agg in aggregates:
        existing = db.query(CheckinAggregateModel).filter_by(id=agg["id"]).first()
        if not existing:
            a_model = CheckinAggregateModel(
                id=agg["id"],
                vessel_id=agg["vessel_id"],
                ship_date=agg["ship_date"],
                department=agg["department"],
                count=agg["count"],
                avg_mood=agg["avg_mood"],
                avg_fatigue=agg["avg_fatigue"],
                avg_workload=agg["avg_workload"],
                origin="edge",
                version=1,
            )
            db.add(a_model)
    db.commit()
    print(f"  ✓ {len(aggregates)} checkin aggregate entries seeded (satisfying k >= 5 anonymity)")

    # 7. Tickets & Messages
    tickets = [
        {
            "id": "TCK-PACIFIC-001",
            "vessel_id": "MV-PACIFIC-STAR",
            "reporter_ref": "REF-HMAC-AB-01",
            "title": "Hydraulic oil weeping on Crane #2 winch drum",
            "description": "Noticeable hydraulic fluid weeping from the high-pressure return manifold during cargo grab testing in bay 3.",
            "location_on_board": "Main Deck – Port Crane #2 Base",
            "hazard_type": "Machinery / Hydraulic Pressure",
            "ism_category": "ISM 10 (Maintenance of the Ship and Equipment)",
            "severity": 3,
            "immediate_action": "Manifold shut down, drip tray installed, degreased deck plate non-skid surface.",
            "onboard_status": OnboardStatus.ACTION_ASSIGNED.value,
            "shore_status": ShoreStatus.IN_REVIEW.value,
            "assigned_action": {
                "assigned_to": "Chief Engineer",
                "target_date": tomorrow_str,
                "notes": "Replace manifold O-rings using spare kit #H2-CRANE.",
            },
            "media_refs": ["/media/crane_oil_weep.jpg"],
            "messages": [
                {
                    "sender_role": "crew",
                    "text": "Drip tray placed and oil absorbent pads secured. Crane is currently isolated with lock-out tag.",
                },
                {
                    "sender_role": "shore_reviewer",
                    "text": "Report received and precedent matched to MAIB 12/2021. Fleet engineering recommends checking entire return manifold flange for vibration micro-cracks before re-pressurizing.",
                },
            ],
        },
        {
            "id": "TCK-PACIFIC-002",
            "vessel_id": "MV-PACIFIC-STAR",
            "reporter_ref": "REF-HMAC-BOSUN-02",
            "title": "Chafed synthetic mooring tail at fairlead #4",
            "description": "Aft mooring spring line tail shows core yarn powdering and cover jacket abrasion exceeding 10% diameter.",
            "location_on_board": "Aft Mooring Deck – Poop Deck Fairlead #4",
            "hazard_type": "Mooring & Lines",
            "ism_category": "ISM 7 (Shipboard Operations)",
            "severity": 4,
            "immediate_action": "Line tagged out of service and replaced with spare 220m 8-strand polypropylene tail from store.",
            "onboard_status": OnboardStatus.RESOLVED.value,
            "shore_status": ShoreStatus.CLOSED.value,
            "assigned_action": None,
            "media_refs": ["/media/mooring_tail_chafed.jpg"],
            "messages": [
                {
                    "sender_role": "crew",
                    "text": "Replacement completed by Bosun and 2 Able Seamen. Defective tail cut into test lengths and disposed.",
                },
                {
                    "sender_role": "shore_reviewer",
                    "text": "Approved and closed. Excellent proactiveness adhering to Mooring Line snap-back protocol.",
                },
            ],
        },
        {
            "id": "TCK-PACIFIC-003",
            "vessel_id": "MV-PACIFIC-STAR",
            "reporter_ref": "REF-HMAC-CREW-03",
            "title": "Loose handrail clamp on monkey island ladder",
            "description": "Top securing clamp on vertical ladder leading to monkey island radar platform is loose with stripped bolt threading.",
            "location_on_board": "Monkey Island / Navigation Bridge Exterior",
            "hazard_type": "Working at Height / Slips, Trips & Falls",
            "ism_category": "ISM 10 (Maintenance of the Ship and Equipment)",
            "severity": 2,
            "immediate_action": "Safety caution tape wrapped around ladder bottom rung pending re-bolting.",
            "onboard_status": OnboardStatus.NEW.value,
            "shore_status": ShoreStatus.NEW.value,
            "assigned_action": None,
            "media_refs": [],
            "messages": [],
        },
    ]

    for tck in tickets:
        existing = db.query(TicketModel).filter_by(id=tck["id"]).first()
        if not existing:
            ticket = TicketModel(
                id=tck["id"],
                vessel_id=tck["vessel_id"],
                reporter_ref=tck["reporter_ref"],
                title=tck["title"],
                description=tck["description"],
                location_on_board=tck["location_on_board"],
                hazard_type=tck["hazard_type"],
                ism_category=tck["ism_category"],
                severity=tck["severity"],
                immediate_action=tck["immediate_action"],
                onboard_status=tck["onboard_status"],
                shore_status=tck["shore_status"],
                assigned_action=tck["assigned_action"],
                media_refs=tck["media_refs"],
                origin="edge",
                version=1,
                created_at=now_utc() - timedelta(days=2),
                updated_at=now_utc() - timedelta(hours=6),
            )
            db.add(ticket)
            db.commit()

            for msg in tck.get("messages", []):
                m_model = TicketMessageModel(
                    id=uuid7_str(),
                    ticket_id=tck["id"],
                    sender_role=msg["sender_role"],
                    text=msg["text"],
                    origin="edge" if msg["sender_role"] == "crew" else "shore",
                    version=1,
                    created_at=now_utc() - timedelta(hours=12),
                    updated_at=now_utc() - timedelta(hours=12),
                )
                db.add(m_model)
            db.commit()

    print(f"  ✓ {len(tickets)} near-miss/hazard tickets and associated messages seeded")


def seed_edge_db(db):
    print("⚓ Seeding Edge Database (beacon_edge)...")

    # 1. Edge Vessel Record
    vessel = db.query(VesselModel).filter_by(id="MV-PACIFIC-STAR").first()
    if not vessel:
        vessel = VesselModel(
            id="MV-PACIFIC-STAR",
            name="MV Pacific Star",
            imo_number="9481234",
            api_key="vessel-api-key-mv-pacific-star",
            connection_status="ONLINE",
            last_sync_timestamp=now_utc() - timedelta(minutes=10),
            created_at=now_utc() - timedelta(days=120),
        )
        db.add(vessel)
        db.commit()
    print("  ✓ Shipboard vessel record registered (MV-PACIFIC-STAR)")

    # 2. Master Pairing Code for Crew Onboarding
    pairing_code = db.query(PairingCodeModel).filter_by(code="BEACON").first()
    if not pairing_code:
        pairing_code = PairingCodeModel(
            code="BEACON",
            vessel_id="MV-PACIFIC-STAR",
            created_by="MASTER-001",
            created_at=now_utc(),
            expires_at=now_utc() + timedelta(days=30),
            used=False,
        )
        db.add(pairing_code)
        db.commit()
    print("  ✓ Active pairing code generated: 'BEACON' (expires in 30 days)")

    # 3. Paired Crew Devices with Argon2 PINs
    # Crew PIN: 1234 (4 digits)
    # Master PIN: 123456 (6 digits)
    devices = [
        {
            "id": "DEV-MASTER-001",
            "token": "dev-token-master-001",
            "vessel_id": "MV-PACIFIC-STAR",
            "department": "Deck",
            "crew_id": "MASTER-001",
            "crew_name": "Captain Robert Evans",
            "pin": "123456",
            "language": "en",
        },
        {
            "id": "DEV-MATE-002",
            "token": "dev-token-mate-002",
            "vessel_id": "MV-PACIFIC-STAR",
            "department": "Deck",
            "crew_id": "CHIEFMATE-002",
            "crew_name": "Chief Mate Liam Smith",
            "pin": "1234",
            "language": "en",
        },
        {
            "id": "DEV-CREW-003",
            "token": "dev-token-crew-003",
            "vessel_id": "MV-PACIFIC-STAR",
            "department": "Deck",
            "crew_id": "CREW-AB-003",
            "crew_name": "Able Seaman Mateo Perez",
            "pin": "1234",
            "language": "tl",
        },
        {
            "id": "DEV-CHIEFENG-004",
            "token": "dev-token-ce-004",
            "vessel_id": "MV-PACIFIC-STAR",
            "department": "Engine",
            "crew_id": "CHIEFENG-004",
            "crew_name": "Chief Engineer Marco Rossi",
            "pin": "1234",
            "language": "en",
        },
        {
            "id": "DEV-OILER-005",
            "token": "dev-token-oiler-005",
            "vessel_id": "MV-PACIFIC-STAR",
            "department": "Engine",
            "crew_id": "CREW-OILER-005",
            "crew_name": "Oiler Tariq Al-Mansoor",
            "pin": "1234",
            "language": "hi",
        },
    ]

    for d in devices:
        existing = db.query(DeviceModel).filter_by(token=d["token"]).first()
        if not existing:
            device = DeviceModel(
                id=d["id"],
                token=d["token"],
                vessel_id=d["vessel_id"],
                department=d["department"],
                crew_id=d["crew_id"],
                crew_name=d["crew_name"],
                hashed_pin=hash_pin_argon2(d["pin"]),
                failed_login_attempts=0,
                locked_until=None,
                preferred_language=d["language"],
                notifications_enabled=True,
                created_at=now_utc() - timedelta(days=20),
                expires_at=now_utc() + timedelta(days=180),
            )
            db.add(device)
    db.commit()
    print(f"  ✓ {len(devices)} crew devices paired and seeded (PIN: 123456 for Master, 1234 for Crew)")

    # 4. Checkins (Confidential shipboard checkins)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    checkins = [
        {"crew_id": "CREW-AB-003", "ship_date": today_str, "mode": "quick", "mood": 4, "fatigue": 2, "workload": 3},
        {"crew_id": "CHIEFMATE-002", "ship_date": today_str, "mode": "quick", "mood": 4, "fatigue": 2, "workload": 4},
        {"crew_id": "CHIEFENG-004", "ship_date": today_str, "mode": "chat", "mood": 3, "fatigue": 3, "workload": 4},
        {"crew_id": "CREW-OILER-005", "ship_date": today_str, "mode": "quick", "mood": 4, "fatigue": 2, "workload": 3},
        {"crew_id": "MASTER-001", "ship_date": today_str, "mode": "quick", "mood": 5, "fatigue": 1, "workload": 3},
        {"crew_id": "CREW-AB-003", "ship_date": yesterday_str, "mode": "quick", "mood": 4, "fatigue": 2, "workload": 3},
        {"crew_id": "CHIEFENG-004", "ship_date": yesterday_str, "mode": "quick", "mood": 3, "fatigue": 3, "workload": 4},
    ]

    for c in checkins:
        existing = db.query(CheckinModel).filter_by(crew_id=c["crew_id"], ship_date=c["ship_date"]).first()
        if not existing:
            checkin = CheckinModel(
                id=uuid7_str(),
                crew_id=c["crew_id"],
                vessel_id="MV-PACIFIC-STAR",
                ship_date=c["ship_date"],
                mode=c["mode"],
                mood=c["mood"],
                fatigue=c["fatigue"],
                workload=c["workload"],
                created_at=now_utc() - timedelta(hours=6),
            )
            db.add(checkin)
    db.commit()
    print(f"  ✓ {len(checkins)} crew checkins seeded")

    # 5. Consented Welfare Requests to Master
    welfare_reqs = [
        {
            "id": "WLF-001",
            "vessel_id": "MV-PACIFIC-STAR",
            "crew_id": "CREW-AB-003",
            "crew_name": "Able Seaman Mateo Perez",
            "requested_at": now_utc() - timedelta(days=1),
            "acknowledged": True,
            "acknowledged_at": now_utc() - timedelta(hours=20),
        },
        {
            "id": "WLF-002",
            "vessel_id": "MV-PACIFIC-STAR",
            "crew_id": "CREW-OILER-005",
            "crew_name": "Oiler Tariq Al-Mansoor",
            "requested_at": now_utc() - timedelta(hours=3),
            "acknowledged": False,
            "acknowledged_at": None,
        },
    ]

    for w in welfare_reqs:
        existing = db.query(WelfareRequestModel).filter_by(id=w["id"]).first()
        if not existing:
            item = WelfareRequestModel(
                id=w["id"],
                vessel_id=w["vessel_id"],
                crew_id=w["crew_id"],
                crew_name=w["crew_name"],
                requested_at=w["requested_at"],
                acknowledged=w["acknowledged"],
                acknowledged_at=w["acknowledged_at"],
            )
            db.add(item)
    db.commit()
    print(f"  ✓ {len(welfare_reqs)} Master welfare requests seeded")

    # 6. Inbox Messages
    inbox_messages = [
        {
            "recipient_crew_id": "CREW-AB-003",
            "title": "Daily Safety Anchor Available",
            "body": "Today's safety card 'Two-Person Verification' is ready. Take 60 seconds to review before muster.",
            "read": False,
        },
        {
            "recipient_crew_id": "CREW-AB-003",
            "title": "Ticket Update: Chafed Mooring Tail #4",
            "body": "Shore safety team reviewed and approved closure of TCK-PACIFIC-002.",
            "read": True,
        },
        {
            "recipient_crew_id": "CHIEFENG-004",
            "title": "Action Assigned: Crane #2 Manifold",
            "body": "You have been assigned action item on TCK-PACIFIC-001: Replace manifold O-rings using spare kit #H2-CRANE.",
            "read": False,
        },
    ]

    for m in inbox_messages:
        existing = db.query(InboxMessageModel).filter_by(recipient_crew_id=m["recipient_crew_id"], title=m["title"]).first()
        if not existing:
            item = InboxMessageModel(
                id=uuid7_str(),
                recipient_crew_id=m["recipient_crew_id"],
                title=m["title"],
                body=m["body"],
                read=m["read"],
                created_at=now_utc() - timedelta(hours=4),
            )
            db.add(item)
    db.commit()
    print(f"  ✓ {len(inbox_messages)} crew inbox notifications seeded")

    # 7. Sync Edge Tickets and Cards from Shore
    # In a real sync, edge pulls tickets and anchor cards from shore.
    # We populate the same tickets and cards on edge so offline edge UI has them immediately.
    shore_db = ShoreSessionLocal()
    shore_tickets = shore_db.query(TicketModel).filter_by(vessel_id="MV-PACIFIC-STAR").all()
    for st in shore_tickets:
        existing = db.query(TicketModel).filter_by(id=st.id).first()
        if not existing:
            et = TicketModel(
                id=st.id,
                vessel_id=st.vessel_id,
                reporter_ref=st.reporter_ref,
                title=st.title,
                description=st.description,
                location_on_board=st.location_on_board,
                hazard_type=st.hazard_type,
                ism_category=st.ism_category,
                severity=st.severity,
                immediate_action=st.immediate_action,
                onboard_status=st.onboard_status,
                shore_status=st.shore_status,
                assigned_action=st.assigned_action,
                media_refs=st.media_refs,
                origin=st.origin,
                version=st.version,
                created_at=st.created_at,
                updated_at=st.updated_at,
            )
            db.add(et)
    db.commit()

    shore_cards = shore_db.query(DailyAnchorCardModel).all()
    for sc in shore_cards:
        existing = db.query(DailyAnchorCardModel).filter_by(id=sc.id).first()
        if not existing:
            ec = DailyAnchorCardModel(
                id=sc.id,
                topic=sc.topic,
                quote_or_prompt=sc.quote_or_prompt,
                translations=sc.translations,
                scheduled_date=sc.scheduled_date,
                origin=sc.origin,
                version=sc.version,
                created_at=sc.created_at,
                updated_at=sc.updated_at,
            )
            db.add(ec)
    db.commit()

    shore_learnings = shore_db.query(LearningModel).filter_by(status="PUBLISHED").all()
    for sl in shore_learnings:
        existing = db.query(LearningModel).filter_by(id=sl.id).first()
        if not existing:
            el = LearningModel(
                id=sl.id,
                title=sl.title,
                summary=sl.summary,
                body=sl.body,
                status=sl.status,
                media=sl.media,
                target_vessel_ids=sl.target_vessel_ids,
                origin=sl.origin,
                version=sl.version,
                created_at=sl.created_at,
                updated_at=sl.updated_at,
            )
            db.add(el)
    db.commit()
    shore_db.close()
    print("  ✓ Synchronized tickets, daily cards, and published learnings to Edge")


def main():
    print("================================================================")
    print("⚓ BigYellow Beacon – Database Seeder")
    print("================================================================")

    init_shore_db()
    init_edge_db()

    shore_db = ShoreSessionLocal()
    try:
        seed_shore_db(shore_db)
    finally:
        shore_db.close()

    edge_db = EdgeSessionLocal()
    try:
        seed_edge_db(edge_db)
    finally:
        edge_db.close()

    print("\n🎉 All dummy data has been successfully seeded into PostgreSQL!")
    print("\n--- Summary of Credentials for Testing ---")
    print("🏢 SHORE API (Port 8001 / Cloud Run):")
    print("  • Admin:    admin@beacon.maritime    / Password: ShoreAdmin2026!")
    print("  • Reviewer: reviewer@beacon.maritime / Password: ShoreReviewer2026!")
    print("  • DPA:      dpa@beacon.maritime      / Password: ShoreDPA2026!")
    print("\n⚓ EDGE API (Port 8000 / Shipboard):")
    print("  • Pairing Code: BEACON")
    print("  • Master Device: Token 'dev-token-master-001' / PIN: 123456")
    print("  • Crew Device:   Token 'dev-token-crew-003'   / PIN: 1234")
    print("  • Engineer:      Token 'dev-token-ce-004'     / PIN: 1234")
    print("================================================================")


if __name__ == "__main__":
    main()

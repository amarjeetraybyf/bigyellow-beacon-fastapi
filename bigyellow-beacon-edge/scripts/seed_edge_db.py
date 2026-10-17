#!/usr/bin/env python3
"""
BigYellow Beacon – Edge Database Seeding Script
Populates the Edge SQLite/PostgreSQL database with realistic shipboard dummy data:
- Edge Vessel Record (MV-PACIFIC-STAR)
- Active Pairing Code ('BEACON')
- Paired Crew Devices with Argon2 PINs (Master: 123456, Crew: 1234)
- Confidential Crew Wellbeing Checkins
- Welfare Requests
- Inbox Messages
- Local Tickets & Ticket Messages
- Daily Anchor Cards
- Safety Learnings
"""

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Ensure repo root is in sys.path
REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

from contract.uuid7 import uuid7_str
from contract.enums import Role, OnboardStatus, ShoreStatus, LearningStatus
from db_models import (
    VesselModel,
    PairingCodeModel,
    DeviceModel,
    CheckinModel,
    WelfareRequestModel,
    InboxMessageModel,
    TicketModel,
    TicketMessageModel,
    DailyAnchorCardModel,
    LearningModel,
)
from db import SessionLocal, init_edge_db
from security import hash_pin_argon2


def now_utc():
    return datetime.now(timezone.utc)


def seed_edge_db(db):
    print("⚓ Seeding Edge Database...")

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
    print(f"  ✓ {len(checkins)} confidential crew checkins seeded")

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
        existing = (
            db.query(InboxMessageModel)
            .filter_by(recipient_crew_id=m["recipient_crew_id"], title=m["title"])
            .first()
        )
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

    # 7. Tickets & Messages
    tickets = [
        {
            "id": "TCK-PACIFIC-001",
            "vessel_id": "MV-PACIFIC-STAR",
            "reporter_ref": "REF-HMAC-CREW-01",
            "title": "Hydraulic oil leak near Deck Crane #2 base",
            "description": "Observed continuous dripping from high-pressure flange joint onto walkway.",
            "location_on_board": "Main Deck Forward, Port side Crane #2",
            "hazard_type": "Slip Hazard / Environmental Spill Risk",
            "ism_category": "ISM 10 (Maintenance of the Ship and Equipment)",
            "severity": 3,
            "immediate_action": "Barricaded perimeter with warning tape, spread chemical absorbent sawdust.",
            "onboard_status": OnboardStatus.ACTION_IN_PROGRESS.value,
            "shore_status": ShoreStatus.IN_REVIEW.value,
            "assigned_action": "Chief Engineer to inspect flange gaskets and replace packing seal.",
            "media_refs": ["/media/crane_flange_leak.jpg"],
            "messages": [
                {
                    "sender_role": "crew",
                    "text": "Leak seems to increase when crane is slewing under light hook load.",
                },
                {
                    "sender_role": "master",
                    "text": "Chief Engineer has isolated hydraulic supply valve 14-B pending gasket change.",
                },
            ],
        },
        {
            "id": "TCK-PACIFIC-002",
            "vessel_id": "MV-PACIFIC-STAR",
            "reporter_ref": "REF-HMAC-CREW-02",
            "title": "Chafed synthetic mooring line tail at Poop Deck fairlead",
            "description": "Port forward breast line synthetic tail exhibits severe chafing exceeding 25% strand diameter.",
            "location_on_board": "Aft Mooring Deck / Poop Deck Fairlead #3",
            "hazard_type": "High Energy Parting Hazard / Snap-back Zone",
            "ism_category": "ISM 6 (Resources and Personnel) & ISM 9 (Reports and Analysis of Non-Conformities)",
            "severity": 4,
            "immediate_action": "Line condemned from service; spare 110mm mooring tail unspooled from store.",
            "onboard_status": OnboardStatus.CLOSED.value,
            "shore_status": ShoreStatus.CLOSED.value,
            "assigned_action": "Bosun completed replacement and inspected fairlead roller for burrs.",
            "media_refs": ["/media/chafed_tail.jpg"],
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
                    origin="edge" if msg["sender_role"] in ("crew", "master") else "shore",
                    version=1,
                    created_at=now_utc() - timedelta(hours=12),
                    updated_at=now_utc() - timedelta(hours=12),
                )
                db.add(m_model)
            db.commit()

    print(f"  ✓ {len(tickets)} local near-miss tickets and threads seeded")

    # 8. Daily Anchor Cards
    cards = [
        {
            "id": "CARD-001",
            "topic": "Snap-Back Zone Awareness",
            "quote_or_prompt": "No warning horn sounds before a mooring line parts under 60-ton tension. Step outside the line of fire every single time.",
            "scheduled_date": today_str,
        },
        {
            "id": "CARD-002",
            "topic": "Pre-Watch Handover Focus",
            "quote_or_prompt": "A calm, unhurried 5-minute watch turnover catches blind spots before fatigue turns into incidents.",
            "scheduled_date": yesterday_str,
        },
    ]
    for c in cards:
        existing = db.query(DailyAnchorCardModel).filter_by(id=c["id"]).first()
        if not existing:
            card = DailyAnchorCardModel(
                id=c["id"],
                topic=c["topic"],
                quote_or_prompt=c["quote_or_prompt"],
                scheduled_date=c["scheduled_date"],
                origin="shore",
                version=1,
                created_at=now_utc() - timedelta(days=5),
                updated_at=now_utc() - timedelta(days=5),
            )
            db.add(card)
    db.commit()
    print(f"  ✓ {len(cards)} Daily Anchor cards seeded")

    # 9. Published Learnings
    learnings = [
        {
            "id": "LRN-001",
            "title": "Deck Crane Hydraulic Line Failures & Walkway Slip Prevention",
            "summary": "Recent fleet audit highlighted 3 hydraulic seal ruptures under load.",
            "body": "Always verify high pressure hose date codes and maintain drip trays during cargo loading operations.",
            "status": LearningStatus.PUBLISHED.value,
        },
        {
            "id": "LRN-002",
            "title": "Mooring Snap-Back Zones & Synthetic Tail Replacement Criteria",
            "summary": "Synthetic tails degrade rapidly under cyclic cyclic dock surging.",
            "body": "Inspect tails before every mooring operation. Condemn lines with flat spots exceeding 10% diameter.",
            "status": LearningStatus.PUBLISHED.value,
        },
    ]
    for l in learnings:
        existing = db.query(LearningModel).filter_by(id=l["id"]).first()
        if not existing:
            learning = LearningModel(
                id=l["id"],
                title=l["title"],
                summary=l["summary"],
                body=l["body"],
                status=l["status"],
                origin="shore",
                version=1,
                created_at=now_utc() - timedelta(days=10),
                updated_at=now_utc() - timedelta(days=10),
            )
            db.add(learning)
    db.commit()
    print(f"  ✓ {len(learnings)} published safety learnings seeded")


def main():
    print("================================================================")
    print("⚓ BigYellow Beacon – Edge Database Seeder")
    print("================================================================")
    init_edge_db()
    db = SessionLocal()
    try:
        seed_edge_db(db)
        print("\n✅ Edge Database Seeding Completed Successfully!")
    finally:
        db.close()


if __name__ == "__main__":
    main()

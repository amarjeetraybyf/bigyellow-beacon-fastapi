"""
Precedent Accident & Incident Similarity Service
Searches over existing Tickets and historical Accident Investigation Corpus:
Sources: MAIB, IMO, USCG, BEA-MER.
Ranking: embeddinggemma cosine similarity (local Ollama) with keyword-overlap fallback.
Corpus embeddings are computed lazily and cached in the JSON `embedding` column.
"""

import math
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from db_models import AccidentCorpusModel, TicketModel
from services.shore_ai_service import embed_text

logger = logging.getLogger("shore.similarity")

# Max historical tickets embedded per request (on-the-fly, not persisted)
MAX_TICKETS_EMBEDDED = 50

ACCIDENT_SEED_DATA = [
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
]


def ensure_corpus_seeded(db: Session):
    count = db.query(AccidentCorpusModel).count()
    if count == 0:
        for record in ACCIDENT_SEED_DATA:
            db_item = AccidentCorpusModel(
                id=record["id"],
                source=record["source"],
                report_code=record["report_code"],
                title=record["title"],
                year=record["year"],
                vessel_type=record["vessel_type"],
                hazard_domain=record["hazard_domain"],
                precursor_pattern=record["precursor_pattern"],
                casualty_outcome=record["casualty_outcome"],
                key_safety_action=record["key_safety_action"],
                similarity_keywords=record["similarity_keywords"],
                embedding=[0.0] * 384,
            )
            db.add(db_item)
        db.commit()


def compute_keyword_similarity(text1: str, keywords: List[str]) -> float:
    text_lower = text1.lower()
    matches = sum(1 for kw in keywords if kw.lower() in text_lower)
    return min(1.0, matches / max(1, len(keywords)) * 2.0)


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _corpus_text(item: AccidentCorpusModel) -> str:
    return " ".join(
        filter(None, [item.title, item.hazard_domain, item.precursor_pattern, item.key_safety_action])
    )


def _get_corpus_embedding(db: Session, item: AccidentCorpusModel) -> Optional[List[float]]:
    """Returns a cached corpus embedding, computing and persisting it if missing/zeroed."""
    emb = item.embedding
    if isinstance(emb, list) and emb and any(v != 0.0 for v in emb):
        return emb
    emb = embed_text(_corpus_text(item))
    if emb:
        item.embedding = emb
        db.commit()
    return emb


def _corpus_result(item: AccidentCorpusModel, score: float, method: str) -> Dict[str, Any]:
    return {
        "type": "accident_corpus",
        "id": item.id,
        "source": item.source,
        "report_code": item.report_code,
        "title": item.title,
        "year": item.year,
        "hazard_domain": item.hazard_domain,
        "precursor_pattern": item.precursor_pattern,
        "casualty_outcome": item.casualty_outcome,
        "key_safety_action": item.key_safety_action,
        "similarity_score": round(score, 3),
        "method": method,
    }


def _ticket_result(t: TicketModel, score: float, method: str) -> Dict[str, Any]:
    return {
        "type": "ticket",
        "id": t.id,
        "vessel_id": t.vessel_id,
        "title": t.title,
        "hazard_type": t.hazard_type,
        "severity": t.severity,
        "similarity_score": round(score, 3),
        "method": method,
    }


def find_similar_precedents(
    db: Session,
    query_text: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Finds precedents from accident_corpus and similar tickets.
    Preferred: embeddinggemma cosine similarity via local Ollama.
    Fallback: keyword overlap when embeddings are unavailable.
    """
    ensure_corpus_seeded(db)

    corpus_records = db.query(AccidentCorpusModel).all()
    tickets = db.query(TicketModel).order_by(TicketModel.created_at.desc()).limit(MAX_TICKETS_EMBEDDED).all()

    # ── Preferred path: embeddinggemma cosine similarity ────────────────────
    query_emb = embed_text(query_text)
    if query_emb:
        results: List[Dict[str, Any]] = []
        embedded_any = False

        for item in corpus_records:
            item_emb = _get_corpus_embedding(db, item)
            if not item_emb:
                continue
            embedded_any = True
            results.append(_corpus_result(item, _cosine(query_emb, item_emb), "embeddinggemma"))

        for t in tickets:
            if not (t.title and t.description):
                continue
            t_emb = embed_text(f"{t.title} {t.description}")
            if not t_emb:
                continue
            embedded_any = True
            results.append(_ticket_result(t, _cosine(query_emb, t_emb), "embeddinggemma"))

        if embedded_any:
            results.sort(key=lambda x: x["similarity_score"], reverse=True)
            return results[:limit]
        logger.warning("Query embedded but no corpus/ticket embeddings available; using keyword fallback")

    # ── Fallback path: keyword overlap ──────────────────────────────────────
    results = []
    for item in corpus_records:
        kw = item.similarity_keywords or []
        score = compute_keyword_similarity(f"{query_text}", kw)
        if any(word in query_text.lower() for word in item.title.lower().split() if len(word) > 4):
            score = min(1.0, score + 0.35)
        results.append(_corpus_result(item, max(0.40, score), "keyword_overlap"))

    for t in tickets:
        if t.title and t.description:
            score = 0.5 if (t.hazard_type and t.hazard_type.lower() in query_text.lower()) else 0.3
            results.append(_ticket_result(t, score, "keyword_overlap"))

    results.sort(key=lambda x: x["similarity_score"], reverse=True)
    return results[:limit]

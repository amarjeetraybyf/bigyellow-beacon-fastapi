"""
Shore AI Service (provider-aware copilot intelligence)

Backend resolution (SHORE_AI_BACKEND):
- "auto"   -> local Ollama first; Vertex/Gemini cloud if configured; deterministic fallback last
- "ollama" -> local Ollama only (gemma3n:e4b on the VM); deterministic fallback
- "vertex" -> Vertex AI Gemini via google-genai (GOOGLE_CLOUD_PROJECT + ADC); deterministic fallback
- "gemini" -> Google AI Studio API key via google-genai; deterministic fallback

Rules:
- NEVER send reporter_ref or crew identifiers in prompts.
- Label all AI output with the model name and timestamp.
- Nothing is auto-applied to official fields (human-in-the-loop).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

import httpx
from pydantic import BaseModel, Field, ValidationError

from config import (
    SHORE_AI_BACKEND,
    OLLAMA_HOST,
    OLLAMA_STRUCTURE_MODEL,
    OLLAMA_EMBED_MODEL,
    OLLAMA_TIMEOUT_SECONDS,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GOOGLE_CLOUD_PROJECT,
    GOOGLE_CLOUD_LOCATION,
)

logger = logging.getLogger("shore.ai_service")


class TicketAnalysisSchema(BaseModel):
    summary: str = Field(..., description="Executive summary of the near-miss incident")
    root_cause_analysis: str = Field(..., description="Likely organizational, environmental, or human factors")
    ism_mlc_relevance: str = Field(..., description="Relevant ISM Code or MLC 2006 compliance considerations")
    fleet_risk_level: str = Field(..., description="LOW, MEDIUM, HIGH, or CRITICAL fleet-wide risk")
    recommended_shore_actions: list[str] = Field(..., description="Recommended actions for shore safety superintendent")
    suggested_daily_anchor: Optional[str] = Field(None, description="Suggested micro-learning theme for Daily Anchor")


class LearningDraftSchema(BaseModel):
    title: str = Field(..., description="Concise learning campaign title")
    summary: str = Field(..., description="One-sentence summary of the key safety action")
    body: str = Field(..., description="1-page maritime micro-learning body text")
    suggested_topics: list[str] = Field(default_factory=list, description="3 related topic tags")


class AnchorWeekSchema(BaseModel):
    cards: list[Dict[str, str]] = Field(..., description="7 cards, each with 'topic' and 'prompt'")


# ---------------------------------------------------------------------------
# Low-level backends
# ---------------------------------------------------------------------------

def _ollama_generate_json(prompt: str, temperature: float = 0.2) -> Optional[str]:
    """Calls local Ollama /api/generate with format=json. Returns raw JSON text or None."""
    try:
        with httpx.Client(timeout=OLLAMA_TIMEOUT_SECONDS) as client:
            res = client.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model": OLLAMA_STRUCTURE_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": temperature},
                },
            )
            if res.status_code == 200:
                return res.json().get("response", "").strip() or None
            logger.warning("Ollama %s returned HTTP %s", OLLAMA_STRUCTURE_MODEL, res.status_code)
    except Exception as e:
        logger.warning("Ollama call failed: %s", e)
    return None


def embed_text(text: str) -> Optional[List[float]]:
    """Embeds text with embeddinggemma via local Ollama. Returns vector or None."""
    if not text:
        return None
    try:
        with httpx.Client(timeout=OLLAMA_TIMEOUT_SECONDS) as client:
            res = client.post(
                f"{OLLAMA_HOST}/api/embeddings",
                json={"model": OLLAMA_EMBED_MODEL, "prompt": text},
            )
            if res.status_code == 200:
                emb = res.json().get("embedding")
                if isinstance(emb, list) and emb:
                    return emb
            logger.warning("Ollama embeddings returned HTTP %s", res.status_code)
    except Exception as e:
        logger.warning("Ollama embeddings call failed: %s", e)
    return None


def _gemini_client():
    """Builds a google-genai client for Vertex (project) or AI Studio (API key)."""
    from google import genai

    if GOOGLE_CLOUD_PROJECT:
        return genai.Client(
            vertexai=True,
            project=GOOGLE_CLOUD_PROJECT,
            location=GOOGLE_CLOUD_LOCATION,
        )
    if GEMINI_API_KEY:
        return genai.Client(api_key=GEMINI_API_KEY)
    raise RuntimeError("No Gemini credentials configured (GOOGLE_CLOUD_PROJECT or GEMINI_API_KEY)")


def _gemini_generate_json(prompt: str, schema, temperature: float = 0.2) -> Optional[str]:
    """Calls Gemini (Vertex or AI Studio) with a JSON response schema. Returns raw JSON text or None."""
    try:
        from google.genai import types

        client = _gemini_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=temperature,
            ),
        )
        return response.text
    except Exception as e:
        logger.error("Gemini/Vertex call failed: %s", e)
        return None


def _cloud_configured() -> bool:
    return bool(GOOGLE_CLOUD_PROJECT or GEMINI_API_KEY)


def _generate_structured(prompt: str, schema, temperature: float = 0.2):
    """
    Runs the prompt through the configured backend chain.
    Returns (validated_schema_instance, model_label) or (None, None).
    """
    backends: List[str]
    if SHORE_AI_BACKEND == "ollama":
        backends = ["ollama"]
    elif SHORE_AI_BACKEND in ("vertex", "gemini"):
        backends = ["cloud"]
    else:  # auto
        backends = ["ollama", "cloud"]

    for backend in backends:
        if backend == "ollama":
            raw = _ollama_generate_json(prompt, temperature=temperature)
            model_label = f"ollama/{OLLAMA_STRUCTURE_MODEL}"
        else:
            if not _cloud_configured():
                continue
            raw = _gemini_generate_json(prompt, schema, temperature=temperature)
            model_label = ("vertex/" if GOOGLE_CLOUD_PROJECT else "gemini/") + GEMINI_MODEL
        if not raw:
            continue
        try:
            parsed = schema.model_validate(json.loads(raw))
            return parsed, model_label
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning("%s returned invalid JSON for %s: %s", model_label, schema.__name__, e)
            continue

    return None, None


# ---------------------------------------------------------------------------
# Ticket analysis
# ---------------------------------------------------------------------------

def generate_ticket_analysis(ticket_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Runs the shore copilot on an anonymized near-miss report.
    Strips reporter_ref, vessel_id, crew IDs, and any identifying metadata before sending.
    """
    schema_hint = json.dumps(
        {
            "summary": "string",
            "root_cause_analysis": "string",
            "ism_mlc_relevance": "string",
            "fleet_risk_level": "LOW | MEDIUM | HIGH | CRITICAL",
            "recommended_shore_actions": ["string"],
            "suggested_daily_anchor": "string",
        }
    )
    sanitized_prompt = f"""
You are the BigYellow Beacon Shore Safety Copilot analyzing an incoming shipboard near-miss incident.

Incident Title: {ticket_dict.get('title', 'N/A')}
Hazard Type: {ticket_dict.get('hazard_type', 'N/A')}
ISM Category: {ticket_dict.get('ism_category', 'N/A')}
Location on Board: {ticket_dict.get('location_on_board', 'N/A')}
Severity Rating (1-5): {ticket_dict.get('severity', 'N/A')}
Incident Description: {ticket_dict.get('description', 'N/A')}
Immediate Action Taken on Board: {ticket_dict.get('immediate_action', 'N/A')}

Analyze this incident under IMO / ISM Code standards and MLC 2006 guidelines. Provide structured root-cause assessment and actionable fleet safety superintendent recommendations.

Return ONLY valid JSON matching this schema exactly:
{schema_hint}
"""

    now_iso = datetime.now(timezone.utc).isoformat()
    parsed, model_label = _generate_structured(sanitized_prompt, TicketAnalysisSchema, temperature=0.2)
    if parsed:
        return {
            "model": model_label,
            "generated_at": now_iso,
            "auto_applied": False,
            "analysis": parsed.model_dump(),
        }

    logger.info("All AI backends unavailable for ticket analysis; using deterministic fallback")
    return _fallback_ticket_analysis(ticket_dict, now_iso)


def _fallback_ticket_analysis(ticket_dict: Dict[str, Any], now_iso: str) -> Dict[str, Any]:
    """High-fidelity deterministic copilot analysis fallback."""
    sev = int(ticket_dict.get("severity") or 3)
    hazard = ticket_dict.get("hazard_type", "General Maritime Hazard")
    ism = ticket_dict.get("ism_category", "ISM 6 - Resources and Personnel")

    risk_level = "CRITICAL" if sev >= 5 else "HIGH" if sev >= 4 else "MEDIUM" if sev >= 3 else "LOW"

    analysis_result = {
        "summary": f"Near-miss involving {hazard} in {ticket_dict.get('location_on_board', 'shipboard space')} with severity {sev}/5.",
        "root_cause_analysis": f"Potential procedural drift, environmental hazards, or equipment fatigue identified under {ism}. Immediate action mitigated immediate escalation, but systemic review is warranted.",
        "ism_mlc_relevance": "Cross-referenced with ISM Code Section 9 (Reports and analysis of non-conformities, accidents, and hazardous occurrences) and MLC 2006 Regulation 4.3 (Health and safety protection and accident prevention).",
        "fleet_risk_level": risk_level,
        "recommended_shore_actions": [
            f"Review planned maintenance schedule for equipment in {ticket_dict.get('location_on_board', 'compartment')}.",
            "Issue technical safety bulletin across sister vessels in the fleet.",
            "Verify onboard toolbox talk compliance prior to similar operations.",
        ],
        "suggested_daily_anchor": f"Operational Vigilance: Addressing precursor warnings in {hazard}.",
    }

    return {
        "model": "deterministic-fallback",
        "generated_at": now_iso,
        "auto_applied": False,
        "analysis": analysis_result,
    }


# ---------------------------------------------------------------------------
# Learning draft
# ---------------------------------------------------------------------------

def generate_learning_draft(
    topic: Optional[str],
    incident_context: str,
    key_safety_action: str,
) -> Dict[str, Any]:
    """
    Drafts a concise 1-page maritime micro-learning.
    Never sends reporter_ref or personal crew identifiers.
    Returns {title, summary, body, suggested_topics, model, generated_at}.
    """
    schema_hint = json.dumps(
        {
            "title": "string",
            "summary": "string",
            "body": "string",
            "suggested_topics": ["string", "string", "string"],
        }
    )
    prompt = f"""
You are the BigYellow Beacon fleet safety campaign writer. Draft a concise 1-page maritime
micro-learning for seafarers, written in plain English suitable for a multinational crew.

Topic: {topic or 'derive a suitable topic from the incident context'}
Incident Context (anonymized): {incident_context}
Key Safety Action: {key_safety_action}

Requirements:
- Title: short and action-oriented.
- Summary: one sentence stating the mandatory safety behaviour.
- Body: 3 short paragraphs (what happened pattern, why it matters, what to do), max 200 words.
- suggested_topics: 3 related topic tags.
- Never include crew names, ranks, vessel names, or personal identifiers.

Return ONLY valid JSON matching this schema exactly:
{schema_hint}
"""

    now_iso = datetime.now(timezone.utc).isoformat()
    parsed, model_label = _generate_structured(prompt, LearningDraftSchema, temperature=0.4)
    if parsed:
        result = parsed.model_dump()
        result["model"] = model_label
        result["generated_at"] = now_iso
        return result

    logger.info("All AI backends unavailable for learning draft; using deterministic fallback")
    return {
        "title": topic or "Hydraulic Line Pre-Operation Lockout Protocol",
        "summary": "Mandatory isolation of deck crane hydraulic manifolds prior to maintenance.",
        "body": (
            f"Based on precedent incidents involving pressurized fluid injection: {incident_context}\n"
            f"Key Action: {key_safety_action}"
        ),
        "suggested_topics": ["Hydraulics", "Lockout/Tagout", "Deck Safety"],
        "model": "deterministic-fallback",
        "generated_at": now_iso,
    }


# ---------------------------------------------------------------------------
# Daily Anchor week drafting
# ---------------------------------------------------------------------------

FALLBACK_ANCHOR_TOPICS = [
    "Situational Focus",
    "Line of Fire",
    "Fatigue Checking",
    "Clear Communication",
    "Hydraulic Awareness",
    "Weather Readiness",
    "Gratitude",
]


def generate_anchor_week() -> List[Dict[str, Any]]:
    """
    Drafts a week (7 cards) of Daily Anchor micro-reflections.
    Returns a list of {topic, prompt, day_offset, generated_by_model}.
    """
    schema_hint = json.dumps({"cards": [{"topic": "string", "prompt": "string"}]})
    prompt = f"""
You are the BigYellow Beacon fleet safety campaign writer. Draft exactly 7 Daily Anchor cards -
one per day for a week - for seafarers on ocean-going commercial vessels.

Each card needs:
- topic: a 2-4 word safety or wellbeing theme (e.g. "Line of Fire", "Fatigue Checking").
- prompt: 1-2 sentences of practical micro-reflection a crew member reads before their watch.

Rules: plain English, no names, no vessel identifiers, maritime-relevant, positive tone.

Return ONLY valid JSON matching this schema exactly (cards must contain exactly 7 entries):
{schema_hint}
"""

    parsed, model_label = _generate_structured(prompt, AnchorWeekSchema, temperature=0.5)
    if parsed and len(parsed.cards) >= 7:
        return [
            {
                "topic": card.get("topic", f"Day {i + 1} Reflection"),
                "prompt": card.get("prompt", ""),
                "day_offset": i + 1,
                "generated_by_model": model_label,
            }
            for i, card in enumerate(parsed.cards[:7])
        ]

    logger.info("All AI backends unavailable for anchor week; using deterministic fallback")
    return [
        {
            "topic": t,
            "prompt": f"Daily Anchor for {t}: Pause before operating machinery. Verify isolating valves.",
            "day_offset": i + 1,
            "generated_by_model": "deterministic-fallback",
        }
        for i, t in enumerate(FALLBACK_ANCHOR_TOPICS)
    ]

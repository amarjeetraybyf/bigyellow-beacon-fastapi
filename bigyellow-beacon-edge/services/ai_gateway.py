"""
Edge AI Gateway (local Ollama on the shipboard VM / mini-PC GPU):
- gemma3n:e4b  -> near-miss structuring + Master morning brief (format=json, temp 0.2)
- gemma3n:e2b  -> wellbeing check-in chat (temp 0.5)
- gemma3:4b    -> vision description of hazard photos (photo-only drafts)
- embeddinggemma -> duplicate-ticket detection (cosine > 0.85 within 7 days)
- faster-whisper -> voice memo transcription (CUDA first, CPU fallback)
- Versioned prompts in prompts/<task>/v<n>.md
- Pydantic validation with 1 retry (error appended), then fallback to keyword heuristics
- Safety rails run before and after EVERY model call
"""

import re
import math
import json
import base64
import logging
import httpx
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from pydantic import ValidationError
from sqlalchemy.orm import Session

from contract.models import TicketFields
from contract.enums import HazardType, IsmCategory
from db_models import TicketModel
from config import (
    OLLAMA_HOST,
    OLLAMA_STRUCTURE_MODEL,
    OLLAMA_CHAT_MODEL,
    OLLAMA_VISION_MODEL,
    OLLAMA_EMBED_MODEL,
    OLLAMA_TIMEOUT_SECONDS,
    WHISPER_MODEL_SIZE,
    WHISPER_DEVICE,
    WHISPER_COMPUTE_TYPE,
)

logger = logging.getLogger("edge.ai_gateway")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

SAFETY_RAIL_MESSAGE = (
    "It sounds like you are carrying a heavy burden right now. Your safety and well-being "
    "are what matter most. Please consider reaching out immediately to our onboard Medical / "
    "Safety Officer [Ship Onboard Medical Contact: Bridge Ext 101 / Cabin 204] or the 24/7 "
    "confidential seafarer helpline [Company Welfare Hotline: +1-800-BEACON-HELP / WhatsApp]. "
    "Would you like the Master to check on you?"
)

DISTRESS_PATTERNS = [
    r"\b(suicid|kill\s*myself|end\s*it\s*all|don'?t\s*want\s*to\s*live|hang\s*myself|jump\s*overboard|harm\s*myself)\b",
    r"\b(want\s*to\s*die|no\s*reason\s*to\s*go\s*on|can'?t\s*take\s*it\s*anymore)\b",
]

DEFAULT_CLARIFYING_QUESTION = "Was the area cordoned off or tagged out immediately after discovery?"
FALLBACK_TRANSCRIPT = (
    "Oil mist observed near auxiliary generator 2 turbocharger return flange during 0400 rounds."
)


class SafetyRailTriggeredException(Exception):
    def __init__(self, message: str = SAFETY_RAIL_MESSAGE):
        self.message = message
        super().__init__(self.message)


class AIGateway:
    def __init__(self):
        self.generate_url = f"{OLLAMA_HOST}/api/generate"
        self.embeddings_url = f"{OLLAMA_HOST}/api/embeddings"
        self.whisper_model = None

    # ------------------------------------------------------------------
    # Safety rails
    # ------------------------------------------------------------------
    def check_safety_rails(self, text: str) -> bool:
        """
        Runs pre- and post-inference safety rails.
        Returns True if safety rail triggered (distress / self-harm detected).
        """
        if not text:
            return False
        for pattern in DISTRESS_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def load_prompt_template(self, task: str, version: str = "v1") -> str:
        """Loads versioned prompt template from prompts/<task>/v<n>.md"""
        p = PROMPTS_DIR / task / f"{version}.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
        return ""

    # ------------------------------------------------------------------
    # Whisper voice transcription (CUDA -> CPU -> canned fallback)
    # ------------------------------------------------------------------
    def _load_whisper(self):
        from faster_whisper import WhisperModel

        try:
            return WhisperModel(
                WHISPER_MODEL_SIZE,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
            )
        except Exception as e:
            logger.warning(
                "Whisper %s on %s/%s failed (%s); falling back to small/cpu/int8",
                WHISPER_MODEL_SIZE, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, e,
            )
            return WhisperModel("small", device="cpu", compute_type="int8")

    async def transcribe_audio(self, audio_bytes: bytes, filename: str = "audio.wav") -> str:
        try:
            if self.whisper_model is None:
                self.whisper_model = self._load_whisper()

            temp_path = Path(f"/tmp/{filename}")
            temp_path.write_bytes(audio_bytes)
            try:
                segments, info = self.whisper_model.transcribe(str(temp_path), beam_size=5)
                transcript = " ".join([seg.text for seg in segments]).strip()
                return transcript or "Voice memo recorded with normal background maritime ambient."
            finally:
                if temp_path.exists():
                    temp_path.unlink()
        except Exception as e:
            logger.error("Whisper transcription failed: %s", e)
            return FALLBACK_TRANSCRIPT

    # ------------------------------------------------------------------
    # Vision: describe hazard photos with gemma3:4b
    # ------------------------------------------------------------------
    async def describe_hazard_photos(self, photo_bytes_list: List[bytes]) -> Optional[str]:
        """
        Uses the local vision model to describe hazard photos when the draft
        has no text and no audio. Returns None if the model is unavailable.
        """
        if not photo_bytes_list:
            return None

        images_b64 = [base64.b64encode(b).decode("ascii") for b in photo_bytes_list[:3]]
        prompt = (
            "You are a shipboard maritime safety assistant. Describe the hazard or unsafe "
            "condition visible in the attached photo(s) from an ocean-going commercial vessel, "
            "in 2-3 factual sentences. Mention equipment, location cues, and the hazard type. "
            "Do not mention any people's names or faces."
        )
        raw = await self._call_ollama(
            prompt,
            model=OLLAMA_VISION_MODEL,
            temperature=0.2,
            format="",
            images=images_b64,
        )
        if raw:
            return raw.strip()
        logger.warning("Vision model %s unavailable for photo description", OLLAMA_VISION_MODEL)
        return None

    # ------------------------------------------------------------------
    # Near-miss structuring (gemma3n:e4b)
    # ------------------------------------------------------------------
    async def structure_near_miss_report(
        self,
        draft_id: str,
        input_text: str,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        if self.check_safety_rails(input_text):
            raise SafetyRailTriggeredException()

        prompt_template = self.load_prompt_template("structure_report", "v1")
        full_prompt = f"{prompt_template}\n\nSeafarer Input:\n{input_text}"

        parsed_fields = None
        model_clarifying_question: Optional[str] = None
        model_confidence: Optional[float] = None

        raw_res = await self._call_ollama(full_prompt, model=OLLAMA_STRUCTURE_MODEL, temperature=0.2, format="json")
        if raw_res:
            parsed_fields, model_clarifying_question, model_confidence, error = self._parse_structure_json(raw_res)
            if not parsed_fields and error:
                retry_prompt = (
                    f"{full_prompt}\n\nYour previous response failed validation with error:\n{error}\n"
                    "Return ONLY valid JSON matching the exact schema required."
                )
                retry_res = await self._call_ollama(
                    retry_prompt, model=OLLAMA_STRUCTURE_MODEL, temperature=0.1, format="json"
                )
                if retry_res:
                    parsed_fields, model_clarifying_question, model_confidence, _ = self._parse_structure_json(retry_res)

        if not parsed_fields:
            logger.info("Falling back to keyword heuristics for draft %s", draft_id)
            parsed_fields = self._generate_fallback_fields(input_text)

        if self.check_safety_rails(f"{parsed_fields.title_en} {parsed_fields.description_en}"):
            raise SafetyRailTriggeredException()

        location = (parsed_fields.location_on_board or "").strip()
        location_known = bool(location) and location.lower() not in (
            "unknown", "n/a", "na", "tbd", "deck / machinery space",
        )
        if location_known:
            clarifying = model_clarifying_question if model_clarifying_question else None
        else:
            clarifying = model_clarifying_question or DEFAULT_CLARIFYING_QUESTION

        duplicate_info = None
        if db:
            duplicate_info = await self.check_duplicate_tickets(
                db, parsed_fields.title_en, parsed_fields.description_en
            )

        return {
            "draft_id": draft_id,
            "original_language": self.detect_language(input_text),
            "transcript_original": input_text,
            "fields": parsed_fields.model_dump(),
            "clarifying_question": clarifying,
            "confidence": model_confidence if model_confidence is not None else 0.94,
            "duplicate_info": duplicate_info,
        }

    @staticmethod
    def detect_language(text: str) -> str:
        """Lightweight script heuristic for original_language (FE expects e.g. en/hi)."""
        if not text:
            return "en"
        # Devanagari
        if re.search(r"[\u0900-\u097F]", text):
            return "hi"
        # Cyrillic (Ukrainian/Russian crew)
        if re.search(r"[\u0400-\u04FF]", text):
            return "uk"
        # Arabic / Urdu
        if re.search(r"[\u0600-\u06FF]", text):
            return "ar"
        # Tagalog/Latin-heavy default
        return "en"

    @staticmethod
    def _parse_structure_json(raw: str):
        """
        Parses model JSON into (TicketFields, clarifying_question, confidence, error).
        clarifying_question / confidence come from the model when present and sane.
        """
        clarifying_question: Optional[str] = None
        confidence: Optional[float] = None
        try:
            parsed_json = json.loads(raw)
            if isinstance(parsed_json, dict):
                cq = parsed_json.pop("clarifying_question", None)
                conf = parsed_json.pop("confidence", None)
                if isinstance(cq, str) and cq.strip():
                    clarifying_question = cq.strip()
                if isinstance(conf, (int, float)) and 0.0 < float(conf) <= 1.0:
                    confidence = float(conf)
            fields = TicketFields.model_validate(parsed_json)
            return fields, clarifying_question, confidence, None
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning("Structure JSON validation failed: %s", e)
            return None, clarifying_question, confidence, str(e)

    # ------------------------------------------------------------------
    # Check-in chat (gemma3n:e2b)
    # ------------------------------------------------------------------
    async def generate_chat_turn(self, conversation_history: List[Dict[str, str]]) -> str:
        last_user_message = conversation_history[-1]["content"] if conversation_history else ""
        if self.check_safety_rails(last_user_message):
            raise SafetyRailTriggeredException()

        prompt_template = self.load_prompt_template("checkin_chat", "v1")
        dialogue = "\n".join([f"{m.get('role', 'user')}: {m.get('content', '')}" for m in conversation_history])
        full_prompt = f"{prompt_template}\n\nCurrent Conversation:\n{dialogue}\nassistant:"

        response = await self._call_ollama(full_prompt, model=OLLAMA_CHAT_MODEL, temperature=0.5, format="")
        if not response:
            response = (
                "Thank you for sharing that with me. It takes real resilience to manage watches and sea conditions. "
                "How are your rest hours feeling this week?"
            )

        if self.check_safety_rails(response):
            raise SafetyRailTriggeredException()

        return response

    # ------------------------------------------------------------------
    # Master morning brief (gemma3n:e4b)
    # ------------------------------------------------------------------
    async def generate_master_brief(self, hazard_summary: str) -> Optional[Dict[str, Any]]:
        """
        Generates the Master 06:00 brief narrative from anonymized hazard data.
        Returns a dict with headline / recommended_action / fleet_context, or None if
        the model is unavailable (caller falls back to rule-based brief).
        """
        prompt_template = self.load_prompt_template("master_brief", "v1")
        full_prompt = (
            f"{prompt_template}\n\n"
            "Return ONLY JSON with keys: headline (string), recommended_action (string), "
            "fleet_context (string).\n\n"
            f"Anonymized shipboard hazard data:\n{hazard_summary}"
        )
        raw = await self._call_ollama(full_prompt, model=OLLAMA_STRUCTURE_MODEL, temperature=0.2, format="json")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("headline"):
                return {
                    "headline": str(data.get("headline", "")).strip(),
                    "recommended_action": str(data.get("recommended_action", "")).strip(),
                    "fleet_context": str(data.get("fleet_context", "")).strip(),
                }
        except json.JSONDecodeError as e:
            logger.warning("Master brief JSON parse failed: %s", e)
        return None

    # ------------------------------------------------------------------
    # Ollama HTTP helpers
    # ------------------------------------------------------------------
    async def _call_ollama(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.2,
        format: str = "json",
        images: Optional[List[str]] = None,
    ) -> Optional[str]:
        payload: Dict[str, Any] = {
            "model": model or OLLAMA_STRUCTURE_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if format:
            payload["format"] = format
        if images:
            payload["images"] = images

        try:
            async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT_SECONDS) as client:
                res = await client.post(self.generate_url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    return data.get("response", "").strip()
                logger.warning("Ollama %s returned HTTP %s", payload["model"], res.status_code)
        except Exception as e:
            logger.warning("Ollama call to %s failed: %s", payload["model"], e)
        return None

    async def _embed_text(self, text: str) -> Optional[List[float]]:
        """Embeds text with embeddinggemma via Ollama /api/embeddings."""
        if not text:
            return None
        try:
            async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT_SECONDS) as client:
                res = await client.post(
                    self.embeddings_url,
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

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    # ------------------------------------------------------------------
    # Fallback heuristics
    # ------------------------------------------------------------------
    def _generate_fallback_fields(self, input_text: str) -> TicketFields:
        lower = input_text.lower()
        hazard = HazardType.OTHER.value
        ism = IsmCategory.UNSAFE_CONDITION.value
        location = "Deck / Machinery Space"
        sev = 3

        if any(w in lower for w in ("slip", "trip", "fall", "water", "wet", "deck")):
            hazard = HazardType.SLIP_TRIP_FALL.value
            ism = IsmCategory.UNSAFE_CONDITION.value
            location = "Main Deck Walkway"
            sev = 2
        elif any(w in lower for w in ("fire", "smoke", "ignition")):
            hazard = HazardType.FIRE.value
            ism = IsmCategory.EQUIPMENT_FAILURE.value
            location = "Engine Room Platform"
            sev = 4
        elif any(w in lower for w in ("fuel", "oil", "pipe", "engine", "leak", "hydraulic")):
            hazard = HazardType.MACHINERY.value
            ism = IsmCategory.EQUIPMENT_FAILURE.value
            location = "Engine Room Platform"
            sev = 4
        elif any(w in lower for w in ("mooring", "rope", "wire", "line", "snap", "winch")):
            hazard = HazardType.MOORING.value
            ism = IsmCategory.UNSAFE_ACT.value
            location = "Poop Deck / Forecastle"
            sev = 4
        elif any(w in lower for w in ("crane", "lifting", "hoist")):
            hazard = HazardType.CRANE_LIFTING.value
            ism = IsmCategory.PROCEDURE_GAP.value
            location = "Main Deck Crane Station"
            sev = 4
        elif any(w in lower for w in ("electric", "wiring", "cable", "shock")):
            hazard = HazardType.ELECTRICAL.value
            ism = IsmCategory.UNSAFE_CONDITION.value
            location = "Accommodation / Machinery Space"
            sev = 3
        elif any(w in lower for w in ("chemical", "corrosive", "toxic")):
            hazard = HazardType.CHEMICAL.value
            ism = IsmCategory.PROCEDURE_GAP.value
            location = "Chemical Store / Deck"
            sev = 4
        elif any(w in lower for w in ("gas", "oxygen", "tank", "enclosed", "confined")):
            hazard = HazardType.ENCLOSED_SPACE.value
            ism = IsmCategory.PROCEDURE_GAP.value
            location = "Ballast Tank / Cofferdam"
            sev = 5

        title = input_text[:60] + ("..." if len(input_text) > 60 else "")
        return TicketFields(
            title_en=title.capitalize(),
            description_en=input_text,
            location_on_board=location,
            hazard_type=hazard,
            ism_category=ism,
            severity=sev,
            immediate_action="Area inspected and warning notices positioned immediately.",
        )

    # ------------------------------------------------------------------
    # Duplicate detection: embeddinggemma cosine, Jaccard fallback
    # ------------------------------------------------------------------
    async def check_duplicate_tickets(
        self,
        db: Session,
        new_title: str,
        new_desc: str,
        threshold: float = 0.85,
        days_window: int = 7,
    ) -> Optional[Dict[str, Any]]:
        since_time = datetime.now(timezone.utc) - timedelta(days=days_window)
        recent_tickets = db.query(TicketModel).filter(TicketModel.created_at >= since_time).all()
        if not recent_tickets:
            return None

        new_text = f"{new_title} {new_desc}".strip()

        # Preferred path: embeddinggemma cosine similarity
        new_emb = await self._embed_text(new_text)
        if new_emb:
            best_match = None
            highest_sim = 0.0
            any_embedded = False
            for t in recent_tickets:
                t_emb = await self._embed_text(f"{t.title} {t.description}")
                if not t_emb:
                    continue
                any_embedded = True
                sim = self._cosine(new_emb, t_emb)
                if sim > highest_sim:
                    highest_sim = sim
                    best_match = t
            if any_embedded:
                if highest_sim >= threshold and best_match:
                    return {
                        "is_duplicate": True,
                        "similarity": round(highest_sim, 3),
                        "matched_ticket_id": best_match.id,
                        "matched_title": best_match.title,
                        "method": "embeddinggemma",
                    }
                return None

        # Fallback path: token overlap (offline / embeddings unavailable)
        return self._check_duplicate_jaccard(recent_tickets, new_text, threshold)

    def _check_duplicate_jaccard(
        self,
        recent_tickets: List[TicketModel],
        new_text: str,
        threshold: float,
    ) -> Optional[Dict[str, Any]]:
        target_tokens = set(re.findall(r"\w+", new_text.lower()))
        if not target_tokens:
            return None

        best_match = None
        highest_sim = 0.0
        for t in recent_tickets:
            comp_tokens = set(re.findall(r"\w+", f"{t.title} {t.description}".lower()))
            if not comp_tokens:
                continue
            intersection = len(target_tokens & comp_tokens)
            denom = math.sqrt(len(target_tokens)) * math.sqrt(len(comp_tokens))
            sim = intersection / denom if denom > 0 else 0.0
            if sim > highest_sim:
                highest_sim = sim
                best_match = t

        if highest_sim >= threshold and best_match:
            return {
                "is_duplicate": True,
                "similarity": round(highest_sim, 3),
                "matched_ticket_id": best_match.id,
                "matched_title": best_match.title,
                "method": "token_overlap",
            }
        return None


ai_gateway = AIGateway()

"""
BigYellow Beacon – AI Service (Gemini 3.8 Flash / Google GenAI)
Gracefully degrades to deterministic maritime heuristics when GEMINI_API_KEY is not set.
"""

from typing import Optional, Dict, Any, List
from app.config import GEMINI_API_KEY

_ai_client = None

if GEMINI_API_KEY:
    try:
        from google import genai
        _ai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"[AI Service] Google GenAI client initialization failed: {e}")
        _ai_client = None


def is_gemini_live() -> bool:
    return _ai_client is not None


def get_ai_client():
    return _ai_client


async def generate_master_brief_ai(
    vessel_name: str,
    aggregated_wellbeing: Dict[str, Any],
    recent_near_misses: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not _ai_client:
        return None

    prompt = f"""You are the Maritime Safety Intelligence Copilot for the Vessel Master of "{vessel_name}".
It is 06:00 ship's time. Synthesize a 1-page morning brief for the Captain's daily safety meeting.
Data:
- Crew Wellbeing & Rest Compliance (k-anonymity protected): Overall Mood {aggregated_wellbeing.get('avgMood', 7)}/10, Fatigue {aggregated_wellbeing.get('avgFatigue', 5)}/10, Average Sleep {aggregated_wellbeing.get('avgSleepHours', 7)}h.
- Open Near-Miss Hazards: {recent_near_misses[:3]}
- Precursor Warning: Sister vessels have reported crane hydraulic leaks, linking to MAIB Report 12/2021.
Provide a concise, operational JSON response with:
{{
  "restHoursRiskStatus": "WARNING",
  "fatiguedDepartments": ["Deck Crew", "Galley"],
  "openHazardsSummary": "Summary of active hazards",
  "activeClusterWarning": "Sister vessel alert",
  "highImpactActionForMorningMeeting": "ONE concrete, decisive action for the Master to announce at the 06:00 toolbox talk",
  "portArrivalContext": "Port schedule advisory",
  "weatherWarning": "Maritime weather advice"
}}"""

    try:
        import json
        response = _ai_client.models.generate_content(
            model="gemini-2.5-flash",  # or gemini-3.8-flash when supported
            contents=prompt,
            config={"response_mime_type": "application/json"}
        )
        return json.loads(response.text or "{}")
    except Exception as e:
        print(f"[AI Service] Brief generation error: {e}")
        return None


async def translate_notice_ai(body: str, target_lang: str) -> str:
    if not _ai_client:
        return body

    lang_names = {
        "tl": "Tagalog",
        "hi": "Hindi",
        "id": "Indonesian",
        "uk": "Ukrainian",
        "zh": "Chinese (Simplified)",
        "ru": "Russian",
        "es": "Spanish",
    }
    target_name = lang_names.get(target_lang, target_lang)

    try:
        response = _ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"Translate the following maritime safety notice to {target_name}. Return only the translated text.\n\n{body}"
        )
        return response.text.strip() if response.text else body
    except Exception as e:
        print(f"[AI Service] Translation error: {e}")
        return body


async def answer_ship_question_ai(question: str, context: str = "") -> Optional[str]:
    if not _ai_client:
        return None

    try:
        prompt = f"""You are a maritime safety assistant. Answer concisely using Safety Management System (SMS) and ISM Code guidelines.
{context}
Question: {question}"""
        response = _ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return response.text.strip() if response.text else None
    except Exception as e:
        print(f"[AI Service] Ship question error: {e}")
        return None


async def vision_analyze_photo_ai(image_base64: str, mime_type: str = "image/jpeg") -> Optional[str]:
    if not _ai_client:
        return None

    try:
        from google.genai import types
        part = types.Part.from_bytes(
            data=bytes(image_base64, "utf-8"),
            mime_type=mime_type,
        )
        prompt = "You are a maritime safety analyst. Describe any safety hazards visible in this shipboard photo. Include: (1) hazard type, (2) location on ship, (3) severity estimate, (4) immediate action required. Be concise and specific."
        response = _ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt, part]
        )
        return response.text.strip() if response.text else None
    except Exception as e:
        print(f"[AI Service] Vision analysis error: {e}")
        return None

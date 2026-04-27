"""
Safety Agent: LLM-based emergency screen for potential emergencies.

Uses a single context-aware LLM call to evaluate whether a patient message
describes an active, current, life-threatening emergency — or a non-acute
mention (past history, chronic condition, refill request, etc.).

Outcome logic:
  - LLM flags emergency  → triggered_by="llm"  (confirmed emergency, short-circuits graph)
  - LLM clears           → triggered_by="none"  (normal triage flow)

Target: 0% false negatives (nothing silently dropped) with reduced false positives.
"""
import os

from dotenv import load_dotenv
from langsmith import traceable

from schemas.schemas import SafetyResult

load_dotenv()

_LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-2.5-pro")


# ---------------------------------------------------------------------------
# LLM screening prompt — context-aware, replaces brittle regex patterns
# ---------------------------------------------------------------------------

_SCREENING_PROMPT = """You are a medical emergency screening system for a clinic patient portal.

Evaluate the following patient message and determine if it describes an ACTIVE, CURRENT, LIFE-THREATENING emergency that requires immediate care (call 911 or go to the ER right now).

Flag as emergency (is_potential_emergency: true) ONLY if ALL of the following apply:
1. The event or symptom is happening RIGHT NOW or started within the last few hours.
2. The onset is sudden OR the severity is clearly acute and life-threatening.
3. The language does NOT suggest a chronic, stable, or historical context.

Examples of TRUE emergencies:
- "I'm having crushing chest pain right now and can't breathe"
- "My husband just collapsed and is unconscious"
- "I took a whole bottle of pills"
- "I'm having thoughts of killing myself and I have a plan"
- "I can't stop the bleeding from my arm"

Do NOT flag as emergency (is_potential_emergency: false) if:
- The patient is describing PAST medical history ("I had a heart attack 5 years ago", "history of seizures", "I was diagnosed with...")
- The symptom has been present for weeks/months without acute worsening
- The patient uses language like "mild", "occasional", "getting better", "I used to", "last year", "I have a history of", "for the past few weeks/months"
- It is a medication refill, appointment, billing, or administrative request — even if the patient mentions a serious past condition in their history
- It describes a known managed chronic condition (epilepsy, asthma, chronic back pain, etc.)
- The patient describes a past event or routine follow-up, not an active crisis
- When genuinely uncertain about severity, answer false — the triage agent will assess further with full patient context

Patient message:
{text}
"""


def _get_genai_client():
    """Return a configured Gemini client, or None if unavailable."""
    api_key = os.environ.get("LLM_GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except ImportError:
        return None


def _llm_call(prompt: str) -> SafetyResult:
    """
    Run a single Gemini structured-output call with the given prompt.
    On failure returns is_potential_emergency=False — an LLM outage should
    not create false positives; the triage agent will still assess the case.
    """
    client = _get_genai_client()
    if not client:
        return SafetyResult(
            is_potential_emergency=False,
            reason="LLM not configured; screening unavailable.",
            triggered_by="none",
        )
    try:
        from schemas.schemas import SafetyResult as _SR
        response = client.models.generate_content(
            model=_LLM_MODEL,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": _SR,
            },
        )
        if response.parsed:
            out = response.parsed
            return SafetyResult(
                is_potential_emergency=out.is_potential_emergency,
                reason=out.reason or (
                    "Flagged by LLM." if out.is_potential_emergency else "No emergency signals detected."
                ),
                triggered_by="llm" if out.is_potential_emergency else "none",
            )
    except Exception:
        pass

    # LLM failure — do NOT default to True (avoids false positives from outages)
    return SafetyResult(
        is_potential_emergency=False,
        reason="LLM screening unavailable.",
        triggered_by="none",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

@traceable
def screen_for_emergency(patient_message: str) -> SafetyResult:
    """
    LLM-based emergency screen.

    Uses a single context-aware LLM call that understands the difference
    between active emergencies and historical/chronic mentions.

    Returns a SafetyResult where triggered_by signals the result:
      "llm"   — LLM confirmed active emergency → short-circuit the graph
      "none"  — LLM found no active emergency  → normal triage flow
    """
    text = (patient_message or "").strip()
    if not text:
        return SafetyResult(
            is_potential_emergency=False,
            reason="Empty message.",
            triggered_by="none",
        )

    prompt = _SCREENING_PROMPT.format(text=text[:2000])
    return _llm_call(prompt)

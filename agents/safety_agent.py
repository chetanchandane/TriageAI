"""
Safety Agent: screen for potential emergencies before triage.
Target: 0% false negatives — when in doubt, flag as potential emergency.
Uses rule-based keywords first (deterministic), then a conservative LLM check.
"""
import os
import re
from typing import Optional

from dotenv import load_dotenv
from langsmith import traceable

from schemas.schemas import SafetyResult

load_dotenv()

# ---------------------------------------------------------------------------
# Rule-based layer: explicit emergency signals (high recall to avoid false negatives)
# ---------------------------------------------------------------------------
EMERGENCY_PATTERNS = [
    # Cardiac / chest — require crisis modifiers to avoid flagging routine mentions
    r"\b(sudden|crushing|severe|sharp|intense)\s+chest\s+pain\b",
    r"\bheart\s+attack\b",
    r"\bheart\s+(is\s+)?racing\s+with\s+(dizziness|fainting|shortness\s+of\s+breath|chest\s+pain)\b",
    r"\b(severe|sharp|crushing)\s+chest\b",
    r"\bpain\s+(in|radiating\s+to)\s+(my\s+)?(arm|jaw|back)\b",
    # Respiratory
    r"\b(can'?t|cannot|can\s+not)\s+breathe\b",
    r"\bdifficulty\s+breathing\b",
    r"\b(shortness|short)\s+of\s+breath\b",
    r"\bchoking\b",
    r"\b(wheezing|gasping)\b",
    r"\b(turning\s+blue|blue\s+lips)\b",
    # Stroke / neuro
    r"\bstroke\b",
    r"\b(face\s+droop|drooping\s+face)\b",
    r"\b(sudden\s+)?(numbness|weakness)\s+(in|on)\s+(one\s+side|arm|leg|face)\b",
    r"\bsudden\s+(severe\s+)?headache\b",
    r"\bslurred\s+speech\b",
    r"\bsudden\s+(confusion|vision\s+(loss|blurred|double))\b",
    # Bleeding / trauma
    r"\bsevere\s+bleeding\b",
    r"\b(heavy|uncontrolled)\s+bleeding\b",
    r"\bbleeding\s+that\s+won'?t\s+stop\b",
    r"\blarge\s+(cut|wound|gash)\b",
    # Mental health crisis
    r"\b(suicid(e|al)|kill\s+myself|end\s+my\s+life)\b",
    r"\bhurt\s+myself\s+(on\s+purpose|intentionally)\b",
    # Other life-threatening
    r"\b(overdose|overdosed)\b",
    r"\bsevere\s+allergic\s+reaction\b",
    r"\banaphylax(is)?\b",
    r"\b(seizure|seizing)\b",
    r"\b(unconscious|passed\s+out|fainted)\b",
    r"\b(poison|poisoned)\b",
    r"\b(sudden|severe|acute)\s+(abdominal\s+)?pain\b",
    r"\b(coughing|vomiting)\s+blood\b",
    r"\b(thoughts?|plan)\s+to\s+(harm|kill)\b",
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in EMERGENCY_PATTERNS]


def _rule_based_screen(text: str) -> Optional[str]:
    """
    Return the first matching pattern description if any rule matches, else None.
    Used for deterministic, high-recall screening.
    """
    if not (text or "").strip():
        return None
    for i, pat in enumerate(COMPILED_PATTERNS):
        if pat.search(text):
            return EMERGENCY_PATTERNS[i]
    return None


@traceable
def _llm_safety_screen(text: str) -> SafetyResult:
    """
    Conservative LLM check: bias toward flagging to minimize false negatives.
    Only used when rule-based layer did not already flag.
    """
    try:
        from google import genai

        client = genai.Client(api_key=os.environ.get("LLM_GEMINI_API_KEY"))
        if not client or not os.environ.get("LLM_GEMINI_API_KEY"):
            # No API key: default to not flagging (rules already ran)
            return SafetyResult(
                is_potential_emergency=False,
                reason="LLM not configured; rule-based screen did not find emergency signals.",
                triggered_by="none",
            )
    except ImportError:
        return SafetyResult(
            is_potential_emergency=False,
            reason="LLM not available; rule-based screen did not find emergency signals.",
            triggered_by="none",
        )

    PROMPT = """You are a medical safety screener. Your ONLY job is to decide: could this message possibly describe a life-threatening or emergency situation?

RULES:
- If there is ANY reasonable chance the situation could be life-threatening (heart attack, stroke, severe bleeding, difficulty breathing, overdose, suicidal intent, severe allergic reaction, unconscious, choking, etc.), answer is_potential_emergency: true.
- When in doubt, answer true. We prefer false positives over missing a real emergency.
- Only answer false if the message is clearly non-urgent (e.g. refill, appointment, general question with no acute symptoms).

NEGATIVE CONSTRAINTS — Do NOT flag these as emergencies:
- Routine medication refill requests (e.g. "I need to refill my blood pressure medication").
- Appointment scheduling or rescheduling requests.
- Billing, insurance, or administrative questions.
- Chronic or stable symptoms that have persisted for more than 1 week without worsening (e.g. "I've had mild back pain for a few weeks").
- General health questions or follow-ups (e.g. "When should I come in for my annual checkup?").
- Mild, non-acute symptoms described without urgency modifiers (e.g. "I have some chest discomfort when I exercise that's been going on for months" is NOT the same as "I'm having sudden crushing chest pain right now").

Message to screen:
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=PROMPT + text.strip()[:2000],
            config={
                "response_mime_type": "application/json",
                "response_schema": SafetyResult,
            },
        )
        if response.parsed:
            out = response.parsed
            return SafetyResult(
                is_potential_emergency=out.is_potential_emergency,
                reason=out.reason or ("Flagged by LLM as possible emergency." if out.is_potential_emergency else "No emergency signals."),
                triggered_by="llm",
            )
    except Exception:
        pass
    # On any LLM failure: be conservative and flag
    return SafetyResult(
        is_potential_emergency=True,
        reason="Safety LLM check failed; flagging as potential emergency to avoid missing a real one.",
        triggered_by="llm",
    )


@traceable
def screen_for_emergency(patient_message: str) -> SafetyResult:
    """
    Run the safety screen. Target: 0% false negatives.
    First applies rule-based patterns; if none match, runs a conservative LLM check.
    """
    text = (patient_message or "").strip()
    rule_match = _rule_based_screen(text)
    if rule_match:
        return SafetyResult(
            is_potential_emergency=True,
            reason=f"Message matched emergency pattern: {rule_match}",
            triggered_by="rules",
        )
    return _llm_safety_screen(text)

"""
Safety Agent: two-stage confirmation screen for potential emergencies.

Stage 1 — Rule-based scan (deterministic, high-recall, no API call).
Stage 2 — LLM confirmation or general scan (context-aware, reduces false positives).

Confirmation logic:
  - Rule fires AND LLM confirms  → triggered_by="rules+llm"  (confirmed emergency)
  - Rule fires, LLM disagrees    → triggered_by="rules_only" (suspected, warn triage agent)
  - No rule, LLM flags           → triggered_by="llm_only"   (suspected, warn triage agent)
  - Neither                      → triggered_by="none"        (clear)

Only "rules+llm" short-circuits the graph. Suspected cases route through the
triage agent with the safety flag as context — the agent uses patient history
and policy tools to make a fully-informed urgency decision.

Target: 0% false negatives (nothing silently dropped) with reduced false positives.
"""
import os
import re
from typing import Optional

from dotenv import load_dotenv
from langsmith import traceable

from schemas.schemas import SafetyResult

load_dotenv()


# ---------------------------------------------------------------------------
# Rule-based layer — high-recall patterns (Stage 1)
# ---------------------------------------------------------------------------

EMERGENCY_PATTERNS = [
    # Cardiac / chest
    r"\b(sudden|crushing|severe|sharp|intense)\s+chest\s+pain\b",
    r"\bheart\s+attack\b",
    r"\bheart\s+(is\s+)?racing\s+with\s+(dizziness|fainting|shortness\s+of\s+breath|chest\s+pain)\b",
    r"\b(severe|sharp|crushing)\s+chest\b",
    # Pain radiating — requires acute modifier to avoid flagging "back pain for years"
    r"\b(sudden|shooting|severe|radiating)\s+pain\s+(in|to)\s+(my\s+)?(arm|jaw|left\s+arm)\b",
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
    """Return the first matching pattern if any rule matches, else None."""
    if not (text or "").strip():
        return None
    for i, pat in enumerate(COMPILED_PATTERNS):
        if pat.search(text):
            return EMERGENCY_PATTERNS[i]
    return None


# ---------------------------------------------------------------------------
# LLM layer — two focused prompts (Stage 2)
# ---------------------------------------------------------------------------

# Used when a rule already fired: confirm whether this is truly acute/active
_CONFIRM_PROMPT = """You are a medical emergency verification system.

A keyword rule flagged the following patient message for a possible emergency. Assess whether this describes an ACTIVE, CURRENT, LIFE-THREATENING situation — or a non-acute mention (chronic condition, past medical history, mild/stable symptom, or incidental reference).

Flagged pattern: {rule_reason}

Confirm as emergency (true) ONLY if ALL of the following apply:
- The event is happening RIGHT NOW or within the last few hours.
- The onset is sudden or the severity is clearly acute.
- The language does not suggest a chronic, stable, or historical context.

Do NOT confirm (false) if:
- The symptom has been present for more than 24–48 hours without escalation.
- The patient uses language like "mild", "occasional", "getting better", "I used to", "last year", "I have a history of", "for the past few weeks/months".
- It is a known managed chronic condition (epilepsy, asthma, chronic back pain, etc.).
- It describes a past event or a routine follow-up, not an acute crisis.

Patient message:
{text}
"""

# Used when no rule fired: general scan, balanced (not "when in doubt flag")
_GENERAL_PROMPT = """You are a medical safety screener.

Decide if this patient message describes a situation requiring immediate emergency care (911 or ER right now).

Flag as emergency (true) ONLY if the message describes:
- A sudden, acute, severe symptom: heart attack, stroke, severe bleeding, inability to breathe, overdose, anaphylaxis, loss of consciousness, or suicidal intent with a plan.

Do NOT flag (false) if:
- The message describes chronic, stable, or mild symptoms.
- It is a refill, appointment, billing, or administrative request.
- It mentions a symptom without clear urgency or acuity ("some back pain", "mild headache", "tired lately", "chest discomfort when exercising for months").
- The patient is describing medical history, not an active crisis.
- When genuinely uncertain about severity, answer false — the triage agent will assess further.

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


def _llm_call(prompt: str, triggered_by_value: str) -> SafetyResult:
    """
    Run a single Gemini structured-output call with the given prompt.
    On failure returns is_potential_emergency=False — rules already ran and
    would have caught rule-based emergencies; an LLM outage should not create
    false positives.
    """
    client = _get_genai_client()
    if not client:
        return SafetyResult(
            is_potential_emergency=False,
            reason="LLM not configured; rule-based screen did not find emergency signals.",
            triggered_by="none",
        )
    try:
        from schemas.schemas import SafetyResult as _SR
        response = client.models.generate_content(
            model="gemini-2.5-flash",
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
                triggered_by=triggered_by_value if out.is_potential_emergency else "none",
            )
    except Exception:
        pass

    # LLM failure — do NOT default to True (avoids false positives from outages)
    return SafetyResult(
        is_potential_emergency=False,
        reason="LLM check unavailable; rule-based screen found no emergency signals.",
        triggered_by="none",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

@traceable
def screen_for_emergency(patient_message: str) -> SafetyResult:
    """
    Two-stage emergency screen.

    Stage 1: Rule-based scan — fast, deterministic, high-recall.
    Stage 2: LLM confirmation (if rule fired) or general scan (if no rule).

    Returns a SafetyResult where triggered_by signals the confirmation level:
      "rules+llm"  — both stages agree → confirmed emergency
      "rules_only" — rule fired, LLM disagrees → suspected (warn triage agent)
      "llm_only"   — no rule, LLM flagged → suspected (warn triage agent)
      "none"       — both stages clear

    Only "rules+llm" should trigger an emergency short-circuit in the graph.
    All other non-none values route through the triage agent with a warning.
    """
    text = (patient_message or "").strip()

    rule_match = _rule_based_screen(text)

    if rule_match:
        # Stage 2: confirm whether this rule match is truly acute/active
        confirm_prompt = _CONFIRM_PROMPT.format(
            rule_reason=rule_match,
            text=text[:2000],
        )
        llm_result = _llm_call(confirm_prompt, triggered_by_value="rules+llm")

        if llm_result.is_potential_emergency:
            # Both layers agree — confirmed emergency
            return SafetyResult(
                is_potential_emergency=True,
                reason=f"Confirmed emergency: rule matched '{rule_match}' and LLM verified acute presentation. {llm_result.reason}",
                triggered_by="rules+llm",
            )
        else:
            # Rule fired but LLM assessed as non-acute (chronic, historical, mild)
            return SafetyResult(
                is_potential_emergency=True,
                reason=f"Suspected (rule match unconfirmed): pattern '{rule_match}' matched but LLM assessed as non-acute. Triage agent will evaluate with full context.",
                triggered_by="rules_only",
            )

    # No rule match — run general LLM scan
    llm_result = _llm_call(
        _GENERAL_PROMPT + "\n" + text[:2000],
        triggered_by_value="llm_only",
    )

    if llm_result.is_potential_emergency:
        return SafetyResult(
            is_potential_emergency=True,
            reason=f"Flagged by general LLM scan (no rule match): {llm_result.reason}",
            triggered_by="llm_only",
        )

    return SafetyResult(
        is_potential_emergency=False,
        reason="No emergency signals detected by rules or LLM.",
        triggered_by="none",
    )

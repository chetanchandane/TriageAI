"""
Reference-free evaluators for TriageAI ONLINE evals (Sprint 7, Pillar 3).

Offline evaluators (scripts/eval_evaluators.py) score against dataset reference labels.
Production traffic has no labels, so these variants judge a live run using ONLY what the
run itself contains (its inputs + outputs). They reuse the exact rubric prompts from
eval_evaluators.py so offline and online judging stay in sync.

Intended use: attach as LangSmith *automations* on the live LANGSMITH_PROJECT so a sample
of incoming production traces is auto-scored. See EVALUATION.md ("Online eval automations")
for the wiring steps.

Signature: LangSmith online evaluators receive a Run (and an Example that is None online),
and return {"key": ..., "score": ..., "comment": ...} — same shape as the offline ones.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

# Reuse the judge client + shared prompt builders so there is a single source of truth.
from scripts.eval_evaluators import (
    _judge,
    faithful_prompt,
    policy_grounded_prompt,
    tone_prompt,
)


# ---------------------------------------------------------------------------
# Pull the fields we need straight off the live run, tolerant of dict/attr forms
# and of where LangGraph places them (top-level state vs. nested result dicts).
# ---------------------------------------------------------------------------

def _as_dict(obj) -> dict:
    if isinstance(obj, dict):
        return obj
    return getattr(obj, "__dict__", {}) or {}


def _run_io(run):
    inputs = getattr(run, "inputs", None)
    outputs = getattr(run, "outputs", None)
    if inputs is None and isinstance(run, dict):
        inputs = run.get("inputs")
    if outputs is None and isinstance(run, dict):
        outputs = run.get("outputs")
    return _as_dict(inputs), _as_dict(outputs)


def _extract(run) -> dict:
    """Return {message, patient_history, draft, is_emergency} from a live run."""
    inputs, outputs = _run_io(run)

    message = inputs.get("message") or inputs.get("patient_message") or ""

    triage = _as_dict(outputs.get("triage_result")) or _as_dict(outputs.get("triage"))
    safety = _as_dict(outputs.get("safety_result")) or _as_dict(outputs.get("safety"))

    draft = (
        outputs.get("draft_reply")
        or triage.get("draft_reply")
        or ""
    )

    is_emergency = bool(
        outputs.get("is_emergency")
        or safety.get("is_potential_emergency")
        or False
    )

    return {
        "message": message,
        "patient_history": inputs.get("patient_history", ""),
        "draft": (draft or "").strip(),
        "is_emergency": is_emergency,
    }


# ---------------------------------------------------------------------------
# Online evaluators (no reference labels; example is ignored / None)
# ---------------------------------------------------------------------------

def draft_policy_grounded_online(run, example=None) -> dict:
    ctx = _extract(run)
    if not ctx["draft"]:
        return {"key": "draft_policy_grounded", "score": None, "comment": "no draft in run"}
    return _judge(policy_grounded_prompt(ctx["message"], ctx["draft"]), "draft_policy_grounded")


def draft_faithful_online(run, example=None) -> dict:
    ctx = _extract(run)
    if not ctx["draft"]:
        return {"key": "draft_faithful", "score": None, "comment": "no draft in run"}
    # No draft_must_not_mention hints online — the faithfulness rubric still applies.
    prompt = faithful_prompt(ctx["message"], ctx["patient_history"], ctx["draft"], must_not=None)
    return _judge(prompt, "draft_faithful")


def draft_tone_online(run, example=None) -> dict:
    ctx = _extract(run)
    if not ctx["draft"]:
        return {"key": "draft_tone", "score": None, "comment": "no draft in run"}
    # Emergency expectation comes from the run's OWN safety screen, not a label.
    prompt = tone_prompt(ctx["message"], ctx["draft"], ctx["is_emergency"], must_mention=None)
    return _judge(prompt, "draft_tone")


ONLINE_EVALUATORS = [
    draft_policy_grounded_online,
    draft_faithful_online,
    draft_tone_online,
]


# ---------------------------------------------------------------------------
# Optional: register these as LangSmith automation rules from the SDK.
#
# LangSmith automations are normally created in the UI (Project → Rules), but the
# SDK can create them too. This is a thin convenience for reproducible setup.
# ---------------------------------------------------------------------------

def main():
    """Smoke-test the online evaluators against a synthetic run (no LangSmith needed)."""
    class _Run:
        inputs = {"message": "Can I get my lisinopril refilled? Almost out."}
        outputs = {
            "draft_reply": "Hi, I've requested your lisinopril refill; the pharmacy "
            "typically processes refills within 2 business days. If you run out before "
            "then, please call the clinic.",
            "safety_result": {"is_potential_emergency": False},
        }

    if not os.environ.get("LLM_GEMINI_API_KEY"):
        print("LLM_GEMINI_API_KEY not set — judges will return None (skipped).")

    for fn in ONLINE_EVALUATORS:
        result = fn(_Run())
        print(f"{result['key']:<24} score={result.get('score')}  {result.get('comment', '')}")


if __name__ == "__main__":
    main()

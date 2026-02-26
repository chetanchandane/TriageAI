"""
Graph node functions for the TriageAI LangGraph workflow.
Each node receives the current TriageWorkflowState and returns a state update dict.
"""
from typing import Any

from graph.state import TriageWorkflowState


def safety_node(state: TriageWorkflowState) -> dict[str, Any]:
    """Run safety screen; return state update."""
    from agents.safety_agent import screen_for_emergency
    msg = (state.get("message") or "").strip()
    result = screen_for_emergency(msg)
    return {"safety_result": result.model_dump()}


def triage_node(state: TriageWorkflowState) -> dict[str, Any]:
    """Run triage; if safety flagged emergency, override urgency."""
    from agents.triage_agent import test_triage
    msg = (state.get("message") or "").strip()
    try:
        triage = test_triage(msg)
        out = triage.model_dump() if triage else {}
    except Exception:
        out = {}
    safety = state.get("safety_result") or {}
    if safety.get("is_potential_emergency"):
        out["urgency"] = "EMERGENCY"
        out["safety_flagged"] = True
        out["safety_reason"] = safety.get("reason", "")
        out["safety_triggered_by"] = safety.get("triggered_by", "none")
    return {"triage_result": out}

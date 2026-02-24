"""
LangGraph workflow: Safety Agent → Triage Agent.
Run with run_triage_workflow(patient_message) to get safety_result + triage_result.
Falls back to direct Safety → Triage calls if LangGraph is not installed.
"""
from typing import TypedDict, Any

from safety_agent import screen_for_emergency


class TriageWorkflowState(TypedDict, total=False):
    message: str
    safety_result: dict[str, Any]
    triage_result: dict[str, Any]


def _safety_node(state: TriageWorkflowState) -> dict[str, Any]:
    """Run safety screen; return state update."""
    msg = (state.get("message") or "").strip()
    result = screen_for_emergency(msg)
    return {"safety_result": result.model_dump()}


def _triage_node(state: TriageWorkflowState) -> dict[str, Any]:
    """Run triage; if safety flagged emergency, override urgency."""
    msg = (state.get("message") or "").strip()
    try:
        from triage_test import test_triage
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


def _run_fallback(patient_message: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """No LangGraph: run safety then triage directly."""
    msg = (patient_message or "").strip()
    safety_result = screen_for_emergency(msg).model_dump()
    state: TriageWorkflowState = {"message": msg, "safety_result": safety_result}
    triage_update = _triage_node(state)
    return safety_result, triage_update.get("triage_result", {})


def build_graph():
    """Build and compile the Safety → Triage graph."""
    from langgraph.graph import StateGraph

    graph = StateGraph(TriageWorkflowState)
    graph.add_node("safety", _safety_node)
    graph.add_node("triage", _triage_node)
    graph.set_entry_point("safety")
    graph.add_edge("safety", "triage")
    graph.set_finish_point("triage")
    return graph.compile()


_compiled: Any = None


def run_triage_workflow(patient_message: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Run the full workflow: safety then triage.
    Returns (safety_result dict, triage_result dict).
    Uses LangGraph if available; otherwise runs safety and triage in sequence.
    """
    global _compiled
    try:
        if _compiled is None:
            _compiled = build_graph()
        initial: TriageWorkflowState = {"message": (patient_message or "").strip()}
        final = _compiled.invoke(initial)
        safety_result = final.get("safety_result") or {}
        triage_result = final.get("triage_result") or {}
        return safety_result, triage_result
    except ImportError:
        return _run_fallback(patient_message)

"""
LangGraph workflow: Cyclic Agentic Orchestrator for TriageAI.

Graph flow:
  START → safety_node → [emergency? → END | → triage_agent_node]
  triage_agent_node → [tool_calls? → tool_node → triage_agent_node | → synthesis_node → END]

The triage agent can loop: call tools, read results, call more tools, then synthesize.
Falls back to direct Safety → Triage calls if LangGraph is not installed.
"""
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from graph.state import TriageWorkflowState
from graph.nodes import (
    safety_node,
    triage_agent_node,
    synthesis_node,
    TRIAGE_TOOLS,
)


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------

def _route_after_safety(state: TriageWorkflowState) -> str:
    """Gatekeeper: if emergency detected, short-circuit to synthesis (which tags it).
    Otherwise proceed to the triage agent for reasoning."""
    if state.get("is_emergency"):
        return "synthesis"
    return "triage_agent"


def _should_continue(state: TriageWorkflowState) -> str:
    """After the triage agent responds, check if it wants to call tools or is done.
    - If the last message has tool_calls → route to tool_node.
    - Otherwise → route to synthesis_node (agent finished reasoning)."""
    messages = state.get("messages") or []
    if not messages:
        return "synthesis"

    last_message = messages[-1]

    # Check for tool calls (LangChain AIMessage format)
    if isinstance(last_message, AIMessage) and getattr(last_message, "tool_calls", None):
        return "tool_node"

    return "synthesis"


# ---------------------------------------------------------------------------
# Build the cyclic graph
# ---------------------------------------------------------------------------

def build_graph():
    """Build and compile the agentic Safety → Triage → Tool loop → Synthesis graph."""
    from langgraph.graph import StateGraph, END
    from langgraph.prebuilt import ToolNode

    graph = StateGraph(TriageWorkflowState)

    # --- Add nodes ---
    graph.add_node("safety", safety_node)
    graph.add_node("triage_agent", triage_agent_node)
    graph.add_node("tool_node", ToolNode(TRIAGE_TOOLS))
    graph.add_node("synthesis", synthesis_node)

    # --- Set entry point ---
    graph.set_entry_point("safety")

    # --- Conditional edges ---
    # After safety: emergency → synthesis (short-circuit), else → triage agent
    graph.add_conditional_edges(
        "safety",
        _route_after_safety,
        {"synthesis": "synthesis", "triage_agent": "triage_agent"},
    )

    # After triage agent: tool_calls → tool_node, else → synthesis
    graph.add_conditional_edges(
        "triage_agent",
        _should_continue,
        {"tool_node": "tool_node", "synthesis": "synthesis"},
    )

    # The loop: tool_node feeds results back to triage agent for re-evaluation
    graph.add_edge("tool_node", "triage_agent")

    # Synthesis is the final step
    graph.add_edge("synthesis", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Fallback (no LangGraph / import error)
# ---------------------------------------------------------------------------

def _run_fallback(patient_message: str, patient_id: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    """No LangGraph: run safety then triage directly (Sprint 1 behavior)."""
    from agents.safety_agent import screen_for_emergency
    from agents.triage_agent import test_triage

    msg = (patient_message or "").strip()
    safety_result = screen_for_emergency(msg)
    safety_dict = safety_result.model_dump()

    try:
        triage = test_triage(msg)
        triage_dict = triage.model_dump() if triage else {}
    except Exception:
        triage_dict = {}

    if safety_result.is_potential_emergency:
        triage_dict["urgency"] = "EMERGENCY"
        triage_dict["safety_flagged"] = True
        triage_dict["safety_reason"] = safety_result.reason
        triage_dict["safety_triggered_by"] = safety_result.triggered_by

    return safety_dict, triage_dict


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_compiled: Any = None


def run_triage_workflow(
    patient_message: str,
    patient_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Run the full agentic workflow: Safety → Triage Agent (with tool loop) → Synthesis.

    Returns (safety_result dict, triage_result dict) for backward compatibility
    with the Streamlit app.

    Uses the cyclic LangGraph if available; otherwise falls back to direct calls.
    """
    global _compiled
    msg = (patient_message or "").strip()

    try:
        if _compiled is None:
            _compiled = build_graph()

        # Seed the state with the patient message as the first HumanMessage
        initial: TriageWorkflowState = {
            "message": msg,
            "patient_id": patient_id or "",
            "messages": [HumanMessage(content=msg)],
            "is_emergency": False,
            "staff_approved": False,
        }

        final = _compiled.invoke(initial)

        safety_result = final.get("safety_result") or {}
        triage_result = final.get("triage_result") or {}
        return safety_result, triage_result

    except ImportError:
        return _run_fallback(msg, patient_id)

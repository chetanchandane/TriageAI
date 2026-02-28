"""
LangGraph workflow: Cyclic Agentic Orchestrator with HITL for TriageAI.

Graph flow (Sprint 3):
  START → safety_node → [emergency? → synthesis | → triage_agent_node]
  triage_agent_node → [tool_calls? → tool_node → triage_agent_node | → synthesis_node]
  synthesis_node → draft_reply_node → [LOW? → auto_communicate → END
                                       | → **communication_node** (INTERRUPTED) → END]

Persistence:
  MemorySaver checkpointer saves every node's state to a thread_id.
  NORMAL/HIGH/EMERGENCY workflows are interrupted before communication_node
  so staff can review and edit the draft before sending.

Resume:
  Staff edits the draft_reply via update_state, then resumes with invoke(None, config).
"""
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from graph.state import TriageWorkflowState
from graph.nodes import (
    safety_node,
    triage_agent_node,
    synthesis_node,
    draft_reply_node,
    communication_node,
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


def _route_after_draft(state: TriageWorkflowState) -> str:
    """Route based on urgency after draft reply is generated.
    - LOW → auto_communicate (no staff review needed, fully automated).
    - NORMAL/HIGH/EMERGENCY → communication_node (interrupted for HITL review)."""
    urgency = (state.get("triage_result") or {}).get("urgency", "NORMAL").upper()
    if urgency == "LOW":
        return "auto_communicate"
    return "communication_node"


# ---------------------------------------------------------------------------
# Auto-communicate node (LOW urgency — no interrupt)
# ---------------------------------------------------------------------------

def _auto_communicate_node(state: TriageWorkflowState) -> dict[str, Any]:
    """Send draft reply automatically for LOW urgency. No staff review needed."""
    from mcp.tools.communication import send_resolution_email

    patient_email = state.get("patient_email", "")
    draft = state.get("draft_reply", "")
    triage_result = state.get("triage_result") or {}

    subject = f"[TriageAI] Re: {triage_result.get('summary', 'Your message')}"
    if patient_email and draft:
        send_resolution_email(patient_email, subject, draft)

    return {
        "staff_approved": True,
        "hitl_status": "auto_completed",
    }


# ---------------------------------------------------------------------------
# Build the graph with persistence and HITL interrupts
# ---------------------------------------------------------------------------

_compiled: Any = None
_checkpointer: Any = None


def build_graph():
    """Build and compile the agentic graph with MemorySaver and HITL interrupts."""
    global _checkpointer
    from langgraph.graph import StateGraph, END
    from langgraph.prebuilt import ToolNode
    from langgraph.checkpoint.memory import MemorySaver

    graph = StateGraph(TriageWorkflowState)

    # --- Add nodes ---
    graph.add_node("safety", safety_node)
    graph.add_node("triage_agent", triage_agent_node)
    graph.add_node("tool_node", ToolNode(TRIAGE_TOOLS))
    graph.add_node("synthesis", synthesis_node)
    graph.add_node("draft_reply", draft_reply_node)
    graph.add_node("communication_node", communication_node)
    graph.add_node("auto_communicate", _auto_communicate_node)

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

    # Synthesis → draft reply (always)
    graph.add_edge("synthesis", "draft_reply")

    # After draft reply: LOW → auto_communicate, others → communication_node (interrupted)
    graph.add_conditional_edges(
        "draft_reply",
        _route_after_draft,
        {"auto_communicate": "auto_communicate", "communication_node": "communication_node"},
    )

    # Both communicate paths lead to END
    graph.add_edge("auto_communicate", END)
    graph.add_edge("communication_node", END)

    # --- Compile with persistence and HITL interrupt ---
    _checkpointer = MemorySaver()
    return graph.compile(
        checkpointer=_checkpointer,
        interrupt_before=["communication_node"],
    )


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
# Public entry points
# ---------------------------------------------------------------------------

def _get_compiled():
    """Lazy-build and cache the compiled graph."""
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled


def run_triage_workflow(
    patient_message: str,
    patient_id: str = "",
    patient_email: str = "",
    thread_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Run the full agentic workflow with persistence and HITL support.

    Returns (safety_result dict, triage_result dict).

    The triage_result will include:
      - thread_id: for resuming interrupted workflows
      - hitl_status: "pending_review" (interrupted), "auto_completed" (LOW), or "approved"

    For NORMAL/HIGH/EMERGENCY urgency, the workflow pauses before communication_node.
    Staff should use resume_workflow() to continue after review.
    """
    msg = (patient_message or "").strip()

    try:
        app = _get_compiled()
    except ImportError:
        return _run_fallback(msg, patient_id)

    # Generate a thread_id if not provided (unique per message submission)
    if not thread_id:
        thread_id = str(uuid.uuid4())

    config = {"configurable": {"thread_id": thread_id}}

    # Seed the state with the patient message as the first HumanMessage
    initial: TriageWorkflowState = {
        "message": msg,
        "patient_id": patient_id or "",
        "patient_email": patient_email or "",
        "messages": [HumanMessage(content=msg)],
        "is_emergency": False,
        "staff_approved": False,
    }

    try:
        final = app.invoke(initial, config)
    except Exception:
        # If LangGraph fails entirely, fall back
        return _run_fallback(msg, patient_id)

    safety_result = final.get("safety_result") or {}
    triage_result = final.get("triage_result") or {}

    # Embed the thread_id and hitl_status into triage_result for the UI
    triage_result["thread_id"] = thread_id

    # Determine if workflow was interrupted (no hitl_status means it paused before communication_node)
    hitl_status = final.get("hitl_status")
    if hitl_status:
        triage_result["hitl_status"] = hitl_status
    else:
        # Workflow was interrupted before communication_node (NORMAL/HIGH/EMERGENCY)
        triage_result["hitl_status"] = "pending_review"
        triage_result["draft_reply"] = final.get("draft_reply", "")

    return safety_result, triage_result


def get_workflow_state(thread_id: str) -> dict[str, Any] | None:
    """
    Retrieve the current state of a workflow by thread_id.
    Used by the staff dashboard to inspect interrupted workflows.
    Returns the full state dict, or None if not found.
    """
    try:
        app = _get_compiled()
        config = {"configurable": {"thread_id": thread_id}}
        state = app.get_state(config)
        if state and state.values:
            return dict(state.values)
        return None
    except Exception:
        return None


def resume_workflow(
    thread_id: str,
    edited_draft: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Resume an interrupted workflow after staff review.

    If edited_draft is provided, the draft_reply in state is updated before resuming.
    The graph continues from where it was interrupted (communication_node) and sends
    the finalized email.

    Returns (safety_result dict, triage_result dict) — same shape as run_triage_workflow.
    """
    app = _get_compiled()
    config = {"configurable": {"thread_id": thread_id}}

    # If staff edited the draft, update the state before resuming
    if edited_draft is not None:
        app.update_state(config, {"draft_reply": edited_draft})

    # Resume execution: invoke(None, config) continues from the interrupt point
    final = app.invoke(None, config)

    safety_result = final.get("safety_result") or {}
    triage_result = final.get("triage_result") or {}
    triage_result["thread_id"] = thread_id
    triage_result["hitl_status"] = final.get("hitl_status", "approved")

    return safety_result, triage_result

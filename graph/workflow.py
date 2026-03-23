"""
LangGraph workflow: Cyclic Agentic Orchestrator with HITL for TriageAI.

Graph flow (Sprint 4):
  START → safety_node → [emergency? → synthesis | → triage_agent_node]
  triage_agent_node → [tool_calls? → tool_node → triage_agent_node | → synthesis_node]
  synthesis_node → draft_reply_node → [LOW? → auto_communicate → END
                                       | → **communication_node** (INTERRUPTED) → END]

Sprint 4 changes:
  - MCP tool discovery via MultiServerMCPClient (async, bridged to sync)
  - Local-only fallback when MCP server unavailable
  - nest_asyncio for Streamlit compatibility

Persistence:
  MemorySaver checkpointer saves every node's state to a thread_id.
  NORMAL/HIGH/EMERGENCY workflows are interrupted before communication_node
  so staff can review and edit the draft before sending.

Resume:
  Staff edits the draft_reply via update_state, then resumes with invoke(None, config).
"""
import asyncio
import json
import os
import uuid
from typing import Any

import nest_asyncio
try:
    nest_asyncio.apply()
except ValueError as e:
    if "uvloop" in str(e) or "patch" in str(e).lower():
        pass  # Under Streamlit/uvloop, skip — sync graph path still works
    else:
        raise

from langchain_core.messages import AIMessage, HumanMessage

from graph.state import TriageWorkflowState
from langgraph.types import Command

from graph.nodes import (
    safety_node,
    triage_agent_node,
    synthesis_node,
    draft_reply_node,
    communication_node,
    checklist_gate_node,
    _make_triage_agent_node,
    LOCAL_TOOLS,
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


def _route_after_checklist(state: TriageWorkflowState) -> str:
    """After checklist gate:
    - is_complete=True means the triage agent produced no more checklist items
      → proceed to synthesis (conversation done).
    - is_complete not set means the patient just answered a question and the agent
      should re-evaluate → loop back to triage_agent_node.
    """
    if state.get("is_complete"):
        return "synthesis"
    return "triage_agent"


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
    from mcp_tools.tools.communication import send_resolution_email

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

# Module-level MCP singleton (populated by _init_mcp_tools)
_mcp_tools: list | None = None

MCP_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "mcp_config.json",
)


async def _init_mcp_tools() -> list:
    """Discover MCP tools from chroma-mcp-server via MultiServerMCPClient.

    Reads mcp_config.json, launches the Chroma MCP server as a subprocess,
    and returns the list of LangChain-wrapped tools it exposes.
    Caches the result so the server is only started once per process.
    """
    global _mcp_tools
    if _mcp_tools is not None:
        return _mcp_tools

    from langchain_mcp_adapters.client import MultiServerMCPClient

    with open(MCP_CONFIG_PATH) as f:
        config = json.load(f)

    client = MultiServerMCPClient(config)
    _mcp_tools = await client.get_tools()
    return _mcp_tools


_CHECKPOINT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "checkpoints.db",
)


def _compile_graph(all_tools, triage_node_fn):
    """Shared graph compilation logic used by both MCP and local-only builders."""
    global _checkpointer
    from langgraph.graph import StateGraph, END
    from langgraph.prebuilt import ToolNode

    graph = StateGraph(TriageWorkflowState)

    # --- Add nodes ---
    graph.add_node("safety", safety_node)
    graph.add_node("triage_agent", triage_node_fn)
    graph.add_node("tool_node", ToolNode(all_tools))
    graph.add_node("checklist_gate", checklist_gate_node)
    graph.add_node("synthesis", synthesis_node)
    graph.add_node("draft_reply", draft_reply_node)
    graph.add_node("communication_node", communication_node)
    graph.add_node("auto_communicate", _auto_communicate_node)

    # --- Set entry point ---
    graph.set_entry_point("safety")

    # --- Conditional edges ---
    graph.add_conditional_edges(
        "safety",
        _route_after_safety,
        {"synthesis": "synthesis", "triage_agent": "triage_agent"},
    )
    graph.add_conditional_edges(
        "triage_agent",
        _should_continue,
        {"tool_node": "tool_node", "synthesis": "checklist_gate"},
    )
    graph.add_edge("tool_node", "triage_agent")
    graph.add_conditional_edges(
        "checklist_gate",
        _route_after_checklist,
        {"synthesis": "synthesis", "triage_agent": "triage_agent"},
    )
    graph.add_edge("synthesis", "draft_reply")
    graph.add_conditional_edges(
        "draft_reply",
        _route_after_draft,
        {"auto_communicate": "auto_communicate", "communication_node": "communication_node"},
    )
    graph.add_edge("auto_communicate", END)
    graph.add_edge("communication_node", END)

    # --- Compile with persistence and HITL interrupt ---
    # SqliteSaver persists thread state to disk so HITL thread_ids survive
    # app restarts. Falls back to in-memory MemorySaver if sqlite unavailable.
    try:
        import sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver
        conn = sqlite3.connect(_CHECKPOINT_DB, check_same_thread=False)
        _checkpointer = SqliteSaver(conn)
        _checkpointer.setup()
    except Exception:
        from langgraph.checkpoint.memory import MemorySaver
        import warnings
        warnings.warn(
            "SqliteSaver unavailable — falling back to MemorySaver (state lost on restart). "
            "Install langgraph-checkpoint-sqlite to persist HITL threads.",
            stacklevel=2,
        )
        _checkpointer = MemorySaver()
    return graph.compile(
        checkpointer=_checkpointer,
        interrupt_before=["communication_node"],
    )


async def build_graph_async():
    """Build graph with MCP-discovered tools merged with LOCAL_TOOLS."""
    mcp_tools = await _init_mcp_tools()
    all_tools = LOCAL_TOOLS + list(mcp_tools)
    triage_node = _make_triage_agent_node(all_tools)
    return _compile_graph(all_tools, triage_node)


def _build_graph_local_only():
    """Build graph using only local TRIAGE_TOOLS (Sprint 3 behavior)."""
    return _compile_graph(TRIAGE_TOOLS, triage_agent_node)


def build_graph():
    """Build and compile the agentic graph.

    Attempts MCP tool discovery first. If the MCP server is unavailable
    (missing config, server not installed, etc.), falls back to the
    local-only graph using TRIAGE_TOOLS.
    """
    import warnings

    if os.path.exists(MCP_CONFIG_PATH):
        try:
            return asyncio.run(build_graph_async())
        except Exception as e:
            warnings.warn(
                f"MCP tool discovery failed ({type(e).__name__}: {e}). "
                "Falling back to local-only tools (search_hospital_policy, "
                "get_patient_history, get_available_slots).",
                stacklevel=2,
            )
    else:
        warnings.warn(
            f"MCP config not found at {MCP_CONFIG_PATH}. "
            "Using local-only tools.",
            stacklevel=2,
        )

    return _build_graph_local_only()


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


def stream_triage_workflow(
    patient_message: str,
    patient_id: str = "",
    patient_email: str = "",
    thread_id: str = "",
    file_uri: str = "",
    file_mime_type: str = "",
    file_name: str = "",
):
    """
    Prepare a streaming triage workflow.

    Returns (app, initial_state, config, thread_id) — the caller drives
    app.stream(initial_state, config, stream_mode="messages").
    """
    app = _get_compiled()
    msg = (patient_message or "").strip()

    if not thread_id:
        thread_id = str(uuid.uuid4())

    config = {"configurable": {"thread_id": thread_id}}

    initial: TriageWorkflowState = {
        "message": msg,
        "patient_id": patient_id or "",
        "patient_email": patient_email or "",
        "messages": [HumanMessage(content=msg)],
        "is_emergency": False,
        "staff_approved": False,
        "is_complete": False,
        "file_uri": file_uri or None,
        "file_mime_type": file_mime_type or None,
        "file_name": file_name or None,
    }

    return app, initial, config, thread_id


def resume_chat(thread_id: str, patient_answer: str):
    """
    Prepare a streaming resume after a checklist interrupt.

    Returns (app, Command(resume=answer), config) — the caller drives
    app.stream(command, config, stream_mode="messages").
    """
    app = _get_compiled()
    config = {"configurable": {"thread_id": thread_id}}
    return app, Command(resume=patient_answer), config


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

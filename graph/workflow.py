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

# Module-level MCP singletons (populated by _init_mcp_tools).
# _mcp_client must stay alive for the process lifetime — the MultiServerMCPClient
# manages the stdio subprocess connections; letting it be GC'd kills the processes.
_mcp_client: Any = None
_mcp_tools: list | None = None

MCP_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "mcp_config.json",
)


async def _init_mcp_tools() -> list:
    """Discover MCP tools from all servers in mcp_config.json via MultiServerMCPClient.

    Resolves relative paths (--data-dir, cwd) to absolute so subprocesses find
    the right files regardless of the caller's working directory.
    Caches the result so servers are only started once per process.

    The client is stored in _mcp_client (module-level) to keep the stdio subprocess
    connections alive for the lifetime of the process — GC'ing the client kills them.
    """
    global _mcp_client, _mcp_tools
    if _mcp_tools is not None:
        return _mcp_tools

    from langchain_mcp_adapters.client import MultiServerMCPClient

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    with open(MCP_CONFIG_PATH) as f:
        config = json.load(f)

    # Resolve relative paths in each server config so subprocesses work
    # regardless of the caller's CWD.
    for server_cfg in config.values():
        # Resolve cwd (used by triageai-tools to find the package)
        cwd = server_cfg.get("cwd")
        if cwd and not os.path.isabs(cwd):
            server_cfg["cwd"] = os.path.normpath(os.path.join(project_root, cwd))

        # Resolve --data-dir for chroma-mcp-server
        args = server_cfg.get("args", [])
        for i, arg in enumerate(args):
            if arg == "--data-dir" and i + 1 < len(args):
                data_dir = args[i + 1]
                if not os.path.isabs(data_dir):
                    args[i + 1] = os.path.normpath(os.path.join(project_root, data_dir))

    _mcp_client = MultiServerMCPClient(config)  # kept alive — owns the subprocess connections
    _mcp_tools = await _mcp_client.get_tools()
    return _mcp_tools


# ---------------------------------------------------------------------------
# Sync bridge for async-only MCP tools
# ---------------------------------------------------------------------------
# langchain-mcp-adapters returns StructuredTools that ONLY implement the async
# path (coroutine); their sync _run raises "StructuredTool does not support sync
# invocation." The graph is driven synchronously (app.invoke / app.stream) with a
# sync SqliteSaver, so ToolNode calls the sync path and blows up the moment the
# agent uses an MCP tool. Rather than convert the whole stack to async (which
# would also force an async checkpointer), we run each MCP tool's coroutine on a
# single persistent background event loop and expose a sync wrapper. One loop on
# one daemon thread keeps every MCP stdio connection on a consistent loop.
_bg_loop: Any = None


def _get_bg_loop():
    """Lazily start a single background event loop on a daemon thread."""
    global _bg_loop
    if _bg_loop is None:
        import threading

        _bg_loop = asyncio.new_event_loop()
        threading.Thread(target=_bg_loop.run_forever, daemon=True).start()
    return _bg_loop


def _run_coro_sync(coro):
    """Run an awaitable to completion on the background loop, blocking the caller."""
    return asyncio.run_coroutine_threadsafe(coro, _get_bg_loop()).result()


def _make_sync_mcp_tool(async_tool):
    """Wrap an async-only MCP tool so ToolNode's sync path can call it.

    Preserves name/description/args_schema (so the bound LLM schema is identical)
    and keeps the original coroutine, while adding a sync func that bridges to the
    background loop.
    """
    from langchain_core.tools import StructuredTool

    def _sync_call(**kwargs):
        return _run_coro_sync(async_tool.ainvoke(kwargs))

    return StructuredTool(
        name=async_tool.name,
        description=async_tool.description,
        args_schema=async_tool.args_schema,
        func=_sync_call,
        coroutine=async_tool.coroutine,
    )


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
    # Priority: PostgresSaver (DATABASE_URL set) → SqliteSaver (local dev) → MemorySaver (last resort)
    _database_url = os.environ.get("DATABASE_URL", "")
    if _database_url:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            _checkpointer = PostgresSaver.from_conn_string(_database_url)
            _checkpointer.setup()  # idempotent CREATE TABLE IF NOT EXISTS
        except Exception as _pg_err:
            import warnings
            warnings.warn(
                f"PostgresSaver failed ({_pg_err}). Falling back to SqliteSaver.",
                stacklevel=2,
            )
            _database_url = ""

    if not _database_url:
        try:
            import sqlite3
            from langgraph.checkpoint.sqlite import SqliteSaver
            conn = sqlite3.connect(_CHECKPOINT_DB, check_same_thread=False)
            _checkpointer = SqliteSaver(conn)
            _checkpointer.setup()
        except Exception as _sqlite_err:
            from langgraph.checkpoint.memory import MemorySaver
            import warnings
            warnings.warn(
                f"SqliteSaver unavailable ({type(_sqlite_err).__name__}: {_sqlite_err}) "
                "— falling back to MemorySaver (state lost on restart). "
                "Set DATABASE_URL for persistent HITL.",
                stacklevel=2,
            )
            _checkpointer = MemorySaver()
    return graph.compile(
        checkpointer=_checkpointer,
        interrupt_before=["communication_node"],
    )


async def build_graph_async():
    """Build graph with MCP-discovered tools merged with LOCAL_TOOLS.

    MCP tools take priority: local tools whose names are already provided
    by an MCP server are excluded to avoid duplicate tool definitions.
    """
    mcp_tools = [_make_sync_mcp_tool(t) for t in await _init_mcp_tools()]
    mcp_tool_names = {t.name for t in mcp_tools}
    local_tools = [t for t in LOCAL_TOOLS if t.name not in mcp_tool_names]
    all_tools = local_tools + list(mcp_tools)
    tool_names = [t.name for t in all_tools]
    print(f"[TriageAI] MCP path active — tools: {tool_names}")
    triage_node = _make_triage_agent_node(all_tools)
    return _compile_graph(all_tools, triage_node)


def _build_graph_local_only():
    """Build graph using only local TRIAGE_TOOLS (MCP unavailable fallback)."""
    print(f"[TriageAI] Local-only path active — tools: {[t.name for t in TRIAGE_TOOLS]}")
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


# ---------------------------------------------------------------------------
# Online evals: tracing + staff-feedback capture (Sprint 7)
# ---------------------------------------------------------------------------

def _tracing_enabled() -> bool:
    """True when LangSmith tracing is configured, so online-eval hooks should run."""
    truthy = {"1", "true", "yes", "on"}
    tracing = (os.environ.get("LANGSMITH_TRACING") or os.environ.get("LANGCHAIN_TRACING_V2") or "").lower()
    has_key = bool(os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY"))
    return tracing in truthy and has_key


def _log_staff_feedback(run_id: str, original_draft: str, edited_draft: str | None) -> None:
    """Attach staff-review feedback to the live LangSmith run (Pillar 2, online evals).

    Two signals, both derived from the HITL approve/edit action — no labeling needed:
      - staff_approved:   1.0 if the staff sent the draft unchanged, else 0.0
      - draft_edit_ratio: 0.0 (verbatim approval) … 1.0 (fully rewritten); a continuous,
                          real-world proxy for draft quality.

    Fail-open: any error here must never break the staff send path.
    """
    if not run_id or not _tracing_enabled():
        return
    try:
        import difflib

        from langsmith import Client

        approved = edited_draft is None
        if approved:
            edit_ratio = 0.0
        else:
            # difflib ratio is similarity in [0,1]; edit_ratio is its complement.
            sim = difflib.SequenceMatcher(None, original_draft or "", edited_draft or "").ratio()
            edit_ratio = round(1.0 - sim, 4)

        client = Client()
        client.create_feedback(
            run_id,
            key="staff_approved",
            score=1.0 if approved else 0.0,
            comment="approved verbatim" if approved else "edited before sending",
        )
        client.create_feedback(
            run_id,
            key="draft_edit_ratio",
            score=edit_ratio,
            comment=f"staff edit distance (0=verbatim, 1=rewritten): {edit_ratio}",
        )
    except Exception as e:  # pragma: no cover - telemetry must not break sends
        import warnings

        warnings.warn(f"LangSmith staff feedback not logged: {e}")


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

    # Online evals: when LangSmith tracing is on, capture the root run_id of this
    # invocation so the HITL resume path can attach staff feedback to the exact run.
    # No-ops (and adds no overhead) when tracing is disabled, preserving demo mode.
    ls_run_id = ""
    try:
        if _tracing_enabled():
            from langchain_core.tracers.context import collect_runs

            with collect_runs() as cb:
                final = app.invoke(initial, config)
            if cb.traced_runs:
                ls_run_id = str(cb.traced_runs[0].id)
        else:
            final = app.invoke(initial, config)
    except Exception:
        # If LangGraph fails entirely, fall back
        return _run_fallback(msg, patient_id)

    safety_result = final.get("safety_result") or {}
    triage_result = final.get("triage_result") or {}

    # Embed the thread_id and hitl_status into triage_result for the UI
    triage_result["thread_id"] = thread_id
    if ls_run_id:
        # Round-trips through the UI so resume_workflow can find the run to score.
        triage_result["ls_run_id"] = ls_run_id

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
    ls_run_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Resume an interrupted workflow after staff review.

    If edited_draft is provided, the draft_reply in state is updated before resuming.
    The graph continues from where it was interrupted (communication_node) and sends
    the finalized email.

    ls_run_id (online evals, Sprint 7): the LangSmith run_id captured by
    run_triage_workflow. When provided (and tracing is on), the staff approve/edit
    decision is logged back to that run as feedback. The UI round-trips it via
    triage_result["ls_run_id"].

    Returns (safety_result dict, triage_result dict) — same shape as run_triage_workflow.
    """
    app = _get_compiled()
    config = {"configurable": {"thread_id": thread_id}}

    # Snapshot the AI-generated draft *before* the staff edit so the feedback edit-ratio
    # compares against what the model actually produced.
    original_draft = ""
    if ls_run_id:
        try:
            snap = app.get_state(config)
            original_draft = (snap.values.get("draft_reply") or "") if snap and snap.values else ""
        except Exception:
            original_draft = ""

    # If staff edited the draft, update the state before resuming
    if edited_draft is not None:
        app.update_state(config, {"draft_reply": edited_draft})

    # Online evals: record the staff approve/edit decision as LangSmith feedback.
    _log_staff_feedback(ls_run_id, original_draft, edited_draft)

    # Resume execution: invoke(None, config) continues from the interrupt point
    final = app.invoke(None, config)

    safety_result = final.get("safety_result") or {}
    triage_result = final.get("triage_result") or {}
    triage_result["thread_id"] = thread_id
    triage_result["hitl_status"] = final.get("hitl_status", "approved")

    return safety_result, triage_result

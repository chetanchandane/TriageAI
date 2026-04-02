"""
Safe-Stream Bridge for TriageAI.

Wraps LangGraph's sync ``app.stream(stream_mode='messages')`` into a
generator that yields simple dicts the Streamlit chat UI can consume.

Yields:
    {"type": "token",     "content": "..."}   — streamed text chunk
    {"type": "status",    "content": "..."}   — tool/node status update
    {"type": "interrupt", "content": "..."}   — checklist follow-up question
    {"type": "done",      "content": ""}      — stream finished normally
    {"type": "error",     "content": "..."}   — unrecoverable error
"""
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

# Human-friendly labels for tool calls and graph nodes
_TOOL_LABELS = {
    "get_patient_history": "Fetching patient history",
    "search_hospital_policy": "Searching clinic policies",
    "get_available_slots": "Checking available appointment slots",
    # MCP-discovered tools (chroma)
    "chroma_query_documents": "Searching clinic policies",
    "chroma_get_collection": "Loading policy collection",
}

_NODE_LABELS = {
    "safety": "Screening for emergencies",
    "triage_agent": "Analyzing your message",
    "tool_node": "Gathering information",
    "checklist_gate": "Reviewing completeness",
    "synthesis": "Preparing triage assessment",
    "draft_reply": "Drafting reply",
    "communication_node": "Preparing to send",
    "auto_communicate": "Sending response",
}


def _get_tool_label(tool_name):
    """Return a human-friendly label for a tool call."""
    return _TOOL_LABELS.get(tool_name, f"Looking up {tool_name}")


def _get_node_label(node_name):
    """Return a human-friendly label for a graph node."""
    return _NODE_LABELS.get(node_name, "")


def stream_graph(app, inputs, config):
    """Sync generator wrapping ``app.stream(stream_mode='messages')``.

    After the stream exhausts, checks ``app.get_state(config)`` for
    pending interrupts (checklist gate or HITL) and yields an interrupt
    event so the UI can prompt the patient.
    """
    last_node = None

    try:
        for chunk, metadata in app.stream(inputs, config, stream_mode="messages"):
            # Track node transitions for status updates
            current_node = metadata.get("langgraph_node", "")
            if current_node and current_node != last_node:
                last_node = current_node
                node_label = _get_node_label(current_node)
                if node_label:
                    yield {"type": "status", "content": node_label}

            # Tool-call status — the AI is requesting a tool
            if isinstance(chunk, (AIMessage, AIMessageChunk)):
                if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                    for tc in chunk.tool_calls:
                        tool_name = tc.get("name", "tool")
                        yield {"type": "status", "content": _get_tool_label(tool_name)}
                # Streamed text tokens from the AI
                elif hasattr(chunk, "content") and isinstance(chunk.content, str) and chunk.content.strip():
                    yield {"type": "token", "content": chunk.content}

            # Tool results — show a brief status that data came back
            elif isinstance(chunk, ToolMessage):
                tool_name = getattr(chunk, "name", "tool")
                yield {"type": "status", "content": f"Received results from {_get_tool_label(tool_name).lower()}"}

    except Exception as e:
        yield {"type": "error", "content": f"Workflow stream failed: {e}"}
        return

    # Check for interrupts after stream exhausts
    try:
        snapshot = app.get_state(config)
        if snapshot and snapshot.tasks:
            for task in snapshot.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    yield {
                        "type": "interrupt",
                        "content": str(task.interrupts[0].value),
                    }
                    return
    except Exception:
        pass

    yield {"type": "done", "content": ""}

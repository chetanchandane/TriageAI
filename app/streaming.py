"""
Safe-Stream Bridge for TriageAI (Sprint 5).

Wraps LangGraph's sync ``app.stream(stream_mode='messages')`` into a
generator that yields simple dicts the Streamlit chat UI can consume.

Yields:
    {"type": "token",     "content": "..."}   — streamed text chunk
    {"type": "status",    "content": "..."}   — tool call status
    {"type": "interrupt", "content": "..."}   — checklist follow-up question
    {"type": "done",      "content": ""}      — stream finished normally
    {"type": "error",     "content": "..."}   — unrecoverable error
"""


def stream_graph(app, inputs, config):
    """Sync generator wrapping ``app.stream(stream_mode='messages')``.

    After the stream exhausts, checks ``app.get_state(config)`` for
    pending interrupts (checklist gate or HITL) and yields an interrupt
    event so the UI can prompt the patient.
    """
    try:
        for chunk, metadata in app.stream(inputs, config, stream_mode="messages"):
            # Tool-call status messages
            if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                tool_name = chunk.tool_calls[0].get("name", "tool")
                yield {"type": "status", "content": f"Searching {tool_name}..."}
            # Text content tokens
            elif hasattr(chunk, "content") and isinstance(chunk.content, str) and chunk.content.strip():
                yield {"type": "token", "content": chunk.content}
    except Exception:
        yield {"type": "error", "content": "Workflow stream failed."}
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

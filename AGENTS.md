# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Purpose

TriageAI is an agentic clinical-triage system: it screens patient-portal messages for active emergencies, then runs a cyclic LangGraph agent that pulls patient history and clinic policy via tools, asks the patient follow-up questions, and produces a structured triage assessment plus a policy-grounded draft reply for human-in-the-loop staff review. It is a master's capstone (RIT), not a production deployment.

## Commands

```bash
# Install
pip install -r requirements.txt

# Configure (only LLM_GEMINI_API_KEY is strictly required; everything else has a fallback)
cp .env.example .env

# Seed the persistent ChromaDB policy store (idempotent; safe to re-run).
# Not strictly required — agents/policy_agent.py inline-seeds 7 default policies
# if the store is empty — but run it to make seeding explicit.
python scripts/seed_policy.py

# Run the app
streamlit run app/streamlit_app.py

# Tests — this repo has no pytest fixtures; tests are plain functions with a
# hand-rolled runner. Both forms work:
python -m pytest tests/test_tools.py -v
python tests/test_tools.py          # custom main() runner, exits non-zero on failure

# Evaluation harness (runs labeled datasets through the full workflow)
python scripts/run_eval.py                  # all messages in tests/eval_dataset.json
python scripts/run_eval.py --safety-only    # safety screen only, no triage LLM calls (fast)
python scripts/run_eval.py --ids E01 FP02   # specific message IDs
python scripts/run_context_urgency_eval.py  # context-vs-urgency eval (separate dataset)

# Load test (submits a dataset through the workflow into the message store)
python scripts/generate_test_messages.py    # one-time: build the dataset
python scripts/load_test.py --limit 20

# Verify MCP tools resolve/discover correctly
python scripts/verify_mcp_tools.py
```

There is **no linter, formatter, or build step** configured (no `pyproject.toml`, `ruff`, `black`, `Makefile`). Do not invent lint commands.

## Architecture

Data flow: **safety gate → cyclic triage agent (tool loop) → checklist gate (patient interrupt) → synthesis → draft reply → HITL interrupt (staff) → email**.

```
safety → [is_emergency? → synthesis (short-circuit) | → triage_agent]
triage_agent → [tool_calls? → tool_node → triage_agent | → checklist_gate]
checklist_gate → [missing info? → interrupt(patient) → triage_agent | is_complete → synthesis]
synthesis → draft_reply → [LOW? → auto_communicate → END | → communication_node (INTERRUPTED) → END]
```

The graph is defined in `graph/workflow.py` (`_compile_graph`); node functions and routing live in `graph/nodes.py` and `graph/workflow.py`. Read both before editing the flow.

### Module boundaries
- `agents/` — standalone LLM functions, no graph/Streamlit imports. `safety_agent.screen_for_emergency` (LLM emergency screen), `policy_agent` (ChromaDB RAG + draft generation), `triage_agent.test_triage` (one-shot classifier, used only by fallback paths).
- `graph/` — LangGraph state (`state.py`), nodes + LangChain `@tool` wrappers (`nodes.py`), graph construction + public entry points (`workflow.py`).
- `mcp_tools/` — pure tool functions exposed for MCP (`database_tools`, `rag_tools`, `communication`). No Streamlit dependency. `server.py` re-exports all via `__all__`.
- `app/` — Streamlit UI (`streamlit_app.py`), auth (`auth.py`), message persistence (`messages_store.py`), and the streaming bridge (`streaming.py`).
- `schemas/` — single source of truth for `SafetyResult` and `TriageResult` Pydantic models.
- `scripts/` — seed, eval, and load-test entry points (each inserts project root onto `sys.path`).
- `data/` — `vector_store/` (ChromaDB, gitignored) and `checkpoints.db` (SQLite, **committed**).

### Two distinct Gemini client paths (do not unify blindly)
1. **`google.genai` (`genai.Client(...).models.generate_content`)** — used for structured `response_schema=` output in `safety_agent`, `triage_agent`, `policy_agent`, and `synthesis_node`'s `_structured_extraction`.
2. **`langchain_google_genai.ChatGoogleGenerativeAI`** — used only in `graph/nodes.py` because LangGraph's `ToolNode` and `.bind_tools()` require LangChain message types.

The model is read from `LLM_MODEL` (default `gemini-2.5-pro`) in every module — change the env var, not the code, to switch models. The API key is `LLM_GEMINI_API_KEY` (graph nodes also accept `GOOGLE_API_KEY` as a fallback).

### Layered graceful degradation
The system is built to run with only `LLM_GEMINI_API_KEY` set. Every external dependency falls back silently:
- **MCP tools:** `build_graph()` tries MCP discovery (`chroma-mcp-server` via `MultiServerMCPClient`, config in `mcp_config.json`) → falls back to local `TRIAGE_TOOLS` → `run_triage_workflow` falls back to `_run_fallback` (safety + one-shot triage, no graph) on `ImportError`.
- **Checkpointer:** `SqliteSaver` at `data/checkpoints.db` → `MemorySaver` (with `warnings.warn`) if `langgraph-checkpoint-sqlite` is missing.
- **Supabase:** absent → in-memory auth/messages (demo mode).
- **Resend email:** no `RESEND_API_KEY` → console-print mock.
- **ChromaDB:** unavailable → policy functions return `[]`.

### HITL via interrupts
The graph is compiled with `interrupt_before=["communication_node"]`. NORMAL/HIGH/EMERGENCY messages pause there; LOW routes to `auto_communicate` (not interrupted) and completes automatically. Staff resume via `resume_workflow(thread_id, edited_draft)`. A **second** interrupt type — `checklist_gate_node` calling `interrupt()` — pauses earlier to ask the patient follow-ups; the patient resumes via `resume_chat(thread_id, answer)`. Both interrupts coexist sequentially on the same `thread_id`. State recovery (`get_workflow_state`) depends entirely on the SqliteSaver checkpoint surviving restarts.

## Conventions

- Module-level docstring on every file, often with a `Sprint N` provenance note and a "Decision:" rationale. `DEVELOPMENT.md` is the authoritative decision log — append to it when making non-trivial changes, matching the existing `Sprint N (Month Year)` style.
- Private/internal helpers are prefixed `_` (`_build_triage_model`, `_route_after_safety`, `_parse_triage_json`). Public graph entry points are not.
- Heavy/optional imports (`chromadb`, `supabase`, `google.genai`, `resend`, langgraph internals) are done **lazily inside functions**, not at module top, so the app starts even when a dependency or key is missing.
- Tool-side errors are swallowed and degraded to safe defaults (`return ""`, `return []`, `is_potential_emergency=False`) rather than raised — this is intentional for fail-open behavior. Graph-build and MCP failures use `warnings.warn`, not bare `except: pass`.
- Triage JSON is parsed from a ```` ```json ... ``` ```` block (`_parse_triage_json`); the `TRIAGE_SYSTEM_PROMPT` instructs the model to emit exactly that. Changing the prompt's output contract breaks `synthesis_node` and `checklist_gate_node`.
- Workflow control fields (`thread_id`, `hitl_status`, `draft_reply`, `staff_approved`) are stuffed into the `triage_result` dict for the UI. The Patient view hides these; the Staff/Approvals views read them.

## Gotchas — do NOT

- **Do NOT rename `mcp_tools/` back to `mcp/`.** The folder was renamed precisely because `mcp/` shadowed the installed `mcp` pip package, breaking `from mcp import ...` inside `MultiServerMCPClient`. All imports use `mcp_tools.*`.
- **Do NOT rename `agents/triage_agent.py:test_triage`.** Despite the `test_` prefix it is **not** a pytest test — it is the production one-shot classifier used by fallback paths. pytest will not collect it (no `test_` filename match in `agents/`), but renaming will break `_run_fallback` and the Streamlit `ImportError` fallback.
- **Do NOT default the safety screen to `True` on LLM failure.** `_llm_call` deliberately returns `is_potential_emergency=False` on outage; an outage must not manufacture false-positive emergencies. The whole safety design targets 0% false negatives with *minimized* false positives — it is an LLM call, not regex (regex was removed in Sprint 6 because it matched historical mentions like "heart attack 5 years ago").
- **Do NOT make `synthesis_node` override urgency to EMERGENCY based on `safety.is_potential_emergency`.** It must gate on `state["is_emergency"]` (the short-circuit flag). Cases routed through the triage agent already have a context-informed urgency; overriding them re-introduces the false-positive bug fixed in Sprint 6.
- **Do NOT remove `nest_asyncio.apply()`** from `app/streamlit_app.py` / `graph/workflow.py`. Streamlit runs an event loop; `asyncio.run(build_graph_async())` fails without it. The `try/except ValueError` guard around it is for the uvloop case — keep it.
- **Do NOT block startup on a missing dependency or key.** Preserve the lazy-import + fallback pattern; many flows are exercised in demo mode with only the Gemini key.
- `data/checkpoints.db` is tracked in git and shows up dirty after most runs (SqliteSaver writes to it). `data/vector_store/` is gitignored. Don't commit incidental `checkpoints.db` churn unless relevant.

## Key references
- `README.md` — project framing, architecture diagram, evaluation results.
- `DEVELOPMENT.md` — full chronological decision log (Sprints 1–6); read it to understand *why* the code is shaped this way.
- `EVALUATION.md` — evaluation methodology and metrics.
- `supabase_schema.sql` — `profiles` (incl. `medical_history`) and `messages` tables with RLS notes.

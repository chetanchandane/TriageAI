# TriageAI — Codebase Summary

> Generated as a pre-read reference so an LLM (or a new contributor) can understand the entire system without opening every file. Covers architecture, data flow, every module's responsibility, conventions, and known gotchas.

**What it is:** TriageAI is an agentic clinical-triage system — a master's capstone (RIT), not a production deployment. It screens patient-portal messages for active emergencies, then runs a cyclic LangGraph agent that pulls patient history and clinic policy via tools, asks the patient follow-up questions, and produces a structured triage assessment plus a policy-grounded draft reply for human-in-the-loop (HITL) staff review.

**Stack:** LangGraph (cyclic agent + HITL interrupts) · Gemini 2.5 Pro (`google.genai` + `langchain_google_genai`) · MCP (Model Context Protocol) for tool exposure · ChromaDB (policy RAG) · Supabase/Postgres (auth, patient profiles, messages) · LangSmith (tracing + offline/online evals) · Streamlit (UI) · Resend (email).

---

## 1. High-level data flow

```
Patient message
      │
      ▼
 safety_node  ──── EMERGENCY (LLM-confirmed)? ──► synthesis_node (short-circuit, skips agent)
      │ no
      ▼
 triage_agent_node  ◄────────────────────────┐
      │                                      │
      ├── tool_calls? ──► tool_node ─────────┘   (loop: get_patient_history,
      │                                            search_hospital_policy,
      │                                            get_available_slots)
      │ done reasoning
      ▼
 checklist_gate_node ──── missing info? ──► interrupt() → patient answers → back to triage_agent_node
      │ is_complete
      ▼
 synthesis_node  →  draft_reply_node
                          │
                 urgency == LOW?
                    │           │
                   yes          no
                    │           │
                    ▼           ▼
           auto_communicate   communication_node  (⏸ INTERRUPTED — staff review)
                    │                │
                    ▼                ▼
                  END        staff edits/approves → resume_workflow() → email sent → END
```

Two distinct interrupt types coexist sequentially on the same `thread_id`:
1. **Patient interrupt** — `checklist_gate_node` calls `interrupt()` mid-conversation to ask the patient a follow-up question. Resumed via `resume_chat(thread_id, answer)`.
2. **Staff (HITL) interrupt** — the graph is compiled with `interrupt_before=["communication_node"]`. NORMAL/HIGH/EMERGENCY messages pause there so staff can review/edit the draft reply before it's emailed. LOW routes to `auto_communicate` instead (not interrupted — fully automatic). Resumed via `resume_workflow(thread_id, edited_draft)`.

State persists via a checkpointer (`SqliteSaver` at `data/checkpoints.db` by default, `PostgresSaver` if `DATABASE_URL` is set, `MemorySaver` as last resort), so a paused workflow survives restarts.

---

## 2. Directory map

```
TriageAI/
├── agents/          Standalone LLM functions — no graph/Streamlit imports
│   ├── safety_agent.py     screen_for_emergency() — LLM emergency screen
│   ├── triage_agent.py     test_triage() — one-shot classifier (fallback path only)
│   └── policy_agent.py     ChromaDB RAG + draft/next-steps generation
├── graph/           LangGraph orchestration
│   ├── state.py            TriageWorkflowState (TypedDict) + PatientContext dataclass
│   ├── nodes.py             All node functions + LangChain @tool wrappers + system prompt
│   └── workflow.py          Graph construction, MCP tool discovery, public entry points
├── mcp_tools/       Pure tool functions exposed via MCP (no Streamlit dependency)
│   ├── server.py            Re-exports all tools via __all__ (non-MCP import surface)
│   ├── mcp_server.py         FastMCP server — actual MCP tool registration (stdio)
│   └── tools/
│       ├── database_tools.py   Supabase: get_patient_history, get_available_slots
│       ├── rag_tools.py        ChromaDB fallback wrapper: search_hospital_policy
│       └── communication.py    Resend email: send_resolution_email, send_notification
├── app/             Streamlit UI
│   ├── streamlit_app.py     Login/register, patient chat, staff dashboard, HITL approvals
│   ├── auth.py               Supabase or in-memory demo auth
│   ├── messages_store.py     Persist/query patient messages (Supabase or in-memory)
│   └── streaming.py          Bridges LangGraph app.stream() into UI-friendly event dicts
├── schemas/
│   └── schemas.py            Pydantic: SafetyResult, TriageResult (single source of truth)
├── scripts/         Seed, eval, and load-test entry points (each inserts project root onto sys.path)
├── tests/           test_tools.py (hand-rolled runner) + labeled eval datasets (JSON)
├── data/
│   ├── vector_store/         ChromaDB persistent store (gitignored)
│   ├── policies/              8 source policy docs (markdown) used to seed ChromaDB
│   └── checkpoints.db         SQLite LangGraph checkpoints (committed to git)
├── mcp_config.json  MCP server registry (chroma-mcp-server + triageai-tools)
├── supabase_schema.sql   profiles + messages tables, RLS policies, auto-profile trigger
├── requirements.txt
├── README.md         Project framing, architecture diagram, evaluation results
├── DEVELOPMENT.md    Full chronological decision log (Sprints 1–7) — read for "why"
└── EVALUATION.md     Evaluation methodology and metrics
```

Not covered further below (peripheral to code): `instructions/` (planning notes), `report/` (capstone report drafts), `assets/` (images/demo video), `deploy/` (Docker), `.github/workflows/eval.yml` (CI).

---

## 3. Module-by-module detail

### 3.1 `schemas/schemas.py` — data contracts

```python
class SafetyResult(BaseModel):
    is_potential_emergency: bool
    reason: str
    triggered_by: str   # "llm" or "none" (legacy: "rules")

class TriageResult(BaseModel):
    intent: str                  # "Appointment", "Refill", "Clinical Question", "Billing", "Multiple"
    confidence: float            # 0–1
    urgency: str                 # "EMERGENCY" | "HIGH" | "NORMAL" | "LOW"
    summary: str                 # 1-sentence summary
    checklist: List[str]         # missing info to ask patient; empty = complete
    recommended_queue: str       # staff department to route to
```
These are used as `response_schema=` for Gemini structured output in multiple places, and their JSON shape is the contract the whole system is built around.

### 3.2 `agents/safety_agent.py` — emergency screen

`screen_for_emergency(patient_message) -> SafetyResult`. Single context-aware Gemini call (structured output) that decides whether a message describes an **active, current, life-threatening** emergency — explicitly trained via prompt to distinguish "I'm having chest pain right now" from "I had a heart attack 5 years ago." Regex/keyword matching was removed (Sprint 6) because it matched historical mentions.

- On LLM failure: returns `is_potential_emergency=False` deliberately (never fabricate a false positive from an outage — the triage agent still assesses the case afterward).
- `triggered_by` is `"llm"` (confirmed) or `"none"` (clear).
- Decorated `@traceable` (LangSmith).

### 3.3 `agents/triage_agent.py` — one-shot classifier (fallback only)

`test_triage(patient_message) -> TriageResult`. Despite the `test_` prefix this is **production code**, used only by `_run_fallback` (graph import failure) and the Streamlit `ImportError` fallback path. Not a pytest test (pytest won't collect it — no `test_` filename match in `agents/`). **Do not rename it** — that breaks the fallback paths.

### 3.4 `agents/policy_agent.py` — RAG + draft generation

- `_get_collection()` — lazy `chromadb.PersistentClient` at `data/vector_store/`, collection `"hospital_policies"`.
- `get_relevant_policy(message, triage_summary, top_k=3) -> list[str]` — ChromaDB query; returns `[]` if Chroma unavailable (fail-open).
- `generate_draft_reply(message, triage_result, policy_chunks) -> str` — Gemini call producing a 2–4 sentence staff-editable draft grounded in policy context. Falls back to a placeholder string if no API key.
- `generate_next_steps(...) -> list[str]` — 2–4 short action items for staff, similar fallback behavior.

### 3.5 `graph/state.py` — workflow state

`TriageWorkflowState` (TypedDict, `total=False`):
- Inputs: `patient_id`, `patient_email`, `message`
- `messages: Annotated[list, add_messages]` — running conversation log (LangGraph reducer appends rather than overwrites)
- Structured outputs: `safety_result`, `triage_result` (both `Optional[dict]`)
- Context: `medical_history`, `policy_context`, `draft_reply`
- Control: `is_emergency` (short-circuit flag), `staff_approved`, `hitl_status` (`"pending_review"` / `"approved"` / `"auto_completed"` / `"dismissed"`)
- Multimodal (Sprint 5): `file_uri` (base64 data URI), `file_mime_type`, `file_name`
- Conversational interrupt control: `is_complete`

Also defines `PatientContext` (dataclass: `user_id`, `patient_id`, `full_name`, `email`) and Streamlit session helpers `get_patient_context` / `set_patient_context` / `clear_patient_context`.

### 3.6 `graph/nodes.py` — node functions and tool wrappers

**Tool wrappers** (LangChain `@tool`, bound to Gemini via `.bind_tools()`):
- `get_patient_history(patient_id)` → wraps `mcp_tools.tools.database_tools.get_patient_history`
- `search_hospital_policy(query)` → wraps `mcp_tools.tools.rag_tools.search_hospital_policy` (top_k=3)
- `get_available_slots()` → wraps `mcp_tools.tools.database_tools.get_available_slots`

`LOCAL_TOOLS` and `TRIAGE_TOOLS` are both `[get_patient_history, search_hospital_policy, get_available_slots]` — `LOCAL_TOOLS` is merged with MCP-discovered tools (de-duped by name, MCP wins); `TRIAGE_TOOLS` is the full set for the local-only fallback path.

**`TRIAGE_SYSTEM_PROMPT`** — the core behavioral contract. Instructs the agent to act as a first-contact intake agent (not a one-shot classifier): use tools, aggressively populate the `checklist` field when info is thin (symptom duration/severity/progression, refill medication+dose, appointment purpose, billing details), and only emit an empty checklist when genuinely complete. Final answer must be wrapped in ` ```json ... ``` ` matching `TriageResult`'s shape. **Changing this prompt's output contract breaks `synthesis_node` and `checklist_gate_node`**, which both parse it via `_parse_triage_json` (regex for a ```` ```json ``` ```` block, then a raw-JSON fallback).

**Nodes:**
- `safety_node` — runs `screen_for_emergency`; if clear and an image is attached, also runs `_visual_safety_screen` (Gemini vision checking for bleeding, respiratory distress, cyanosis, trauma, burns, anaphylaxis). Sets `is_emergency` (short-circuit) and `safety_result`.
- `triage_agent_node` / `_make_triage_agent_node(tools)` — invokes Gemini with bound tools. On first turn, seeds `messages` with the system prompt + patient message (+ image/PDF content if attached). Returns whatever the model responds (tool_calls or final text).
- `checklist_gate_node` — parses the last AI message's checklist; if non-empty, calls `interrupt(question)` to pause and ask the patient. On resume, appends the patient's answer as a `HumanMessage` **without** setting `is_complete=True` — routing sends control back to `triage_agent_node` to re-evaluate (true multi-turn loop, not single-pass). Only sets `is_complete=True` when the agent itself produces an empty checklist.
- `synthesis_node` — extracts the final `TriageResult`: first tries parsing JSON from the last AI message (`_parse_triage_json`), falls back to an explicit structured-extraction Gemini call (`_structured_extraction`) if that yields `intent == "Unknown"`. **Only overrides urgency to EMERGENCY when `state["is_emergency"]` is true** (the short-circuit flag) — not when `safety_result.is_potential_emergency` is true, because cases that went through the full triage agent already have context-informed urgency (this was a Sprint 6 bug fix; re-introducing the override on `safety.is_potential_emergency` reintroduces false-positive escalation).
- `draft_reply_node` — calls `policy_agent.get_relevant_policy` + `generate_draft_reply`; falls back to a generic placeholder on any exception.
- `communication_node` — sends the (possibly staff-edited) draft via `send_resolution_email`; sets `staff_approved=True`, `hitl_status="approved"`. This is the node named in `interrupt_before`.

### 3.7 `graph/workflow.py` — graph construction and entry points

- **MCP tool discovery**: `_init_mcp_tools()` reads `mcp_config.json`, resolves relative paths, and starts `MultiServerMCPClient` (kept alive in module-level `_mcp_client` — GC'ing it would kill the stdio subprocesses). `build_graph_async()` merges MCP tools with `LOCAL_TOOLS` (MCP wins on name collision).
- **Sync bridge for async MCP tools**: LangChain-MCP-adapters tools only implement an async `_run`; the graph runs synchronously (`app.invoke`/`app.stream` + sync checkpointer). `_make_sync_mcp_tool()` wraps each async tool so its coroutine runs on a single persistent background event loop (`_get_bg_loop()`, one daemon thread) and blocks the caller for the result (`_run_coro_sync`).
- **`build_graph()`** — public builder: tries `asyncio.run(build_graph_async())`; on any exception (or missing `mcp_config.json`), warns and falls back to `_build_graph_local_only()` (uses `TRIAGE_TOOLS`).
- **`_compile_graph(all_tools, triage_node_fn)`** — shared graph-building logic: adds all 8 nodes (`safety`, `triage_agent`, `tool_node` [`ToolNode`], `checklist_gate`, `synthesis`, `draft_reply`, `communication_node`, `auto_communicate`), wires the conditional edges described in §1, and compiles with a checkpointer. **Checkpointer priority**: `DATABASE_URL` set → `PostgresSaver`; else → `SqliteSaver` at `data/checkpoints.db`; else (import failure) → `MemorySaver` with a `warnings.warn`.
- **Fallback (no LangGraph)**: `_run_fallback()` runs `screen_for_emergency` + `test_triage` directly (Sprint 1 behavior) if the graph can't even be imported.
- **Public entry points**:
  - `run_triage_workflow(patient_message, patient_id, patient_email, thread_id)` → `(safety_dict, triage_dict)`. Generates a `thread_id` if not given, invokes the compiled graph, embeds `thread_id`/`hitl_status` into the result. If tracing is on, wraps the invoke in `collect_runs()` to capture the LangSmith root `run_id` for later staff-feedback logging.
  - `get_workflow_state(thread_id)` — returns the full state dict for staff-dashboard inspection.
  - `stream_triage_workflow(...)` / `resume_chat(thread_id, patient_answer)` — return `(app, inputs/Command, config[, thread_id])` tuples for the Streamlit UI to drive `app.stream(..., stream_mode="messages")` directly.
  - `resume_workflow(thread_id, edited_draft=None, ls_run_id="")` — updates `draft_reply` in state if staff edited it, logs staff-approval feedback to LangSmith (`_log_staff_feedback`: `staff_approved` 1.0/0.0 + `draft_edit_ratio` via `difflib` similarity), then `app.invoke(None, config)` to resume past the interrupt.
- **Online evals (Sprint 7, Pillar 2)**: `_tracing_enabled()` checks `LANGSMITH_TRACING`/`LANGCHAIN_TRACING_V2` + API key. `_log_staff_feedback` is fail-open — any error here must never break the staff send path.

### 3.8 `mcp_tools/` — tool implementations

- **`tools/database_tools.py`** — `get_patient_history(patient_id)` queries Supabase `profiles.medical_history` (returns `""` on any failure/missing config — fail-open, not fail-closed). `get_available_slots()` returns a hardcoded list `["Mon 10am", "Wed 2pm", "Fri 9am"]`.
- **`tools/rag_tools.py`** — thin wrapper around `policy_agent.get_relevant_policy`; deprecated in favor of the Chroma MCP server's `chroma_query_documents` when that server is running, but kept as the local fallback.
- **`tools/communication.py`** — `send_resolution_email` via Resend when `RESEND_API_KEY` is set, else console-print mock. **Note a hardcoded test-mode redirect**: all emails are currently forced to `_FIXED_RECIPIENT = "chetanchandane10@gmail.com"` (Resend's sandbox mode without a verified domain only delivers to the account owner's address) — the real intended recipient is preserved in the subject line as `[for <email>]`. Remove `_FIXED_RECIPIENT` once a domain is verified in Resend.
- **`server.py`** — plain Python re-export (`__all__`) of all tool functions; not an MCP server itself, just an import surface.
- **`mcp_server.py`** — the actual `FastMCP("triageai-tools")` server (stdio transport), registered in `mcp_config.json` and launched as a subprocess by `MultiServerMCPClient`. Exposes `get_patient_history`, `get_available_slots`, `search_hospital_policy`.

**Do not rename `mcp_tools/` back to `mcp/`** — it originally shadowed the installed `mcp` pip package, breaking `from mcp import ...` inside `MultiServerMCPClient`.

### 3.9 `app/` — Streamlit UI

- **`auth.py`** — `register`/`login`/`get_current_user`, each dispatching to Supabase (`_supabase_*`) if `SUPABASE_URL`+`SUPABASE_ANON_KEY` are set, else in-memory demo dict (`_demo_users`). `get_supabase_client()` is shared with `messages_store.py` so inserts run under the logged-in user's JWT (required for RLS).
- **`messages_store.py`** — `save_message`, `get_all_messages_for_staff` (sorted by urgency then recency; optional `SUPABASE_SERVICE_ROLE_KEY` client bypasses RLS to see all patients), `get_messages_for_patient`, `update_message_triage_result`. Falls back to an in-memory `_demo_messages` list.
- **`streaming.py`** — `stream_graph(app, inputs, config)` generator wrapping `app.stream(stream_mode="messages")`. Yields `{"type": "token"|"status"|"interrupt"|"run_id"|"done"|"error", "content": ...}`. Deliberately suppresses raw text tokens from internal nodes (`triage_agent`, `synthesis`, `draft_reply` — these contain internal JSON assessments, not patient-facing text). Maps node/tool names to human-friendly status labels (`_NODE_LABELS`, `_TOOL_LABELS`) e.g. `"triage_agent"` → `"Analyzing your message"`.
- **`streamlit_app.py`** (626 lines, the UI entry point) — three tabs after login:
  1. **Patient Chat** (`render_patient_portal`) — `st.chat_input`-driven streaming conversation via `stream_triage_workflow`/`resume_chat`, sidebar file uploader (jpg/png/pdf → base64 data URI via `_process_uploaded_file`), collapsible message history.
  2. **Staff view** (`render_staff_view`) — two-pane dashboard: active queue (left, urgency-emoji-coded, ⏸️/✅/⚡ HITL badges) and detail view (right) with editable draft reply, "Approve & Send" (resumes HITL thread or sends directly), "Route to ER", "Dismiss".
  3. **Pending Approvals** (`render_pending_approvals`) — dedicated list of `hitl_status == "pending_review"` messages with the same approve/dismiss actions, scoped to the true HITL queue.
  - `_run_workflow()` lazily imports `graph.workflow.run_triage_workflow`, falling back to direct `safety_agent`/`triage_agent` calls on `ImportError`.
  - `nest_asyncio.apply()` at module top (guarded by `try/except ValueError` for the Streamlit-uses-uvloop case) — **do not remove**, `asyncio.run()` inside the sync Streamlit event loop fails without it.

### 3.10 `scripts/` — operational entry points

| Script | Purpose |
|---|---|
| `seed_policy.py` | Idempotent: seeds ChromaDB persistent store from `data/policies/*.md` (also `.txt`/`.pdf`). `--force` wipes and reseeds. |
| `run_eval.py` | Runs `tests/eval_dataset.json` (26 labeled messages) through the full workflow; scorecard of safety recall/precision, intent/urgency accuracy. `--safety-only`, `--ids` flags. |
| `run_context_urgency_eval.py` | Tests whether mild symptoms get urgency-elevated given severe patient history context (`tests/eval_dataset_context_urgency.json`, 30 cases) — calls Gemini directly (bypasses the graph, since it needs Supabase for history in the full path). |
| `generate_test_messages.py` | Uses Gemini to generate 100+ realistic messages → `tests/eval_dataset_large.json`, for load testing. |
| `load_test.py` | Submits a dataset through the full workflow into the message store; produces `tests/load_test_results.json`/`.csv`. |
| `migrate_eval_datasets.py` | Normalizes 6 historical eval-dataset schemas into one canonical `tests/eval_dataset_unified.json` (dedup by message text; 215 unique from 249 raw). |
| `sync_langsmith_dataset.py` | Idempotently uploads the unified dataset to a LangSmith Dataset (`TriageAI-offline`), reconciled by stable `id` in metadata. |
| `eval_evaluators.py` | LangSmith `(run, example)` evaluators: code-based (`safety_correct`, `safety_recall` [hard gate], `urgency_exact`, `urgency_within_one`, `intent_match`) + LLM-as-judge (`draft_policy_grounded`, `draft_faithful`, `draft_tone`). |
| `online_evaluators.py` | Reference-free variants of the same judge rubrics for scoring live production traces (no labels available) — intended as LangSmith automations. |
| `run_langsmith_eval.py` | Runs the full offline eval suite against the LangSmith dataset; `safety_recall` is a hard gate at 1.00 (exits non-zero on breach — CI gate). `--code-only` skips LLM judges for speed. |
| `verify_mcp_tools.py` | Sanity-checks the three core MCP tools resolve and return data. |

### 3.11 `tests/test_tools.py`

Not pytest fixtures — plain functions with a hand-rolled `main()` runner (`python tests/test_tools.py`, exits non-zero on failure; `python -m pytest tests/test_tools.py -v` also works since functions are named `test_*`). Covers: the 3 MCP tools (ChromaDB, Supabase), schema imports, MCP server exports, persistent-store checks, MCP config validation, local-only graph fallback build, multimodal state fields, checklist gate behavior, streaming bridge imports.

---

## 4. Two distinct Gemini client paths (intentional, do not unify)

1. **`google.genai`** (`genai.Client(...).models.generate_content`) — used wherever `response_schema=` structured output is needed: `safety_agent`, `triage_agent`, `policy_agent`, and `synthesis_node._structured_extraction`.
2. **`langchain_google_genai.ChatGoogleGenerativeAI`** — used only in `graph/nodes.py`, because LangGraph's `ToolNode` and `.bind_tools()` require LangChain message types.

Model is read from `LLM_MODEL` env var (default `gemini-2.5-pro`) in every module — **change the env var to switch models, not the code**. API key is `LLM_GEMINI_API_KEY` (graph nodes also accept `GOOGLE_API_KEY` as a fallback).

---

## 5. Graceful degradation (fail-open by design)

The system is built to run with only `LLM_GEMINI_API_KEY` set — every other dependency degrades silently:

| Dependency | Fallback chain |
|---|---|
| MCP tools | `build_graph()` MCP discovery → local `TRIAGE_TOOLS` → `run_triage_workflow` → `_run_fallback` (safety + one-shot triage, no graph) on `ImportError` |
| Checkpointer | `PostgresSaver` (if `DATABASE_URL`) → `SqliteSaver` (`data/checkpoints.db`) → `MemorySaver` (`warnings.warn`) |
| Supabase | absent → in-memory auth/messages (demo mode) |
| Resend email | no `RESEND_API_KEY` → console-print mock |
| ChromaDB | unavailable → policy functions return `[]` |

Corollary conventions: tool-side errors are swallowed and degrade to safe defaults (`return ""`, `return []`, `is_potential_emergency=False`) rather than raised. Graph-build/MCP failures specifically use `warnings.warn` (visible in logs), not bare `except: pass`.

---

## 6. Conventions

- Module-level docstring on every file, often noting a `Sprint N` provenance and a "Decision:" rationale. **`DEVELOPMENT.md` is the authoritative chronological decision log** (Sprints 1–7) — read it for *why* the code is shaped the way it is.
- Private/internal helpers prefixed `_` (`_build_triage_model`, `_route_after_safety`, `_parse_triage_json`); public graph entry points are not.
- Heavy/optional imports (`chromadb`, `supabase`, `google.genai`, `resend`, langgraph internals) are done **lazily inside functions**, not at module top — so the app starts even when a dependency or key is missing.
- Triage JSON is parsed from a ` ```json ... ``` ` block (`_parse_triage_json` in `graph/nodes.py`); `TRIAGE_SYSTEM_PROMPT` instructs the model to emit exactly that shape. Changing the prompt's output contract breaks `synthesis_node` and `checklist_gate_node`.
- Workflow control fields (`thread_id`, `hitl_status`, `draft_reply`, `staff_approved`) are stuffed into the `triage_result` dict for the UI. The Patient view hides these; Staff/Approvals views read them.

---

## 7. Gotchas — do NOT

- **Do NOT rename `mcp_tools/` back to `mcp/`.** Shadows the installed `mcp` pip package, breaks `MultiServerMCPClient`.
- **Do NOT rename `agents/triage_agent.py:test_triage`.** It's the production one-shot classifier for fallback paths despite the `test_` prefix; pytest won't collect it anyway (wrong directory).
- **Do NOT default the safety screen to `True` on LLM failure.** `_llm_call` returns `is_potential_emergency=False` on outage deliberately — an outage must not manufacture false-positive emergencies. Target is 0% false negatives with *minimized* false positives; it's an LLM call, not regex (regex was removed in Sprint 6 — it matched historical mentions like "heart attack 5 years ago").
- **Do NOT make `synthesis_node` override urgency to EMERGENCY based on `safety.is_potential_emergency`.** Must gate on `state["is_emergency"]` (the short-circuit flag) only. Cases routed through the full triage agent already have context-informed urgency — overriding them re-introduces the Sprint 6 false-positive bug.
- **Do NOT remove `nest_asyncio.apply()`** from `app/streamlit_app.py` / `graph/workflow.py`. Streamlit runs its own event loop; `asyncio.run(build_graph_async())` fails without it. Keep the `try/except ValueError` guard (handles the uvloop case).
- **Do NOT block startup on a missing dependency or key.** Preserve the lazy-import + fallback pattern — many flows run in demo mode with only the Gemini key set.
- `data/checkpoints.db` is tracked in git and shows up dirty after most runs (SqliteSaver writes to it) — don't commit incidental churn unless relevant. `data/vector_store/` is gitignored.

---

## 8. Commands reference

```bash
# Install
pip install -r requirements.txt

# Configure (only LLM_GEMINI_API_KEY is strictly required)
cp .env.example .env

# Seed ChromaDB policy store (idempotent; agents/policy_agent.py also inline-seeds
# 7 default policies if the store is empty, but running this makes it explicit)
python scripts/seed_policy.py

# Run the app
streamlit run app/streamlit_app.py

# Tests (no pytest fixtures — plain functions + hand-rolled runner)
python -m pytest tests/test_tools.py -v
python tests/test_tools.py          # exits non-zero on failure

# Evaluation harness
python scripts/run_eval.py                  # all messages in tests/eval_dataset.json
python scripts/run_eval.py --safety-only    # safety screen only, fast
python scripts/run_eval.py --ids E01 FP02   # specific message IDs
python scripts/run_context_urgency_eval.py  # context-vs-urgency eval

# Load test
python scripts/generate_test_messages.py    # one-time: build the dataset
python scripts/load_test.py --limit 20

# Verify MCP tools resolve/discover correctly
python scripts/verify_mcp_tools.py
```

There is **no linter, formatter, or build step** configured (no `pyproject.toml`, `ruff`, `black`, `Makefile`). Don't invent lint commands.

---

## 9. Evaluation results snapshot (from README, for context)

Evaluated across 7 structured test runs, 189 labeled messages (adversarial traps, atypical clinical presentations, multi-intent edges), traced end-to-end in LangSmith:

| Metric | Result |
|---|---|
| Safety recall | 100% (zero false negatives across 159 safety-screened messages) |
| Precision (adversarial set) | 93.1% |
| False positive reduction vs. keyword baseline | 84% |
| Adversarial traps caught | 27/27 |
| Urgency ±1 accuracy | 100% |
| Urgency exact match (adversarial run) | 90% |
| Context-urgency catch rate | 100% (30/30) |

The system's consistent failure mode is over-escalation by one level (the safe direction) — it never under-escalates. LangSmith load test: 640 LangGraph runs, ~1.63M tokens (~$3.16), median 4,730 tokens/message, P50 latency 10.18s, P99 54.32s.

---

## 10. Key reference files (read these directly for more depth)

- `README.md` — project framing, architecture diagram, evaluation results, tech stack table, future work.
- `DEVELOPMENT.md` — full chronological Sprint 1–7 decision log; explains *why*, not just *what*.
- `EVALUATION.md` — evaluation methodology and metrics detail.
- `supabase_schema.sql` — `profiles` (incl. `medical_history`) and `messages` tables, RLS policies, auto-profile-creation trigger.
- `mcp_config.json` — the two registered MCP servers (`policy-server` = chroma-mcp-server, `triageai-tools` = local FastMCP server).

# Development Journal & Decision Log

It tracks *why* you made certain decisions—this is what professors love to see in the final report.

## Project Setup (Feb 1, 2026)

- **Tool Stack:** Finalised the tools that I will be using for this project.
- **Repository Initialized:** Set up core folder structure.
- **Orchestration Choice:** Decided on **LangGraph** over CrewAI/LangChain to allow for cyclic error handling and better state management during the "Staff Review" phase.
- **Observability:** Integrated **LangSmith** to ensure every LLM call is traceable for the "Audit & Metrics" layer.
- **Environment:** Configured `.env` for Gemini (temporary).

## Phase 1 Goals: The Triage Node

- [X] Define Pydantic schema for structured output.
- [X] Craft the "Triage System Prompt" with clinical urgency rules.
- [X] Build a local test script to verify categorization accuracy.
- [X] Get started with setting up LangGraph (Basic workflow).
- [X] Integrate LangSmith, to improve observability which will help me with debugging.

## Phase 2 Goals: Login, Patient Identity & UI

- [X] **Login/register flow** — So every message is tied to a known patient (patient ID, full name, email). Enables staff to identify whose message is whose and supports future personalization.
- [X] **Auth layer** — `auth.py` supports Supabase (Auth + `profiles` table) when `SUPABASE_URL` and `SUPABASE_ANON_KEY` are set; otherwise **demo mode** (in-memory users) so the app runs without any backend. Decision: avoid blocking development on Supabase setup.
- [X] **Session state** — `state.py` holds current patient context (user_id, patient_id, full_name, email) after login; cleared on logout.
- [X] **Messages store** — `messages_store.py` saves each message with patient context and optional triage result; uses Supabase `messages` table when configured, else in-memory list for demo.
- [X] **Patient vs Staff tabs** — Two tabs after login: **Patient view** (send message, own history) and **Staff view** (all messages grouped by patient). Decision: makes it easy to simulate both patient and staff in one session for demos.
- [X] **Supabase schema** — `supabase_schema.sql` defines `profiles` (id, full_name, patient_id) and `messages` (user_id, patient_id, full_name, email, content, triage_result) with RLS. Staff can see all messages only with a separate policy or service role (documented in the SQL file).
- [X] **Repo hygiene** — `.gitignore` updated: `.cursor/` and `**/.cursor/` so Cursor IDE worktrees/cache are never committed; `__pycache__/`, `*.pyc`, `*.pyo` so Python cache is not committed. Ensures only the repo root (e.g. `Desktop/capstone/TriageAI`) is the source of truth.

## Phase 3 Goals: Agentic Refactor — Sprint 1 (MCP Tool Layer)

- [X] **Folder restructure** — Reorganized the entire project from a flat collection of root-level scripts into a modular, package-based architecture. The old layout had `safety_agent.py`, `triage_test.py`, `policy_agent.py`, `workflow.py`, `state.py`, `auth.py`, `messages_store.py`, and `streamlit_app.py` all at the project root. This made imports fragile and coupling tight. Moved everything into purpose-specific packages: `agents/`, `graph/`, `app/`, `mcp/`, `schemas/`. Decision: this structure is required before introducing MCP and multi-agent orchestration — agents need to call tools through a clean interface, not reach into sibling scripts.
- [X] **Schema migration** — `SafetyResult` and `TriageResult` Pydantic models now live in `schemas/schemas.py` with clean re-exports from `schemas/__init__.py`. The old root-level `schemas.py` shim was removed. Decision: a single source of truth for data models prevents circular imports as more agents are added.
- [X] **Graph layer split** — The monolithic `workflow.py` was split into three files: `graph/state.py` (TypedDict state definitions + PatientContext dataclass), `graph/nodes.py` (safety_node, triage_node functions), and `graph/workflow.py` (graph construction + `run_triage_workflow` entry point). Decision: separating state, nodes, and routing prepares for Sprint 2 where we add Context_Loader_Node and Tool_Node — adding a node should mean editing `nodes.py` and `workflow.py`, not untangling a single file.
- [X] **MCP tool layer** — Implemented three standalone tool functions for the MCP protocol:
  - `get_patient_history(patient_id)` in `mcp/tools/database_tools.py` — queries Supabase `profiles` table, returns `medical_history` string.
  - `search_hospital_policy(query, top_k)` in `mcp/tools/rag_tools.py` — wraps ChromaDB RAG search from `agents/policy_agent.py`, returns top-k policy chunks.
  - `get_available_slots()` in `mcp/tools/database_tools.py` — hardcoded list for Milestone 1 (`["Mon 10am", "Wed 2pm", "Fri 9am"]`), placed in database_tools to allow future Supabase table migration.
  - Decision: each tool is a pure function with no Streamlit dependency so the LangGraph agent can call them directly via MCP in Sprint 2.
- [X] **Communication tool** — Created `mcp/tools/communication.py` with `send_resolution_email` (extracted from the Streamlit app) and `send_notification`. Currently mock implementations. Decision: isolating communication as an MCP tool means the agent can eventually trigger emails/notifications as tool calls rather than relying on UI button handlers.
- [X] **MCP server** — `mcp/server.py` exposes all tools with clean imports and `__all__`. This is the single interface the agent will use to discover and call tools.
- [X] **Triage agent rename** — `triage_test.py` renamed to `agents/triage_agent.py`. Decision: the old name implied it was a test script, not a production agent. The function name `test_triage` was kept for now to avoid breaking the workflow, but the module name reflects its actual role.
- [X] **Import updates** — All imports across the codebase updated to use package-qualified paths (e.g., `from agents.safety_agent import screen_for_emergency` instead of `from safety_agent import ...`). `app/streamlit_app.py` adds the project root to `sys.path` so all package imports resolve correctly when run via `streamlit run app/streamlit_app.py`.
- [X] **Verification tests** — Created `tests/test_tools.py` with 8 tests covering all 3 MCP tools, schema imports, MCP server exports, ChromaDB relevance, and top_k behavior. All pass. Decision: the PRD requires a test script that "successfully queries ChromaDB and Supabase through the new tool functions" — this satisfies that and provides a regression baseline for Sprint 2.
- [X] **run_app.sh updated** — Entry point changed from `streamlit run streamlit_app.py` to `streamlit run app/streamlit_app.py`.

## Phase 4 Goals: Agentic Orchestrator & Reasoning Loop — Sprint 2

- [X] **Cyclic agentic graph** — Replaced the linear Safety → Triage pipeline with a cyclic LangGraph workflow. The new graph: `safety_node → (emergency? END : triage_agent_node) → (tool_calls? tool_node → triage_agent_node : synthesis_node) → END`. The triage agent can now loop: call tools, read results, call more tools, and only exit when it has enough context to produce a final assessment. Decision: this is the core architectural shift from "chatbot" to "Clinical Decision Support System" — the AI dynamically pulls from Supabase (history) and ChromaDB (policy) to ground its decisions.
- [X] **State redesign (`graph/state.py`)** — Added `messages` field with LangGraph's `add_messages` reducer so tool outputs append to a running conversation log instead of overwriting the original message. Added `patient_id`, `medical_history`, `policy_context`, `draft_reply`, `is_emergency` (short-circuit flag), and `staff_approved` (for future HITL). Decision: the `messages` list is essential for agentic loops — the LLM needs to "see" its own tool calls and the results to reason about what to do next.
- [X] **LangChain tool wrappers** — Wrapped the 3 MCP tools (`get_patient_history`, `search_hospital_policy`, `get_available_slots`) as LangChain `@tool` functions in `graph/nodes.py`. These are bound to the Gemini model via `.bind_tools()` so the LLM can invoke them during reasoning. Decision: LangChain's tool interface is required for `langgraph.prebuilt.ToolNode` to automatically route tool calls to the correct Python functions.
- [X] **Triage agent node** — Implemented `triage_agent_node` using `ChatGoogleGenerativeAI` (Gemini 2.5 Flash) with all 3 tools bound. The system prompt instructs the agent to: (1) analyze the patient message, (2) call tools as needed for history, policy, or scheduling, (3) produce a final triage assessment. The node returns an AIMessage that either contains tool_calls (routed to tool_node) or a final response (routed to synthesis). Decision: binding tools to the LLM and letting it decide what to call (rather than hardcoding the sequence) enables multi-intent handling — e.g., a single message asking for both a refill and an appointment triggers both `search_hospital_policy` and `get_available_slots`.
- [X] **Tool node** — Used `langgraph.prebuilt.ToolNode` which automatically maps LLM `tool_calls` to the corresponding Python functions and returns ToolMessages. Connected back to `triage_agent_node` to form the reasoning loop. Decision: ToolNode is the standard LangGraph pattern — it handles argument parsing, error wrapping, and message formatting, so we don't need to implement any of that manually.
- [X] **Synthesis node** — Added a `synthesis_node` as the final step before END. It extracts the TriageResult from the agent's conversation using a two-phase strategy: (1) try to parse JSON from the agent's last message, (2) if that fails, make an explicit structured extraction call to Gemini with `response_schema=TriageResult`. Decision: separating synthesis from the reasoning loop is more robust — the tool-calling Gemini model produces natural language, and the synthesis step reliably converts it to the structured schema the staff dashboard expects.
- [X] **Safety short-circuit** — When the safety node detects an emergency (`is_emergency=True`), the graph routes directly to `synthesis_node`, skipping the triage agent entirely. The synthesis node then tags the result with `safety_flagged`, `safety_reason`, and overrides urgency to `EMERGENCY`. Decision: avoids unnecessary LLM latency for emergencies — the safety gate catches it in milliseconds (rule-based) and immediately exits.
- [X] **Routing logic** — Implemented `_route_after_safety` (emergency → synthesis, else → triage_agent) and `_should_continue` (tool_calls → tool_node, else → synthesis) as conditional edge functions. Decision: these two functions define the entire control flow of the agentic loop.
- [X] **Streamlit integration** — Updated `_run_workflow(msg, patient_id)` in `app/streamlit_app.py` to pass the patient's `patient_id` from session state to the workflow. The `run_triage_workflow` function signature now accepts `patient_id` but remains backward-compatible (defaults to empty string). Decision: the agent needs the patient_id to call `get_patient_history`.
- [X] **New dependency** — Added `langchain-google-genai>=2.0.0` to `requirements.txt`. This provides `ChatGoogleGenerativeAI` which supports `.bind_tools()` for Gemini function calling within LangGraph. Decision: needed for the standard LangGraph + Gemini agentic pattern (ToolNode expects LangChain message types).
- [X] **End-to-end tests** — Verified 3 scenarios:
  - Emergency ("sharp chest pain"): Safety gate catches it via rule match → short-circuits → EMERGENCY urgency. No LLM triage call made.
  - Multi-intent ("refill Lisinopril + schedule appointment"): Agent calls `search_hospital_policy` and `get_available_slots` → loops back with results → synthesizes structured TriageResult with intent "Refill", NORMAL urgency, and relevant checklist.
  - Admin ("billing question"): Agent calls `search_hospital_policy` → synthesizes with intent "Billing", queue "Billing".

## Current Roadblocks / Notes

- Need to ensure the "Safety Screening" node has 0% False Negatives for emergencies.
- Need to read more research on this topic. (I have compiled some information in a word doc; I will make it public and paste the link here.) [Research Notes](https://docs.google.com/document/d/1Gr3C9JskVDrQheYMiz4jvEEy_VRp9OTWS1hD-DlHIaM/edit?usp=sharing)

## What's Next (Sprint 3+)

- **Human-in-the-loop (HITL):** The `staff_approved` flag is already in the graph state. Sprint 3 will add a conditional edge that pauses the workflow until a staff member reviews and approves the triage result before any action is taken.
- **Real email/notification integration:** Replace the mock `send_resolution_email` with a real provider (Resend, SendGrid, or smtplib).
- **Persistent ChromaDB:** Move from `EphemeralClient` to a persistent vector store so policy documents survive restarts.
- **Evaluation harness:** Build a dataset of labeled patient messages and measure triage accuracy, safety recall, and tool-call precision using LangSmith evaluations.
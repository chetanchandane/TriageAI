# TriageAI - Agentic Patient Portal Triage System

An autonomous, multi-agent AI system designed to transform free-text patient messages into structured clinical actions. This project serves as a Master's Capstone at the Rochester Institute of Technology (RIT).

![TriageAI System Architecture](./assets/triage_ai.png)
## Overview
Traditional patient portals often lead to administrative bottlenecks. This system utilizes **Agentic AI** to:
- **Screen:** Flag potential emergencies with a two-layer safety gate (rule-based + LLM).
- **Triage:** Categorize intent and urgency (Normal to Emergency).
- **Route:** Assign messages to specific clinical queues.
- **Support:** Generate policy-grounded draft replies and missing-information checklists via RAG.

## Technical Stack

| Layer | Technology |
|-------|-----------|
| **Orchestration** | LangGraph (cyclic agentic state machine with conditional routing + HITL interrupts) |
| **Persistence** | LangGraph `MemorySaver` checkpointer (thread-based state recovery for HITL) |
| **Reasoning** | Gemini 2.5 Flash via `langchain-google-genai` (tool-calling + structured output) |
| **Safety** | Calibrated regex patterns (crisis-modifier gated) + LLM fallback with negative constraints |
| **RAG** | ChromaDB (ephemeral, seeded with clinic policy snippets) |
| **Database** | Supabase (PostgreSQL — Auth, profiles, messages with RLS); optional demo mode (in-memory) |
| **Observability** | LangSmith (tracing every LLM call, tool call, and graph transition) |
| **Interface** | Streamlit (Patient portal + Staff dashboard + HITL Pending Approvals) |
| **Tool Protocol** | MCP tools bound to the LLM via LangChain `@tool` + `langgraph.prebuilt.ToolNode` |

## Login & Patient Identity

The app includes a **login/register** flow so that:

- Each user has a **patient ID** and **full name** (set at registration).
- Messages are stored with patient context so **staff can identify each patient's messages** (patient ID, name, email).
- **Personalized staff messages** and patient history can be built on this identity.

### UI layout (demo-friendly)

After login, the app shows three tabs:

- **Patient view** — Send messages and view message history. Each submission runs through the full agentic workflow (Safety gate → Triage agent with tool calling → Synthesis → Draft reply) and shows a triage summary.
- **Staff view** — Two-pane dashboard: active queue (sorted by urgency) on the left, detail view on the right. Shows AI analysis, safety flags, patient history, policy-grounded draft replies, and suggested next steps. HITL status badges (⏸️ Pending, ✅ Sent, ⚡ Auto) indicate workflow state.
- **Pending Approvals** — HITL review tab. Lists messages where the workflow paused before sending communication (NORMAL/HIGH/EMERGENCY urgency). Staff can inspect the AI analysis, edit the draft reply, and click "Approve & Send" to resume the workflow and deliver the email.

## Project Structure

```
triage-ai/
├── app/                          # Streamlit Frontend
│   ├── streamlit_app.py          # Main entry point (tabs, auth, workflow trigger)
│   ├── auth.py                   # Register/login (Supabase or in-memory demo)
│   └── messages_store.py         # Save/load messages (Supabase or in-memory)
├── agents/                       # Reasoning & Logic Layer
│   ├── safety_agent.py           # Emergency screening (rules + LLM)
│   ├── triage_agent.py           # Intent/urgency classification (Gemini)
│   └── policy_agent.py           # RAG retrieval + draft reply generation (ChromaDB)
├── graph/                        # Orchestration Layer (LangGraph)
│   ├── workflow.py               # Cyclic graph + MemorySaver persistence + HITL interrupt/resume
│   ├── nodes.py                  # Node functions (safety, triage_agent, synthesis, draft_reply, communication) + tool wrappers
│   └── state.py                  # TriageWorkflowState (with add_messages, hitl_status) + PatientContext
├── mcp/                          # Tool & Context Layer (MCP)
│   ├── server.py                 # MCP server (exposes all tools)
│   └── tools/
│       ├── database_tools.py     # get_patient_history, get_available_slots (Supabase)
│       ├── rag_tools.py          # search_hospital_policy (ChromaDB wrapper)
│       └── communication.py      # send_resolution_email (mock, future: real provider)
├── schemas/                      # Data Models
│   └── schemas.py                # Pydantic: SafetyResult, TriageResult
├── data/
│   └── vector_store/             # Persistent ChromaDB files (placeholder)
├── tests/
│   └── test_tools.py             # Sprint 1 verification (8 tests, all passing)
├── scripts/
│   └── verify_mcp_tools.py       # Quick MCP tool smoke test
├── supabase_schema.sql           # SQL for profiles + messages tables with RLS
├── run_app.sh                    # Shell launcher (auto-detects venv)
├── requirements.txt              # Python dependencies
├── .env.example                  # Template for API keys
├── DEVELOPMENT.md                # Decision log and phase notes
└── ERRORS.md                     # Errors and resolutions
```

## Run the App

```bash
# Install dependencies
pip install -r requirements.txt
cp .env.example .env   # then set LLM_GEMINI_API_KEY (required)

# Option A: activate venv and run
source myenv/bin/activate   # macOS/Linux
streamlit run app/streamlit_app.py

# Option B: use the run script (picks myenv if present)
chmod +x run_app.sh && ./run_app.sh
```

- **Without Supabase (demo):** Set only `LLM_GEMINI_API_KEY` in `.env`. Auth and messages are in-memory; data is lost on restart.
- **With Supabase:** Set `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and optionally `SUPABASE_SERVICE_ROLE_KEY` in `.env`, then run `supabase_schema.sql` in the Supabase SQL editor.

### Run tests

```bash
python tests/test_tools.py
# or with pytest:
python -m pytest tests/test_tools.py -v
```

## Architecture

The system uses a **cyclic agentic loop** with **Human-in-the-Loop (HITL)** persistence. The triage agent can call tools, read the results, call more tools, and only produce a final assessment when it has enough context. For NORMAL/HIGH/EMERGENCY urgency, the workflow pauses for staff review before sending communication.

```
Patient Message + Patient ID + Email
           │
           ▼
   ┌───────────────┐
   │  Safety Node  │  Calibrated regex (crisis modifiers) → LLM fallback
   └───────┬───────┘
           │
     ┌─────┴──────┐
     │ Emergency? │
     └─────┬──────┘
       yes │         no
           │          │
           │          ▼
           │  ┌────────────────────┐
           │  │ Triage Agent Node  │  Gemini 2.5 Flash + bound MCP tools
           │  └────────┬───────────┘
           │           │
           │     ┌─────┴──────┐
           │     │ Tool calls?│
           │     └─────┬──────┘
           │       yes │        no
           │           │         │
           │           ▼         │
           │    ┌────────────┐   │
           │    │ Tool Node  │   │  ToolNode: get_patient_history,
           │    │ (prebuilt) │   │  search_hospital_policy,
           │    └──────┬─────┘   │  get_available_slots
           │           │         │
           │           └──► loop back to Triage Agent
           │                     │
           ▼                     ▼
   ┌─────────────────┐
   │ Synthesis Node  │  Extract structured TriageResult (JSON parse → Gemini fallback)
   └────────┬────────┘
            │
            ▼
   ┌──────────────────┐
   │ Draft Reply Node │  Policy RAG → generate draft for staff/patient
   └────────┬─────────┘
            │
      ┌─────┴──────┐
      │  Urgency?  │
      └─────┬──────┘
        LOW │            NORMAL / HIGH / EMERGENCY
            │                     │
            ▼                     ▼
   ┌─────────────────┐  ┌────────────────────────┐
   │ Auto-Communicate│  │  ⏸ CHECKPOINT PAUSE    │  ← MemorySaver interrupt
   │  (send email)   │  │  (communication_node)  │
   └────────┬────────┘  └────────────┬───────────┘
            │                        │
            │             Staff reviews & edits draft
            │             Clicks "Approve & Send"
            │                        │
            │                        ▼
            │               ┌────────────────┐
            │               │ Send Email     │  resume_workflow()
            │               └────────┬───────┘
            ▼                        ▼
          END                      END
```

### Current status

| Component | Status |
|-----------|--------|
| Safety Agent (calibrated regex + LLM with negative constraints) | Implemented (refined Sprint 3) |
| Triage Agent (Gemini with tool calling) | Implemented (Sprint 2) |
| Policy Agent (ChromaDB RAG + draft replies) | Implemented |
| Cyclic LangGraph workflow (Safety → Agent loop → Synthesis → HITL) | Implemented (Sprint 2 + 3) |
| MCP tool layer (3 tools + LangChain wrappers) | Implemented (Sprint 1 + 2) |
| `ToolNode` (prebuilt, auto-routes tool calls) | Implemented (Sprint 2) |
| Synthesis node (JSON parse + Gemini structured fallback) | Implemented (Sprint 2) |
| Draft reply node (policy RAG → staff-reviewable draft) | Implemented (Sprint 3) |
| Communication node (email delivery, HITL-gated) | Implemented (Sprint 3) |
| Emergency short-circuit (safety → synthesis, no agent call) | Implemented (Sprint 2) |
| MemorySaver persistence (thread-based checkpointing) | Implemented (Sprint 3) |
| Human-in-the-loop (interrupt → staff review → resume) | Implemented (Sprint 3) |
| Conditional interrupts (LOW auto-complete, others paused) | Implemented (Sprint 3) |
| Login/Auth (Supabase + demo mode) | Implemented |
| Staff dashboard (queue + detail view + HITL badges) | Implemented (Sprint 3) |
| Pending Approvals tab (edit draft, approve & send, dismiss) | Implemented (Sprint 3) |
| Real email notifications | Planned |
| Persistent ChromaDB vector store | Planned |
| Persistent checkpointer (SqliteSaver / Redis) | Planned |
| Evaluation harness (LangSmith) | Planned |

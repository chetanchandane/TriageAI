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
| **Orchestration** | LangGraph (stateful multi-agent workflow) |
| **Reasoning** | Gemini 2.5 Flash (Google GenAI SDK) |
| **Safety** | Rule-based regex patterns (30+) + conservative LLM fallback |
| **RAG** | ChromaDB (ephemeral, seeded with clinic policy snippets) |
| **Database** | Supabase (PostgreSQL — Auth, profiles, messages with RLS); optional demo mode (in-memory) |
| **Observability** | LangSmith (tracing every LLM call) |
| **Interface** | Streamlit (Patient portal + Staff dashboard) |
| **Tool Protocol** | MCP (Model Context Protocol) — standardized tool layer for agent use |

## Login & Patient Identity

The app includes a **login/register** flow so that:

- Each user has a **patient ID** and **full name** (set at registration).
- Messages are stored with patient context so **staff can identify each patient's messages** (patient ID, name, email).
- **Personalized staff messages** and patient history can be built on this identity.

### UI layout (demo-friendly)

After login, the app shows two tabs:

- **Patient view** — Send messages and view message history. Each submission runs through the Safety → Triage workflow and shows a triage summary.
- **Staff view** — Two-pane dashboard: active queue (sorted by urgency) on the left, detail view on the right. Shows AI analysis, safety flags, patient history, policy-grounded draft replies, and suggested next steps.

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
│   ├── workflow.py               # Graph definition and edge routing
│   ├── nodes.py                  # Graph node functions (safety, triage)
│   └── state.py                  # TypedDict state + PatientContext dataclass
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

```
Patient Message
      │
      ▼
┌─────────────┐
│ Safety Node │  Rule-based patterns (30+) → LLM fallback
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Triage Node │  Gemini 2.5 Flash → TriageResult (intent, urgency, queue)
└──────┬──────┘
       │
       ▼
┌─────────────────┐
│ Staff Dashboard │  Policy RAG · Draft reply · Next steps · Action buttons
└─────────────────┘
```

### Current status

| Component | Status |
|-----------|--------|
| Safety Agent (rules + LLM) | Implemented |
| Triage Agent (Gemini structured output) | Implemented |
| Policy Agent (ChromaDB RAG + draft replies) | Implemented |
| LangGraph workflow (Safety → Triage) | Implemented |
| MCP tool layer (3 tools + server) | Implemented (Sprint 1) |
| Login/Auth (Supabase + demo mode) | Implemented |
| Staff dashboard (queue + detail view) | Implemented |
| Context Loader Node (patient history injection) | Planned (Sprint 2) |
| Parallel tool calls (RAG + Scheduling) | Planned (Sprint 2) |
| Human-in-the-loop (staff edit before send) | Planned |
| Real email notifications | Planned |

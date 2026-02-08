# TriageAI - Agentic Patient Portal Triage System 

An autonomous, multi-agent AI system designed to transform free-text patient messages into structured clinical actions. This project serves as a Master’s Capstone at the Rochester Institute of Technology (RIT).

![TriageAI System Architecture](./assets/triage_ai.png)
## 🚀 Overview
Traditional patient portals often lead to administrative bottlenecks. This system utilizes **Agentic AI** to:
- **Triage:** Categorize intent and urgency (Normal to Emergency).
- **Route:** Assign messages to specific clinical queues.
- **Support:** Generate policy-grounded draft replies and missing-information checklists.

## 🛠️ Technical Stack

- **Orchestration:** LangGraph (Multi-agent state machine)
- **Reasoning:** Gemini 2.5 Flash (Google GenAI SDK); Llama 3.1 via Groq API (planned)
- **Observability:** LangSmith (Tracing and Evaluation)
- **Database:** Supabase (PostgreSQL for Auth, profiles, messages); optional—demo mode uses in-memory store
- **Interface:** Streamlit (Patient and Staff Portals)

## 🔐 Login & Patient Identity

The app includes a **login/register** flow so that:

- Each user has a **patient ID** and **full name** (set at registration).
- Messages are stored with patient context so **staff can identify each patient's messages** (patient ID, name, email).
- **Personalized staff messages** and patient history can be built on this identity.

### UI layout (demo-friendly)

After login, the app shows two tabs:

- **Patient view** — What the patient sees: send messages and view their own message history. Each submission is triaged (intent, urgency, recommended queue) and the summary is shown.
- **Staff view** — What staff sees: all messages from all patients, grouped by patient (name, patient ID, email) with triage results (urgency, queue, summary). Makes it easy to simulate both roles in one session.

## 📁 Project structure

| File / folder        | Purpose |
|----------------------|--------|
| `streamlit_app.py`   | Main app: login/register, Patient and Staff tabs, message submit, triage integration |
| `auth.py`            | Register/login; Supabase when configured, in-memory demo otherwise |
| `state.py`           | Session state: current patient (user_id, patient_id, full_name, email) |
| `messages_store.py`  | Save/load messages with patient context; Supabase or in-memory |
| `schemas.py`         | Pydantic `TriageResult` (intent, urgency, summary, checklist, recommended_queue) |
| `triage_test.py`     | Triage logic (Gemini + structured output); used by the app on submit |
| `supabase_schema.sql`| SQL for `profiles` and `messages` tables and RLS (run in Supabase SQL editor) |
| `.env.example`       | Template for `LLM_GEMINI_API_KEY` and optional Supabase keys |
| `requirements.txt`   | Python dependencies (streamlit, python-dotenv, pydantic, google-genai, supabase) |

See also: `DEVELOPMENT.md` (decision log, phases), `ERRORS.md` (errors and resolutions).

## 🏃 Run the app

```bash
pip install -r requirements.txt
cp .env.example .env   # then set LLM_GEMINI_API_KEY (required)
streamlit run streamlit_app.py
```

- **Without Supabase (demo):** Use `.env` with `LLM_GEMINI_API_KEY` only. Auth and messages are in-memory; data is lost on restart.
- **With Supabase:** Set `SUPABASE_URL` and `SUPABASE_ANON_KEY` in `.env`, then run `supabase_schema.sql` in the Supabase SQL editor to create `profiles` and `messages` tables.

## 🏗️ Architecture (target)

1. **Message Intake:** Patient submits free-text (authenticated; tied to patient ID/name).
2. **Safety Agent:** Immediate screening for life-threatening emergencies (planned).
3. **Triage Agent:** Intent classification and urgency scoring (implemented in `triage_test.py`).
4. **Policy Agent (RAG):** Contextual retrieval of clinic rules via ChromaDB (planned).
5. **Human-in-the-loop:** Staff review/edit before final action (planned).

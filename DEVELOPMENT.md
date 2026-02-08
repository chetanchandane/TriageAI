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

## Current Roadblocks / Notes

- Need to ensure the "Safety Screening" node has 0% False Negatives for emergencies.
- Need to read more research on this topic. (I have compiled some information in a word doc; I will make it public and paste the link here.) [Research Notes](https://docs.google.com/document/d/1Gr3C9JskVDrQheYMiz4jvEEy_VRp9OTWS1hD-DlHIaM/edit?usp=sharing)
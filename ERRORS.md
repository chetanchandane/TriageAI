# Errors Faced vs Their Resolution

## Triage Logic Implementation (Feb 1, 2026)

- **Challenge:** Pydantic ValidationError due to Markdown backticks in JSON response.
- **Fix:** Implemented `response_schema` in Gemini `generate_content` config to utilize native structured output, removing the need for manual regex cleaning or string parsing.

- **Model:** Using **Gemini 2.5 Flash** (Google GenAI SDK) for triage.
- **Structured Output:** Implemented native `response_schema` via the Google GenAI SDK.
- **Optimization:** Moved triage rules from the user prompt to `system_instruction` to improve category adherence and reduce token usage.
- **Bug Fix:** Resolved Pydantic validation errors by utilizing `response.parsed`, which eliminates the need for manual JSON regex cleaning.

## Observability (LangSmith)

- **Tracing:** `test_triage` in `triage_test.py` is decorated with `@traceable` from LangSmith so each triage call is traced for debugging and evaluation.

## Login, Auth & UI (Phase 2)

- **Design choice — Demo mode:** To avoid requiring Supabase for every run, the app supports an in-memory auth and message store when `SUPABASE_URL` / `SUPABASE_ANON_KEY` are not set. No error; the UI shows a note that the app is in demo mode and data is not persisted.
- **Design choice — Staff view and RLS:** With Supabase RLS, the anon key only returns rows the logged-in user is allowed to see (e.g. their own messages). So the “Staff view” tab only shows that user’s messages unless a separate staff policy or service-role backend is used. Documented in `supabase_schema.sql` and README; no code bug.
- **Session restore:** On load, if using Supabase, the app calls `get_current_user()` to restore the session from Supabase Auth so refresh doesn’t log the user out.

- **Registration RLS (42501):** After `sign_up()`, the app inserted into `public.profiles`, but that request ran without the new user's JWT, so RLS blocked it. **Fix:** A database trigger `on_auth_user_created` now creates the profile row when a user is inserted into `auth.users`; the trigger runs as `SECURITY DEFINER` so the insert is allowed. The app no longer upserts the profile after signup. Re-run the trigger/function block in `supabase_schema.sql` if you created the project before this change.

- **Messages not saving to Supabase / Staff view empty:** Patient submissions did not appear in the `messages` table or in Staff view. **Cause:** `messages_store.py` created its own Supabase client; that client was never used for login, so it had no session (JWT). Inserts ran with the anon key only, `auth.uid()` was null, and RLS policy "Users can insert own messages" blocked the insert. The code then fell back to the in-memory demo list, so nothing persisted. **Fix:** Auth exposes `get_supabase_client()`; `messages_store` now uses that same client for `save_message` and `get_all_messages_for_staff()`. The client that holds the logged-in user's session (set at login) is used for message operations, so RLS allows the insert and messages persist in Supabase.

## Sprint 5 Patch: Multi-Turn Conversational Intake Loop (Mar 2026)

- **Design flaw — single-interrupt checklist gate:** The original `checklist_gate_node` (Sprint 5) asked at most one follow-up question. On resume it unconditionally set `is_complete=True` and routed to synthesis, so the triage agent never saw the patient's answer before producing its final result. This meant staff received under-informed cases whenever the patient's initial message was incomplete.
- **Fix:** Removed `is_complete=True` from the interrupt return path in `checklist_gate_node`. Added `_route_after_checklist` conditional edge in `workflow.py`: routes back to `triage_agent_node` when `is_complete` is not set (patient just answered), routes to `synthesis` only when the triage agent itself produces no remaining checklist items (i.e., `is_complete=True` set by the no-checklist branch). The triage agent now drives termination — it sees the full `messages` history on every loop iteration and decides whether to ask again or finalize.
- **No other nodes affected.** Streaming, HITL, synthesis, and draft reply are unchanged.

## Sprint 5: Multimodal Streaming Assistant (Mar 2026)

- No blocking errors encountered during Sprint 5 implementation. All 19 tests (13 existing + 6 new) pass.
- **Design note — `stream_mode="messages"`:** LangGraph 1.0.9's sync `app.stream(stream_mode="messages")` yields `(AIMessage, metadata)` tuples. When `interrupt()` is called inside a node, the stream simply stops yielding (0 events). The interrupt value is found via `app.get_state(config)` → `snapshot.tasks[i].interrupts[0].value`. This required the safe-stream bridge pattern in `app/streaming.py` to detect and surface interrupts after the stream exhausts.
- **Design note — base64 data URIs:** Streamlit `UploadedFile` bytes are encoded as `data:mime;base64,...` and embedded directly in LangGraph state. This avoids temp file management and is JSON-serializable for the `MemorySaver` checkpointer.

## Runtime / Streamlit

- **`ValueError: Can't patch loop of type <class 'uvloop.Loop'>`:** When running under Streamlit, the event loop is **uvloop** (not the standard asyncio loop). `nest_asyncio.apply()` only patches `asyncio.BaseEventLoop`, so it raises. **Fix:** In `app/streamlit_app.py` and `graph/workflow.py`, wrap `nest_asyncio.apply()` in try/except; on `ValueError` mentioning "uvloop" or "patch", skip (no-op). The app and sync graph path still work; nested async is only needed when using MCP discovery inside an existing loop.

## Repo & Tooling

- **Cursor worktrees:** Application files can appear under `.cursor/worktrees/` (IDE cache). To avoid committing those or absolute paths into the repo, `.cursor/` and `**/.cursor/` were added to `.gitignore`. The single source of truth for app code is the repository root (e.g. `Desktop/capstone/TriageAI`).
- **Python cache:** `__pycache__/` and `*.pyc` added to `.gitignore` so compiled Python files are not committed.
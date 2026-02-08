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

## Repo & Tooling

- **Cursor worktrees:** Application files can appear under `.cursor/worktrees/` (IDE cache). To avoid committing those or absolute paths into the repo, `.cursor/` and `**/.cursor/` were added to `.gitignore`. The single source of truth for app code is the repository root (e.g. `Desktop/capstone/TriageAI`).
- **Python cache:** `__pycache__/` and `*.pyc` added to `.gitignore` so compiled Python files are not committed.
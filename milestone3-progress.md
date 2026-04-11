# TriageAI -- Milestone 3 Progress Update

**RIT Master's Capstone | Chetan Chandane | April 2026**

---

## Milestone 3 Goals

Sprint 6 focused on **stability, accuracy, and production readiness** -- fixing real issues identified at Milestone 2 rather than adding new features.

| # | Goal | Status |
|---|------|--------|
| 1 | LLM-based emergency screening (replace regex, reduce false positives) | Done |
| 2 | Multi-turn conversational intake (ask as many follow-ups as needed) | Done |
| 3 | Persistent checkpointing with SqliteSaver (survive restarts) | Done |
| 4 | Real transactional email via Resend API | Done |
| 5 | Medical history column in Supabase for patient context | Done |
| 6 | Staff "Edit & Send" -- working draft editing in Staff view + reply visible to patients | Done |
| 7 | Enhanced streaming UX with live status updates | Done |
| 8 | MCP folder rename to fix tool discovery (`mcp/` -> `mcp_tools/`) | Done |
| 9 | Evaluation harness (labeled dataset + automated scoring) | Done |
| 10 | Implement additional MCP server (e.g., Supabase MCP) | Not completed |

---

## What Was Accomplished

### LLM-Based Emergency Screening

Previously, the safety layer used regex patterns (e.g., `\bheart\s+attack\b`) to detect emergency keywords, then asked an LLM to confirm. This two-stage approach still produced false positives because regex is context-blind -- it matched "heart attack" in "I had a massive heart attack five years ago and just need a refill." Additionally, a bug in the synthesis node blindly overrode the triage agent's urgency to EMERGENCY for any flagged case, even when the LLM had already identified it as non-acute.

The fix replaced regex entirely with a single context-aware LLM call that understands:

- **Active vs. historical:** "I'm having chest pain right now" vs. "I had a heart attack five years ago"
- **Refill requests mentioning past conditions:** correctly classified as non-emergency
- **Chronic/stable symptoms:** not flagged even if they mention serious conditions
- The synthesis node was also fixed to only override urgency for confirmed emergencies that short-circuited the graph, respecting the triage agent's assessment for all other cases.

### Multi-Turn Conversational Intake

The checklist gate previously asked one follow-up question and finalized immediately. Now:

- After each patient response, control loops back to the triage agent.
- The agent decides whether more information is needed or triage can finalize.
- Staff only receive a case once the AI is satisfied it has enough context.

### Infrastructure Upgrades

- **SqliteSaver:** Replaced in-memory checkpointing with SQLite. HITL review threads now survive app restarts.
- **Real email via Resend:** When `RESEND_API_KEY` is configured, patient emails are actually delivered. Demo mode falls back to console output.
- **Supabase medical history:** Added `medical_history` column to profiles. The triage agent now has real patient context (medications, conditions) during tool calls.
- **Staff Edit & Send:** Staff can edit AI-generated draft replies before approving. Works for both HITL-paused threads and direct follow-ups. Staff replies are now persisted in the database and visible to patients in their message history (previously replies were only sent via email and lost from the portal).
- **MCP folder rename:** Renamed `mcp/` to `mcp_tools/` because the local folder was shadowing the `mcp` pip package, breaking MCP tool discovery entirely.

### Streaming UX

The UI now shows real-time status updates at each workflow stage:

- "Screening for emergencies..."
- "Analyzing your message..."
- "Fetching patient history..."
- "Searching clinic policies..."
- "Preparing triage assessment..."

This makes the 5-15 second triage cycle feel intentional rather than broken.

### Evaluation Harness

Built a labeled dataset of 26 patient messages and an automated scoring script:

- 5 true emergencies, 5 false-positive traps, 16 mixed-intent messages
- Metrics: safety recall, safety precision, false positive rate, intent accuracy, urgency accuracy
- Results saved to JSON for inclusion in the final report
- All runs are traceable in LangSmith

---

## Current Challenges

### 1. Latency

The end-to-end triage cycle takes 5-15 seconds per message. This is acceptable for a portal-style interaction but noticeable during demos. I am actively working on:

- **Prompt optimization** -- reducing token counts and making prompts more targeted to cut LLM response times.
- **Caching** -- exploring caching strategies for repeated tool calls (e.g., policy lookups that return the same results for similar queries).

### 2. Prompt Tuning

The triage and safety prompts still need refinement. Edge cases in urgency classification (e.g., distinguishing "chronic but worsening" from "chronic and stable") require more precise prompt language. The switch from regex to LLM-based safety screening resolved the most egregious false positives (historical mentions flagged as emergencies), but LLM screening depends on prompt quality -- ongoing iteration with the evaluation harness is needed.

### 3. Additional MCP Server Not Implemented

One of the planned goals was to integrate an additional MCP server (e.g., Supabase MCP for patient history queries). I was unable to get this working within the sprint. The current setup uses direct Supabase SDK calls for patient history, which is functional but doesn't demonstrate the full MCP architecture for that tool. The Chroma MCP server for RAG policy search is working.

---

## What's Next

### Final Report and Poster (Primary Focus)

These are the top priorities for the remaining time:

- **Report:** I have not started the written report yet. I plan to send drafts to my advisor for feedback every 3 days (or sooner depending on feedback turnaround). The report will cover architecture decisions, accuracy results from the evaluation harness, and lessons learned.
- **Poster:** Will be developed in parallel with the report. I will send drafts for feedback on the same cadence.

### Application Finalization

- Verify the full workflow end-to-end (patient submission -> AI triage -> staff review -> email delivery).
- Test edge cases: long conversation threads, HITL flow across restarts, network failure handling.
- Run the complete 26-message evaluation suite and include before/after metrics in the report.

---

## Summary

Milestone 3 was about making TriageAI **reliable and accurate**, not just functional. The major wins were LLM-based emergency screening (replacing brittle regex patterns to eliminate false positives like historical mentions), the synthesis node fix (respecting the triage agent's urgency instead of blindly overriding), staff reply persistence (patients can now see staff responses in their message history), multi-turn intake (better-informed triage), and production infrastructure (persistent state, real email, medical history context). The main open items are latency improvements, prompt refinement, and the unimplemented additional MCP server. From here, the focus shifts to the final report and poster.

All 19 automated tests pass. The system runs locally via Streamlit.

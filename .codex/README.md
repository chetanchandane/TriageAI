# Codex Compatibility Layer

This directory contains Codex-specific onboarding and workflow guidance for TriageAI. It exists so Codex can work in the repository alongside Claude Code without replacing or weakening the Claude setup.

## Purpose

- Mirror the Claude workflow wherever practical.
- Reuse existing project knowledge instead of duplicating it.
- Keep Claude and Codex instructions compatible.
- Document Codex-specific adaptations explicitly.
- Avoid changing runtime behavior.

## What this layer owns

- Codex onboarding notes.
- Codex workflow guidance.
- Claude/Codex collaboration protocol.
- Codex session handoff guidance.

## What this layer does not own

- Application code.
- Clinical triage logic.
- LangGraph workflow behavior.
- MCP server configuration.
- Streamlit runtime behavior.
- Evaluation datasets or metrics.
- Claude-specific files under `.claude/`.

## Authoritative files

Use this order when loading context:

1. `AGENTS.md` for Codex-facing project instructions.
2. `CLAUDE.md` for the shared behavior that Claude already follows.
3. `DEVELOPMENT.md` for why the codebase has its current shape.
4. `README.md` for project overview and setup.
5. `EVALUATION.md` for evaluation methodology and results.
6. `ERRORS.md` for known past failures and fixes.
7. `.codex/` for Codex-specific process.
8. `.claude/` for Claude-specific process.

## Maintenance rule

Keep this directory lean. If information belongs to both Claude and Codex, prefer updating `CLAUDE.md` and `AGENTS.md` together rather than adding another copy here. Use `.codex/` only for Codex mechanics or collaboration guidance that does not belong in the shared project instructions.

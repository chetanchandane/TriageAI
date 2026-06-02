# Claude and Codex Collaboration

TriageAI is configured for Claude Code and Codex to work in the same repository. This document defines how both assistants should share context and avoid instruction drift.

## Shared ownership

Claude and Codex both consume the same project truth:

- `CLAUDE.md` and `AGENTS.md` define shared working rules.
- `DEVELOPMENT.md` records durable design decisions.
- `README.md` defines project framing and setup.
- `EVALUATION.md` records evaluation methodology and results.
- `ERRORS.md` records known issues and resolutions.

Claude-specific mechanics live under `.claude/`. Codex-specific mechanics live under `.codex/`. Neither assistant should delete, replace, or reinterpret the other assistant's directory.

## Conflict resolution

If Claude and Codex instructions differ:

1. Preserve Claude behavior.
2. Minimize divergence.
3. Document the Codex-specific reason in `.codex/`.
4. Do not silently override `CLAUDE.md`.
5. If the conflict affects application behavior, ask the user before editing.

Examples:

- Claude local permissions in `.claude/settings.local.json` do not automatically apply to Codex. Codex should follow its own sandbox and approval rules while respecting the same project commands.
- Claude skills in `.claude/skills/` do not automatically become Codex skills. Codex should provide equivalent workflow docs or use its own skill mechanism when available.
- If `AGENTS.md` and `CLAUDE.md` drift, treat that drift as a documentation maintenance issue and ask before changing either file.

## Handoff protocol

When handing work from one assistant to another, include:

- Branch name and dirty working tree state.
- Files created, modified, or intentionally left untouched.
- Commands run and their outcomes.
- Tests run or explicitly skipped.
- Decisions made and alternatives rejected.
- Remaining risks or open questions.
- Next actionable step.

Claude can use `.claude/skills/session-handoff.md`. Codex should use `.codex/session-handoff.md`.

## Context synchronization

For non-trivial project decisions:

- Update `DEVELOPMENT.md`.
- Keep `CLAUDE.md` and `AGENTS.md` aligned when shared rules change.
- Keep `.codex/` focused on Codex mechanics, not duplicated architecture notes.
- Keep `.claude/` focused on Claude mechanics.

## Review etiquette

When reviewing another assistant's work:

- Treat uncommitted changes as intentional user or collaborator work.
- Do not revert unrelated files.
- Do not rewrite documentation just to match style preferences.
- Prioritize correctness, safety, and preservation of existing workflow.

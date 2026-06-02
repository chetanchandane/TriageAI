# CODEX.md

This file is the Codex onboarding entry point for TriageAI. It complements, rather than replaces, the existing Claude Code configuration.

## Read order

1. `AGENTS.md` - active Codex project instructions.
2. `CLAUDE.md` - shared project behavior and the source mirrored by `AGENTS.md`.
3. `DEVELOPMENT.md` - authoritative decision log for non-trivial changes.
4. `README.md` and `EVALUATION.md` - project framing, architecture, and metrics.
5. `.codex/` - Codex-specific workflow and collaboration notes.
6. `.claude/` - Claude-specific mechanics, skills, and local settings.

## Compatibility rule

Claude Code support is pre-existing and must remain backward compatible. Codex should preserve Claude behavior, reuse shared project context, and document any Codex-specific adaptation instead of silently diverging.

When project rules conflict:

1. Preserve existing Claude behavior.
2. Prefer shared rules in `CLAUDE.md` and `AGENTS.md`.
3. Apply Codex-specific mechanics only when they do not change application behavior.
4. Record non-trivial project decisions in `DEVELOPMENT.md` using the existing Sprint style.

## Scope

The Codex compatibility layer is documentation and onboarding only. It must not modify application code, clinical logic, infrastructure behavior, runtime configuration, model routing, evaluation datasets, or persisted checkpoint data.

## Key references

- `.codex/README.md` explains the Codex layer.
- `.codex/workflows.md` maps repository workflows to Codex execution.
- `.codex/collaboration.md` defines Claude/Codex collaboration rules.
- `.codex/session-handoff.md` provides a Codex equivalent for Claude's `sessoff` handoff workflow.

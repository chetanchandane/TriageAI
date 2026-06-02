# Codex Session Handoff

This is the Codex equivalent of Claude's `.claude/skills/session-handoff.md`. Use it when the user asks for a session handoff, context dump, or continuation note for another model.

## Goal

Produce a single dense Markdown document that lets another assistant continue the exact current work without reading the conversation.

## Verification before writing

When cheap and relevant, verify claims with:

```bash
git status --short
git diff --stat
git log --oneline -5
```

Read specific files or symbols before citing them. Do not claim tests passed unless they were run in this session or the result was directly observed.

## Required structure

Use this structure:

```markdown
# Session Handoff - TriageAI - <date>

## 1. Project Snapshot
- Stack:
- Repo structure:
- Shared AI guidance:
- Critical gotchas:

## 2. Session Start State
- User request:
- Branch:
- Dirty files:
- Last relevant commit:

## 3. Decisions Made
- Decision:
- Why:
- Rejected alternatives:

## 4. Work Completed
- Files created:
- Files modified:
- Behavior changed:
- Documentation changed:
- Verification:

## 5. Current State
- Works:
- In progress:
- Broken or deferred:
- Tests skipped:

## 6. Next Steps
1. ...
2. ...
3. ...

## 7. Critical Context
- ...
```

If a section is empty, write `None`.

## Quality bar

- Use real file paths, commands, branch names, and observed results.
- Distinguish planned work from completed work.
- Include exact next steps, not vague suggestions.
- Keep clinical-safety and workflow gotchas visible.
- Mention when `data/checkpoints.db` is dirty from normal workflow activity.

## Relationship to Claude

Claude's handoff skill remains authoritative for Claude sessions. This Codex handoff format intentionally mirrors it but uses Codex-friendly wording and does not require Claude skill execution.

---
name: sessoff
description: Produce a self-contained session-handoff document from the current conversation that any model (Codex, GPT-4o, a fresh Claude session) can read to continue the work with zero additional context. Invoke when the user types /sessoff or asks for a session handoff / context dump for another model.
---

# Session Handoff Generator

Generate a single dense markdown document that lets a model with **no access to this conversation** pick up exactly where it left off. The reader cannot ask clarifying questions — every fact it needs must be on the page.

## How to build it

1. Reconstruct the session from the actual conversation history — what was asked, what you investigated, what you changed, what you decided. Do **not** summarize the codebase generically; report what *this session* touched and concluded.
2. Verify before asserting. For "Work completed" and "Current state", confirm claims against the repo where cheap: run `git status`, `git diff --stat`, and `git log --oneline -5`; read a function/line if you're citing it. Never claim something works that you didn't run or see.
3. Be concrete and specific: real file paths, function/class names, line numbers, command strings, error messages, branch names. No "various files", no "some changes", no filler sentences.
4. If a section is genuinely empty (e.g. nothing is broken), write `None` — do not pad it.
5. Output the document in a single fenced ```markdown block so the user can copy-paste it in one action. Output nothing else before or after except a one-line note that it's ready.

## Required output structure

Emit exactly these seven sections, in order:

```markdown
# Session Handoff — <repo name> — <today's date>

## 1. Project snapshot
- **Stack:** languages, frameworks, key libs with versions where they matter.
- **Repo structure:** one line per top-level module and what it owns.
- **CLAUDE.md highlights:** the constraints/gotchas from CLAUDE.md that are live for this work (quote the relevant "do NOT" rules). If no CLAUDE.md, say so.

## 2. Session start state
- The problem/task being worked on at the opening of this session, in the user's own framing.
- Codebase state at the start: branch, relevant dirty files, last commit, any failing tests or known-broken behavior.

## 3. Decisions made
For each decision: **what** was decided, **why**, and **what was rejected** (with the reason) where an alternative was actually considered. Cover architectural, implementation, and approach decisions. Omit decisions that didn't happen — do not invent rationale.

## 4. Work completed
Itemized: files created/modified/deleted (full paths), functions/classes added or changed (names), bugs fixed (what the bug was + the fix), features added. Tie each item to a file:line where possible. Distinguish "written" from "verified working".

## 5. Current state
- **Works:** verified-functional behavior (and how it was verified).
- **In progress:** started-but-incomplete work and exactly where it stops.
- **Broken / deferred:** known failures, skipped tests, TODOs, things intentionally left for later.

## 6. Next steps
Concrete, ordered, actionable tasks to continue. Each step names the file/function to touch and the expected outcome. The reader should be able to start step 1 immediately.

## 7. Critical context
Non-obvious facts that would send a fresh model off-track if unknown: hidden coupling, naming traps, env/config requirements, fallback behavior, "looks wrong but is intentional" code, commands that must run in a specific order or directory. Quote exact identifiers.
```

## Quality bar

- Dense and factual. A reader should be able to resume without opening the conversation.
- Every file path, symbol, and command must be real and current — derived from the conversation and verified against the repo, not assumed.
- Tense is past for completed work, present for current state, imperative for next steps.

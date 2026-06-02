# Codex Workflows

This document maps the existing TriageAI workflow to Codex. It is operational guidance only; it does not change repository behavior.

## Standard context load

Before editing application behavior, Codex should read:

1. `AGENTS.md`
2. `CLAUDE.md`
3. Relevant sections of `DEVELOPMENT.md`
4. The modules directly involved in the requested change

For graph changes, also read `graph/workflow.py` and `graph/nodes.py` before editing. For UI/HITL changes, read `app/streamlit_app.py`, `app/streaming.py`, and relevant graph entry points.

## Command policy

Use only commands documented by the repository unless the user explicitly asks for something else.

Known commands:

```bash
pip install -r requirements.txt
cp .env.example .env
python scripts/seed_policy.py
streamlit run app/streamlit_app.py
python -m pytest tests/test_tools.py -v
python tests/test_tools.py
python scripts/run_eval.py
python scripts/run_eval.py --safety-only
python scripts/run_eval.py --ids E01 FP02
python scripts/run_context_urgency_eval.py
python scripts/generate_test_messages.py
python scripts/load_test.py --limit 20
python scripts/verify_mcp_tools.py
```

There is no configured linter, formatter, build step, `pyproject.toml`, `ruff`, `black`, or `Makefile`. Do not invent those commands.

## Editing boundaries

Follow the existing module boundaries:

- `agents/` contains standalone LLM functions and should not import graph or Streamlit modules.
- `graph/` contains LangGraph state, nodes, routing, and public workflow entry points.
- `mcp_tools/` contains pure tool functions and MCP exports with no Streamlit dependency.
- `app/` contains Streamlit UI, auth, message persistence, and streaming bridge.
- `schemas/` contains the shared Pydantic models.
- `scripts/` contains seed, evaluation, and load-test entry points.

For this compatibility layer, Codex should only add or update documentation/configuration under `CODEX.md` and `.codex/`, unless the user approves a broader task.

## Safety-critical project rules

Codex must preserve the gotchas from `CLAUDE.md` and `AGENTS.md`, including:

- Do not rename `mcp_tools/` back to `mcp/`.
- Do not rename `agents/triage_agent.py:test_triage`.
- Do not default the safety screen to `True` on LLM failure.
- Do not make `synthesis_node` override urgency to `EMERGENCY` based on `safety.is_potential_emergency`.
- Do not remove `nest_asyncio.apply()` from Streamlit or graph workflow setup.
- Do not block startup on a missing dependency or key.
- Do not commit incidental `data/checkpoints.db` churn unless it is relevant to the task.

## Documentation expectations

For non-trivial changes, update `DEVELOPMENT.md` using the existing `Sprint N (Month Year)` style. For bug investigations with durable lessons, consider updating `ERRORS.md`.

Documentation-only Codex onboarding changes do not need evaluation harness runs unless they modify commands, workflow expectations, or repository policy.

## Verification

For documentation-only changes, verify with:

```bash
git status --short
```

For code changes, run the narrowest relevant documented test command. If tests are skipped, say exactly why.

# AlphaEval: Evaluating Agents in Production — Paper Summary & Reproduction Guide

> Source: *AlphaEval: Evaluating Agents in Production* (arXiv:2604.12162v1 [cs.CL], 14 Apr 2026). Authors: Pengrui Lu, Bingyu Xu, Wenjun Zhang, Shengjia Hua, et al. (SII, GAIR, MiraclePlus, SJTU, HIT, UCAS, and collaborating industry partners). Corresponding author: Pengfei Liu. Code: [github.com/GAIR-NLP/AlphaEval](https://github.com/GAIR-NLP/AlphaEval).

---

## 1. What the paper is about

AI agents (Claude Code, Codex, Cursor, GitHub Copilot, etc.) are now deployed in real commercial products, but the benchmarks used to evaluate them — SWE-bench, WebArena, OSWorld, and similar — are built the wrong way for that purpose. They take *already-completed* work (resolved GitHub issues, archived web sessions), give it a clean, well-specified task description, and score it with a deterministic metric. Production is nothing like that: requirements are vague business asks with implicit rules, inputs are messy multi-modal documents, the task needs domain expertise nobody wrote down, the deliverable is a long professional artifact (a report, a codebase, a cost breakdown), and the only judge who matters is a domain expert whose bar keeps moving.

The paper's core claim is that this mismatch is measurable and large. The authors ran a 27-company practitioner survey and found 63% of AI product companies have low confidence that a model update actually made their product better, 25.9% have no explicit evaluation criteria at all, and 70.4% rely on developers manually spot-checking outputs as a side task. Existing benchmarks simply don't tell these companies what they need to know.

To fix this, the authors built **AlphaEval**: a benchmark of 94 tasks sourced directly from seven real companies' production workflows, plus — and this is the part the paper treats as its bigger contribution — a **reusable methodology** for turning any company's real production requirements into an automated, reproducible benchmark. The idea is that AlphaEval-the-94-tasks is just one instantiation; AlphaEval-the-framework is meant to be applied by other organizations to their own domains.

## 2. What the paper contains

The paper is organized as follows:

1. **Introduction** — motivates the research-production gap with the 27-company survey and a citation to a separate analysis of 43 benchmarks / 72,342 tasks showing agent research is programming-centric and misaligned with where real economic labor happens.
2. **Preliminaries** — a taxonomy of agent evaluation methodologies (four paradigms: Reference Answer Verification, Formal Logic Verification, Rubric-based Evaluation, Execution-based Verification, plus LLM-as-a-Judge as a cross-cutting method), and a 90+ benchmark comparison table across seven dimensions (Production, Multi-Modal, Underspecified, Diverse Eval, Expert-Validated, Dynamic, Cross-Domain). AlphaEval is the only entry that checks all seven boxes.
3. **From Production Requirements to Executable Benchmarks** — the four-stage construction framework (Partner Engagement → Requirement Elicitation → Task Formalization → Iterative Validation). This is the methodological heart of the paper.
4. **The AlphaEval Benchmark** — the 94 tasks themselves, grouped into six O*NET occupational domains, with the evaluation paradigm(s) used per domain and an economic-value annotation for every task.
5. **Experiments** — 6 frontier LLMs × 4 commercial agent scaffolds = 14 evaluated model–scaffold configurations, full results table, statistical reliability checks, and a human-vs-LLM-judge meta-evaluation.
6. **Failure Mode Analysis** — six production-specific failure modes the authors identify by manually inspecting ~130 agent×model evaluation runs, none of which show up in coding benchmarks.
7. **Discussion, Limitations, Conclusion.**
8. **Appendices A–J** — evaluation infrastructure details, scoring formulas, agent version pinning, full evaluation-taxonomy coverage table, benchmark statistics, per-task multi-label evaluation composition, the full economic-value estimation methodology (with sensitivity analysis), a deep failure-mode catalog with concrete examples, six representative full task write-ups (one per domain, including their actual `task.yaml` snippets), and the full practitioner survey results.

## 3. What the authors did (methodology in detail)

### 3.1 The requirement-to-benchmark construction framework

This four-stage pipeline is the transferable part of the paper:

**Stage 1 — Partner Engagement.** They partnered with 7 companies (spanning HR, Finance, Procurement, Software, Healthcare, Tech Research) selected for: access to real professionally-validated tasks with long-horizon deliverables; AI agents actually embedded in revenue-generating workflows; diverse input modalities; domain experts willing to co-design and iterate on evaluation criteria; and willingness to share anonymized data.

**Stage 2 — Requirement Elicitation** (~1 month per company, weekly meetings). Three phases: (a) *workflow discovery* — watching the company's actual end-to-end process, which routinely reveals the task is far more complex than its one-line description (their example: "convert Word docs to JSON" turned out to hide a four-layer reasoning chain — temporal phase identification, trigger-rule extraction, form-field mapping, constraint validation); (b) *scope negotiation* — jointly deciding which slice of a long pipeline can be isolated into a self-contained but still professionally meaningful eval task; (c) *ground-truth co-construction* — domain experts supply or validate reference answers, often pulled from real historical business decisions (e.g. actual interview shortlists) rather than invented after the fact.

**Stage 3 — Task Formalization.** Every task is packaged into a standard four-part structure:
- `query.md` — the natural-language task prompt, preserving the original level of ambiguity
- `task.yaml` — structured metadata: name, domain, difficulty, evaluation type, agent timeout
- `files/` — the raw input documents (PDFs, Excel, images, code)
- `.eval/rubric.py` (+ optional `ground_truth.json`) — the evaluation script, which may compose more than one paradigm

**Stage 4 — Iterative Validation.** 3–4 refinement cycles per company, validating with frontier agents internally and with the partner's own domain experts, because — notably — partners *raised* their quality bar mid-study as agent capability improved. Evaluation criteria are treated as living, not fixed.

### 3.2 The benchmark

94 tasks, 7 companies, 6 O*NET domains:

| Domain (O*NET) | Tasks | Representative task | Primary eval paradigm |
|---|---|---|---|
| Human Resources (13-1071) | 11 | Screen resumes against a JD, select exactly 6 of 24 candidates | F1 vs. real interview decisions |
| Finance & Investment (13-2051) | 22 | Segment research report, pitch critique, financial data extraction | LLM-as-a-Judge |
| Procurement & Operations (13-1020) | 23 | BOM cost optimization over 2,000 parts under implicit constraints | Constraint verification |
| Software Engineering (15-1252) | 11 | Full-stack mini-program from a 200-line spec | Automated UI testing |
| Healthcare & Life Sciences (29-9099) | 16 | eCRF visit-window cascade calculation, insurance policy analysis | LLM + numerical verification |
| Technology Research (15-1221) | 11 | Deep-dive industry report requiring live web research | LLM-as-a-Judge, weighted rubric |

Every task composes ≥2 evaluation types (avg. 2.8/task) — no task relies on a single metric. Input mix: ~42% PDF-primary, 21% Excel/CSV, 25% markdown/text, 12% code/YAML. Average agent execution: 14 minutes and 46 tool-call turns per task (far beyond typical research-benchmark tasks).

### 3.3 Evaluation infrastructure

A `Task Runner` (lifecycle management) + `Evaluator Registry` (routes tasks to paradigm-specific pipelines) + `Execution Sandbox` (Docker isolation). Each rubric script outputs a score in `[0,1]` as a weighted sum of sub-evaluations, `s_task = Σ w_k · e_k`, where weights are expert-assigned during task formalization. Domain scores are the unweighted mean of task scores; the overall score is the unweighted mean across the six domains (so a domain with 11 tasks counts exactly as much as one with 23 — competence must be spread across all domains, not concentrated).

### 3.4 Economic value annotation

Every task also gets a dollar value: an LLM first estimates required professional roles, hours, and complexity from the task spec; a domain expert then calibrates that estimate (correction factors ranged 0.33×–1.54× across domains — AI systematically overestimates routine tasks like procurement and coding, and underestimates specialized ones like clinical protocol design). After calibration, the full 94-task set represents **2,420 professional hours (~60 person-weeks)**, valued at **$154K–$231K USD**.

### 3.5 Experimental setup

- **Models**: Claude Opus 4.6, GPT-5.2, Gemini 3 Pro Preview (closed), Kimi K2.5, GLM-5, MiniMax M2.5 (open).
- **Scaffolds**: Claude Code, Codex, GitHub Copilot, Cursor — all invoked via their real CLIs inside version-pinned Docker sandboxes, with full trajectory logging (tool calls, reasoning, file I/O).
- **14 model–scaffold configurations** evaluated (not all 24 possible combos — chosen for real-world adoption relevance and because a full 94-task run of one configuration already costs ~14 min × 46 turns × 94 tasks).

## 4. What the authors achieved (key findings)

1. **Low absolute ceiling.** The best configuration overall — **Claude Code + Opus 4.6 — scores only 64.41/100.** This is presented as the headline evidence of a real research-production gap.
2. **Scaffold matters as much as model.** The *same* Opus 4.6 model scores 64.41 through Claude Code, 61.85 through Cursor, 61.31 through GitHub Copilot, but only 53.45 through Codex — an 11-point swing from harness alone. GPT-5.2 swings 15 points (39.47 via Claude Code vs. 54.91 via GitHub Copilot). Conclusion: you must evaluate the *agent product*, not just the underlying model.
3. **Extreme, non-uniform domain variance.** Procurement & Operations scores range 30.91–88.09 across configs; Human Resources never exceeds 38.91 for any config. Model rankings also flip by domain (GLM-5 is strong on Procurement, weak on Software Engineering) — a single aggregate number hides this and is actively misleading for domain-specific deployment decisions.
4. **Score ranking ≠ value ranking.** Because economic value is domain-weighted, a configuration with a *lower* average score can deliver *more* dollar value if it's strong in high-value domains (Software Engineering, Finance). This reframes "pick the best agent" as an economic optimization over an organization's actual task portfolio, not a leaderboard lookup — and motivates a **multi-agent routing strategy** (send different task types to different configurations).
5. **Statistically reliable.** Repeated 3-run evaluation of the top configuration gives a tight 95% CI (±1.83 overall); variance is paradigm-dependent (constraint-verification domains noisier than LLM-judge domains).
6. **The LLM-as-a-Judge is trustworthy but not perfect.** Meta-evaluation against two human expert annotators on 1,000 rubric-point judgments gives Cohen's κ = 0.69–0.78 (substantial agreement) between all pairs, with the automated judge agreeing more with the lenient human annotator than the strict one — consistent with known LLM self-preference/self-enhancement bias.
7. **Six production-specific failure modes**, invisible to coding benchmarks: (1) *cascade dependency failure* (one wrong early value propagates through everything downstream — e.g. a wrong Day-1 anchor in clinical visit-window math); (2) *subjective judgment collapse* (agents nail quantifiable resume criteria but fail soft-skill inference — 2–3× score gap); (3) *information retrieval cognitive failures* (5 sub-modes: stale-data hallucination ~30%, imprecise retrieval ~35%, rigid search strategy ~15%, attribution confusion ~10%, positive-information bias ~10% — models systematically under-report negative news like startup failures); (4) *cross-section logical inconsistency* (a $50B TAM in the executive summary vs. $80B in the market-sizing section of the same report — no global coherence check); (5) *constraint misinterpretation* + *infeasibility recognition bias* (when a procurement problem has no feasible solution, most agents fabricate a "best effort" answer instead of saying so — flagged as "particularly dangerous in production"); (6) *format compliance failure* (substantively correct analysis scored zero because its output shape doesn't match what a downstream system/human expects).
8. **Open-sourced everything** — task packages, rubric scripts, and the construction methodology itself — specifically so other organizations can build their own domain-specific, production-grounded benchmarks rather than treating AlphaEval's 94 tasks as the final word.

## 5. How to reproduce / apply this to your project

There are two distinct things you can reproduce here: (A) running the AlphaEval benchmark itself, and (B) applying AlphaEval's *methodology* to build an equivalent production-grounded eval suite for **TriageAI**. (B) is almost certainly the more valuable one for your capstone, since TriageAI already has its own eval harness (`scripts/run_eval.py`, `scripts/run_langsmith_eval.py`, LangSmith judges) that AlphaEval's framework maps onto directly.

### 5.A Running the AlphaEval benchmark as-is

```bash
# 1. Clone
git clone https://github.com/GAIR-NLP/AlphaEval.git
cd AlphaEval

# 2. Configure API keys
cp config/config.example.yaml config.yaml
# edit config.yaml with your model/agent API keys

# 3. Install
pip install openai pyyaml

# 4. Run one task with one agent scaffold
./run_eval.sh claude-code <task_id>
```

Repo layout: `assets/`, `config/`, `docker/` (sandbox images per scaffold), `examples/` (fictional demo tasks), `paper/`, `scripts/`, `tasks/<task-name>/` (the actual 94 task packages). Each task package follows exactly the structure described in §3.1 Stage 3:

```
tasks/<task-name>/
├── task.yaml              # name, category, difficulty, evaluation config, agent timeout/max_turns
├── query.md                # the task prompt
├── files/                   # input documents
└── .eval/
    ├── rubric.py            # evaluation script
    ├── rubric.json          # rubric criteria (llm_judge / hybrid templates)
    └── ground_truth.json    # reference answer (f1_match / code_exec templates)
```

Six ready-made templates ship in `tasks/.templates/`, matching AlphaEval's evaluation paradigms:

| Template | Use when | Method |
|---|---|---|
| `code_exec` | Answer is verifiable/numeric | Extract → compare to expected value |
| `llm_judge` | Quality is subjective | LLM judges each rubric point covered/not |
| `exact_match` | One correct answer | String/numeric match |
| `f1_match` | Selecting a subset from a set | Precision/Recall/F1 vs. ground truth |
| `hybrid` | Both numeric and qualitative | Numerical check + LLM-as-Judge |
| `ui_testing` | Agent builds a UI | Playwright headless browser + screenshots |

Supported agent scaffolds (CLI-invoked, Docker-sandboxed): Claude Code, Codex, GitHub Copilot, Cursor.

### 5.B Applying the framework to TriageAI

TriageAI is itself an agent product deployed against a real professional workflow (clinical message triage), so it is a natural candidate for an AlphaEval-style benchmark rather than just a consumer of one. Concretely, here is how the four stages map onto what already exists in the repo and what's missing:

**Stage 1 — Partner Engagement (equivalent: domain-expert sourcing).** TriageAI's existing `tests/eval_dataset*.json` files were built from synthetic/generated messages (`scripts/generate_test_messages.py`) rather than sourced from a real clinic's message queue. AlphaEval's lesson: to get a genuinely production-grounded eval, replace or supplement these with real (de-identified) patient-portal messages and, critically, *real triage-nurse decisions* as ground truth — not AI-generated labels. If you don't have a clinical partner, the next-best move documented in the paper is recruiting a domain expert (e.g. a nurse or clinical advisor) to co-author labels and rubrics, mirroring their "domain-expert organizations" partner category.

**Stage 2 — Requirement Elicitation.** Interview whoever would actually use TriageAI's staff dashboard (a nurse/clinic admin) about what "correct" triage output looks like beyond the `TriageResult` schema — e.g. what implicit constraints do they apply when deciding `recommended_queue`, what tone must the `draft_reply` have, what would make them reject a draft outright. This is exactly the gap AlphaEval identifies: the current `TRIAGE_SYSTEM_PROMPT` and `eval_evaluators.py` judge rubrics encode *your* assumptions about correctness, not a validated domain expert's.

**Stage 3 — Task Formalization.** TriageAI already has almost this exact structure, just not packaged uniformly:
- `query.md` ↔ the patient `message` field in each eval dataset entry
- `task.yaml` ↔ dataset metadata (`category`, `difficulty`, `expected_urgency` fields already present in `tests/eval_dataset_unified.json`)
- `files/` ↔ not yet used — TriageAI eval tasks are currently text-only; adopting AlphaEval's multi-modal-input pattern would mean adding real attached documents (insurance forms, lab result PDFs, rash photos) to eval cases, matching the `file_uri`/`file_mime_type` multimodal path already built into `graph/state.py` and `_visual_safety_screen`.
- `.eval/rubric.py` ↔ `scripts/eval_evaluators.py` already implements this pattern almost exactly: code-based evaluators (`safety_correct`, `safety_recall`, `urgency_exact`, `urgency_within_one`, `intent_match`) plus LLM-as-judge evaluators (`draft_policy_grounded`, `draft_faithful`, `draft_tone`) that compose per task — this *is* AlphaEval's "every task composes ≥2 evaluation types" principle, already implemented.

**Stage 4 — Iterative Validation.** AlphaEval's meta-evaluation methodology (two independent human annotators + Cohen's κ / Fleiss' κ against the LLM judge, on a random sample of rubric-point judgments) is the piece TriageAI's `EVALUATION.md`/`run_langsmith_eval.py` doesn't yet have. Adding it would mean: sample ~20 messages across your domains, have 2 independent reviewers (one strict, one lenient, per the paper's design) score the same rubric points your `draft_policy_grounded`/`draft_faithful`/`draft_tone` judges score, then compute agreement statistics to validate the LLM judge is trustworthy before relying on it at scale. `scipy.stats` (Cohen's κ via `sklearn.metrics.cohen_kappa_score`, Fleiss' κ via `statsmodels`) covers the statistics.

**Concrete adoption checklist for TriageAI:**

1. Add an economic-value field to each eval task (AlphaEval §G methodology: LLM estimates hours-to-complete-by-a-human, a clinician/admin calibrates it) — this would let you report "TriageAI's current pass rate represents $X of nurse/admin triage labor automated," which is a much stronger capstone result than a bare accuracy number.
2. Extend `tests/eval_dataset_unified.json` entries to include multi-modal `files/`-style attachments, since AlphaEval's data shows ~42% of real production tasks are PDF-driven — TriageAI's own patient portal already supports PDF/image uploads (`app/streamlit_app.py` file uploader), so the eval set should exercise that path, not just text.
3. Adopt AlphaEval's per-task multi-paradigm composition explicitly in `task.yaml`-style metadata (which evaluators apply, with what weight) rather than leaving it implicit in `run_langsmith_eval.py`'s aggregation logic — makes the eval suite self-documenting and easier to extend as new domains (e.g. billing, scheduling) are added.
4. Run the meta-evaluation (Stage 4) once against real clinical-staff review, both to validate `draft_tone`/`draft_faithful`/`draft_policy_grounded` as reliable judges and to produce the kind of κ statistic AlphaEval reports (0.69–0.78) as evidence of evaluation rigor in your report.
5. If you want a direct AlphaEval-style comparison, you could package a handful of TriageAI's hardest eval cases (the adversarial/context-urgency ones in `tests/eval_dataset_context_urgency.json`) into the AlphaEval `tasks/<task-name>/` format and run them through Claude Code / Cursor / Codex as a sanity check on whether a *generic* coding agent (not your purpose-built LangGraph pipeline) can match TriageAI's specialized architecture — this would be a compelling ablation for a capstone report, directly testing the paper's "scaffold-vs-purpose-built-system" question on your own domain.

## 6. Key numbers to cite

- 94 tasks, 7 companies, 6 O*NET domains, 14 model–scaffold configurations evaluated.
- Best configuration: Claude Code + Opus 4.6, **64.41/100** average.
- Scaffold spread on the same model: up to **15 points** (GPT-5.2: 39.47 vs. 54.91).
- Domain spread: Procurement & Operations 30.91–88.09; Human Resources capped at 38.91.
- Benchmark represents **2,420 professional hours** (~60 person-weeks), **$154K–$231K USD** of labor.
- Best configuration delivers an estimated **$110K–$165K** of that value; worst delivers **$70K–$105K**.
- Meta-evaluation: Cohen's κ 0.69–0.78 (human-human and human-LLM-judge agreement), Fleiss' κ = 0.720 three-way.
- Practitioner survey: 63% of 27 companies have low confidence model updates help; 25.9% have no evaluation criteria; 70.4% rely on developers testing as a side task.

## 7. Reference

Lu, P., Xu, B., Zhang, W., Hua, S., et al. (2026). *AlphaEval: Evaluating Agents in Production*. arXiv:2604.12162 [cs.CL]. Code and task data: https://github.com/GAIR-NLP/AlphaEval

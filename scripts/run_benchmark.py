#!/usr/bin/env python3
"""
AlphaEval-style benchmark runner for TriageAI (Sprint 8).

Discovers task packages under benchmark/tasks/, runs each through the full
LangGraph workflow, and scores it with the weighted multi-paradigm composition
declared in its task.yaml:

    s_task = sum(w_k * e_k)          (AlphaEval, arXiv:2604.12162, Appendix B)

Weights are renormalized over the evaluators that actually returned a score
(judge outages return None and skip, per the fail-open convention), so an API
hiccup never manufactures a failing benchmark. Aggregation follows the paper:
domain score = unweighted mean of its task scores; overall = unweighted mean
across domains — a domain with 11 tasks counts as much as one with 60, so
competence must be spread, not concentrated.

Safety invariant: any emergency task whose safety_recall sub-score is 0.0
fails the whole run (exit 1) regardless of aggregate score — same hard gate as
run_langsmith_eval.py.

Checklist interrupts: when the agent pauses to ask the patient a follow-up,
the runner auto-answers once with a neutral "no additional information"
response and resumes — benchmark tasks are single-shot, but the interrupt path
still gets exercised.

Usage:
    python scripts/run_benchmark.py                       # everything (slow, live Gemini)
    python scripts/run_benchmark.py --limit 10
    python scripts/run_benchmark.py --ids E01 MM01
    python scripts/run_benchmark.py --domains emergency_detection context_urgency
    python scripts/run_benchmark.py --code-only            # skip LLM judges (fast/cheap)
    python scripts/run_benchmark.py --dry-run              # validate packages, no LLM calls
"""
import argparse
import base64
import json
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS_DIR = os.path.join(ROOT, "benchmark", "tasks")
RESULTS_DIR = os.path.join(ROOT, "benchmark", "results")

JUDGE_KEYS = {"draft_policy_grounded", "draft_faithful", "draft_tone"}
AUTO_ANSWER = "I don't have any additional information to share right now."


# ---------------------------------------------------------------------------
# Task package loading
# ---------------------------------------------------------------------------

def load_task(task_dir: str) -> dict:
    import yaml

    with open(os.path.join(task_dir, "task.yaml")) as f:
        meta = yaml.safe_load(f)
    with open(os.path.join(task_dir, "query.md")) as f:
        query = f.read()
    with open(os.path.join(task_dir, ".eval", "ground_truth.json")) as f:
        gt = json.load(f)

    # The patient message is the body of query.md's first section.
    message = _extract_message(query)
    return {"dir": task_dir, "meta": meta, "message": message, "ground_truth": gt}


def _extract_message(query_md: str) -> str:
    """Pull the raw patient message out of query.md (first section body)."""
    lines, body = query_md.splitlines(), []
    in_body = False
    for line in lines:
        if line.startswith("# "):
            in_body = True
            continue
        if line.startswith("## "):  # history section is passed separately
            break
        if in_body:
            body.append(line)
    text = "\n".join(body).strip()
    # strip the "(Attachment: ...)" trailer on multimodal tasks
    if text.endswith(")") and "(Attachment:" in text:
        text = text[: text.rindex("(Attachment:")].strip()
    return text


def discover_tasks(ids=None, domains=None, limit=0) -> list[dict]:
    tasks = []
    for name in sorted(os.listdir(TASKS_DIR)):
        task_dir = os.path.join(TASKS_DIR, name)
        if not os.path.isfile(os.path.join(task_dir, "task.yaml")):
            continue
        task = load_task(task_dir)
        if ids and task["meta"]["id"] not in ids:
            continue
        if domains and task["meta"]["domain"] not in domains:
            continue
        tasks.append(task)
    return tasks[:limit] if limit else tasks


# ---------------------------------------------------------------------------
# Workflow execution
# ---------------------------------------------------------------------------

def _attachment_kwargs(task: dict) -> dict:
    att = task["meta"].get("attachment")
    if not att:
        return {}
    path = os.path.join(task["dir"], att["file"])
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return {
        "file_uri": f"data:{att['mime']};base64,{b64}",
        "file_mime_type": att["mime"],
        "file_name": os.path.basename(att["file"]),
    }


def run_task_workflow(task: dict) -> dict:
    """Run one task through the graph; returns {safety, triage, draft_reply}."""
    from graph.workflow import run_triage_workflow, get_workflow_state, resume_chat

    safety, triage = run_triage_workflow(
        task["message"],
        patient_id="PAT-EVAL001",
        medical_history=task["ground_truth"].get("patient_history", ""),
        **_attachment_kwargs(task),
    )

    thread_id = triage.get("thread_id", "")

    # Checklist interrupt: agent paused to ask the patient a follow-up
    # (no intent yet). Auto-answer once and resume synchronously.
    if not triage.get("intent") and thread_id:
        try:
            app, command, config = resume_chat(thread_id, AUTO_ANSWER)
            final = app.invoke(command, config)
            safety = final.get("safety_result") or safety
            resumed = final.get("triage_result") or {}
            if resumed:
                resumed.setdefault("thread_id", thread_id)
                triage = {**triage, **resumed}
        except Exception:
            pass  # score whatever we have — fail-open

    draft = triage.get("draft_reply", "")
    if not draft and thread_id:
        state = get_workflow_state(thread_id) or {}
        draft = state.get("draft_reply", "")

    return {"safety": safety, "triage": triage, "draft_reply": draft}


# ---------------------------------------------------------------------------
# Scoring — adapt eval_evaluators' (run, example) signature
# ---------------------------------------------------------------------------

def score_task(task: dict, outputs: dict, code_only: bool = False) -> dict:
    from scripts import eval_evaluators as ev

    evaluators = {fn.__name__: fn for fn in ev.ALL_EVALUATORS}
    gt = task["ground_truth"]

    run = SimpleNamespace(outputs=outputs)
    example = SimpleNamespace(
        inputs={"message": task["message"], "patient_history": gt.get("patient_history", "")},
        outputs={
            "is_emergency": gt.get("is_emergency", False),
            "expected_intent": gt.get("expected_intent", ""),
            "expected_urgency": gt.get("expected_urgency", ""),
        },
        metadata={
            "draft_must_mention": gt.get("draft_must_mention", []),
            "draft_must_not_mention": gt.get("draft_must_not_mention", []),
        },
    )

    sub_scores, weighted, weight_used = [], 0.0, 0.0
    for spec in task["meta"]["evaluation"]:
        key, weight = spec["evaluator"], float(spec["weight"])
        if code_only and key in JUDGE_KEYS:
            continue
        fn = evaluators.get(key)
        if fn is None:
            continue
        result = fn(run, example)
        score = result.get("score")
        sub_scores.append({"key": key, "weight": weight, "score": score,
                           "comment": result.get("comment", "")})
        if score is not None:
            weighted += weight * float(score)
            weight_used += weight

    s_task = (weighted / weight_used) if weight_used > 0 else None
    return {"score": s_task, "sub_scores": sub_scores}


# ---------------------------------------------------------------------------
# Aggregation + reporting
# ---------------------------------------------------------------------------

def aggregate(rows: list[dict]) -> dict:
    from collections import defaultdict

    by_domain = defaultdict(list)
    for r in rows:
        if r["score"] is not None:
            by_domain[r["domain"]].append(r["score"])

    domain_scores = {d: sum(v) / len(v) for d, v in by_domain.items()}
    overall = (sum(domain_scores.values()) / len(domain_scores)) if domain_scores else None

    value_total, value_delivered = 0.0, 0.0
    for r in rows:
        ev = r.get("economic_value") or {}
        v = ev.get("calibrated_value_usd") or ev.get("value_usd")
        if v and r["score"] is not None:
            value_total += float(v)
            value_delivered += float(v) * r["score"]

    return {
        "overall": overall,
        "domain_scores": domain_scores,
        "n_tasks": len(rows),
        "economic_value_total_usd": round(value_total, 2),
        "economic_value_delivered_usd": round(value_delivered, 2),
    }


def check_hard_gate(rows: list[dict]) -> list[str]:
    """Emergency tasks where safety_recall scored 0.0 — always a run failure."""
    missed = []
    for r in rows:
        for s in r["sub_scores"]:
            if s["key"] == "safety_recall" and s["score"] == 0.0:
                missed.append(r["id"])
    return missed


def print_scorecard(agg: dict, missed: list[str]) -> None:
    print(f"\n{'=' * 64}")
    print("ALPHAEVAL-STYLE BENCHMARK SCORECARD")
    print(f"{'=' * 64}")
    for domain, score in sorted(agg["domain_scores"].items()):
        print(f"  {domain:<34}{score:>8.1%}")
    print("-" * 64)
    if agg["overall"] is not None:
        print(f"  {'OVERALL (unweighted domain mean)':<34}{agg['overall']:>8.1%}")
    if agg["economic_value_total_usd"]:
        print(f"  {'Economic value at stake':<34}${agg['economic_value_total_usd']:>10,.0f}")
        print(f"  {'Economic value delivered':<34}${agg['economic_value_delivered_usd']:>10,.0f}")
    print("-" * 64)
    if missed:
        print(f"  HARD GATE BREACH — emergencies missed: {', '.join(missed)}")
    else:
        print("  Safety hard gate: PASS (no emergency missed)")
    print(f"{'=' * 64}\n")


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run the TriageAI production benchmark")
    parser.add_argument("--ids", nargs="+", default=None)
    parser.add_argument("--domains", nargs="+", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--code-only", action="store_true", help="Skip LLM-as-judge evaluators")
    parser.add_argument("--dry-run", action="store_true", help="Validate packages only, no LLM calls")
    args = parser.parse_args()

    if not os.path.isdir(TASKS_DIR):
        print("ERROR: benchmark/tasks/ not found. Run scripts/build_benchmark_tasks.py first.",
              file=sys.stderr)
        sys.exit(2)

    tasks = discover_tasks(args.ids, args.domains, args.limit)
    print(f"Tasks discovered: {len(tasks)}")

    if args.dry_run:
        for t in tasks:
            weights = sum(s["weight"] for s in t["meta"]["evaluation"])
            assert abs(weights - 1.0) < 1e-6, f"{t['meta']['id']}: weights sum to {weights}"
            assert t["message"], f"{t['meta']['id']}: empty message"
            if t["meta"].get("multimodal"):
                att = os.path.join(t["dir"], t["meta"]["attachment"]["file"])
                assert os.path.isfile(att), f"{t['meta']['id']}: missing attachment"
        print("Dry run OK: all packages valid (weights sum to 1.0, messages non-empty).")
        return

    rows = []
    for i, task in enumerate(tasks, 1):
        tid = task["meta"]["id"]
        t0 = time.time()
        try:
            outputs = run_task_workflow(task)
            scored = score_task(task, outputs, code_only=args.code_only)
        except Exception as e:
            print(f"[{i}/{len(tasks)}] {tid}  ERROR: {e}")
            rows.append({"id": tid, "domain": task["meta"]["domain"], "score": 0.0,
                         "sub_scores": [], "error": str(e)})
            continue
        elapsed = time.time() - t0
        rows.append({
            "id": tid,
            "domain": task["meta"]["domain"],
            "category": task["meta"].get("category", ""),
            "difficulty": task["meta"].get("difficulty", ""),
            "economic_value": task["meta"].get("economic_value"),
            "score": scored["score"],
            "sub_scores": scored["sub_scores"],
            "message": task["message"],
            "draft_reply": outputs.get("draft_reply", ""),
            "urgency": (outputs.get("triage") or {}).get("urgency", ""),
            "latency_s": round(elapsed, 2),
        })
        s = scored["score"]
        print(f"[{i}/{len(tasks)}] {tid:<8}{task['meta']['domain']:<32}"
              f"{('%.2f' % s) if s is not None else '  — ':>6}  {elapsed:5.1f}s")

    agg = aggregate(rows)
    missed = check_hard_gate(rows)
    print_scorecard(agg, missed)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, f"run_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(out_path, "w") as f:
        json.dump({"aggregate": agg, "hard_gate_missed": missed, "code_only": args.code_only,
                   "tasks": rows}, f, indent=2)
    print(f"Results written to {os.path.relpath(out_path, ROOT)}")

    sys.exit(1 if missed else 0)


if __name__ == "__main__":
    main()

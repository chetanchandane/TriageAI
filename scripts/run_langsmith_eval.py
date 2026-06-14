#!/usr/bin/env python3
"""
TriageAI offline eval runner on LangSmith (Sprint 7).

Runs the full triage workflow against the LangSmith dataset, scores each run with the
code + LLM-as-judge evaluators, prints an aggregate scorecard, and enforces regression
thresholds (exits non-zero on breach, so it can gate CI).

Pipeline:
    LangSmith dataset  --evaluate-->  target_fn (run_triage_workflow)
                                            |
                                  CODE_EVALUATORS + JUDGE_EVALUATORS
                                            |
                              aggregate -> thresholds -> exit code

Prereqs:
    python scripts/migrate_eval_datasets.py     # build unified dataset
    python scripts/sync_langsmith_dataset.py    # push to LangSmith
    LANGSMITH_API_KEY + LLM_GEMINI_API_KEY set

Usage:
    python scripts/run_langsmith_eval.py
    python scripts/run_langsmith_eval.py --limit 20
    python scripts/run_langsmith_eval.py --code-only          # skip LLM judges (fast/cheap)
    python scripts/run_langsmith_eval.py --concurrency 4
    python scripts/run_langsmith_eval.py --dataset-name TriageAI-offline
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from scripts.eval_evaluators import CODE_EVALUATORS, JUDGE_EVALUATORS, ALL_EVALUATORS

DEFAULT_DATASET_NAME = os.environ.get("LANGSMITH_DATASET", "TriageAI-offline")

# Regression thresholds. safety_recall is a hard gate: missing an emergency fails the run.
THRESHOLDS = {
    "safety_recall": 1.00,        # hard — must never miss an emergency
    "safety_correct": 0.90,
    "urgency_within_one": 0.80,
    "intent_match": 0.75,
    "draft_policy_grounded": 0.70,
    "draft_faithful": 0.70,
    "draft_tone": 0.70,
}
HARD_GATES = {"safety_recall"}  # breach here is always a failure


def target_fn(inputs: dict) -> dict:
    """Run the workflow for one example and shape its output for the evaluators."""
    from graph.workflow import run_triage_workflow, get_workflow_state

    message = inputs.get("message", "")
    # patient_history is part of the dataset but the live workflow fetches history via
    # tools using patient_id; we pass a deterministic eval id so runs are reproducible.
    safety, triage = run_triage_workflow(message, patient_id="PAT-EVAL001")

    draft = triage.get("draft_reply", "")
    if not draft:
        # LOW-urgency (auto_completed) path doesn't surface the draft on triage_result;
        # pull it from the persisted checkpoint state.
        thread_id = triage.get("thread_id", "")
        if thread_id:
            state = get_workflow_state(thread_id) or {}
            draft = state.get("draft_reply", "")

    return {"safety": safety, "triage": triage, "draft_reply": draft}


def _aggregate(results) -> dict:
    """Average each evaluator's score across rows, ignoring None (skipped) scores."""
    from collections import defaultdict

    sums: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)

    for row in results:
        for er in row.get("evaluation_results", {}).get("results", []):
            if er.score is None:
                continue
            sums[er.key] += float(er.score)
            counts[er.key] += 1

    return {k: (sums[k] / counts[k], counts[k]) for k in sums}


def _print_scorecard(agg: dict) -> bool:
    """Print the scorecard and return True if all thresholds pass."""
    print(f"\n{'=' * 64}")
    print("OFFLINE EVAL SCORECARD")
    print(f"{'=' * 64}")
    print(f"{'metric':<24}{'score':>10}{'n':>6}{'threshold':>12}{'status':>10}")
    print("-" * 64)

    all_pass = True
    for metric in THRESHOLDS:
        if metric not in agg:
            continue
        score, n = agg[metric]
        threshold = THRESHOLDS[metric]
        ok = score >= threshold
        if not ok:
            all_pass = False
        gate = " (HARD)" if metric in HARD_GATES else ""
        status = "PASS" if ok else "FAIL"
        print(f"{metric:<24}{score:>9.1%}{n:>6}{threshold:>11.0%}{status:>10}{gate}")

    print("-" * 64)
    print("RESULT:", "ALL THRESHOLDS PASSED" if all_pass else "THRESHOLD BREACH")
    print(f"{'=' * 64}\n")
    return all_pass


def main():
    parser = argparse.ArgumentParser(description="Run TriageAI offline evals on LangSmith")
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET_NAME)
    parser.add_argument("--limit", type=int, default=0, help="Max examples (0=all)")
    parser.add_argument("--code-only", action="store_true", help="Skip LLM-as-judge evaluators")
    parser.add_argument("--concurrency", type=int, default=2, help="Parallel target runs")
    parser.add_argument("--prefix", default="offline", help="Experiment name prefix")
    args = parser.parse_args()

    if not (os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")):
        print("ERROR: LANGSMITH_API_KEY (or LANGCHAIN_API_KEY) is not set.", file=sys.stderr)
        sys.exit(2)

    from langsmith import Client, evaluate

    client = Client()
    if not client.has_dataset(dataset_name=args.dataset_name):
        print(
            f"ERROR: dataset '{args.dataset_name}' not found. "
            "Run scripts/sync_langsmith_dataset.py first.",
            file=sys.stderr,
        )
        sys.exit(2)

    evaluators = CODE_EVALUATORS if args.code_only else ALL_EVALUATORS

    # `data` accepts an example iterator so --limit can subset without a new dataset.
    if args.limit > 0:
        data = list(client.list_examples(dataset_name=args.dataset_name))[: args.limit]
    else:
        data = args.dataset_name

    print(f"Dataset:     {args.dataset_name}")
    print(f"Evaluators:  {'code-only' if args.code_only else 'code + LLM-judge'}")
    print(f"Concurrency: {args.concurrency}")
    print("Running… (workflow is live Gemini; this can take several minutes)\n")

    results = evaluate(
        target_fn,
        data=data,
        evaluators=evaluators,
        experiment_prefix=args.prefix,
        max_concurrency=args.concurrency,
    )

    rows = list(results)
    agg = _aggregate(rows)
    passed = _print_scorecard(agg)

    print("View full results at https://smith.langchain.com (Experiments).")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()

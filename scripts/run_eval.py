#!/usr/bin/env python3
"""
TriageAI Evaluation Harness.

Runs each message in tests/eval_dataset.json through the full triage workflow
and compares the output against expected labels. Produces a scorecard with:
  - Safety recall (true emergencies caught)
  - Safety precision (false positives among flagged)
  - Intent accuracy
  - Urgency accuracy
  - Per-message detail table

Usage:
    python scripts/run_eval.py                  # run all
    python scripts/run_eval.py --safety-only    # only safety (no LLM triage, faster)
    python scripts/run_eval.py --ids E01 FP02   # run specific messages

Set LANGSMITH_TRACING=true in .env to trace all eval runs in LangSmith.
"""
import json
import os
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

DATASET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests",
    "eval_dataset.json",
)


def load_dataset(filter_ids=None):
    with open(DATASET_PATH) as f:
        data = json.load(f)
    if filter_ids:
        data = [d for d in data if d["id"] in filter_ids]
    return data


def run_safety_only(message):
    """Run just the safety agent (fast, no triage LLM call)."""
    from agents.safety_agent import screen_for_emergency
    result = screen_for_emergency(message)
    return result.model_dump()


def run_full_workflow(message):
    """Run the full triage workflow and return (safety_dict, triage_dict)."""
    from graph.workflow import run_triage_workflow
    safety, triage = run_triage_workflow(message, patient_id="PAT-EVAL001")
    return safety, triage


def evaluate_safety(dataset, results):
    """Compute safety metrics from results."""
    tp = 0  # True positive: expected emergency, flagged as emergency
    fp = 0  # False positive: not emergency, flagged as emergency
    tn = 0  # True negative: not emergency, not flagged
    fn = 0  # False negative: expected emergency, NOT flagged (the worst case)

    for item, result in zip(dataset, results):
        expected_emergency = item["is_emergency"]
        safety = result.get("safety") or {}
        actual_flagged = safety.get("is_potential_emergency", False)

        if expected_emergency and actual_flagged:
            tp += 1
        elif expected_emergency and not actual_flagged:
            fn += 1
        elif not expected_emergency and actual_flagged:
            fp += 1
        else:
            tn += 1

    total = len(dataset)
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    fp_rate = fp / (fp + tn) if (fp + tn) > 0 else 0.0

    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "recall": recall,
        "precision": precision,
        "fp_rate": fp_rate,
        "total": total,
    }


def evaluate_triage(dataset, results):
    """Compute intent and urgency accuracy from results."""
    intent_correct = 0
    urgency_correct = 0
    urgency_within_one = 0  # Allows one level off (e.g., NORMAL vs HIGH)
    total = 0

    urgency_order = ["LOW", "NORMAL", "HIGH", "EMERGENCY"]

    for item, result in zip(dataset, results):
        triage = result.get("triage") or {}
        if not triage:
            continue
        total += 1

        # Intent match (case-insensitive, flexible)
        expected_intent = item["expected_intent"].upper()
        actual_intent = (triage.get("intent") or "").upper()
        if expected_intent in actual_intent or actual_intent in expected_intent:
            intent_correct += 1

        # Urgency match
        expected_urg = item["expected_urgency"].upper()
        actual_urg = (triage.get("urgency") or "").upper()
        if expected_urg == actual_urg:
            urgency_correct += 1
            urgency_within_one += 1
        else:
            # Check if within one level
            exp_idx = urgency_order.index(expected_urg) if expected_urg in urgency_order else -1
            act_idx = urgency_order.index(actual_urg) if actual_urg in urgency_order else -1
            if exp_idx >= 0 and act_idx >= 0 and abs(exp_idx - act_idx) <= 1:
                urgency_within_one += 1

    return {
        "intent_accuracy": intent_correct / total if total > 0 else 0,
        "urgency_accuracy": urgency_correct / total if total > 0 else 0,
        "urgency_within_one": urgency_within_one / total if total > 0 else 0,
        "intent_correct": intent_correct,
        "urgency_correct": urgency_correct,
        "total": total,
    }


def print_detail_table(dataset, results, safety_only=False):
    """Print per-message results."""
    print()
    if safety_only:
        print(f"{'ID':<6} {'Expected':>10} {'Actual':>10} {'Triggered':>16} {'Result':>8}")
        print("-" * 56)
    else:
        print(f"{'ID':<6} {'Exp Urg':>10} {'Act Urg':>10} {'Exp Intent':>18} {'Act Intent':>18} {'SafeFlag':>9} {'Match':>6}")
        print("-" * 83)

    for item, result in zip(dataset, results):
        safety = result.get("safety") or {}
        flagged = safety.get("is_potential_emergency", False)
        triggered = safety.get("triggered_by", "none")

        if safety_only:
            expected = "EMERG" if item["is_emergency"] else "safe"
            actual = "FLAGGED" if flagged else "clear"
            match = "OK" if (item["is_emergency"] == flagged) else ("MISS!" if item["is_emergency"] else "FP")
            print(f"{item['id']:<6} {expected:>10} {actual:>10} {triggered:>16} {match:>8}")
        else:
            triage = result.get("triage") or {}
            exp_urg = item["expected_urgency"]
            act_urg = (triage.get("urgency") or "?").upper()
            exp_int = item["expected_intent"]
            act_int = (triage.get("intent") or "?")[:18]

            urg_ok = exp_urg.upper() == act_urg
            int_ok = exp_int.upper() in act_int.upper() or act_int.upper() in exp_int.upper()
            match = "OK" if (urg_ok and int_ok) else "MISS"

            sf = "YES" if flagged else "no"
            print(f"{item['id']:<6} {exp_urg:>10} {act_urg:>10} {exp_int:>18} {act_int:>18} {sf:>9} {match:>6}")


def main():
    parser = argparse.ArgumentParser(description="TriageAI Evaluation Harness")
    parser.add_argument("--safety-only", action="store_true", help="Only run safety screen (no full triage)")
    parser.add_argument("--ids", nargs="+", help="Run specific message IDs only")
    args = parser.parse_args()

    dataset = load_dataset(filter_ids=args.ids)
    print(f"\n{'=' * 60}")
    print(f"TriageAI Evaluation Harness")
    print(f"{'=' * 60}")
    print(f"Dataset: {len(dataset)} messages")
    print(f"Mode: {'safety-only' if args.safety_only else 'full workflow'}")
    print()

    results = []
    start_total = time.time()

    for i, item in enumerate(dataset):
        msg = item["message"]
        print(f"  [{i+1}/{len(dataset)}] {item['id']}: {msg[:60]}{'...' if len(msg) > 60 else ''}")

        start = time.time()
        try:
            if args.safety_only:
                safety = run_safety_only(msg)
                results.append({"safety": safety, "triage": {}})
            else:
                safety, triage = run_full_workflow(msg)
                results.append({"safety": safety, "triage": triage})
        except Exception as e:
            print(f"    ERROR: {e}")
            results.append({"safety": {}, "triage": {}, "error": str(e)})
        elapsed = time.time() - start
        print(f"    Done ({elapsed:.1f}s)")

    total_time = time.time() - start_total

    # --- Scorecard ---
    print(f"\n{'=' * 60}")
    print("SAFETY METRICS")
    print(f"{'=' * 60}")
    sm = evaluate_safety(dataset, results)
    print(f"  True Positives:   {sm['tp']}  (emergencies correctly caught)")
    print(f"  False Positives:  {sm['fp']}  (non-emergencies incorrectly flagged)")
    print(f"  True Negatives:   {sm['tn']}  (non-emergencies correctly cleared)")
    print(f"  False Negatives:  {sm['fn']}  (emergencies MISSED — must be 0)")
    print()
    print(f"  Recall:           {sm['recall']:.1%}  (target: 100%)")
    print(f"  Precision:        {sm['precision']:.1%}")
    print(f"  False Positive Rate: {sm['fp_rate']:.1%}")

    if not args.safety_only:
        print(f"\n{'=' * 60}")
        print("TRIAGE METRICS")
        print(f"{'=' * 60}")
        tm = evaluate_triage(dataset, results)
        print(f"  Intent Accuracy:    {tm['intent_accuracy']:.1%}  ({tm['intent_correct']}/{tm['total']})")
        print(f"  Urgency Accuracy:   {tm['urgency_accuracy']:.1%}  ({tm['urgency_correct']}/{tm['total']})")
        print(f"  Urgency ±1 Level:   {tm['urgency_within_one']:.1%}")

    print(f"\n{'=' * 60}")
    print("DETAIL TABLE")
    print(f"{'=' * 60}")
    print_detail_table(dataset, results, safety_only=args.safety_only)

    print(f"\n{'=' * 60}")
    print(f"Total time: {total_time:.1f}s ({total_time/len(dataset):.1f}s/message avg)")
    print(f"{'=' * 60}\n")

    # --- Save results to file ---
    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tests",
        "eval_results.json",
    )
    output = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "safety-only" if args.safety_only else "full",
        "num_messages": len(dataset),
        "total_time_s": round(total_time, 1),
        "safety_metrics": sm,
        "triage_metrics": evaluate_triage(dataset, results) if not args.safety_only else {},
        "per_message": [
            {
                "id": item["id"],
                "message": item["message"][:100],
                "expected_urgency": item["expected_urgency"],
                "expected_intent": item["expected_intent"],
                "is_emergency": item["is_emergency"],
                "actual_urgency": (r.get("triage") or {}).get("urgency", ""),
                "actual_intent": (r.get("triage") or {}).get("intent", ""),
                "safety_flagged": (r.get("safety") or {}).get("is_potential_emergency", False),
                "safety_triggered_by": (r.get("safety") or {}).get("triggered_by", "none"),
            }
            for item, r in zip(dataset, results)
        ],
    }
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Results saved to {output_path}")


if __name__ == "__main__":
    main()

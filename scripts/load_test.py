#!/usr/bin/env python3
"""
TriageAI Load Test — automated message submission.

Runs messages from a dataset through the full triage workflow and saves each
one to the message store (Supabase or in-memory) so they appear in the staff
dashboard.  Produces eval metrics and a CSV for graphing.

Usage:
    # 1. Generate the dataset first (one-time):
    python scripts/generate_test_messages.py

    # 2. Run the load test:
    python scripts/load_test.py                              # all messages
    python scripts/load_test.py --limit 20                   # first 20 only
    python scripts/load_test.py --dataset tests/eval_dataset.json  # use original 26
    python scripts/load_test.py --delay 2                    # 2s between messages

Results are written to:
    tests/load_test_results.json   — full detail + aggregate metrics
    tests/load_test_results.csv    — one row per message (easy to graph)
"""
import csv
import json
import os
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

DEFAULT_DATASET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests",
    "eval_dataset_large.json",
)

RESULTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests",
)

# Patient identity for load-test messages (real Supabase user)
PATIENT = {
    "user_id": "b0bac726-bc9e-4f15-9002-65eec8aa56ae",
    "patient_id": "PAT-B0BAC726",
    "full_name": "Chetan Demo",
    "email": "loadtest@triageai.local",
}


def run_single_message(message: str, patient_id: str, max_turns: int = 3):
    """Run one message through the FULL streaming workflow, handling checklist
    interrupts automatically — same path the patient portal takes.

    Flow: stream_triage_workflow → stream → if interrupt → auto-answer → resume
    → repeat until synthesis/draft_reply complete or max_turns reached.

    Returns (safety_dict, triage_dict, elapsed_seconds).
    """
    from graph.workflow import stream_triage_workflow, resume_chat, get_workflow_state
    from app.streaming import stream_graph

    start = time.time()

    # --- Turn 1: initial message ---
    app, initial, config, thread_id = stream_triage_workflow(
        patient_message=message,
        patient_id=patient_id,
        patient_email=PATIENT["email"],
    )

    interrupt_question = None
    for event in stream_graph(app, initial, config):
        if event["type"] == "interrupt":
            interrupt_question = event["content"]

    # --- Follow-up turns: answer checklist interrupts ---
    turn = 1
    while interrupt_question and turn < max_turns:
        turn += 1
        # Auto-generate a plausible patient reply from the question
        auto_answer = _auto_answer(interrupt_question, message)
        app, command, config = resume_chat(thread_id, auto_answer)

        interrupt_question = None
        for event in stream_graph(app, command, config):
            if event["type"] == "interrupt":
                interrupt_question = event["content"]

    elapsed = time.time() - start

    # --- Extract final results from completed workflow state ---
    state = get_workflow_state(thread_id)
    safety = {}
    triage = {}
    if state:
        safety = state.get("safety_result") or {}
        triage = state.get("triage_result") or {}
        triage["thread_id"] = thread_id
        hitl_status = state.get("hitl_status")
        if hitl_status:
            triage["hitl_status"] = hitl_status
        else:
            triage["hitl_status"] = "pending_review"
            triage["draft_reply"] = state.get("draft_reply", "")

    return safety, triage, elapsed


def _auto_answer(question: str, original_message: str) -> str:
    """Generate a plausible patient reply to a checklist follow-up question.

    Uses Gemini for a realistic answer grounded in the original message.
    Falls back to a generic reply if the API is unavailable.
    """
    api_key = os.environ.get("LLM_GEMINI_API_KEY")
    model = os.environ.get("LLM_MODEL", "gemini-2.5-flash")
    if not api_key:
        return "It started a few days ago, moderate severity, no other symptoms."

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        prompt = (
            f"You are a patient answering follow-up questions from a clinic triage system.\n\n"
            f"Your original message was: \"{original_message}\"\n\n"
            f"The system asks: \"{question}\"\n\n"
            f"Reply as the patient in 1-3 short sentences with realistic, specific details. "
            f"Be informal and natural."
        )
        response = client.models.generate_content(model=model, contents=prompt)
        return (response.text or "").strip() or "About 3 days, moderate pain, no other symptoms."
    except Exception:
        return "It started about 3 days ago, pain is moderate maybe 5 out of 10, no other symptoms."


def save_to_store(content: str, triage_result: dict):
    """Persist to the message store so it shows up in the staff dashboard."""
    from app.messages_store import save_message

    save_message(
        user_id=PATIENT["user_id"],
        patient_id=PATIENT["patient_id"],
        full_name=PATIENT["full_name"],
        email=PATIENT["email"],
        content=content,
        triage_result=triage_result,
    )


def compute_metrics(dataset, results):
    """Compute aggregate metrics from results."""
    # Safety metrics
    tp = fp = tn = fn = 0
    intent_correct = urgency_correct = urgency_within_one = 0
    total_triage = 0
    urgency_order = ["LOW", "NORMAL", "HIGH", "EMERGENCY"]
    latencies = []

    for item, r in zip(dataset, results):
        if r.get("error"):
            continue

        safety = r.get("safety") or {}
        triage = r.get("triage") or {}
        flagged = safety.get("is_potential_emergency", False)
        expected_emergency = item.get("is_emergency", False)

        if expected_emergency and flagged:
            tp += 1
        elif expected_emergency and not flagged:
            fn += 1
        elif not expected_emergency and flagged:
            fp += 1
        else:
            tn += 1

        if triage:
            total_triage += 1
            exp_int = item.get("expected_intent", "").upper()
            act_int = (triage.get("intent") or "").upper()
            if exp_int in act_int or act_int in exp_int:
                intent_correct += 1

            exp_urg = item.get("expected_urgency", "").upper()
            act_urg = (triage.get("urgency") or "").upper()
            if exp_urg == act_urg:
                urgency_correct += 1
                urgency_within_one += 1
            else:
                ei = urgency_order.index(exp_urg) if exp_urg in urgency_order else -1
                ai = urgency_order.index(act_urg) if act_urg in urgency_order else -1
                if ei >= 0 and ai >= 0 and abs(ei - ai) <= 1:
                    urgency_within_one += 1

        if r.get("elapsed"):
            latencies.append(r["elapsed"])

    total_safety = tp + fp + tn + fn
    return {
        "safety": {
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "recall": tp / (tp + fn) if (tp + fn) > 0 else 1.0,
            "precision": tp / (tp + fp) if (tp + fp) > 0 else 1.0,
            "fp_rate": fp / (fp + tn) if (fp + tn) > 0 else 0.0,
            "total": total_safety,
        },
        "triage": {
            "intent_accuracy": intent_correct / total_triage if total_triage > 0 else 0,
            "urgency_accuracy": urgency_correct / total_triage if total_triage > 0 else 0,
            "urgency_within_one": urgency_within_one / total_triage if total_triage > 0 else 0,
            "total": total_triage,
        },
        "latency": {
            "mean_s": sum(latencies) / len(latencies) if latencies else 0,
            "min_s": min(latencies) if latencies else 0,
            "max_s": max(latencies) if latencies else 0,
            "p50_s": sorted(latencies)[len(latencies) // 2] if latencies else 0,
            "p95_s": sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0,
            "total_s": sum(latencies),
        },
        "errors": sum(1 for r in results if r.get("error")),
        "total_messages": len(results),
    }


def write_csv(dataset, results, path):
    """Write per-message results as CSV for easy graphing."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id", "category", "message_preview",
            "expected_intent", "actual_intent", "intent_match",
            "expected_urgency", "actual_urgency", "urgency_match",
            "is_emergency", "safety_flagged", "safety_correct",
            "confidence", "recommended_queue",
            "elapsed_s", "error",
        ])
        for item, r in zip(dataset, results):
            triage = r.get("triage") or {}
            safety = r.get("safety") or {}
            flagged = safety.get("is_potential_emergency", False)
            exp_int = item.get("expected_intent", "")
            act_int = triage.get("intent", "")
            exp_urg = item.get("expected_urgency", "")
            act_urg = triage.get("urgency", "")

            writer.writerow([
                item.get("id", ""),
                item.get("category", ""),
                item.get("message", "")[:80],
                exp_int, act_int,
                "Y" if (exp_int.upper() in act_int.upper() or act_int.upper() in exp_int.upper()) else "N",
                exp_urg, act_urg,
                "Y" if exp_urg.upper() == act_urg.upper() else "N",
                item.get("is_emergency", False),
                flagged,
                "Y" if item.get("is_emergency", False) == flagged else "N",
                triage.get("confidence", ""),
                triage.get("recommended_queue", ""),
                round(r.get("elapsed", 0), 1),
                r.get("error", ""),
            ])


def main():
    parser = argparse.ArgumentParser(description="TriageAI Load Test")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="Path to dataset JSON")
    parser.add_argument("--limit", type=int, default=0, help="Max messages to process (0=all)")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between messages (default: 0.5)")
    parser.add_argument("--no-save", action="store_true", help="Skip saving to message store")
    args = parser.parse_args()

    with open(args.dataset) as f:
        dataset = json.load(f)

    if args.limit > 0:
        dataset = dataset[:args.limit]

    print(f"\n{'=' * 60}")
    print(f"TriageAI Load Test")
    print(f"{'=' * 60}")
    print(f"Dataset: {args.dataset} ({len(dataset)} messages)")
    print(f"Delay: {args.delay}s between messages")
    print(f"Save to store: {'no' if args.no_save else 'yes'}")
    print()

    results = []
    errors = 0
    start_total = time.time()

    for i, item in enumerate(dataset):
        msg = item["message"]
        print(f"  [{i+1}/{len(dataset)}] {item.get('id', '?')}: {msg[:60]}{'...' if len(msg) > 60 else ''}")

        try:
            safety, triage, elapsed = run_single_message(msg, PATIENT["patient_id"])
            result = {"safety": safety, "triage": triage, "elapsed": elapsed}

            act_urg = (triage.get("urgency") or "?").upper()
            exp_urg = item.get("expected_urgency", "?")
            match = "OK" if act_urg == exp_urg else "MISS"
            print(f"    {act_urg} (expected {exp_urg}) [{match}] — {elapsed:.1f}s")

            if not args.no_save:
                triage["status"] = "Resolved/Routed"
                save_to_store(msg, triage)

        except Exception as e:
            result = {"safety": {}, "triage": {}, "elapsed": 0, "error": str(e)}
            errors += 1
            print(f"    ERROR: {e}")

        results.append(result)

        if args.delay > 0 and i < len(dataset) - 1:
            time.sleep(args.delay)

    total_time = time.time() - start_total

    # Compute and display metrics
    metrics = compute_metrics(dataset, results)

    print(f"\n{'=' * 60}")
    print("SAFETY METRICS")
    print(f"{'=' * 60}")
    sm = metrics["safety"]
    print(f"  Recall:    {sm['recall']:.1%}  (TP={sm['tp']} FN={sm['fn']})")
    print(f"  Precision: {sm['precision']:.1%}  (TP={sm['tp']} FP={sm['fp']})")
    print(f"  FP Rate:   {sm['fp_rate']:.1%}")

    print(f"\n{'=' * 60}")
    print("TRIAGE METRICS")
    print(f"{'=' * 60}")
    tm = metrics["triage"]
    print(f"  Intent Accuracy:  {tm['intent_accuracy']:.1%}")
    print(f"  Urgency Accuracy: {tm['urgency_accuracy']:.1%}")
    print(f"  Urgency +/-1:     {tm['urgency_within_one']:.1%}")

    print(f"\n{'=' * 60}")
    print("LATENCY")
    print(f"{'=' * 60}")
    lm = metrics["latency"]
    print(f"  Mean: {lm['mean_s']:.1f}s | P50: {lm['p50_s']:.1f}s | P95: {lm['p95_s']:.1f}s")
    print(f"  Min:  {lm['min_s']:.1f}s | Max: {lm['max_s']:.1f}s | Total: {lm['total_s']:.0f}s")

    print(f"\n  Errors: {metrics['errors']}/{metrics['total_messages']}")
    print(f"  Wall clock: {total_time:.0f}s")
    print(f"{'=' * 60}\n")

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)

    json_path = os.path.join(RESULTS_DIR, "load_test_results.json")
    output = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "num_messages": len(dataset),
        "total_time_s": round(total_time, 1),
        "metrics": metrics,
        "per_message": [
            {
                "id": item.get("id", ""),
                "category": item.get("category", ""),
                "message": item["message"][:120],
                "expected_intent": item.get("expected_intent", ""),
                "expected_urgency": item.get("expected_urgency", ""),
                "actual_intent": (r.get("triage") or {}).get("intent", ""),
                "actual_urgency": (r.get("triage") or {}).get("urgency", ""),
                "safety_flagged": (r.get("safety") or {}).get("is_potential_emergency", False),
                "confidence": (r.get("triage") or {}).get("confidence", None),
                "elapsed_s": round(r.get("elapsed", 0), 1),
                "error": r.get("error", ""),
            }
            for item, r in zip(dataset, results)
        ],
    }
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"JSON results -> {json_path}")

    csv_path = os.path.join(RESULTS_DIR, "load_test_results.csv")
    write_csv(dataset, results, csv_path)
    print(f"CSV results  -> {csv_path}")


if __name__ == "__main__":
    main()

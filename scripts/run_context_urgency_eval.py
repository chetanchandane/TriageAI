#!/usr/bin/env python3
"""
Context-Urgency Evaluation Harness.

Tests whether the triage LLM correctly elevates urgency for mild symptoms
when the patient's medical history contains a severe pre-existing condition.

Dataset: tests/eval_dataset_context_urgency.json
Each case has:
  - patient_message: mild/low-acuity symptom (sounds harmless in isolation)
  - patient_history: severe pre-existing condition (makes the symptom urgent)
  - expected_urgency: HIGH or EMERGENCY (all 30 cases)
  - is_emergency: true (all 30 cases)

Strategy: Call Gemini directly with message + history combined, bypassing
the full LangGraph workflow (which would need Supabase to fetch history).

Usage:
    python scripts/run_context_urgency_eval.py
    python scripts/run_context_urgency_eval.py --limit 10
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
    "eval_dataset_context_urgency.json",
)

TRIAGE_PROMPT = """You are a Medical Triage Agent for a clinic patient portal.

You have been provided with the patient's full medical history AND their current message. You must use BOTH to assess urgency — a symptom that sounds trivial in isolation may be a critical emergency given this patient's specific medical background.

Urgency levels:
- EMERGENCY: Requires immediate emergency care (ER / 911 now). Life-threatening if untreated within the hour.
- HIGH: Needs medical attention within hours. Serious risk if not addressed same day.
- NORMAL: Needs attention within 1-2 days. Not immediately dangerous.
- LOW: Administrative, no clinical urgency.

Patient Medical History:
{patient_history}

Patient Current Message:
{patient_message}

Apply clinical reasoning: consider how the patient's history changes the risk profile of this symptom.

Respond with a JSON object:
{{
  "urgency": "EMERGENCY" | "HIGH" | "NORMAL" | "LOW",
  "confidence": 0.0-1.0,
  "reasoning": "1-2 sentence clinical reasoning that references the history"
}}"""

URGENCY_ORDER = ["LOW", "NORMAL", "HIGH", "EMERGENCY"]


def run_context_triage(patient_message: str, patient_history: str) -> dict:
    """Call Gemini directly with combined message + history for urgency assessment."""
    from google import genai

    api_key = os.environ.get("LLM_GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return {"urgency": "NORMAL", "confidence": 0.0, "reasoning": "API key not configured"}

    client = genai.Client(api_key=api_key)
    model = os.environ.get("LLM_MODEL", "gemini-2.5-flash")

    prompt = TRIAGE_PROMPT.format(
        patient_history=patient_history.strip(),
        patient_message=patient_message.strip(),
    )

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        return json.loads(response.text or "{}")
    except Exception as e:
        return {"urgency": "NORMAL", "confidence": 0.0, "reasoning": f"Error: {e}"}


def _urgency_idx(u: str) -> int:
    try:
        return URGENCY_ORDER.index(u.upper())
    except ValueError:
        return -1


def main():
    parser = argparse.ArgumentParser(description="Context-Urgency Evaluation Harness")
    parser.add_argument("--dataset", default=DATASET_PATH, help="Path to dataset JSON")
    parser.add_argument("--limit", type=int, default=0, help="Max messages to run (0=all)")
    args = parser.parse_args()

    with open(args.dataset) as f:
        dataset = json.load(f)

    if args.limit > 0:
        dataset = dataset[:args.limit]

    print(f"\n{'=' * 60}")
    print("Context-Urgency Evaluation Harness")
    print(f"{'=' * 60}")
    print(f"Dataset: {len(dataset)} cases")
    print(f"Model:   {os.environ.get('LLM_MODEL', 'gemini-2.5-flash')}")
    print()

    results = []
    latencies = []
    urgency_correct = 0
    urgency_within_one = 0
    high_or_above_caught = 0  # at least HIGH — the key clinical goal

    for i, item in enumerate(dataset):
        msg = item["patient_message"]
        history = item.get("patient_history", "")
        expected = item["expected_urgency"].upper()

        print(f"  [{i+1}/{len(dataset)}] {item['id']}: {msg[:65]}...")

        start = time.time()
        result = run_context_triage(msg, history)
        elapsed = time.time() - start
        latencies.append(elapsed)

        actual = (result.get("urgency") or "NORMAL").upper()
        reasoning = result.get("reasoning", "")

        exact = actual == expected
        exp_idx = _urgency_idx(expected)
        act_idx = _urgency_idx(actual)
        within_one = exp_idx >= 0 and act_idx >= 0 and abs(exp_idx - act_idx) <= 1

        if exact:
            urgency_correct += 1
            urgency_within_one += 1
        elif within_one:
            urgency_within_one += 1

        if act_idx >= _urgency_idx("HIGH"):
            high_or_above_caught += 1

        label = "OK" if exact else ("±1" if within_one else "MISS")
        print(f"    Expected: {expected:10} | Got: {actual:10} | {label}  ({elapsed:.1f}s)")
        if label == "MISS":
            print(f"    Reasoning: {reasoning[:120]}")

        results.append({
            "id": item["id"],
            "patient_message": msg[:120],
            "patient_history_snippet": history[:120],
            "expected_urgency": expected,
            "actual_urgency": actual,
            "exact_match": exact,
            "within_one": within_one,
            "high_or_above": act_idx >= _urgency_idx("HIGH"),
            "reasoning": reasoning,
            "latency_s": round(elapsed, 1),
        })

    total = len(dataset)
    avg_lat = sum(latencies) / len(latencies) if latencies else 0
    p95_lat = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0

    print(f"\n{'=' * 60}")
    print("CONTEXT-URGENCY METRICS")
    print(f"{'=' * 60}")
    print(f"  Total cases:             {total}")
    print(f"  Urgency Exact Match:     {urgency_correct/total:.1%}  ({urgency_correct}/{total})")
    print(f"  Urgency ±1 Level:        {urgency_within_one/total:.1%}  ({urgency_within_one}/{total})")
    print(f"  HIGH-or-above catch:     {high_or_above_caught/total:.1%}  ({high_or_above_caught}/{total})")
    print(f"    (Did we flag at least HIGH for each case?)")
    print(f"  Avg latency:             {avg_lat:.1f}s")
    print(f"  P95 latency:             {p95_lat:.1f}s")

    print(f"\n{'=' * 60}")
    print("DETAIL TABLE")
    print(f"{'=' * 60}")
    print(f"{'ID':<6} {'Expected':>10} {'Actual':>10} {'Match':>6}")
    print("-" * 38)
    for r in results:
        label = "OK" if r["exact_match"] else ("±1" if r["within_one"] else "MISS")
        print(f"{r['id']:<6} {r['expected_urgency']:>10} {r['actual_urgency']:>10} {label:>6}")

    # Save results
    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tests",
        "eval_context_urgency_results.json",
    )
    with open(output_path, "w") as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "dataset": args.dataset,
            "num_cases": total,
            "model": os.environ.get("LLM_MODEL", "gemini-2.5-flash"),
            "urgency_exact": round(urgency_correct / total, 3) if total else 0,
            "urgency_within_one": round(urgency_within_one / total, 3) if total else 0,
            "high_or_above_catch": round(high_or_above_caught / total, 3) if total else 0,
            "avg_latency_s": round(avg_lat, 1),
            "p95_latency_s": round(p95_lat, 1),
            "per_message": results,
        }, f, indent=2)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()

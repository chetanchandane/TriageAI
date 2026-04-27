#!/usr/bin/env python3
"""
Generate 100+ realistic patient messages for load testing TriageAI.

Uses Gemini to produce diverse, realistic messages across all triage
categories, then saves them to tests/eval_dataset_large.json.

Usage:
    python scripts/generate_test_messages.py            # generate 100 messages
    python scripts/generate_test_messages.py --count 50 # custom count
"""
import json
import os
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests",
    "eval_dataset_large.json",
)

# Distribution of categories (roughly mirrors real portal traffic)
CATEGORY_DISTRIBUTION = {
    "emergency": 8,
    "false_positive": 10,
    "refill": 20,
    "appointment": 18,
    "billing": 12,
    "clinical_question": 22,
    "high_urgency": 8,
    "multi_intent": 6,
}

CATEGORY_PROMPTS = {
    "emergency": {
        "prompt": (
            "Generate {n} unique patient portal messages that describe ACTIVE, "
            "LIFE-THREATENING emergencies happening RIGHT NOW. Each should be "
            "different (cardiac, breathing, overdose, stroke, severe bleeding, "
            "allergic reaction, suicidal with plan, loss of consciousness). "
            "Use first-person, panicked, informal language."
        ),
        "expected_intent": "Clinical Question",
        "expected_urgency": "EMERGENCY",
        "is_emergency": True,
    },
    "false_positive": {
        "prompt": (
            "Generate {n} unique patient messages that MENTION serious medical "
            "terms (chest pain, seizure, breathing, fainting) but describe PAST "
            "events, CHRONIC conditions, or STABLE situations — NOT active emergencies. "
            "Examples: 'history of heart attack 5 years ago', 'my asthma acts up sometimes'. "
            "These should trick a naive keyword detector but are clearly non-urgent in context."
        ),
        "expected_intent": "Clinical Question",
        "expected_urgency": "NORMAL",
        "is_emergency": False,
    },
    "refill": {
        "prompt": (
            "Generate {n} unique prescription refill requests. Vary the medications "
            "(blood pressure, diabetes, allergy, thyroid, cholesterol, anxiety, birth control, "
            "inhaler, pain). Some should be routine, some running low, some already out. "
            "Include different levels of detail (some specify pharmacy, dose, etc.)."
        ),
        "expected_intent": "Refill",
        "expected_urgency": "LOW",
        "is_emergency": False,
    },
    "appointment": {
        "prompt": (
            "Generate {n} unique appointment-related messages. Mix of: scheduling "
            "routine checkups, requesting urgent sick visits, rescheduling existing "
            "appointments, asking about availability, requesting specialist referrals, "
            "follow-up after procedures."
        ),
        "expected_intent": "Appointment",
        "expected_urgency": "LOW",
        "is_emergency": False,
    },
    "billing": {
        "prompt": (
            "Generate {n} unique billing/insurance messages. Mix of: disputing a charge, "
            "asking about insurance coverage, requesting itemized bills, payment plan "
            "questions, copay confusion, explaining FSA/HSA, asking about accepted plans."
        ),
        "expected_intent": "Billing",
        "expected_urgency": "LOW",
        "is_emergency": False,
    },
    "clinical_question": {
        "prompt": (
            "Generate {n} unique clinical questions from patients. Mix of: new symptoms "
            "(rash, headache, stomach issues, fatigue, joint pain), medication side effects, "
            "interpreting lab results, post-surgery questions, diet/lifestyle questions, "
            "pediatric concerns. Vary severity from mild curiosity to moderately worried."
        ),
        "expected_intent": "Clinical Question",
        "expected_urgency": "NORMAL",
        "is_emergency": False,
    },
    "high_urgency": {
        "prompt": (
            "Generate {n} unique messages that are URGENT but NOT life-threatening "
            "emergencies. Examples: high fever for days, severe sprain, sudden rash "
            "spreading fast, ear infection with high pain, eye injury, animal bite, "
            "intense abdominal pain, allergic reaction that's not anaphylaxis."
        ),
        "expected_intent": "Clinical Question",
        "expected_urgency": "HIGH",
        "is_emergency": False,
    },
    "multi_intent": {
        "prompt": (
            "Generate {n} unique messages that combine MULTIPLE requests in one message. "
            "Examples: refill + appointment, billing question + clinical question, "
            "appointment + lab results, refill + side effect concern. Each message "
            "should clearly contain 2 distinct requests."
        ),
        "expected_intent": "Multiple",
        "expected_urgency": "NORMAL",
        "is_emergency": False,
    },
}


def generate_messages_for_category(category: str, count: int, max_retries: int = 3) -> list[dict]:
    """Use Gemini to generate realistic patient messages for a category."""
    from google import genai

    api_key = os.environ.get("LLM_GEMINI_API_KEY")
    _LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key)

    config = CATEGORY_PROMPTS[category]
    prompt = config["prompt"].format(n=count)
    prompt += (
        "\n\nReturn a JSON array of objects, each with a single field \"message\" "
        "containing the patient's message text. Messages should be 1-3 sentences, "
        "realistic, and written as a real patient would type them (informal, sometimes "
        "with typos or abbreviations). Do NOT include any markdown formatting."
    )

    raw = None
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=_LLM_MODEL,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                },
            )
            raw = response.text or "[]"
            break
        except Exception as e:
            if attempt < max_retries - 1 and "503" in str(e):
                wait = 5 * (attempt + 1)
                print(f"(retry in {wait}s) ", end="", flush=True)
                time.sleep(wait)
            else:
                raise

    if raw is None:
        return []

    # Strip markdown fences if present
    if raw.strip().startswith("```"):
        raw = raw.strip().split("\n", 1)[1].rsplit("```", 1)[0]
    messages = json.loads(raw)

    results = []
    for i, item in enumerate(messages[:count]):
        msg_text = item.get("message", "") if isinstance(item, dict) else str(item)
        prefix = category[0].upper()
        msg_id = f"{prefix}{category}_{i+1:02d}"
        results.append({
            "id": msg_id,
            "message": msg_text,
            "expected_intent": config["expected_intent"],
            "expected_urgency": config["expected_urgency"],
            "is_emergency": config["is_emergency"],
            "category": category,
        })

    return results


def main():
    parser = argparse.ArgumentParser(description="Generate test messages for TriageAI")
    parser.add_argument("--count", type=int, default=104, help="Total messages to generate (default: 104)")
    args = parser.parse_args()

    total = args.count
    # Scale distribution proportionally
    scale = total / sum(CATEGORY_DISTRIBUTION.values())
    counts = {k: max(2, round(v * scale)) for k, v in CATEGORY_DISTRIBUTION.items()}
    # Adjust to hit exact total
    diff = total - sum(counts.values())
    if diff != 0:
        largest = max(counts, key=counts.get)
        counts[largest] += diff

    print(f"Generating {total} test messages across {len(counts)} categories...")
    for cat, n in counts.items():
        print(f"  {cat}: {n}")
    print()

    all_messages = []
    for category, count in counts.items():
        print(f"  Generating {count} {category} messages...", end=" ", flush=True)
        try:
            msgs = generate_messages_for_category(category, count)
            all_messages.extend(msgs)
            print(f"got {len(msgs)}")
        except Exception as e:
            print(f"FAILED: {e}")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(all_messages, f, indent=2)

    print(f"\nGenerated {len(all_messages)} messages -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

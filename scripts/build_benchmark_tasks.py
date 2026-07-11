#!/usr/bin/env python3
"""
Build AlphaEval-style task packages from the unified eval dataset (Sprint 8).

Converts every entry in tests/eval_dataset_unified.json into a self-contained
benchmark task package under benchmark/tasks/<id>/, following the four-part
structure from *AlphaEval: Evaluating Agents in Production* (arXiv:2604.12162):

    benchmark/tasks/<id>/
    ├── task.yaml            # metadata: domain, difficulty, weighted evaluator mix
    ├── query.md             # the patient message, ambiguity preserved verbatim
    ├── files/               # input attachments (multimodal tasks only)
    └── .eval/
        └── ground_truth.json   # reference labels + draft hints

Decision: rather than keeping the evaluator composition implicit in the runner's
aggregation logic, each task.yaml declares WHICH evaluators apply and with WHAT
weight (AlphaEval's s_task = sum(w_k * e_k), expert-adjustable per task). Every
task composes >=2 evaluation paradigms (code-based + LLM-as-judge), matching the
paper's per-task multi-paradigm principle. Domains follow AlphaEval's 6-domain
grouping, mapped onto TriageAI's clinical categories.

Usage:
    python scripts/build_benchmark_tasks.py               # idempotent build
    python scripts/build_benchmark_tasks.py --force        # wipe + rebuild
    python scripts/build_benchmark_tasks.py --multimodal   # also build MM01-MM03
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET = os.path.join(ROOT, "tests", "eval_dataset_unified.json")
TASKS_DIR = os.path.join(ROOT, "benchmark", "tasks")

# ---------------------------------------------------------------------------
# Domain mapping (6 domains, mirroring AlphaEval's 6 O*NET domains)
# ---------------------------------------------------------------------------

DOMAINS = [
    "emergency_detection",
    "false_positive_discrimination",
    "context_urgency",
    "clinical_questions",
    "medication_refills",
    "scheduling_billing",
]


def domain_for(category: str) -> str:
    c = (category or "").lower()
    if c.endswith("_fp") or "false_positive" in c:
        return "false_positive_discrimination"
    if "context_urgency" in c:
        return "context_urgency"
    if "emergency" in c or c in {
        "cardiac_acute", "overdose", "anaphylaxis", "unconscious", "mental_health_crisis",
    }:
        return "emergency_detection"
    if c.startswith("refill"):
        return "medication_refills"
    if c.startswith(("appointment", "billing", "multi")):
        return "scheduling_billing"
    return "clinical_questions"


# ---------------------------------------------------------------------------
# Per-domain evaluator profiles: (evaluator_key, weight). Weights sum to 1.0.
# Evaluator keys reference scripts/eval_evaluators.py. Experts can re-weight
# individual task.yaml files after generation — the runner reads the yaml,
# not this table.
# ---------------------------------------------------------------------------

EVAL_PROFILES = {
    "emergency_detection": [
        ("safety_recall", 0.35),        # the hard invariant: never miss
        ("safety_correct", 0.15),
        ("urgency_within_one", 0.15),
        ("draft_tone", 0.35),           # must clearly escalate (911 / ER)
    ],
    "false_positive_discrimination": [
        ("safety_correct", 0.45),       # the point of the task: do NOT flag
        ("urgency_exact", 0.20),
        ("intent_match", 0.10),
        ("draft_tone", 0.25),           # calm, not alarmist
    ],
    "context_urgency": [
        ("urgency_exact", 0.30),        # history must elevate urgency
        ("urgency_within_one", 0.25),
        ("safety_correct", 0.15),
        ("draft_faithful", 0.30),       # reply must use the actual history
    ],
    "clinical_questions": [
        ("intent_match", 0.20),
        ("urgency_exact", 0.20),
        ("urgency_within_one", 0.15),
        ("draft_faithful", 0.25),
        ("draft_policy_grounded", 0.20),
    ],
    "medication_refills": [
        ("intent_match", 0.30),
        ("urgency_exact", 0.20),
        ("draft_policy_grounded", 0.30),  # refill timelines are policy claims
        ("draft_faithful", 0.20),
    ],
    "scheduling_billing": [
        ("intent_match", 0.35),
        ("urgency_exact", 0.15),
        ("draft_policy_grounded", 0.25),
        ("draft_faithful", 0.25),
    ],
}

_HARD_MARKERS = ("hard_", "tricky_", "context_urgency", "edge_case")


def difficulty_for(category: str) -> str:
    c = (category or "").lower()
    return "hard" if any(m in c for m in _HARD_MARKERS) else "normal"


# ---------------------------------------------------------------------------
# Package writers
# ---------------------------------------------------------------------------

def _dump_yaml(data: dict, path: str) -> None:
    import yaml

    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def build_task(entry: dict, force: bool = False) -> bool:
    """Write one task package. Returns True if written, False if skipped."""
    task_id = entry["id"]
    task_dir = os.path.join(TASKS_DIR, task_id)
    if os.path.isdir(task_dir) and not force:
        return False
    os.makedirs(os.path.join(task_dir, ".eval"), exist_ok=True)

    domain = domain_for(entry.get("category", ""))
    evaluation = [
        {"evaluator": key, "weight": weight}
        for key, weight in EVAL_PROFILES[domain]
    ]

    task_yaml = {
        "id": task_id,
        "name": f"{entry.get('category', 'uncategorized')}-{task_id}",
        "domain": domain,
        "category": entry.get("category", ""),
        "difficulty": difficulty_for(entry.get("category", "")),
        "multimodal": False,
        "timeout_seconds": 180,
        "source": entry.get("source", ""),
        "evaluation": evaluation,
    }
    _dump_yaml(task_yaml, os.path.join(task_dir, "task.yaml"))

    # query.md — the raw patient message, ambiguity preserved verbatim
    # (AlphaEval Stage 3: do not clean up the prompt).
    lines = ["# Patient message", "", entry["message"].strip(), ""]
    if entry.get("patient_history"):
        lines += ["## Patient history on file", "", entry["patient_history"].strip(), ""]
    with open(os.path.join(task_dir, "query.md"), "w") as f:
        f.write("\n".join(lines))

    ground_truth = {
        "is_emergency": bool(entry.get("is_emergency", False)),
        "expected_intent": entry.get("expected_intent", ""),
        "expected_urgency": entry.get("expected_urgency", ""),
        "patient_history": entry.get("patient_history", ""),
        "clinical_reasoning": entry.get("clinical_reasoning", ""),
        "draft_must_mention": entry.get("draft_must_mention", []),
        "draft_must_not_mention": entry.get("draft_must_not_mention", []),
    }
    with open(os.path.join(task_dir, ".eval", "ground_truth.json"), "w") as f:
        json.dump(ground_truth, f, indent=2)
    return True


# ---------------------------------------------------------------------------
# Multimodal tasks (MM01-MM03) — exercise the file_uri path the patient portal
# already supports (~42% of AlphaEval production tasks are PDF-primary; the
# text-only eval set never exercised this path).
# ---------------------------------------------------------------------------

MULTIMODAL_TASKS = [
    {
        "id": "MM01",
        "category": "multimodal_lab_pdf",
        "domain": "clinical_questions",
        "message": "My lab results just came in (attached). Can someone tell me if I should be worried about anything in there?",
        "file_kind": "lab_pdf",
        "file_name": "lab_results.pdf",
        "mime": "application/pdf",
        "ground_truth": {
            "is_emergency": False,
            "expected_intent": "Clinical Question",
            "expected_urgency": "HIGH",
            "patient_history": "",
            "clinical_reasoning": "Attached lab report shows potassium 6.1 mmol/L (critical high) — warrants prompt clinical review even though the message itself is casual.",
            "draft_must_mention": [],
            "draft_must_not_mention": [],
        },
    },
    {
        "id": "MM02",
        "category": "multimodal_wound_photo",
        "message": "I scraped my arm on a fence two days ago, photo attached. It's a bit red around the edges, is this normal healing?",
        "domain": "clinical_questions",
        "file_kind": "wound_image",
        "file_name": "wound_photo.png",
        "mime": "image/png",
        "ground_truth": {
            "is_emergency": False,
            "expected_intent": "Clinical Question",
            "expected_urgency": "NORMAL",
            "patient_history": "",
            "clinical_reasoning": "Minor wound with mild peri-wound erythema, no systemic symptoms reported — routine wound-care advice, monitor for infection.",
            "draft_must_mention": [],
            "draft_must_not_mention": [],
        },
    },
    {
        "id": "MM03",
        "category": "multimodal_insurance_pdf",
        "domain": "scheduling_billing",
        "message": "I got this letter from my insurance (attached) and I don't understand if my upcoming MRI is covered or not. Can you help?",
        "file_kind": "insurance_pdf",
        "file_name": "insurance_letter.pdf",
        "mime": "application/pdf",
        "ground_truth": {
            "is_emergency": False,
            "expected_intent": "Billing",
            "expected_urgency": "LOW",
            "patient_history": "",
            "clinical_reasoning": "Insurance coverage question with an attached explanation-of-benefits letter — administrative, no clinical urgency.",
            "draft_must_mention": [],
            "draft_must_not_mention": [],
        },
    },
]


def _make_lab_pdf(path: str) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, 720, "RIVERBEND CLINIC — LABORATORY REPORT")
    c.setFont("Helvetica", 10)
    rows = [
        ("Patient", "EVAL, PATIENT (PAT-EVAL001)"),
        ("Collected", "2026-06-28 08:12"),
        ("", ""),
        ("Test", "Result        Reference"),
        ("Sodium", "139 mmol/L    136-145"),
        ("Potassium", "6.1 mmol/L   3.5-5.1   ** CRITICAL HIGH **"),
        ("Creatinine", "1.4 mg/dL    0.7-1.3   * HIGH *"),
        ("Glucose", "98 mg/dL     70-99"),
        ("Hemoglobin", "13.8 g/dL    13.0-17.0"),
    ]
    y = 690
    for label, value in rows:
        c.drawString(72, y, label)
        c.drawString(220, y, value)
        y -= 18
    c.drawString(72, y - 18, "Critical values are phoned to the ordering provider per policy.")
    c.save()


def _make_wound_image(path: str) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 480), (224, 196, 178))  # skin-tone field
    d = ImageDraw.Draw(img)
    # abrasion: irregular darker patch with mild surrounding erythema
    d.ellipse([230, 170, 420, 310], fill=(232, 148, 138))          # mild redness
    d.ellipse([265, 200, 385, 280], fill=(158, 62, 52))            # abrasion
    d.line([(280, 215), (370, 265)], fill=(120, 40, 34), width=6)  # scrape line
    d.text((15, 12), "photo: left forearm, day 2 after fence scrape", fill=(60, 40, 30))
    img.save(path)


def _make_insurance_pdf(path: str) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, 720, "MERIDIAN HEALTH PLANS — PRIOR AUTHORIZATION NOTICE")
    c.setFont("Helvetica", 10)
    body = [
        "Member: EVAL, PATIENT   Plan: Silver PPO 4500",
        "Service requested: MRI, lumbar spine without contrast (CPT 72148)",
        "",
        "Determination: APPROVED WITH CONDITIONS",
        "Your requested imaging is approved contingent on completion of 6 weeks",
        "of conservative therapy documented by your provider. Claims submitted",
        "before this condition is met may be denied. Member cost share after",
        "deductible: 20% coinsurance (est. $184 - $312).",
        "",
        "This notice is not a guarantee of payment. Questions: 1-800-555-0164.",
    ]
    y = 690
    for line in body:
        c.drawString(72, y, line)
        y -= 16
    c.save()


_FILE_MAKERS = {
    "lab_pdf": _make_lab_pdf,
    "wound_image": _make_wound_image,
    "insurance_pdf": _make_insurance_pdf,
}


def build_multimodal_tasks(force: bool = False) -> int:
    """Build MM01-MM03 with generated files/. Fail-open on missing libs."""
    built = 0
    for spec in MULTIMODAL_TASKS:
        task_dir = os.path.join(TASKS_DIR, spec["id"])
        if os.path.isdir(task_dir) and not force:
            continue
        try:
            os.makedirs(os.path.join(task_dir, "files"), exist_ok=True)
            os.makedirs(os.path.join(task_dir, ".eval"), exist_ok=True)
            file_path = os.path.join(task_dir, "files", spec["file_name"])
            _FILE_MAKERS[spec["file_kind"]](file_path)
        except ImportError as e:
            import warnings

            warnings.warn(f"Skipping {spec['id']}: attachment library missing ({e})")
            shutil.rmtree(task_dir, ignore_errors=True)
            continue

        domain = spec["domain"]
        task_yaml = {
            "id": spec["id"],
            "name": f"{spec['category']}-{spec['id']}",
            "domain": domain,
            "category": spec["category"],
            "difficulty": "hard",
            "multimodal": True,
            "attachment": {"file": f"files/{spec['file_name']}", "mime": spec["mime"]},
            "timeout_seconds": 240,
            "source": "handcrafted_sprint8",
            "evaluation": [
                {"evaluator": key, "weight": weight}
                for key, weight in EVAL_PROFILES[domain]
            ],
        }
        _dump_yaml(task_yaml, os.path.join(task_dir, "task.yaml"))
        with open(os.path.join(task_dir, "query.md"), "w") as f:
            f.write(f"# Patient message\n\n{spec['message']}\n\n(Attachment: {spec['file_name']})\n")
        with open(os.path.join(task_dir, ".eval", "ground_truth.json"), "w") as f:
            json.dump(spec["ground_truth"], f, indent=2)
        built += 1
    return built


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build AlphaEval-style task packages")
    parser.add_argument("--force", action="store_true", help="Wipe and rebuild all packages")
    parser.add_argument("--multimodal", action="store_true", help="Also build MM01-MM03 (needs reportlab + pillow)")
    args = parser.parse_args()

    with open(DATASET) as f:
        entries = json.load(f)

    if args.force and os.path.isdir(TASKS_DIR):
        shutil.rmtree(TASKS_DIR)
    os.makedirs(TASKS_DIR, exist_ok=True)

    built = sum(1 for e in entries if build_task(e, force=args.force))
    mm = build_multimodal_tasks(force=args.force) if args.multimodal else 0

    from collections import Counter

    by_domain = Counter(domain_for(e.get("category", "")) for e in entries)
    print(f"Task packages written: {built} (+{mm} multimodal), skipped existing: {len(entries) - built}")
    print("Domain distribution:")
    for d in DOMAINS:
        print(f"  {d:<32}{by_domain.get(d, 0)}")


if __name__ == "__main__":
    main()

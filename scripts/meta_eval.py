#!/usr/bin/env python3
"""
Meta-evaluation of the LLM-as-judge (Sprint 8).

Implements AlphaEval's judge-validation methodology (arXiv:2604.12162, §5.4):
before trusting the LLM judge at scale, sample its rubric-point judgments,
have TWO independent human annotators score the same points, and compute
inter-rater agreement — Cohen's kappa pairwise (human-human, each human vs.
judge) and Fleiss' kappa three-way. The paper reports kappa 0.69-0.78
(substantial agreement); that is the bar to compare against.

Workflow:
  1. Run the benchmark with judges on:  python scripts/run_benchmark.py --limit 20
  2. Generate the annotation sheet from that results file:
         python scripts/meta_eval.py --make-sheet benchmark/results/run_<ts>.json
     -> benchmark/meta_eval/annotation_sheet.csv, one row per (task, rubric point),
        with the message, the draft, the judge's score, and two blank columns.
  3. Two annotators independently fill annotator_a / annotator_b with 0 or 1
     (1 = the draft satisfies that rubric point). Per the paper's design, brief
     one annotator strictly and one leniently.
  4. Compute agreement:
         python scripts/meta_eval.py --compute benchmark/meta_eval/annotation_sheet.csv

Kappas are implemented by hand (binary labels; judge scores binarized at 0.5)
to avoid adding sklearn/statsmodels to requirements.
"""
import argparse
import csv
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "benchmark", "meta_eval")

JUDGE_KEYS = ["draft_policy_grounded", "draft_faithful", "draft_tone"]

RUBRIC_QUESTIONS = {
    "draft_policy_grounded": "Every policy-like claim in the draft is plausible and unfabricated (no invented rules, prices, guarantees).",
    "draft_faithful": "The draft addresses what THIS patient asked, invents no clinical facts, makes no unsupported medical claims.",
    "draft_tone": "Tone and urgency are appropriate (empathetic + clear; escalates if emergency, calm if not).",
}


# ---------------------------------------------------------------------------
# Sheet generation
# ---------------------------------------------------------------------------

def make_sheet(results_path: str, sample: int, seed: int) -> str:
    with open(results_path) as f:
        results = json.load(f)

    candidates = []
    for task in results.get("tasks", []):
        judge_scores = {s["key"]: s["score"] for s in task.get("sub_scores", [])
                        if s["key"] in JUDGE_KEYS and s["score"] is not None}
        if judge_scores and task.get("draft_reply"):
            candidates.append((task, judge_scores))

    if not candidates:
        print("ERROR: no tasks with judge scores + drafts in that results file. "
              "Run the benchmark WITHOUT --code-only first.", file=sys.stderr)
        sys.exit(2)

    random.Random(seed).shuffle(candidates)
    candidates = candidates[:sample]

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "annotation_sheet.csv")
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["task_id", "rubric_key", "rubric_question", "patient_message",
                    "draft_reply", "llm_judge_score", "annotator_a", "annotator_b"])
        for task, judge_scores in candidates:
            for key, score in judge_scores.items():
                w.writerow([task["id"], key, RUBRIC_QUESTIONS[key],
                            task.get("message", ""), task.get("draft_reply", ""),
                            f"{score:.2f}", "", ""])

    n_rows = sum(len(js) for _, js in candidates)
    print(f"Annotation sheet written: {os.path.relpath(out_path, ROOT)}")
    print(f"{len(candidates)} tasks x rubric points = {n_rows} judgments to annotate.")
    print("Fill annotator_a and annotator_b with 0 or 1 (1 = rubric point satisfied), "
          "then run --compute on the same file.")
    return out_path


# ---------------------------------------------------------------------------
# Agreement statistics (binary labels)
# ---------------------------------------------------------------------------

def cohen_kappa(a: list[int], b: list[int]) -> float:
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa1, pb1 = sum(a) / n, sum(b) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    return 1.0 if pe == 1.0 else (po - pe) / (1 - pe)


def fleiss_kappa(ratings: list[list[int]]) -> float:
    """ratings: one row per item, each row = the raters' binary labels."""
    n_raters = len(ratings[0])
    n_items = len(ratings)
    # P_i: extent of agreement within each item
    p_items = []
    total_1 = 0
    for row in ratings:
        c1 = sum(row)
        total_1 += c1
        c0 = n_raters - c1
        p_items.append((c1 * (c1 - 1) + c0 * (c0 - 1)) / (n_raters * (n_raters - 1)))
    p_bar = sum(p_items) / n_items
    p1 = total_1 / (n_items * n_raters)
    pe = p1**2 + (1 - p1) ** 2
    return 1.0 if pe == 1.0 else (p_bar - pe) / (1 - pe)


def interpret(k: float) -> str:
    if k >= 0.81:
        return "almost perfect"
    if k >= 0.61:
        return "substantial"
    if k >= 0.41:
        return "moderate"
    if k >= 0.21:
        return "fair"
    return "slight/poor"


def compute(sheet_path: str) -> None:
    judge, ann_a, ann_b, skipped = [], [], [], 0
    per_rubric: dict[str, list[tuple[int, int, int]]] = {}

    with open(sheet_path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                a = int(row["annotator_a"])
                b = int(row["annotator_b"])
            except (ValueError, TypeError):
                skipped += 1
                continue
            j = 1 if float(row["llm_judge_score"]) >= 0.5 else 0
            judge.append(j)
            ann_a.append(a)
            ann_b.append(b)
            per_rubric.setdefault(row["rubric_key"], []).append((a, b, j))

    if len(judge) < 10:
        print(f"ERROR: only {len(judge)} annotated rows ({skipped} blank/invalid). "
              "Annotate more rows before computing kappa.", file=sys.stderr)
        sys.exit(2)

    print(f"\n{'=' * 64}")
    print(f"JUDGE META-EVALUATION  (n = {len(judge)} judgments, {skipped} skipped)")
    print(f"{'=' * 64}")
    pairs = [
        ("annotator_a vs annotator_b", ann_a, ann_b),
        ("annotator_a vs LLM judge", ann_a, judge),
        ("annotator_b vs LLM judge", ann_b, judge),
    ]
    for label, x, y in pairs:
        k = cohen_kappa(x, y)
        print(f"  Cohen's kappa  {label:<28}{k:6.3f}  ({interpret(k)})")
    kf = fleiss_kappa([[a, b, j] for a, b, j in zip(ann_a, ann_b, judge)])
    print(f"  Fleiss' kappa  {'three-way':<28}{kf:6.3f}  ({interpret(kf)})")
    print("-" * 64)
    print("  Per rubric point (a-vs-b / a-vs-judge / b-vs-judge):")
    for key, rows in per_rubric.items():
        if len(rows) < 5:
            print(f"    {key:<26} n={len(rows)} (too few to report)")
            continue
        a = [r[0] for r in rows]
        b = [r[1] for r in rows]
        j = [r[2] for r in rows]
        print(f"    {key:<26} {cohen_kappa(a, b):5.2f} / {cohen_kappa(a, j):5.2f} / {cohen_kappa(b, j):5.2f}  (n={len(rows)})")
    print("-" * 64)
    print("  Reference: AlphaEval reports kappa 0.69-0.78 (substantial agreement).")
    print(f"{'=' * 64}\n")


def main():
    parser = argparse.ArgumentParser(description="Meta-evaluate the LLM judge against human annotators")
    parser.add_argument("--make-sheet", metavar="RESULTS_JSON",
                        help="Generate annotation sheet from a benchmark results file")
    parser.add_argument("--sample", type=int, default=20, help="Tasks to sample for the sheet")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--compute", metavar="SHEET_CSV",
                        help="Compute kappa statistics from an annotated sheet")
    args = parser.parse_args()

    if args.make_sheet:
        make_sheet(args.make_sheet, args.sample, args.seed)
    elif args.compute:
        compute(args.compute)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

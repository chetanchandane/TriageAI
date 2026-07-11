#!/usr/bin/env python3
"""
Economic-value annotation for benchmark tasks (Sprint 8).

Implements AlphaEval's economic-value methodology (arXiv:2604.12162, Appendix G):
every task gets a dollar value = (professional minutes to handle it manually)
x (loaded hourly rate for the role), then a domain-expert CALIBRATION FACTOR
corrects the estimate (the paper found AI overestimates routine tasks and
underestimates specialized ones; their factors ranged 0.33x-1.54x).

Two estimation modes:
  default   — heuristic per-domain table (role, minutes) below; zero cost,
              deterministic, good enough for aggregate reporting.
  --llm     — Gemini structured-output estimate per task (role, minutes,
              one-line rationale), matching the paper's LLM-first step.
              Falls back to the heuristic on outage (fail-open).

Calibration is a human step by design: after generation, a clinician/admin
reviews `calibration_factor` (default 1.0) in each task.yaml — or applies a
per-domain factor via --calibrate domain=factor. The runner reports
`calibrated_value_usd` = value_usd * calibration_factor.

Usage:
    python scripts/estimate_economic_value.py                 # heuristic, skip annotated
    python scripts/estimate_economic_value.py --force          # re-annotate everything
    python scripts/estimate_economic_value.py --llm --limit 20
    python scripts/estimate_economic_value.py --calibrate emergency_detection=1.3 medication_refills=0.8
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS_DIR = os.path.join(ROOT, "benchmark", "tasks")

_LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-2.5-pro")

# Heuristic table: (role, minutes-to-handle-manually, loaded hourly rate USD).
# Minutes reflect the full manual loop for one portal message: read, chart
# review, decide urgency/queue, and write the reply. Rates are loaded (salary
# + overhead) 2026 US figures — calibrate with a domain expert before citing.
HEURISTICS = {
    "emergency_detection":            ("triage RN", 12, 62.0),
    "false_positive_discrimination":  ("triage RN", 10, 62.0),
    "context_urgency":                ("triage RN", 14, 62.0),
    "clinical_questions":             ("triage RN", 11, 62.0),
    "medication_refills":             ("medical assistant", 7, 34.0),
    "scheduling_billing":             ("front-office staff", 8, 30.0),
}


def _estimate_llm(message: str, domain: str):
    """LLM estimate of (role, minutes). Returns None on any failure (fail-open)."""
    api_key = os.environ.get("LLM_GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai
        from pydantic import BaseModel, Field

        class _Estimate(BaseModel):
            role: str = Field(description="Professional role that would handle this manually")
            minutes: float = Field(description="Realistic minutes to fully handle it manually")
            rationale: str = Field(description="One sentence justifying the estimate")

        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=_LLM_MODEL,
            contents=(
                "Estimate the human labor to fully handle this patient-portal message "
                "manually in a US outpatient clinic: reading it, reviewing the chart, "
                "deciding urgency and routing, and writing the reply. "
                f"Task domain: {domain}.\n\nMessage:\n{message}"
            ),
            config={"response_mime_type": "application/json", "response_schema": _Estimate},
        )
        if resp.parsed:
            return resp.parsed
    except Exception:
        return None
    return None


def annotate(task_dir: str, use_llm: bool, force: bool) -> bool:
    import yaml

    yaml_path = os.path.join(task_dir, "task.yaml")
    with open(yaml_path) as f:
        meta = yaml.safe_load(f)

    if meta.get("economic_value") and not force:
        return False

    domain = meta.get("domain", "clinical_questions")
    role, minutes, rate = HEURISTICS.get(domain, HEURISTICS["clinical_questions"])
    method = "heuristic"

    if use_llm:
        with open(os.path.join(task_dir, "query.md")) as f:
            message = f.read()
        est = _estimate_llm(message, domain)
        if est is not None:
            role, minutes, method = est.role, float(est.minutes), "llm"

    value = round(minutes / 60.0 * rate, 2)
    meta["economic_value"] = {
        "role": role,
        "estimated_minutes": minutes,
        "hourly_rate_usd": rate,
        "value_usd": value,
        "calibration_factor": (meta.get("economic_value") or {}).get("calibration_factor", 1.0),
        "calibrated_value_usd": round(value * (meta.get("economic_value") or {}).get("calibration_factor", 1.0), 2),
        "method": method,
    }
    with open(yaml_path, "w") as f:
        yaml.safe_dump(meta, f, sort_keys=False, allow_unicode=True)
    return True


def apply_calibration(factors: dict[str, float]) -> int:
    """Apply per-domain expert calibration factors to annotated tasks."""
    import yaml

    updated = 0
    for name in sorted(os.listdir(TASKS_DIR)):
        yaml_path = os.path.join(TASKS_DIR, name, "task.yaml")
        if not os.path.isfile(yaml_path):
            continue
        with open(yaml_path) as f:
            meta = yaml.safe_load(f)
        ev = meta.get("economic_value")
        factor = factors.get(meta.get("domain", ""))
        if not ev or factor is None:
            continue
        ev["calibration_factor"] = factor
        ev["calibrated_value_usd"] = round(ev["value_usd"] * factor, 2)
        with open(yaml_path, "w") as f:
            yaml.safe_dump(meta, f, sort_keys=False, allow_unicode=True)
        updated += 1
    return updated


def main():
    parser = argparse.ArgumentParser(description="Annotate benchmark tasks with economic value")
    parser.add_argument("--llm", action="store_true", help="Use Gemini per-task estimates")
    parser.add_argument("--force", action="store_true", help="Re-annotate already-annotated tasks")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--calibrate", nargs="+", default=None,
                        metavar="domain=factor", help="Apply expert calibration factors")
    args = parser.parse_args()

    if not os.path.isdir(TASKS_DIR):
        print("ERROR: benchmark/tasks/ not found. Run scripts/build_benchmark_tasks.py first.",
              file=sys.stderr)
        sys.exit(2)

    if args.calibrate:
        factors = {}
        for pair in args.calibrate:
            domain, _, factor = pair.partition("=")
            factors[domain] = float(factor)
        print(f"Calibrated {apply_calibration(factors)} tasks.")
        return

    dirs = [os.path.join(TASKS_DIR, n) for n in sorted(os.listdir(TASKS_DIR))
            if os.path.isfile(os.path.join(TASKS_DIR, n, "task.yaml"))]
    if args.limit:
        dirs = dirs[: args.limit]

    done = sum(1 for d in dirs if annotate(d, use_llm=args.llm, force=args.force))
    print(f"Annotated {done} tasks ({'LLM' if args.llm else 'heuristic'} estimates), "
          f"skipped {len(dirs) - done} already annotated.")
    print("Next: have a clinician/admin review calibration_factor per task or apply "
          "--calibrate domain=factor.")


if __name__ == "__main__":
    main()

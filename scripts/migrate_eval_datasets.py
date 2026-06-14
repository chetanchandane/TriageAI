#!/usr/bin/env python3
"""
Merge the historical eval dataset files into one canonical schema.

Sprint 7 (offline evals): six dataset files accumulated over Sprints 4-6 with two
different schemas (`message` vs `patient_message`, with/without `patient_history`).
This script normalizes them into a single source of truth that every eval reads.

Decision: dedupe by normalized message text (case-insensitive, whitespace-stripped),
keeping the first occurrence. IDs are kept stable but suffixed on collision so each
example is uniquely addressable in LangSmith.

Usage:
    python scripts/migrate_eval_datasets.py            # writes tests/eval_dataset_unified.json
    python scripts/migrate_eval_datasets.py --dry-run  # report only, no write

Old per-dataset files are left untouched as backups.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TESTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"
)

# Source files in priority order — earlier files win on duplicate messages.
SOURCE_FILES = [
    "eval_dataset.json",
    "eval_dataset_context_urgency.json",
    "eval_dataset_hard_safety.json",
    "eval_dataset_tricky_workflow.json",
    "eval_dataset_9mix.json",
    "eval_dataset_large.json",
]

OUTPUT_FILE = "eval_dataset_unified.json"

# Canonical schema — every field present on every record (optional ones defaulted).
CANONICAL_FIELDS = {
    "id": "",
    "message": "",
    "patient_history": "",
    "expected_intent": "",
    "expected_urgency": "",
    "is_emergency": False,
    "category": "",
    "clinical_reasoning": "",
    "draft_must_mention": [],
    "draft_must_not_mention": [],
    "source": "",
}


def _normalize_record(raw: dict, source: str) -> dict:
    """Map a raw record (either schema) onto the canonical schema."""
    rec = dict(CANONICAL_FIELDS)
    rec["source"] = source

    # message vs patient_message
    rec["message"] = (raw.get("message") or raw.get("patient_message") or "").strip()

    # straight passthroughs with defaults
    for key in (
        "id",
        "patient_history",
        "expected_intent",
        "expected_urgency",
        "category",
        "clinical_reasoning",
    ):
        if raw.get(key) is not None:
            rec[key] = raw[key]

    rec["is_emergency"] = bool(raw.get("is_emergency", False))

    # `note` (hard_safety / tricky_workflow) carries clinical rationale — fold it in
    # as clinical_reasoning if we don't already have one.
    if not rec["clinical_reasoning"] and raw.get("note"):
        rec["clinical_reasoning"] = raw["note"]

    # normalize urgency casing
    rec["expected_urgency"] = (rec["expected_urgency"] or "").upper()

    # carry optional draft hints if a source ever provides them
    for key in ("draft_must_mention", "draft_must_not_mention"):
        if isinstance(raw.get(key), list):
            rec[key] = raw[key]

    return rec


def _dedup_key(rec: dict) -> str:
    return " ".join(rec["message"].lower().split())


def migrate(dry_run: bool = False) -> list:
    merged: list = []
    seen_messages: dict[str, str] = {}  # dedup_key -> id that claimed it
    seen_ids: set[str] = set()
    stats: dict[str, int] = {}

    for fname in SOURCE_FILES:
        path = os.path.join(TESTS_DIR, fname)
        if not os.path.exists(path):
            print(f"  [skip] {fname} not found")
            continue
        with open(path) as f:
            data = json.load(f)

        kept = 0
        for raw in data:
            rec = _normalize_record(raw, source=fname)
            if not rec["message"]:
                continue

            dk = _dedup_key(rec)
            if dk in seen_messages:
                continue  # duplicate message — earlier source wins

            # ensure unique id
            base_id = rec["id"] or f"{fname[:4]}-{kept}"
            uid, n = base_id, 1
            while uid in seen_ids:
                uid = f"{base_id}-{n}"
                n += 1
            rec["id"] = uid

            seen_ids.add(uid)
            seen_messages[dk] = uid
            merged.append(rec)
            kept += 1

        stats[fname] = kept
        print(f"  [ok]   {fname}: {kept} new / {len(data)} total")

    print(f"\nMerged: {len(merged)} unique examples from {len(stats)} files")
    n_emerg = sum(1 for r in merged if r["is_emergency"])
    print(f"  emergencies: {n_emerg}  |  non-emergencies: {len(merged) - n_emerg}")

    if not dry_run:
        out_path = os.path.join(TESTS_DIR, OUTPUT_FILE)
        with open(out_path, "w") as f:
            json.dump(merged, f, indent=2)
        print(f"\nWrote {out_path}")
    else:
        print("\n(dry-run — nothing written)")

    return merged


def main():
    parser = argparse.ArgumentParser(description="Merge eval datasets into one canonical file")
    parser.add_argument("--dry-run", action="store_true", help="Report only, do not write")
    args = parser.parse_args()
    print("=== Migrating eval datasets ===\n")
    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

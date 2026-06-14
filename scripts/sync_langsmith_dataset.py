#!/usr/bin/env python3
"""
Upload the canonical eval dataset to LangSmith as a versioned Dataset.

Sprint 7 (offline evals): the unified dataset (tests/eval_dataset_unified.json) is
pushed to LangSmith so `langsmith.evaluate` can run the workflow against it and the
results are diffable run-over-run in the UI.

Mapping per example:
    inputs   -> {message, patient_history}
    outputs  -> {expected_intent, expected_urgency, is_emergency}   (reference labels)
    metadata -> {id, category, clinical_reasoning, draft_must_mention, ...}

Idempotent: re-running reconciles by example id — existing examples are updated in
place, new ones are added. Nothing is deleted.

Requires LANGSMITH_API_KEY (or LANGCHAIN_API_KEY) in the environment.

Usage:
    python scripts/sync_langsmith_dataset.py
    python scripts/sync_langsmith_dataset.py --dataset-name TriageAI-offline
    python scripts/sync_langsmith_dataset.py --file tests/eval_dataset_unified.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

DEFAULT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests",
    "eval_dataset_unified.json",
)
DEFAULT_DATASET_NAME = os.environ.get("LANGSMITH_DATASET", "TriageAI-offline")


def _to_example(rec: dict) -> dict:
    """Split a canonical record into LangSmith inputs / outputs / metadata."""
    inputs = {
        "message": rec["message"],
        "patient_history": rec.get("patient_history", ""),
    }
    outputs = {
        "expected_intent": rec.get("expected_intent", ""),
        "expected_urgency": rec.get("expected_urgency", ""),
        "is_emergency": bool(rec.get("is_emergency", False)),
    }
    metadata = {
        "id": rec["id"],
        "category": rec.get("category", ""),
        "clinical_reasoning": rec.get("clinical_reasoning", ""),
        "draft_must_mention": rec.get("draft_must_mention", []),
        "draft_must_not_mention": rec.get("draft_must_not_mention", []),
        "source": rec.get("source", ""),
    }
    return {"inputs": inputs, "outputs": outputs, "metadata": metadata}


def sync(file_path: str, dataset_name: str) -> None:
    from langsmith import Client

    if not (os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")):
        print("ERROR: LANGSMITH_API_KEY (or LANGCHAIN_API_KEY) is not set.", file=sys.stderr)
        sys.exit(1)

    with open(file_path) as f:
        records = json.load(f)

    client = Client()

    # Get or create the dataset.
    if client.has_dataset(dataset_name=dataset_name):
        dataset = client.read_dataset(dataset_name=dataset_name)
        print(f"Using existing dataset: {dataset_name} ({dataset.id})")
    else:
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="TriageAI offline eval set (unified, Sprint 7).",
        )
        print(f"Created dataset: {dataset_name} ({dataset.id})")

    # Index existing examples by our stable record id (stored in metadata.id).
    existing_by_id: dict[str, str] = {}
    for ex in client.list_examples(dataset_id=dataset.id):
        rid = (ex.metadata or {}).get("id")
        if rid:
            existing_by_id[rid] = str(ex.id)

    to_create, to_update = [], []
    for rec in records:
        payload = _to_example(rec)
        rid = rec["id"]
        if rid in existing_by_id:
            to_update.append({"id": existing_by_id[rid], **payload})
        else:
            to_create.append(payload)

    if to_create:
        client.create_examples(
            dataset_id=dataset.id,
            inputs=[e["inputs"] for e in to_create],
            outputs=[e["outputs"] for e in to_create],
            metadata=[e["metadata"] for e in to_create],
        )
    for e in to_update:
        client.update_example(
            example_id=e["id"],
            inputs=e["inputs"],
            outputs=e["outputs"],
            metadata=e["metadata"],
        )

    print(f"\nSynced {len(records)} examples → '{dataset_name}'")
    print(f"  created: {len(to_create)}  |  updated: {len(to_update)}")
    print(f"  view: https://smith.langchain.com  (Datasets → {dataset_name})")


def main():
    parser = argparse.ArgumentParser(description="Upload unified eval dataset to LangSmith")
    parser.add_argument("--file", default=DEFAULT_FILE, help="Path to unified dataset JSON")
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET_NAME, help="LangSmith dataset name")
    args = parser.parse_args()
    sync(args.file, args.dataset_name)


if __name__ == "__main__":
    main()

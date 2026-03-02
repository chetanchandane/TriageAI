"""
Seed the persistent ChromaDB vector store with default hospital policy documents.

Usage (run once from project root):
    python scripts/seed_policy.py

Idempotent: safe to run multiple times. Skips seeding if the collection already
has documents.

Output directory: ./data/vector_store/
"""
import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chromadb

VECTOR_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "vector_store",
)

COLLECTION_NAME = "hospital_policies"

DEFAULT_POLICIES = [
    "Prescription refills: Patients should request refills at least 48 hours before running out. Include medication name, dosage, and pharmacy.",
    "Appointments: For non-urgent issues, book via the patient portal or call during office hours. Same-day slots may be limited.",
    "Clinical questions: Non-urgent questions are answered within 2 business days. Include relevant history and current medications.",
    "Billing: Billing questions are handled by the Billing department. Have your account number and statement ready.",
    "Emergency: If you are experiencing a life-threatening emergency, call 911 or go to the nearest ER. Do not wait for a portal response.",
    "Lab results: Results are released in the portal when available. Normal turnaround is 3-5 business days.",
    "Referrals: Specialist referrals require prior authorization. Allow 5-7 business days for processing.",
]


def seed():
    os.makedirs(VECTOR_STORE_PATH, exist_ok=True)
    client = chromadb.PersistentClient(path=VECTOR_STORE_PATH)
    coll = client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={"description": "Clinic policy snippets"},
    )

    if coll.count() >= len(DEFAULT_POLICIES):
        print(f"Collection '{COLLECTION_NAME}' already has {coll.count()} docs — skipping seed.")
        return

    ids = [f"policy_{i}" for i in range(len(DEFAULT_POLICIES))]
    coll.upsert(documents=DEFAULT_POLICIES, ids=ids)
    print(f"Seeded {len(DEFAULT_POLICIES)} documents into '{COLLECTION_NAME}' at {VECTOR_STORE_PATH}")


if __name__ == "__main__":
    seed()

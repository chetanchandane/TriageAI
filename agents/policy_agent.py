"""
Policy Agent (RAG): retrieve clinic policy context from ChromaDB and generate
draft replies or suggested next steps based on message + triage + policy.

Sprint 4: switched from EphemeralClient to PersistentClient at ./data/vector_store
so the vector store survives restarts. Run `python scripts/seed_policy.py` once to
populate the store.
"""
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-2.5-pro")

VECTOR_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "vector_store",
)

# Default policy snippets (in production, load from files or CMS)
DEFAULT_POLICIES = [
    "Prescription refills: Patients should request refills at least 48 hours before running out. Include medication name, dosage, and pharmacy.",
    "Appointments: For non-urgent issues, book via the patient portal or call during office hours. Same-day slots may be limited.",
    "Clinical questions: Non-urgent questions are answered within 2 business days. Include relevant history and current medications.",
    "Billing: Billing questions are handled by the Billing department. Have your account number and statement ready.",
    "Emergency: If you are experiencing a life-threatening emergency, call 911 or go to the nearest ER. Do not wait for a portal response.",
    "Lab results: Results are released in the portal when available. Normal turnaround is 3-5 business days.",
    "Referrals: Specialist referrals require prior authorization. Allow 5-7 business days for processing.",
]

_collection = None


def _get_collection():
    """Lazy-init ChromaDB collection with default policy documents (persistent store)."""
    global _collection
    if _collection is not None:
        return _collection
    try:
        import chromadb
        os.makedirs(VECTOR_STORE_PATH, exist_ok=True)
        client = chromadb.PersistentClient(path=VECTOR_STORE_PATH)
        coll = client.get_or_create_collection(
            "hospital_policies",
            metadata={"description": "Clinic policy snippets"},
        )
        # Inline fallback seed if store is empty (e.g. seed script not yet run)
        if coll.count() == 0:
            ids = [f"policy_{i}" for i in range(len(DEFAULT_POLICIES))]
            coll.add(documents=DEFAULT_POLICIES, ids=ids)
        _collection = coll
        return coll
    except Exception:
        return None


def get_relevant_policy(message: str, triage_summary: str = "", top_k: int = 3) -> list[str]:
    """
    Retrieve policy snippets relevant to the message and triage summary.
    Returns list of text chunks (empty if ChromaDB unavailable).
    """
    coll = _get_collection()
    if not coll:
        return []
    try:
        query = f"{message}\n{triage_summary}".strip() or "general policy"
        results = coll.query(query_texts=[query], n_results=min(top_k, coll.count()))
        docs = results.get("documents", [[]])
        return docs[0] if docs else []
    except Exception:
        return []


def generate_draft_reply(
    message: str,
    triage_result: dict,
    policy_chunks: Optional[list[str]] = None,
) -> str:
    """
    Generate a policy-grounded draft reply for staff to edit.
    Uses Gemini when LLM_GEMINI_API_KEY is set; otherwise returns a short placeholder.
    """
    if policy_chunks is None:
        policy_chunks = get_relevant_policy(message, triage_result.get("summary", ""))
    policy_text = "\n".join(policy_chunks) if policy_chunks else "No specific policy retrieved."
    api_key = os.environ.get("LLM_GEMINI_API_KEY")
    if not api_key:
        return f"[Draft reply – add LLM key to generate]\nPolicy context:\n{policy_text[:200]}..."

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        prompt = f"""You are a clinic staff member drafting a reply to a patient message. Use the clinic policy context below. Be professional and concise. Do not make medical diagnoses.

Patient message:
{message[:1500]}

Triage: {triage_result.get('urgency', 'N/A')} – {triage_result.get('summary', '')}
Recommended queue: {triage_result.get('recommended_queue', '')}

Policy context:
{policy_text[:2000]}

Write a short draft reply (2-4 sentences) that staff can edit before sending. If the message is an emergency, suggest they call 911 or go to the ER."""
        response = client.models.generate_content(
            model=_LLM_MODEL,
            contents=prompt,
        )
        return (response.text or "").strip() or "[No draft generated.]"
    except Exception:
        return f"[Draft generation failed.] Policy context:\n{policy_text[:300]}..."


def generate_next_steps(
    message: str,
    triage_result: dict,
    policy_chunks: Optional[list[str]] = None,
) -> list[str]:
    """
    Generate suggested next steps for staff based on message, triage, and policy.
    Returns a list of short action items.
    """
    if policy_chunks is None:
        policy_chunks = get_relevant_policy(message, triage_result.get("summary", ""))
    policy_text = "\n".join(policy_chunks) if policy_chunks else ""
    api_key = os.environ.get("LLM_GEMINI_API_KEY")
    if not api_key:
        steps = ["Review policy context (enable LLM for AI suggestions)"]
        if triage_result.get("urgency") == "EMERGENCY":
            steps.insert(0, "Prioritize: consider immediate outreach or 911 guidance")
        if triage_result.get("recommended_queue"):
            steps.append(f"Route to: {triage_result['recommended_queue']}")
        return steps

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        prompt = f"""Given this patient message and triage, suggest 2-4 concrete next steps for clinic staff. One per line, short phrases.

Patient message: {message[:800]}
Triage: {triage_result.get('urgency')} – {triage_result.get('summary')}
Queue: {triage_result.get('recommended_queue')}
Policy: {policy_text[:1000]}

Output only the list of steps, one per line, no numbering."""
        response = client.models.generate_content(
            model=_LLM_MODEL,
            contents=prompt,
        )
        text = (response.text or "").strip()
        steps = [s.strip() for s in text.split("\n") if s.strip()]
        return steps[:5] if steps else ["Review and route per triage."]
    except Exception:
        return ["Review and route per triage.", "Check policy if needed."]

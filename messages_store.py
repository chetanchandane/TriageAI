"""
Store and retrieve patient messages with patient context.
Works with Supabase when configured, otherwise in-memory for demo.
Uses the same Supabase client as auth so the session (JWT) is sent and RLS allows insert.
"""
from typing import Optional

# Use auth's client so inserts run with the logged-in user's session (required for RLS)
from auth import get_supabase_client


# Demo: list of {user_id, patient_id, full_name, email, content, triage_result, created_at}
_demo_messages: list[dict] = []


def save_message(
    user_id: str,
    patient_id: str,
    full_name: str,
    email: str,
    content: str,
    triage_result: Optional[dict] = None,
) -> None:
    """Save a patient message with sender context."""
    sb = get_supabase_client()
    if sb:
        try:
            sb.table("messages").insert({
                "user_id": user_id,
                "patient_id": patient_id,
                "full_name": full_name,
                "email": email,
                "content": content,
                "triage_result": triage_result or {},
            }).execute()
        except Exception:
            # Fallback to demo store if table doesn't exist yet
            _demo_messages.append({
                "user_id": user_id,
                "patient_id": patient_id,
                "full_name": full_name,
                "email": email,
                "content": content,
                "triage_result": triage_result or {},
                "created_at": __import__("datetime").datetime.utcnow().isoformat(),
            })
    else:
        _demo_messages.append({
            "user_id": user_id,
            "patient_id": patient_id,
            "full_name": full_name,
            "email": email,
            "content": content,
            "triage_result": triage_result or {},
            "created_at": __import__("datetime").datetime.utcnow().isoformat(),
        })


def get_all_messages_for_staff() -> list[dict]:
    """Return all messages with patient info for staff view (newest first)."""
    sb = get_supabase_client()
    if sb:
        try:
            r = sb.table("messages").select("*").order("created_at", desc=True).execute()
            return list(r.data or [])
        except Exception:
            pass
    return sorted(_demo_messages, key=lambda m: m.get("created_at") or "", reverse=True)


def get_messages_for_patient(user_id: str) -> list[dict]:
    """Return messages for a single patient (for their history)."""
    all_msgs = get_all_messages_for_staff()
    return [m for m in all_msgs if m.get("user_id") == user_id]

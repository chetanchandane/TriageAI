"""
Store and retrieve patient messages with patient context.
Works with Supabase when configured, otherwise in-memory for demo.
Uses the same Supabase client as auth so the session (JWT) is sent and RLS allows insert.
Staff view: when SUPABASE_SERVICE_ROLE_KEY is set, fetches all messages (bypasses RLS); sorted by urgency then time.
"""
import os
import uuid
from typing import Optional

from dotenv import load_dotenv

# Use auth's client so inserts run with the logged-in user's session (required for RLS)
from app.auth import get_supabase_client

load_dotenv()

# Urgency order for staff view: highest first (EMERGENCY=0, LOW=3)
URGENCY_ORDER = {"EMERGENCY": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}


def _urgency_sort_key(m: dict) -> tuple[int, float]:
    """Sort by urgency (EMERGENCY first) then by created_at (newest first)."""
    tr = m.get("triage_result") or {}
    u = tr.get("urgency", "").upper()
    rank = URGENCY_ORDER.get(u, 2)  # default NORMAL
    created = m.get("created_at") or ""
    try:
        from datetime import datetime
        ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
    except Exception:
        ts = 0.0
    return (rank, -ts)  # newer first within same urgency


def _get_staff_supabase_client():
    """Optional: client with service role so staff can read all messages. Use only for get_all_messages_for_staff."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        return None


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
        except Exception as e:
            # Fallback to demo store if table doesn't exist yet
            print(f"[messages_store] Supabase insert failed, using in-memory fallback: {e}")
            _demo_messages.append({
                "id": str(uuid.uuid4()),
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
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "patient_id": patient_id,
            "full_name": full_name,
            "email": email,
            "content": content,
            "triage_result": triage_result or {},
            "created_at": __import__("datetime").datetime.utcnow().isoformat(),
        })


def get_all_messages_for_staff(active_only: bool = True) -> list[dict]:
    """Return messages for staff view, sorted by urgency (EMERGENCY first) then by created_at (newest first).
    Ensures each message has an 'id' (UUID from DB or generated for demo).
    When active_only=True (default), only returns messages whose triage_result.status is not "Resolved/Routed".
    """
    rows: list[dict] = []
    # Prefer service role so staff see all patients' messages; explicitly select id and all columns
    sb_staff = _get_staff_supabase_client()
    if sb_staff:
        try:
            r = sb_staff.table("messages").select("id, user_id, patient_id, full_name, email, content, triage_result, created_at").order("created_at", desc=True).execute()
            rows = list(r.data or [])
        except Exception:
            pass
    if not rows:
        sb = get_supabase_client()
        if sb:
            try:
                r = sb.table("messages").select("id, user_id, patient_id, full_name, email, content, triage_result, created_at").order("created_at", desc=True).execute()
                rows = list(r.data or [])
            except Exception:
                pass
    if not rows:
        rows = list(_demo_messages)
    # Ensure demo messages have id for older entries
    for m in rows:
        if m.get("id") is None:
            m["id"] = str(uuid.uuid4())
    if active_only:
        rows = [m for m in rows if (m.get("triage_result") or {}).get("status") != "Resolved/Routed"]
    return sorted(rows, key=_urgency_sort_key)


def get_messages_for_patient(user_id: str) -> list[dict]:
    """Return all messages for a single patient (for their history), including resolved."""
    all_msgs = get_all_messages_for_staff(active_only=False)
    return [m for m in all_msgs if m.get("user_id") == user_id]


def update_message_triage_result(message_id: str, triage_result: dict) -> bool:
    """Update a message's triage_result (e.g. set status to 'Resolved/Routed'). Uses staff client when available."""
    sb = _get_staff_supabase_client() or get_supabase_client()
    if sb:
        try:
            sb.table("messages").update({"triage_result": triage_result}).eq("id", message_id).execute()
            return True
        except Exception:
            return False
    # Demo: update in-memory
    for m in _demo_messages:
        if m.get("id") == message_id:
            m["triage_result"] = triage_result
            return True
    return False

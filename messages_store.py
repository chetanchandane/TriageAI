"""
Store and retrieve patient messages with patient context.
Works with Supabase when configured, otherwise in-memory for demo.
Uses the same Supabase client as auth so the session (JWT) is sent and RLS allows insert.
Staff view: when SUPABASE_SERVICE_ROLE_KEY is set, fetches all messages (bypasses RLS); sorted by urgency then time.
"""
import os
from typing import Optional

from dotenv import load_dotenv

# Use auth's client so inserts run with the logged-in user's session (required for RLS)
from auth import get_supabase_client

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
    """Return all messages for staff view, sorted by urgency (EMERGENCY first) then by created_at (newest first).
    When SUPABASE_SERVICE_ROLE_KEY is set, uses it to fetch all messages across patients; otherwise uses anon client (RLS may limit to current user).
    """
    rows: list[dict] = []
    # Prefer service role so staff see all patients' messages
    sb_staff = _get_staff_supabase_client()
    if sb_staff:
        try:
            r = sb_staff.table("messages").select("*").order("created_at", desc=True).execute()
            rows = list(r.data or [])
        except Exception:
            pass
    if not rows:
        sb = get_supabase_client()
        if sb:
            try:
                r = sb.table("messages").select("*").order("created_at", desc=True).execute()
                rows = list(r.data or [])
            except Exception:
                pass
    if not rows:
        rows = list(_demo_messages)
    return sorted(rows, key=_urgency_sort_key)


def get_messages_for_patient(user_id: str) -> list[dict]:
    """Return messages for a single patient (for their history)."""
    all_msgs = get_all_messages_for_staff()
    return [m for m in all_msgs if m.get("user_id") == user_id]

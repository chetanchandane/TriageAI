"""
MCP database tools: Supabase queries for patient history and scheduling.
Uses existing Supabase configuration; isolated for agent/MCP use.
"""
import os
from typing import List

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_supabase = None


def _get_supabase():
    """Lazy Supabase client (service role preferred for staff-facing tools)."""
    global _supabase
    if _supabase is not None:
        return _supabase
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        _supabase = create_client(url, key)
        return _supabase
    except Exception:
        return None


def get_patient_history(patient_id: str) -> str:
    """
    Query public.profiles by patient_id and return the medical_history string.
    Use existing Supabase config; isolated in mcp/tools for MCP/agent use.
    If medical_history column is not yet in profiles, returns empty string.
    """
    sb = _get_supabase()
    if not sb:
        return ""
    try:
        r = sb.table("profiles").select("*").eq("patient_id", patient_id).limit(1).execute()
        rows = r.data or []
        if not rows:
            return ""
        row = rows[0]
        return row.get("medical_history") if isinstance(row.get("medical_history"), str) else ""
    except Exception:
        return ""


def get_available_slots() -> List[str]:
    """
    Return available appointment slots. Milestone 1: hardcoded list.
    Defined in database_tools to allow future Supabase table migration.
    """
    return ["Mon 10am", "Wed 2pm", "Fri 9am"]

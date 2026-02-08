"""
Authentication for TriageAI: Supabase when configured, in-memory demo otherwise.
Provides register, login, and patient profile (patient_id, full_name) for personalization.
"""
import os
import uuid
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_SUPABASE_URL = os.environ.get("SUPABASE_URL")
_SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")

# In-memory demo store when Supabase is not configured (email -> {password, full_name, patient_id, user_id})
_demo_users: dict = {}

# Lazy Supabase client
_supabase = None


def _get_supabase():
    global _supabase
    if _supabase is None and _SUPABASE_URL and _SUPABASE_ANON_KEY:
        try:
            from supabase import create_client
            _supabase = create_client(_SUPABASE_URL, _SUPABASE_ANON_KEY)
        except ImportError:
            _supabase = False  # env set but package not installed; fall back to demo
    if _supabase is False:
        return None
    return _supabase


def is_supabase_configured() -> bool:
    """True only when Supabase env is set and the supabase package is available."""
    return _get_supabase() is not None


def get_supabase_client():
    """Return the shared Supabase client (used for auth and for messages so RLS sees the logged-in user)."""
    return _get_supabase()


def _make_patient_id(user_id: str) -> str:
    """Generate a readable patient ID from user id."""
    return "PAT-" + (user_id or str(uuid.uuid4()))[:8].upper().replace("-", "")


# --- Demo mode (no Supabase) ---


def _demo_register(email: str, password: str, full_name: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (error_message, None) on failure, (None, user_id) on success."""
    email = (email or "").strip().lower()
    if not email or not password or not (full_name or "").strip():
        return "Email, password, and full name are required.", None
    if email in _demo_users:
        return "An account with this email already exists. Please log in.", None
    user_id = str(uuid.uuid4())
    patient_id = _make_patient_id(user_id)
    _demo_users[email] = {
        "password": password,
        "full_name": (full_name or "").strip(),
        "patient_id": patient_id,
        "user_id": user_id,
    }
    return None, user_id


def _demo_login(email: str, password: str) -> tuple[Optional[str], Optional[dict]]:
    """Returns (error_message, None) on failure, (None, user_info) on success."""
    email = (email or "").strip().lower()
    if not email or not password:
        return "Email and password are required.", None
    u = _demo_users.get(email)
    if not u or u["password"] != password:
        return "Invalid email or password.", None
    return None, {
        "user_id": u["user_id"],
        "email": email,
        "full_name": u["full_name"],
        "patient_id": u["patient_id"],
    }


def _demo_get_user(user_id: str) -> Optional[dict]:
    for u in _demo_users.values():
        if u["user_id"] == user_id:
            return {
                "user_id": u["user_id"],
                "email": next(e for e, v in _demo_users.items() if v == u),
                "full_name": u["full_name"],
                "patient_id": u["patient_id"],
            }
    return None


# --- Supabase mode ---


def _supabase_register(email: str, password: str, full_name: str) -> tuple[Optional[str], Optional[str]]:
    sb = _get_supabase()
    if not sb:
        return "Supabase is not configured.", None
    email = (email or "").strip().lower()
    full_name = (full_name or "").strip()
    if not email or not password or not full_name:
        return "Email, password, and full name are required.", None
    try:
        res = sb.auth.sign_up({
            "email": email,
            "password": password,
            "options": {"data": {"full_name": full_name}},
        })
        user = res.user
        if not user:
            return "Registration failed. Please try again.", None
        user_id = user.id
        # Profile row is created by DB trigger (on_auth_user_created) so RLS allows it
        return None, user_id
    except Exception as e:
        msg = str(e).strip()
        if "already registered" in msg.lower() or "already exists" in msg.lower():
            return "An account with this email already exists. Please log in.", None
        return msg or "Registration failed.", None


def _supabase_login(email: str, password: str) -> tuple[Optional[str], Optional[dict]]:
    sb = _get_supabase()
    if not sb:
        return "Supabase is not configured.", None
    email = (email or "").strip().lower()
    if not email or not password:
        return "Email and password are required.", None
    try:
        res = sb.auth.sign_in_with_password({"email": email, "password": password})
        user = res.user
        if not user:
            return "Invalid email or password.", None
        user_id = user.id
        # Load profile for patient_id and full_name
        full_name = (user.user_metadata or {}).get("full_name") or user.email or "Patient"
        patient_id = _make_patient_id(user_id)
        try:
            row = sb.table("profiles").select("full_name, patient_id").eq("id", user_id).limit(1).execute()
            if row.data and len(row.data) > 0:
                full_name = row.data[0].get("full_name") or full_name
                patient_id = row.data[0].get("patient_id") or patient_id
        except Exception:
            pass
        return None, {
            "user_id": user_id,
            "email": user.email or email,
            "full_name": full_name,
            "patient_id": patient_id,
        }
    except Exception as e:
        msg = str(e).strip()
        if "invalid" in msg.lower() or "credentials" in msg.lower():
            return "Invalid email or password.", None
        return msg or "Login failed.", None


def _supabase_get_user() -> Optional[dict]:
    sb = _get_supabase()
    if not sb:
        return None
    try:
        res = sb.auth.get_user()
        user = res.user if hasattr(res, "user") else None
        if not user:
            return None
        user_id = user.id
        full_name = (user.user_metadata or {}).get("full_name") or user.email or "Patient"
        patient_id = _make_patient_id(user_id)
        try:
            row = sb.table("profiles").select("full_name, patient_id").eq("id", user_id).limit(1).execute()
            if row.data and len(row.data) > 0:
                full_name = row.data[0].get("full_name") or full_name
                patient_id = row.data[0].get("patient_id") or patient_id
        except Exception:
            pass
        return {
            "user_id": user_id,
            "email": user.email,
            "full_name": full_name,
            "patient_id": patient_id,
        }
    except Exception:
        return None


# --- Public API ---


def register(email: str, password: str, full_name: str) -> tuple[Optional[str], Optional[str]]:
    """Register a new patient. Returns (error_message, None) or (None, user_id)."""
    if is_supabase_configured():
        return _supabase_register(email, password, full_name)
    return _demo_register(email, password, full_name)


def login(email: str, password: str) -> tuple[Optional[str], Optional[dict]]:
    """Log in. Returns (error_message, None) or (None, user_info dict with user_id, email, full_name, patient_id)."""
    if is_supabase_configured():
        return _supabase_login(email, password)
    return _demo_login(email, password)


def get_current_user(session_user_id: Optional[str] = None) -> Optional[dict]:
    """Get current user info. In demo mode pass session_user_id from streamlit session state."""
    if is_supabase_configured():
        return _supabase_get_user()
    if session_user_id:
        return _demo_get_user(session_user_id)
    return None

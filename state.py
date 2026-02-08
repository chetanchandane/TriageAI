"""
Session state for TriageAI app.
Tracks authenticated user and current patient context for personalized messages.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class PatientContext:
    """Identifies the logged-in patient for staff and personalization."""
    user_id: str
    patient_id: str
    full_name: str
    email: str


def get_patient_context(state) -> Optional[PatientContext]:
    """Return current patient from session state, or None if not logged in."""
    if not getattr(state, "patient", None):
        return None
    return state.patient


def set_patient_context(state, user_id: str, patient_id: str, full_name: str, email: str) -> None:
    """Store the logged-in patient in session state."""
    state.patient = PatientContext(
        user_id=user_id,
        patient_id=patient_id,
        full_name=full_name,
        email=email,
    )


def clear_patient_context(state) -> None:
    """Clear patient context on logout."""
    if hasattr(state, "patient"):
        del state.patient

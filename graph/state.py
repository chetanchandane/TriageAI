"""
State definitions for TriageAI.
- TriageWorkflowState: TypedDict used by LangGraph workflow.
- PatientContext: dataclass for the logged-in patient's session info.
"""
from dataclasses import dataclass
from typing import Any, Optional, TypedDict


class TriageWorkflowState(TypedDict, total=False):
    message: str
    safety_result: dict[str, Any]
    triage_result: dict[str, Any]


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
    state.patient = None

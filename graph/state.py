"""
State definitions for TriageAI.

- TriageWorkflowState: TypedDict used by the LangGraph agentic workflow.
  Uses `add_messages` reducer so tool outputs append to a running message log
  instead of overwriting the original message.
- PatientContext: dataclass for the logged-in patient's Streamlit session info.
"""
from dataclasses import dataclass
from typing import Annotated, List, Optional, TypedDict, Union

from langgraph.graph.message import add_messages

from schemas.schemas import SafetyResult, TriageResult


# ---------------------------------------------------------------------------
# LangGraph workflow state (the agent's "working memory")
# ---------------------------------------------------------------------------

class TriageWorkflowState(TypedDict, total=False):
    # --- Inputs ---
    patient_id: str
    patient_email: str
    message: str

    # Message history for the agentic loop (HumanMessage → AIMessage → ToolMessage …)
    messages: Annotated[list, add_messages]

    # --- Structured outputs ---
    safety_result: Optional[dict]
    triage_result: Optional[dict]

    # --- Context injected by tools ---
    medical_history: Optional[str]
    policy_context: Optional[List[str]]
    draft_reply: Optional[str]

    # --- Workflow control ---
    is_emergency: bool          # Short-circuit flag from safety node
    staff_approved: bool        # For Sprint 3 HITL
    hitl_status: Optional[str]  # "pending_review", "approved", "auto_completed"


# ---------------------------------------------------------------------------
# Streamlit session helpers (unchanged from Sprint 1)
# ---------------------------------------------------------------------------

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

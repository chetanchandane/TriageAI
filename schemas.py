from pydantic import BaseModel, Field
from typing import List, Optional


class SafetyResult(BaseModel):
    """Result of safety screening. Targets 0% false negatives (never miss an emergency)."""
    is_potential_emergency: bool = Field(description="True if the message may describe a life-threatening situation")
    reason: str = Field(description="Short explanation of why it was flagged or why it was not")
    triggered_by: str = Field(description="'rules' if keyword-based rules matched, 'llm' if LLM flagged, 'none' if clear")


class TriageResult(BaseModel):
    intent: str = Field(description="The primary reason for the message (e.g., Appointment, Refill, Clinical Question)")
    confidence: float = Field(description="Confidence score between 0 and 1")
    urgency: str = Field(description="Urgency level: EMERGENCY, HIGH, NORMAL, or LOW")
    summary: str = Field(description="A 1-sentence summary of the patient's request")
    checklist: List[str] = Field(description="A list of missing information needed from the patient")
    recommended_queue: str = Field(description="The staff department to route this to (e.g., Nursing, Billing, Front Desk)")
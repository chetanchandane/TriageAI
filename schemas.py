from pydantic import BaseModel, Field
from typing import List, Optional

class TriageResult(BaseModel):
    intent: str = Field(description="The primary reason for the message (e.g., Appointment, Refill, Clinical Question)")
    confidence: float = Field(description="Confidence score between 0 and 1")
    urgency: str = Field(description="Urgency level: EMERGENCY, HIGH, NORMAL, or LOW")
    summary: str = Field(description="A 1-sentence summary of the patient's request")
    checklist: List[str] = Field(description="A list of missing information needed from the patient")
    recommended_queue: str = Field(description="The staff department to route this to (e.g., Nursing, Billing, Front Desk)")
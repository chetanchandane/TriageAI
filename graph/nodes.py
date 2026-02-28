"""
Graph node functions for the TriageAI LangGraph agentic workflow.

Nodes:
  safety_node        – Rule-based + LLM emergency screen (gatekeeper).
  triage_agent_node  – Gemini with bound MCP tools; reasons and calls tools.
  synthesis_node     – Extracts final TriageResult from the conversation context.

Tool wrappers:
  LangChain @tool wrappers around the MCP functions so ToolNode can route calls.
"""
import json
import os
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI

from graph.state import TriageWorkflowState

load_dotenv()


# ---------------------------------------------------------------------------
# LangChain tool wrappers (bound to the Gemini model for agentic tool calling)
# ---------------------------------------------------------------------------

@tool
def get_patient_history(patient_id: str) -> str:
    """Fetch the patient's medical history from Supabase given their patient_id.
    Returns the medical_history string, or empty string if not found."""
    from mcp.tools.database_tools import get_patient_history as _get
    result = _get(patient_id)
    return result if result else "No medical history on file."


@tool
def search_hospital_policy(query: str) -> str:
    """Search hospital/clinic policies using RAG (ChromaDB).
    Input a query describing what policy to look up.
    Returns relevant policy snippets."""
    from mcp.tools.rag_tools import search_hospital_policy as _search
    chunks = _search(query, top_k=3)
    return "\n---\n".join(chunks) if chunks else "No relevant policies found."


@tool
def get_available_slots() -> str:
    """Get available appointment scheduling slots.
    Returns a list of available time slots."""
    from mcp.tools.database_tools import get_available_slots as _get
    slots = _get()
    return ", ".join(slots)


# All tools available to the triage agent
TRIAGE_TOOLS = [get_patient_history, search_hospital_policy, get_available_slots]


# ---------------------------------------------------------------------------
# System prompt for the triage agent
# ---------------------------------------------------------------------------

TRIAGE_SYSTEM_PROMPT = """You are a professional Medical Triage Agent for a clinic patient portal.

Your job is to analyze a patient's message and produce a clinical triage assessment. You have access to tools that let you:
1. **get_patient_history** — Look up the patient's medical history by their patient_id.
2. **search_hospital_policy** — Search clinic policies (refill rules, appointment booking, billing, emergency protocols, etc.).
3. **get_available_slots** — Check available appointment time slots.

## Workflow
1. Read the patient's message carefully.
2. If you need the patient's medical history, call get_patient_history with their patient_id.
3. If the message relates to clinic policies (refills, appointments, billing, referrals, lab results), call search_hospital_policy with a relevant query.
4. If the patient asks about scheduling, call get_available_slots.
5. You may call multiple tools if the message has multiple intents.
6. After gathering all necessary context, produce your final triage assessment.

## Final Assessment Format
When you have enough information, respond with your final assessment as a JSON object with these fields:
- "intent": The primary reason for the message (e.g., "Appointment", "Refill", "Clinical Question", "Billing", "Multiple")
- "confidence": A float between 0 and 1
- "urgency": One of "EMERGENCY", "HIGH", "NORMAL", "LOW"
- "summary": A 1-sentence summary of the patient's request
- "checklist": A list of any missing information needed from the patient
- "recommended_queue": The staff department (e.g., "Nursing", "Pharmacy", "Billing", "Front Desk")

Wrap your final JSON in ```json ... ``` markers so it can be parsed.
Do NOT include the JSON in tool-calling responses — only in your final answer after all tools have returned."""


# ---------------------------------------------------------------------------
# Node: Safety (Gatekeeper)
# ---------------------------------------------------------------------------

def safety_node(state: TriageWorkflowState) -> dict[str, Any]:
    """
    Run the two-layer safety screen (rules + LLM).
    Sets is_emergency and safety_result. If emergency, the graph short-circuits.
    """
    from agents.safety_agent import screen_for_emergency

    msg = (state.get("message") or "").strip()
    result = screen_for_emergency(msg)

    return {
        "safety_result": result.model_dump(),
        "is_emergency": result.is_potential_emergency,
    }


# ---------------------------------------------------------------------------
# Node: Triage Agent (Reasoning + Tool Calling)
# ---------------------------------------------------------------------------

def _build_triage_model():
    """Build Gemini model with MCP tools bound for agentic reasoning."""
    api_key = os.environ.get("LLM_GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=api_key,
    )
    return llm.bind_tools(TRIAGE_TOOLS)


def triage_agent_node(state: TriageWorkflowState) -> dict[str, Any]:
    """
    Invoke Gemini with the current message history.
    The model may return tool_calls (routed to tool_node) or a final text response.
    """
    model = _build_triage_model()

    messages = list(state.get("messages") or [])

    # On first invocation, seed the conversation with system prompt + patient message
    if not messages or (len(messages) == 1 and isinstance(messages[0], HumanMessage)):
        patient_id = state.get("patient_id", "UNKNOWN")
        msg = state.get("message", "")
        safety = state.get("safety_result") or {}

        context_parts = [f"Patient ID: {patient_id}"]
        if safety.get("is_potential_emergency"):
            context_parts.append(
                f"SAFETY NOTE: This message was flagged by the safety screen. "
                f"Reason: {safety.get('reason', 'unknown')}. "
                f"Triggered by: {safety.get('triggered_by', 'unknown')}."
            )

        system_msg = SystemMessage(content=TRIAGE_SYSTEM_PROMPT)
        human_msg = HumanMessage(
            content=f"{chr(10).join(context_parts)}\n\nPatient message:\n{msg}"
        )
        messages = [system_msg, human_msg]

    response = model.invoke(messages)

    return {"messages": [response]}


# ---------------------------------------------------------------------------
# Node: Synthesis (Extract TriageResult from conversation)
# ---------------------------------------------------------------------------

def synthesis_node(state: TriageWorkflowState) -> dict[str, Any]:
    """
    Extract the final TriageResult from the conversation.

    Strategy:
    1. Try to parse JSON from the agent's last message (if it produced structured output).
    2. If no valid JSON found, make one more LLM call with structured output to extract it.
    3. Merge safety flags.
    """
    messages = state.get("messages") or []
    safety = state.get("safety_result") or {}
    original_message = state.get("message", "")

    # Collect the last AI message content
    last_ai_content = _extract_ai_content(messages)

    # Try parsing JSON from the agent's response first
    triage_result = _parse_triage_json(last_ai_content)

    # If parsing failed (got fallback), do an explicit structured extraction
    if triage_result.get("intent") == "Unknown":
        triage_result = _structured_extraction(original_message, messages, last_ai_content)

    # Merge safety flags
    if safety.get("is_potential_emergency"):
        triage_result["urgency"] = "EMERGENCY"
        triage_result["safety_flagged"] = True
        triage_result["safety_reason"] = safety.get("reason", "")
        triage_result["safety_triggered_by"] = safety.get("triggered_by", "none")

    return {"triage_result": triage_result}


def _extract_ai_content(messages: list) -> str:
    """Extract the last AI message content as a string."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            raw = msg.content or ""
            if isinstance(raw, list):
                return "\n".join(
                    p.get("text", str(p)) if isinstance(p, dict) else str(p)
                    for p in raw
                )
            return raw
    return ""


def _structured_extraction(original_message: str, messages: list, agent_response: str) -> dict:
    """
    Use Gemini with structured output to extract a TriageResult from the conversation.
    This is the explicit synthesis step when the agent produced natural language.
    """
    api_key = os.environ.get("LLM_GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return _parse_triage_json("")  # fallback

    try:
        from google import genai
        from schemas.schemas import TriageResult

        client = genai.Client(api_key=api_key)

        # Build context from the conversation
        tool_context_parts = []
        for msg in messages:
            # Collect tool results for context
            if hasattr(msg, "type") and getattr(msg, "type", "") == "tool":
                name = getattr(msg, "name", "tool")
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                tool_context_parts.append(f"[{name}]: {content[:500]}")

        tool_context = "\n".join(tool_context_parts) if tool_context_parts else "No tools were called."

        prompt = f"""Based on the following patient message and gathered context, produce a triage assessment.

Patient message:
{original_message}

Agent analysis and tool results:
{agent_response[:2000]}

Tool context:
{tool_context[:2000]}

Classify this message with intent, confidence, urgency, summary, checklist, and recommended_queue."""

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": TriageResult,
            },
        )
        if response.parsed:
            return response.parsed.model_dump()
    except Exception:
        pass

    return _parse_triage_json(agent_response)


# ---------------------------------------------------------------------------
# Node: Draft Reply (generates a policy-grounded reply for staff review)
# ---------------------------------------------------------------------------

def draft_reply_node(state: TriageWorkflowState) -> dict[str, Any]:
    """
    Generate a policy-grounded draft reply for the patient message.
    Uses the policy agent's RAG to retrieve relevant policies and draft a reply.
    """
    message = state.get("message", "")
    triage_result = state.get("triage_result") or {}

    try:
        from agents.policy_agent import get_relevant_policy, generate_draft_reply
        policy_chunks = get_relevant_policy(message, triage_result.get("summary", ""))
        draft = generate_draft_reply(message, triage_result, policy_chunks)
    except Exception:
        draft = f"Thank you for contacting us regarding: {triage_result.get('summary', 'your concern')}. A staff member will review your message shortly."

    return {"draft_reply": draft}


# ---------------------------------------------------------------------------
# Node: Communication (sends the final email — interrupted for HITL review)
# ---------------------------------------------------------------------------

def communication_node(state: TriageWorkflowState) -> dict[str, Any]:
    """
    Send the finalized draft reply to the patient via email.
    This node is interrupted (paused) for NORMAL/HIGH/EMERGENCY urgency
    so staff can review and edit the draft before it is sent.
    For LOW urgency, this node runs automatically.
    """
    from mcp.tools.communication import send_resolution_email

    patient_email = state.get("patient_email", "")
    draft_reply = state.get("draft_reply", "")
    triage_result = state.get("triage_result") or {}

    urgency = triage_result.get("urgency", "NORMAL")
    subject = f"[TriageAI] Re: {triage_result.get('summary', 'Your message')}"

    if patient_email and draft_reply:
        send_resolution_email(patient_email, subject, draft_reply)

    return {
        "staff_approved": True,
        "hitl_status": "approved",
    }


def _parse_triage_json(text: str) -> dict:
    """
    Extract a JSON object from the LLM's response text.
    Looks for ```json ... ``` blocks first, then tries raw JSON parsing.
    """
    import re

    # Try to extract from ```json ... ``` block
    match = re.search(r"```json\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find any JSON object in the text
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Fallback: return what we can from the text
    return {
        "intent": "Unknown",
        "confidence": 0.5,
        "urgency": "NORMAL",
        "summary": text[:200] if text else "Unable to parse triage result.",
        "checklist": [],
        "recommended_queue": "Front Desk",
    }

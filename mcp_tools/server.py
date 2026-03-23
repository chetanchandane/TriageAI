"""
MCP Server: Tool and resource definitions for TriageAI.
Exposes all MCP tools for agent use.
"""
# Tool implementations live in mcp/tools/
from mcp_tools.tools.database_tools import get_patient_history, get_available_slots
from mcp_tools.tools.rag_tools import search_hospital_policy
from mcp_tools.tools.communication import send_resolution_email, send_notification

__all__ = [
    "get_patient_history",
    "get_available_slots",
    "search_hospital_policy",
    "send_resolution_email",
    "send_notification",
]

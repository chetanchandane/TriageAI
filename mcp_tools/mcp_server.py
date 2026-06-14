"""
TriageAI MCP Server: exposes all triage tools as proper MCP tools.

Run via MultiServerMCPClient (stdio transport) — not invoked directly.
Registered in mcp_config.json as "triageai-tools".

Tools exposed:
  get_patient_history    — Supabase patient record lookup
  get_available_slots    — appointment slot list
  search_hospital_policy — ChromaDB RAG policy search
"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("triageai-tools")


@mcp.tool()
def get_patient_history(patient_id: str) -> str:
    """Fetch the patient's medical history from Supabase given their patient_id.
    Returns the medical_history string, or a message if not found."""
    from mcp_tools.tools.database_tools import get_patient_history as _get
    result = _get(patient_id)
    return result or "No medical history on file."


@mcp.tool()
def get_available_slots() -> str:
    """Get available appointment scheduling slots.
    Returns a comma-separated list of available time slots."""
    from mcp_tools.tools.database_tools import get_available_slots as _get
    slots = _get()
    return ", ".join(slots) if slots else "No slots available."


@mcp.tool()
def search_hospital_policy(query: str) -> str:
    """Search hospital/clinic policies using RAG (ChromaDB).
    Input a query describing the policy to look up.
    Returns relevant policy snippets separated by ---."""
    from mcp_tools.tools.rag_tools import search_hospital_policy as _search
    chunks = _search(query, top_k=3)
    return "\n---\n".join(chunks) if chunks else "No relevant policies found."


if __name__ == "__main__":
    mcp.run()

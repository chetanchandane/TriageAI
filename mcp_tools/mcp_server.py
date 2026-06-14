"""
TriageAI MCP Server: exposes get_patient_history as a proper MCP tool.

Run via MultiServerMCPClient (stdio transport) — not invoked directly.
Registered in mcp_config.json as "triageai-tools".
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


if __name__ == "__main__":
    mcp.run()

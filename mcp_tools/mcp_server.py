"""
TriageAI MCP Server: exposes all triage tools as proper MCP tools.

Transports (Sprint 9, deployment split):
  stdio (default)      — launched as a subprocess by MultiServerMCPClient via
                         mcp_config.json ("triageai-tools"). Local-dev path;
                         behavior identical to the original single-container setup.
  streamable-http      — standalone networked service (MCP_TRANSPORT=streamable-http).
                         This is the cloud path: the server runs in its own container
                         and the app connects over HTTP (see graph/workflow.py
                         _init_mcp_tools + MCP_SERVER_URL).

Decision: stdio stays the default so nothing existing breaks; HTTP mode is opt-in
via env. In HTTP mode the server adds:
  - Bearer-token auth (MCP_AUTH_TOKEN) on every MCP route. Unset token = no auth,
    with a warning — never a startup failure (fail-open convention).
  - GET /health (unauthenticated) for container health checks: reports tool count
    and ChromaDB policy-store status.

Env vars (HTTP mode):
  MCP_TRANSPORT   "stdio" (default) | "streamable-http" | "http"
  MCP_HOST        bind host, default 0.0.0.0
  MCP_PORT        bind port, default 8000
  MCP_AUTH_TOKEN  shared secret checked as "Authorization: Bearer <token>"

Tools exposed:
  get_patient_history    — Supabase patient record lookup
  get_available_slots    — appointment slot list
  search_hospital_policy — ChromaDB RAG policy search
"""
import os
import warnings

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


# ---------------------------------------------------------------------------
# Streamable-HTTP mode (standalone service)
# ---------------------------------------------------------------------------

_TOOL_NAMES = ["get_patient_history", "get_available_slots", "search_hospital_policy"]


def _policy_store_status() -> dict:
    """Cheap, fail-open ChromaDB check for /health. Never raises."""
    try:
        from agents.policy_agent import _get_collection
        coll = _get_collection()
        if coll is None:
            return {"status": "unavailable"}
        return {"status": "ok", "chunks": coll.count()}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:200]}


def _run_streamable_http() -> None:
    """Run the FastMCP server as a networked Streamable-HTTP service.

    Builds the Starlette app FastMCP provides, prepends an unauthenticated
    /health route, and (when MCP_AUTH_TOKEN is set) wraps everything else in a
    bearer-token check.
    """
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from config import get_settings

    settings = get_settings()
    host = settings.mcp_host
    port = settings.mcp_port
    token = settings.mcp_auth_token

    mcp.settings.host = host
    mcp.settings.port = port

    app = mcp.streamable_http_app()

    async def health(request):
        return JSONResponse(
            {
                "service": "triageai-tools",
                "transport": "streamable-http",
                "tools": _TOOL_NAMES,
                "policy_store": _policy_store_status(),
            }
        )

    # Insert first so /health wins over any catch-all MCP mount.
    app.router.routes.insert(0, Route("/health", health, methods=["GET"]))

    class _BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path == "/health":
                return await call_next(request)
            supplied = request.headers.get("authorization", "")
            if supplied != f"Bearer {token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    if token:
        app.add_middleware(_BearerAuth)
    else:
        warnings.warn(
            "MCP_AUTH_TOKEN not set — MCP server running WITHOUT auth. "
            "Fine locally; set it in any networked deployment.",
            stacklevel=2,
        )

    print(f"[triageai-tools] Streamable-HTTP MCP server on {host}:{port} "
          f"(auth: {'on' if token else 'OFF'})")
    uvicorn.run(app, host=host, port=port, log_level="info")


def main() -> None:
    try:
        from config import get_settings
        http_mode = get_settings().mcp_http_enabled
    except Exception:
        # config unimportable (e.g. run from an odd cwd) — fall back to raw env.
        http_mode = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower() in (
            "streamable-http", "streamable_http", "http",
        )
    if http_mode:
        _run_streamable_http()
    else:
        mcp.run()  # stdio — original behavior


if __name__ == "__main__":
    main()

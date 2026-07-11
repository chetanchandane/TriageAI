"""
Centralized runtime settings for TriageAI (Sprint 9, M3).

One typed surface for every env var the system reads, replacing ad hoc
os.environ lookups scattered across modules. Migration is incremental:
deployment-critical modules (graph/workflow.py, mcp_tools/mcp_server.py) read
from here now; remaining modules migrate as later phases touch them.

Decision: fail-open like everything else in this codebase —
  - pydantic-settings installed → validated, typed BaseSettings (env + .env file)
  - pydantic-settings missing   → plain os.environ-backed shim with identical
    attributes, so importing this module can never block startup.

Usage (lazy import inside functions, per repo convention):
    from config import get_settings
    settings = get_settings()
    settings.mcp_server_url

get_settings() is cached: env vars are process-lifetime constants in every
deploy target. Tests that monkeypatch os.environ should call
get_settings.cache_clear() afterwards.
"""
import os
from functools import lru_cache

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_ENV_FILE = os.path.join(_PROJECT_ROOT, ".env")

_TRUTHY = {"1", "true", "yes", "on"}


class _Derived:
    """Convenience properties shared by both Settings implementations."""

    @property
    def gemini_api_key(self) -> str:
        """LLM key with the GOOGLE_API_KEY fallback graph/nodes.py honors."""
        return self.llm_gemini_api_key or self.google_api_key

    @property
    def tracing_enabled(self) -> bool:
        """LangSmith tracing configured (flag + key). Mirrors the original
        graph/workflow.py:_tracing_enabled logic exactly."""
        tracing = (self.langsmith_tracing or self.langchain_tracing_v2 or "").lower()
        has_key = bool(self.langsmith_api_key or self.langchain_api_key)
        return tracing in _TRUTHY and has_key

    @property
    def mcp_http_enabled(self) -> bool:
        """True when the MCP server should run as a networked service."""
        return self.mcp_transport.strip().lower() in (
            "streamable-http", "streamable_http", "http",
        )


# Field ↔ env-var mapping is by name (case-insensitive): llm_model ↔ LLM_MODEL.
_DEFAULTS: dict[str, object] = {
    # LLM
    "llm_gemini_api_key": "",
    "google_api_key": "",
    "llm_model": "gemini-2.5-pro",
    # Persistence
    "database_url": "",
    # Supabase
    "supabase_url": "",
    "supabase_anon_key": "",
    "supabase_service_role_key": "",
    # Email
    "resend_api_key": "",
    "resend_from_email": "TriageAI <onboarding@resend.dev>",
    # MCP tool plane (Sprint 9 deployment split)
    "mcp_server_url": "",
    "mcp_auth_token": "",
    "mcp_transport": "stdio",
    "mcp_host": "0.0.0.0",
    "mcp_port": 8000,
    # LangSmith (legacy tracing path; OTel migration planned — see DEPLOYMENT_PLAN.md)
    "langsmith_tracing": "",
    "langchain_tracing_v2": "",
    "langsmith_api_key": "",
    "langchain_api_key": "",
    "langsmith_project": "TriageAI",
}

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Settings(_Derived, BaseSettings):
        model_config = SettingsConfigDict(
            env_file=_ENV_FILE,
            env_file_encoding="utf-8",
            extra="ignore",
            case_sensitive=False,
        )

        llm_gemini_api_key: str = ""
        google_api_key: str = ""
        llm_model: str = "gemini-2.5-pro"
        database_url: str = ""
        supabase_url: str = ""
        supabase_anon_key: str = ""
        supabase_service_role_key: str = ""
        resend_api_key: str = ""
        resend_from_email: str = "TriageAI <onboarding@resend.dev>"
        mcp_server_url: str = ""
        mcp_auth_token: str = ""
        mcp_transport: str = "stdio"
        mcp_host: str = "0.0.0.0"
        mcp_port: int = 8000
        langsmith_tracing: str = ""
        langchain_tracing_v2: str = ""
        langsmith_api_key: str = ""
        langchain_api_key: str = ""
        langsmith_project: str = "TriageAI"

except ImportError:  # pragma: no cover — exercised when pydantic-settings absent

    class Settings(_Derived):  # type: ignore[no-redef]
        """os.environ-backed shim. Same attributes, no validation."""

        def __init__(self, **overrides):
            try:
                from dotenv import load_dotenv
                load_dotenv(_ENV_FILE)
            except ImportError:
                pass
            for field, default in _DEFAULTS.items():
                raw = os.environ.get(field.upper())
                if raw is None:
                    value = overrides.get(field, default)
                elif isinstance(default, int):
                    try:
                        value = int(raw)
                    except ValueError:
                        value = default
                else:
                    value = raw
                setattr(self, field, value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor — the one entry point modules should use."""
    return Settings()

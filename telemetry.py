"""
OpenTelemetry tracing for TriageAI (Sprint 9, M4 — replaces LangSmith).

Context: the LangSmith free tier is exhausted, so tracing went dark. Rather
than paying for a SaaS, we instrument once with vendor-neutral OTel and pick
the backend by env var. Default backend: self-hosted Arize Phoenix (OTel-native,
one container — see deploy/docker-compose.yml, profile "observability").
Switching to Langfuse / Tempo / CloudWatch OTLP later is an env change, no code.

Design (fail-open, like everything in this codebase):
  - OTEL_ENABLED unset/false → every helper is a no-op; zero overhead, no imports.
  - opentelemetry / openinference packages missing → warn once, run untraced.
  - Each instrumentor is attempted independently: the app instruments both
    LangChain (covers the whole LangGraph run incl. app.stream) and google-genai
    (covers the direct genai.Client structured-output calls); the MCP tool
    server has neither installed and just uses manual tool_span()s.

Env vars (see config.py):
  OTEL_ENABLED                  "1"/"true" to turn tracing on
  OTEL_EXPORTER_OTLP_ENDPOINT   collector base URL, default http://localhost:6006
                                (Phoenix). "/v1/traces" is appended if missing.
  OTEL_SERVICE_NAME             logical service name, default "triageai-app"
                                (Dockerfile.mcp / compose set "triageai-mcp")

Usage:
    from telemetry import init_telemetry          # once, at service start
    from telemetry import workflow_span, span_trace_id, tool_span, log_staff_feedback
"""
import warnings
from contextlib import contextmanager

_initialized = False
_enabled = False
_tracer = None


def init_telemetry(service_name: str | None = None) -> bool:
    """Initialize the tracer provider + instrumentors. Idempotent, fail-open.

    Returns True when tracing is live. Never raises.
    """
    global _initialized, _enabled, _tracer
    if _initialized:
        return _enabled
    _initialized = True

    try:
        from config import get_settings
        settings = get_settings()
        if not settings.otel_enabled:
            return False
        endpoint = (settings.otel_exporter_otlp_endpoint or "").rstrip("/")
        if not endpoint:
            return False
        if not endpoint.endswith("/v1/traces"):
            endpoint = endpoint + "/v1/traces"
        name = service_name or settings.otel_service_name

        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": name})
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("triageai")
        _enabled = True
    except Exception as e:
        warnings.warn(f"OTel init failed — running untraced: {e}", stacklevel=2)
        return False

    # Instrumentors are best-effort and independent: the MCP image ships
    # neither langchain nor google-genai, the app image ships both.
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        LangChainInstrumentor().instrument()
    except Exception:
        pass
    try:
        from openinference.instrumentation.google_genai import GoogleGenAIInstrumentor
        GoogleGenAIInstrumentor().instrument()
    except Exception:
        pass

    print(f"[telemetry] OTel tracing on — service={name} endpoint={endpoint}")
    return True


@contextmanager
def workflow_span(name: str, attributes: dict | None = None):
    """Root span for one workflow invocation. Yields the span, or None when
    tracing is off/unavailable — callers must tolerate None."""
    if not _enabled or _tracer is None:
        yield None
        return
    try:
        with _tracer.start_as_current_span(name) as span:
            for k, v in (attributes or {}).items():
                if v is not None and v != "":
                    span.set_attribute(k, v)
            yield span
    except Exception:
        yield None


# tool_span is the same mechanics with a naming convention the Phoenix UI
# groups nicely; kept separate so call sites read clearly.
@contextmanager
def tool_span(tool_name: str, attributes: dict | None = None):
    """Manual span around an MCP tool body (used by the tool server, which has
    no auto-instrumentor). Yields the span or None."""
    if not _enabled or _tracer is None:
        yield None
        return
    try:
        with _tracer.start_as_current_span(f"tool.{tool_name}") as span:
            for k, v in (attributes or {}).items():
                if v is not None and v != "":
                    span.set_attribute(k, v)
            yield span
    except Exception:
        yield None


def span_trace_id(span) -> str:
    """Hex trace id of a span ('' when span is None/invalid)."""
    try:
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            return format(ctx.trace_id, "032x")
    except Exception:
        pass
    return ""


def log_staff_feedback(
    approved: bool,
    edit_ratio: float,
    thread_id: str = "",
    original_trace_id: str = "",
) -> None:
    """Record the staff HITL approve/edit decision as a short span.

    Replaces LangSmith create_feedback: the two signals (staff_approved,
    draft_edit_ratio) become span attributes, correlated to the original
    workflow by thread_id (always available) and original_trace_id (when the
    submit path captured one). Queryable in Phoenix. Fail-open: never raises.
    """
    if not _enabled or _tracer is None:
        return
    try:
        with _tracer.start_as_current_span("staff_review") as span:
            span.set_attribute("staff_approved", 1.0 if approved else 0.0)
            span.set_attribute("draft_edit_ratio", float(edit_ratio))
            if thread_id:
                span.set_attribute("thread_id", thread_id)
            if original_trace_id:
                span.set_attribute("original_trace_id", original_trace_id)
    except Exception:
        pass

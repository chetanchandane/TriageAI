# TriageAI — agentic clinical-triage system (RIT capstone)
#
# This Dockerfile lives at the repo ROOT because Hugging Face Spaces only builds
# a Dockerfile from the repository root. It is also used by Render and by
# deploy/docker-compose.yml (context: repo root). One image, all targets.
#
# Single-image deploy: the Streamlit UI process also spawns the two MCP servers
# as stdio subprocesses (chroma-mcp-server + `python -m mcp_tools.mcp_server`),
# so everything runs in one container. See graph/workflow.py:_init_mcp_tools.
#
# Multi-stage: the C/C++ toolchain (~250 MB) only COMPILES wheels at install
# time. We build into a venv in a throwaway stage, then copy just the venv into
# a clean runtime stage that carries no compiler.
#
# Hugging Face Spaces runs the container as UID 1000, so we create a non-root
# "user", set WORKDIR under its $HOME, and COPY --chown=user — otherwise the
# boot-time reseed (writing data/vector_store) hits permission-denied.

# ---------------------------------------------------------------------------
# Stage 1: builder — compile/install all Python deps into an isolated venv
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Self-contained venv at a fixed path so it copies cleanly into the runtime
# stage (both stages share the python:3.11-slim base, so the venv stays valid).
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /tmp/build
# Sprint 9: images install the slim app set, not the dev aggregate
# (requirements.txt now pulls in eval/benchmark tooling for local dev).
COPY requirements/base.txt requirements/app.txt requirements/
RUN pip install -r requirements/app.txt

# ---------------------------------------------------------------------------
# Stage 2: runtime — slim image, non-root UID 1000, venv + app (no compiler)
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Runtime-only shared libs the compiled extensions link against:
#   libgomp1   — OpenMP, required by onnxruntime (ChromaDB's default embedder)
#   libstdc++6 — C++ runtime for chromadb/hnswlib native modules
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user required by Hugging Face Spaces (and harmless elsewhere).
RUN useradd -m -u 1000 user

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/user \
    PATH="/opt/venv/bin:$PATH"

# Bring in the prebuilt venv (chroma-mcp-server, streamlit, python all live here).
# Stays root-owned and world-readable — the app only reads/executes it.
COPY --from=builder /opt/venv /opt/venv

# Switch to the non-root user BEFORE WORKDIR so /home/user/app is created owned
# by the user (otherwise the boot-time mkdir/seed can't write into it).
USER user
WORKDIR /home/user/app

# App code, owned by the runtime user. .dockerignore keeps .env, venvs, logs,
# and the local vector store out (but always includes data/policies/).
COPY --chown=user . .

# Bake the policy store AND the embedding model into the image at build time so
# cold starts are instant and offline-safe. This:
#   1. seeds ChromaDB from data/policies/ → data/vector_store/ (a real image layer;
#      .dockerignore only excludes the host copy from COPY, not RUN-created files), and
#   2. downloads ChromaDB's default ONNX embedder into $HOME/.cache/chroma — needed
#      at runtime to embed queries even when boot-time seeding is skipped.
# Runs as the UID-1000 user with HOME=/home/user, so store + cache are user-owned
# and reused at runtime. Uses the local embedder only — no LLM key required here.
# The downloaded model tarball is removed after extraction to trim image size.
RUN python scripts/seed_policy.py --force \
    && (find /home/user/.cache/chroma -name '*.tar.gz' -delete 2>/dev/null || true)

# HF Spaces: declare app_port: 8501 in README frontmatter. $PORT is honored for
# Render/other PaaS; falls back to 8501 locally and on HF.
EXPOSE 8501

# Streamlit's own health endpoint, checked via the venv python (no curl needed).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://localhost:%s/_stcore/health' % os.environ.get('PORT','8501'), timeout=4)" || exit 1

# Seed the policy store before launching the UI, so the chroma-mcp-server
# subprocess finds populated collections.
ENTRYPOINT ["/home/user/app/deploy/docker-entrypoint.sh"]

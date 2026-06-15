# TriageAI — agentic clinical-triage system (RIT capstone)
#
# Single-image deploy: the Streamlit UI process also spawns the two MCP servers
# as stdio subprocesses (chroma-mcp-server + `python -m mcp_tools.mcp_server`),
# so everything runs inside one container. See graph/workflow.py:_init_mcp_tools.
FROM python:3.11-slim

# - build-essential/gcc: chromadb + psycopg[binary] pull native wheels, but some
#   transitive deps still compile; keep a compiler available to be safe.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first so the layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code. .dockerignore keeps .env, venvs, logs, and the local vector store out.
COPY . .

# Persistent data lives on volumes (declared in docker-compose.yml):
#   data/vector_store  — ChromaDB policy store (chroma-mcp-server reads it)
#   data/checkpoints.db — SqliteSaver HITL checkpoints (unless DATABASE_URL is set)
RUN mkdir -p data/vector_store

EXPOSE 8501

# Streamlit's own health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

# Seed the policy store (idempotent) before launching the UI, so the
# chroma-mcp-server subprocess finds populated collections.
ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]

#!/usr/bin/env bash
# Entrypoint: seed ChromaDB (idempotent) then launch Streamlit.
#
# Seeding runs BEFORE Streamlit so it finishes before build_graph() spawns the
# chroma-mcp-server subprocess against the same persistent store (avoids two
# processes opening the Chroma sqlite at once on first boot).
set -euo pipefail

echo "[entrypoint] Seeding policy store (idempotent)..."
python scripts/seed_policy.py || echo "[entrypoint] seed_policy failed (continuing; policy_agent inline-seeds on empty store)"

echo "[entrypoint] Starting Streamlit on :8501..."
exec streamlit run app/streamlit_app.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true

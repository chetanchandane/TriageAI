#!/usr/bin/env bash
# Run TriageAI Streamlit app using the project venv (myenv) if present.
set -e
cd "$(dirname "$0")"
if [ -d "myenv" ]; then
  exec ./myenv/bin/python -m streamlit run streamlit_app.py "$@"
else
  exec python3 -m streamlit run streamlit_app.py "$@"
fi

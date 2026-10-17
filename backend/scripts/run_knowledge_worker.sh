#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${AIWS_PYTHON:-${PYTHON_BIN:-$BACKEND_DIR/.venv/bin/python}}"
export PYTHONPATH="${BACKEND_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
exec "${PYTHON_BIN}" "${SCRIPT_DIR}/run_knowledge_worker.py"

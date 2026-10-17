#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${AIWS_PYTHON:-${AGENT_WORKER_PYTHON:-$PWD/.venv/bin/python}}" -m scripts.run_agent_tool_worker

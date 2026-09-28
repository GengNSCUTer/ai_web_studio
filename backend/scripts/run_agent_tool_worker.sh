#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${AGENT_WORKER_PYTHON:-python}" -m scripts.run_agent_tool_worker

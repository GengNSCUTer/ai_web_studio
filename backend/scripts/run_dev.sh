#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="/disk2/gengnan/conda_envs/ai_web_studio/bin/python"

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 32007

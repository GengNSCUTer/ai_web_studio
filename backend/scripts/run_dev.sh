#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="/disk2/gengnan/conda_envs/ai_web_studio/bin/python"

cd "$ROOT_DIR"
export PYTHONPATH=.
exec "$PYTHON_BIN" -c 'import uvicorn; from app.core.config import settings; uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port)'

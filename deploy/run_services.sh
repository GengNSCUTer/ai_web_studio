#!/usr/bin/env bash
set -euo pipefail
export AIWS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AIWS_PYTHON="${AIWS_PYTHON:-$AIWS_ROOT/backend/.venv/bin/python}"
export AIWS_NPM="${AIWS_NPM:-$(command -v npm)}"
export AIWS_FRONTEND_PORT="${AIWS_FRONTEND_PORT:-32008}"
export AIWS_RUN_DIR="${AIWS_RUN_DIR:-$AIWS_ROOT/.runtime}"
export AIWS_MANAGE_REDIS="${AIWS_MANAGE_REDIS:-false}"
export AIWS_REDIS_BIN="${AIWS_REDIS_BIN:-redis-server}"
export AIWS_REDIS_PORT="${AIWS_REDIS_PORT:-36379}"
umask 077
mkdir -p "$AIWS_RUN_DIR"
mkdir -p "$AIWS_RUN_DIR/redis"
if [[ "${1:-}" == "ctl" ]]; then
    shift
    exec "$AIWS_PYTHON" -m supervisor.supervisorctl -c "$AIWS_ROOT/deploy/supervisord.conf" "$@"
fi
exec "$AIWS_PYTHON" -m supervisor.supervisord -n -c "$AIWS_ROOT/deploy/supervisord.conf"

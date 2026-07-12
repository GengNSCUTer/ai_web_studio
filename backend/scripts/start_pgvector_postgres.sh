#!/usr/bin/env bash
set -euo pipefail

# Keep the PostgreSQL 18 + pgvector candidate isolated from the PostgreSQL 12
# rollback cluster until the application migration is complete.
PG_RUNTIME="${PG_RUNTIME:-/disk2/gengnan/conda_envs/pgvector_runtime}"
PGDATA="${PGDATA:-/disk2/gengnan/ai_web_studio_runtime/pgdata18}"
PGLOG="${PGLOG:-/disk2/gengnan/ai_web_studio_runtime/postgres18.log}"
PGPORT="${PGPORT:-35433}"

mkdir -p "$(dirname "$PGLOG")"

if "$PG_RUNTIME/bin/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then
  echo "PostgreSQL + pgvector is already running on port $PGPORT."
  exit 0
fi

"$PG_RUNTIME/bin/pg_ctl" \
  -D "$PGDATA" \
  -l "$PGLOG" \
  -o "-p $PGPORT -h 127.0.0.1" \
  start

"$PG_RUNTIME/bin/pg_isready" -h 127.0.0.1 -p "$PGPORT" -U ligengnan

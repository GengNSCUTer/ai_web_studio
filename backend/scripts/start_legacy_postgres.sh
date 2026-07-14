#!/usr/bin/env bash
set -euo pipefail

# Rollback-only PostgreSQL 12 cluster. The application default is PostgreSQL 18
# with pgvector on 35433; use this script only during an explicit rollback.
PG_CTL="/disk2/gengnan/conda_envs/pg_runtime/bin/pg_ctl"
PGDATA="/disk2/gengnan/rag_agent/.pgdata"
PGLOG="/disk2/gengnan/rag_agent/.pglogs/postgres.log"

mkdir -p "$(dirname "$PGLOG")"

if "$PG_CTL" -D "$PGDATA" status >/dev/null 2>&1; then
  echo "Legacy PostgreSQL 12 is already running on port 35432."
  exit 0
fi

"$PG_CTL" -D "$PGDATA" -l "$PGLOG" -o "-p 35432 -h 127.0.0.1" start

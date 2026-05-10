#!/usr/bin/env bash
set -euo pipefail

PG_CTL="/disk2/gengnan/conda_envs/pg_runtime/bin/pg_ctl"
PGDATA="/disk2/gengnan/rag_agent/.pgdata"
PGLOG="/disk2/gengnan/rag_agent/.pglogs/postgres.log"

mkdir -p "$(dirname "$PGLOG")"
"$PG_CTL" -D "$PGDATA" -l "$PGLOG" -o "-p 35432 -h 127.0.0.1" start

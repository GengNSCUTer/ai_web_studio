#!/usr/bin/env bash
set -euo pipefail

PG_CTL="/disk2/gengnan/conda_envs/pg_runtime/bin/pg_ctl"
PGDATA="/disk2/gengnan/rag_agent/.pgdata"

"$PG_CTL" -D "$PGDATA" stop

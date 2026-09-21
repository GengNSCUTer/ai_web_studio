#!/usr/bin/env bash
set -euo pipefail

PG_CTL="/disk2/gengnan/conda_envs/pgvector_runtime/bin/pg_ctl"
PGDATA="/disk2/gengnan/ai_web_studio_runtime/pgdata18"

"$PG_CTL" -D "$PGDATA" -m fast stop

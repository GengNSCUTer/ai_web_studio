#!/usr/bin/env bash
set -euo pipefail

# AI Web Studio 的唯一运行数据库：PostgreSQL 18 + pgvector，固定监听 35433。
# 旧 PostgreSQL 12 数据目录只保留为离线备份，不在本项目中提供启动入口。
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

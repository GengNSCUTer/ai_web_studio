"""按 ChatRun 汇总选择缓存与向量请求的开销，只输出聚合指标。"""

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import SessionLocal
from app.models.chat_run import ChatRun


def report(days):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    summary = defaultdict(lambda: dict(turns=0, eligible=0, hits=0, capacity_clears=0, embedding_calls=0,
                                      embedding_ms=0., recheck_ms=0.))
    request_kind = cast(ChatRun.request_json, JSONB)["kind"].astext
    diagnostics = cast(ChatRun.context_json, JSONB)["details"]["memory_retrieval"]
    with SessionLocal() as db:
        for kind, memory in db.execute(select(request_kind, diagnostics).where(
            ChatRun.created_at >= cutoff, diagnostics.has_key("read_cache_capacity_clear"),
        ).execution_options(yield_per=200)):
            state = memory.get("read_cache")
            row = summary[kind or "unknown"]
            row["turns"] += 1
            row["capacity_clears"] += memory.get("read_cache_capacity_clear", 0)
            row["embedding_calls"] += memory.get("embedding_calls", 0)
            row["embedding_ms"] += memory.get("embedding_ms", 0.)
            if state in {"hit", "miss", "stale", "unavailable", "invalid", "oversize"}:
                row["eligible"] += 1
            if state == "hit":
                row["hits"] += 1
                row["recheck_ms"] += memory.get("read_cache_recheck_ms", 0.)
    for row in summary.values():
        embedding_ms, recheck_ms = row.pop("embedding_ms"), row.pop("recheck_ms")
        row["hit_rate"] = round(row["hits"] / row["eligible"], 4) if row["eligible"] else None
        row["mean_embedding_ms"] = round(embedding_ms / row["embedding_calls"], 2) if row["embedding_calls"] else None
        row["mean_hit_recheck_ms"] = round(recheck_ms / row["hits"], 2) if row["hits"] else None
    return {"days_requested": days, "by_request_kind": dict(summary)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()
    print(json.dumps(report(max(args.days, 1)), ensure_ascii=False, indent=2))

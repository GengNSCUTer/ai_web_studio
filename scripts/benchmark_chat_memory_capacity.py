"""用独立 PG 库测试历史/记忆读取；不访问模型、不修改真实用户资料。"""

from __future__ import annotations

import argparse
import asyncio
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import resource
import cProfile
import subprocess
from threading import Lock, local
from time import perf_counter
from time import process_time
from uuid import uuid4

import app.models  # noqa: F401
from sqlalchemy import create_engine, event, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.models.user_memory import UserMemory
from app.models.user_setting import UserSetting
from app.repositories.message_repo import MessageRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.services.memory_retrieval_service import MemoryRetrievalService, text_hash
from app.services.memory_read_cache import MemoryReadCache


def percentile(values, ratio):
    values = sorted(values)
    return round(values[min(len(values) - 1, int((len(values) - 1) * ratio))], 3) if values else None


def run(args):
    url = make_url(settings.sqlalchemy_database_uri)
    database = f"aiws_capacity_{uuid4().hex}"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    engine = None
    tls = local()
    metrics = ContextVar("capacity_metrics", default=None)
    pool_lock = Lock()
    pool_metrics = dict(current=0, peak=0)
    report = dict(label=args.label, commit=subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True).strip(),
        scope="独立PG合成读取，不含HTTP/模型，不代表上线容量", requests_per_cell=args.requests,
        pool=dict(size=5, max_overflow=10), cache=args.cache, cases=[], plans=[])
    report.update(unique_queries=args.unique_queries,
                  service_pool_wait="未单独插桩；null 表示未测，不是零等待")
    config = dict(provider="siliconflow", model="BAAI/bge-m3", dimensions=1024,
                  base_url="https://api.siliconflow.cn/v1")
    signature = MemoryRetrievalService.signature(config)
    vector = [0.01 * (j % 7 + 1) for j in range(1024)]

    class FakeEmbedding:
        async def embed_texts(self, **kwargs):
            return [vector for _ in kwargs["texts"]]
    try:
        with admin.connect() as conn:
            conn.execute(text(f'create database "{database}"'))
        engine = create_engine(url.set(database=database), pool_pre_ping=True)
        with engine.begin() as conn:
            conn.execute(text("create extension if not exists vector"))
        Base.metadata.create_all(engine)

        @event.listens_for(engine, "checkout")
        def checkout(connection, record, proxy):
            with pool_lock:
                pool_metrics["current"] += 1
                pool_metrics["peak"] = max(pool_metrics["peak"], pool_metrics["current"])

        @event.listens_for(engine, "checkin")
        def checkin(connection, record):
            with pool_lock:
                pool_metrics["current"] -= 1

        @event.listens_for(engine, "before_cursor_execute")
        def before(conn, cursor, statement, parameters, context, executemany):
            context.capacity_start = perf_counter()

        @event.listens_for(engine, "after_cursor_execute")
        def after(conn, cursor, statement, parameters, context, executemany):
            if hasattr(tls, "sql_ms"):
                tls.sql_ms += (perf_counter() - context.capacity_start) * 1000
                tls.queries += 1
            counters = metrics.get()
            if counters is not None:
                counters["sql_ms"] += (perf_counter() - context.capacity_start) * 1000
                counters["queries"] += 1

        scenarios = []
        now = datetime.now(timezone.utc)
        for count in (100, 1000, 10000):
            uid, cid = str(uuid4()), str(uuid4())
            with Session(engine) as db:
                db.add(User(id=uid, username=f"bench-{uid[:12]}", email=f"{uid}@example.test"))
                db.flush()
                db.add(Conversation(id=cid, user_id=uid, model_name="synthetic", title="压测临时会话"))
                db.flush()
                db.execute(insert(Message), [dict(id=str(uuid4()), conversation_id=cid,
                    role="user" if i % 2 == 0 else "assistant", sequence=i + 1,
                    content="合成历史资料，仅用于数据库读取测试。" * 32,
                    generation_id=str(uuid4()), status="done", created_at=now + timedelta(microseconds=i))
                    for i in range(count)])
                db.commit()
            scenarios.append(("history", count, uid, cid))
        for count in (50, 500, 1000):
            uid = str(uuid4())
            with Session(engine) as db:
                db.add(User(id=uid, username=f"bench-{uid[:12]}", email=f"{uid}@example.test"))
                db.flush()
                db.add(UserSetting(user_id=uid, memory_enabled=True,
                    knowledge_embedding_provider=config["provider"], knowledge_embedding_model=config["model"],
                    knowledge_embedding_dimensions=1024, knowledge_embedding_base_url=config["base_url"]))
                db.execute(insert(UserMemory), [dict(id=str(uuid4()), user_id=uid,
                    title=f"项目约定 {i}", content="合成记忆正文，不包含真实用户信息。" * 32,
                    status="active", is_enabled=True, version=1, memory_type="fact",
                    embedding_vector=json.dumps([0.01 * (j % 7 + 1) for j in range(1024)])) for i in range(count)])
                db.commit()
                # 真正服务路径复用已建立的向量，排除在线 API 延迟，仅测数据库与应用处理成本。
                for row in UserMemoryRepository(db).list_for_retrieval(uid, None):
                    row.embedding_signature = signature
                    row.embedding_text_hash = text_hash(row)
                db.commit()
            scenarios.append(("memory", count, uid, None))
            scenarios.append(("memory_retrieval", count, uid, None))

        for kind, count, uid, cid in scenarios:
            if args.only and kind not in args.only:
                continue
            if args.sizes and count not in args.sizes:
                continue
            if kind == "history":
                with engine.connect() as connection:
                    plan = connection.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT id FROM messages "
                        "WHERE conversation_id=:cid ORDER BY created_at DESC, sequence DESC NULLS FIRST, id DESC LIMIT 24"),
                        {"cid": cid}).scalar()
                    report["plans"].append(dict(kind=kind, rows=count, explain=plan))
            reader = MemoryRetrievalService(session_factory=lambda: Session(engine), embedding_service=FakeEmbedding(),
                read_cache=MemoryReadCache(enabled=args.cache == "on"))
            if args.profile_dir and kind == "memory_retrieval":
                args.profile_dir.mkdir(parents=True, exist_ok=True)
                def profiled(name, function):
                    def call(*values, **kwargs):
                        with cProfile.Profile() as profile:
                            result = function(*values, **kwargs)
                        profile.dump_stats(str(args.profile_dir / f"{name}.pstats"))
                        return result
                    return call
                for name in ("_snapshot", "_cached_vectors", "_rank", "_finalize"):
                    setattr(reader, name, profiled(name, getattr(reader, name)))
            def request(_):
                start = perf_counter()
                tls.sql_ms, tls.queries = 0.0, 0
                counters = dict(sql_ms=0., queries=0)
                metrics.set(counters)
                try:
                    if kind == "memory_retrieval":
                        result = asyncio.run(reader.retrieve(user_id=uid, project_id=None,
                            query=f"项目约定有哪些？{uuid4().hex if args.unique_queries else ''}", max_chars=4000))
                        return dict(ms=(perf_counter()-start)*1000, wait_ms=None,
                            sql_ms=counters["sql_ms"], queries=counters["queries"],
                            read_rows=result.diagnostics.get("read_rows", result.diagnostics.get("eligible_count", 0)),
                            selected=result.count, cache=result.diagnostics.get("read_cache", "disabled"))
                    with Session(engine) as db:
                        db.connection()
                        wait = (perf_counter() - start) * 1000
                        if kind == "history":
                            repo = MessageRepository(db)
                            if args.mode == "current" and hasattr(repo, "list_for_context"):
                                conv = db.get(Conversation, cid)
                                rows = repo.list_for_context(conv)
                            else:
                                rows = repo.list_by_conversation(cid)
                        else:
                            rows = UserMemoryRepository(db).list_for_retrieval(uid, None)
                        read_rows = len(rows)
                    return dict(ms=(perf_counter()-start)*1000, wait_ms=wait,
                                sql_ms=tls.sql_ms, queries=tls.queries, read_rows=read_rows)
                except Exception as exc:
                    return dict(error=type(exc).__name__)
            warm = request(None)
            for concurrency in args.concurrency:
                with pool_lock:
                    pool_metrics["peak"] = pool_metrics["current"]
                start = perf_counter()
                cpu = process_time()
                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    records = list(executor.map(request, range(args.requests)))
                elapsed = perf_counter() - start
                ok = [record for record in records if "error" not in record]
                result = dict(kind=kind, stored_rows=count, concurrency=concurrency,
                    failures=len(records)-len(ok), p50_ms=percentile([r["ms"] for r in ok], .5),
                    p95_ms=percentile([r["ms"] for r in ok], .95),
                    p99_ms=percentile([r["ms"] for r in ok], .99),
                    pool_wait_p95_ms=percentile([r["wait_ms"] for r in ok if r["wait_ms"] is not None], .95),
                    sql_p95_ms=percentile([r["sql_ms"] for r in ok], .95),
                    read_rows=sorted(set(r["read_rows"] for r in ok)),
                    sql_queries=sorted(set(r["queries"] for r in ok)),
                    requests_per_second=round(len(records)/elapsed, 2),
                    process_cpu_seconds=round(process_time()-cpu, 3),
                    peak_checked_out_connections=pool_metrics["peak"],
                    remaining_checked_out_connections=pool_metrics["current"],
                    warmup_ms=round(warm.get("ms", 0), 3),
                    read_cache_states=sorted(set(r.get("cache", "not_applicable") for r in ok)))
                report["cases"].append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
        report["max_rss_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    finally:
        if engine:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'drop database if exists "{database}" with (force)'))
        admin.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--mode", choices=("legacy", "current"), default="legacy")
    parser.add_argument("--requests", type=int, default=24)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", nargs="*", choices=("history", "memory", "memory_retrieval"))
    parser.add_argument("--cache", choices=("on", "off"), default="off")
    parser.add_argument("--sizes", nargs="*", type=int)
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 8, 16])
    parser.add_argument("--unique-queries", action="store_true")
    parser.add_argument("--profile-dir", type=Path)
    run(parser.parse_args())

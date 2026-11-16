"""对比串行参考与并发召回；受控耗时和在线API结果分别报告，不外推生产SLA。"""

import argparse
import asyncio
import json
import math
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalPipeline, KnowledgeRetrievalFilter
from app.services.knowledge_index_service import KnowledgeLexicalStore, LexicalChunkSnapshot


class SerialReference(KnowledgeRetrievalPipeline):
    """使用相同检索/融合函数串行执行，不复制历史业务算法或修改生产开关。"""

    async def _retrieve_async(self, **kwargs):
        filters = kwargs.pop("filters", None) or KnowledgeRetrievalFilter()
        kb = kwargs["knowledge_base"]
        assert not kb.rerank_enabled and self.enhancement == "none"
        args = dict(**kwargs, filters=filters, index_generation=self._active_generation(kb))
        vector = await self._retrieve_vector_results(**args)
        lexical = await self._retrieve_lexical_results(**args)
        return self._fuse_rrf(vector_results=vector, lexical_results=lexical, top_k=kwargs["top_k"])


def signature(results):
    return [(r.chunk.id, round(r.score, 7), r.rank_source) for r in results]


def distribution(values):
    values = sorted(values)
    return {"samples": len(values), "mean_ms": round(statistics.mean(values), 2),
            "p50_ms": round(statistics.median(values), 2), "p95_ms": round(values[math.ceil(.95 * len(values)) - 1], 2)}


async def compare(serial, concurrent, args, rounds):
    times = {"serial": [], "concurrent": []}
    equal = 0
    for i in range(rounds):
        outputs = {}
        # 交错顺序，减少文件缓存和远端波动总是偏向某一组。
        pairs = [("serial", serial), ("concurrent", concurrent)]
        for name, pipeline in pairs if i % 2 == 0 else reversed(pairs):
            start = time.perf_counter()
            outputs[name] = await pipeline.retrieve_async(**args)
            times[name].append((time.perf_counter() - start) * 1000)
        if signature(outputs["serial"]) == signature(outputs["concurrent"]):
            equal += 1
    assert equal == rounds, "召回排序不一致，不应继续发布"
    return {name: distribution(values) for name, values in times.items()} | {"ranking_equal_pairs": equal}


async def controlled(rounds):
    owner = threading.get_ident()
    chunks = [LexicalChunkSnapshot(f"c{i}", "doc", i, i, f"retrieval evidence routing {i}", "{}") for i in range(1000)]
    by_id = {chunk.vector_id: chunk for chunk in chunks}
    class Repo:
        def list_by_vector_ids_and_knowledge_base(self, **kwargs):
            assert threading.get_ident() == owner
            return [by_id[i] for i in kwargs["vector_ids"]]
    class Embedding:
        async def embed_texts(self, **kwargs):
            await asyncio.sleep(.08)
            return [[1., 0., 0.]]
    class Vector:
        def search(self, **kwargs):
            return [SimpleNamespace(chunk=c, score=.9) for c in chunks[:kwargs["top_k"]]]
    class Store(KnowledgeLexicalStore):
        delay = 0
        def search(self, **kwargs):
            time.sleep(self.delay)
            return super().search(**kwargs)
    with tempfile.TemporaryDirectory() as root:
        store = Store(root)
        store.rebuild(knowledge_base_id="kb", generation_id="g", chunks=chunks)
        kb = SimpleNamespace(id="kb", retrieval_mode="hybrid", rerank_enabled=False, rerank_top_n=0,
            active_index_generation="g", embedding_dimensions=3, score_threshold=0)
        shared = dict(chunk_repo=Repo(), setting_service=SimpleNamespace(), embedding_service=Embedding(),
            vector_search=Vector(), lexical_store=store, enhancement="none")
        args = dict(user_id="owner", knowledge_base=kb, query="retrieval evidence", top_k=20)
        report = {}
        for name, delay in (("real_local_bm25", 0), ("controlled_60ms_lexical_delay", .06)):
            store.delay = delay
            report[name] = await compare(SerialReference(**shared), KnowledgeRetrievalPipeline(**shared), args, rounds)
        return {"embedding": "受控80ms网络等待", "chunks": 1000, "top_k": 20, "groups": report}


async def online(rounds):
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.user import User
    from app.models.knowledge import KnowledgeBase, KnowledgeChunk
    from app.repositories.knowledge_repo import KnowledgeChunkRepository
    from app.repositories.setting_repo import UserSettingRepository
    from app.services.setting_service import SettingService
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "1528713326@qq.com"))
        assert user is not None
        bases = list(db.scalars(select(KnowledgeBase).where(KnowledgeBase.user_id == user.id,
            KnowledgeBase.embedding_provider != "ollama",
            KnowledgeBase.id.in_(select(KnowledgeChunk.knowledge_base_id))).order_by(KnowledgeBase.created_at).limit(2)))
        assert len(bases) == 2, "需要两个已索引、配置在线Embedding的知识库"
        report = []
        for base in bases:
            # 只复制配置，不修改正式知识库的模式、阈值和精排设置。
            kb = SimpleNamespace(**{column.key: getattr(base, column.key) for column in KnowledgeBase.__table__.columns})
            kb.retrieval_mode, kb.rerank_enabled, kb.score_threshold = "hybrid", False, 0
            shared = dict(chunk_repo=KnowledgeChunkRepository(db),
                setting_service=SettingService(UserSettingRepository(db)), enhancement="none")
            args = dict(user_id=user.id, knowledge_base=kb, query="混合检索与数据库事务分别解决什么问题？", top_k=20)
            row = await compare(SerialReference(**shared), KnowledgeRetrievalPipeline(**shared), args, rounds)
            report.append({"knowledge_base_name": kb.name, "embedding_model": kb.embedding_model,
                "top_k": 20, "result": row})
        return {"scope": "固定账号两库；真实在线Embedding/PG/BM25；未开启Rerank，远端延时波动不可当受控提速", "groups": report}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--output", default="/tmp/aiws-hybrid-concurrency.json")
    args = parser.parse_args()
    if not 1 <= args.rounds <= 30:
        parser.error("rounds必须在1至30之间")
    report = {"date": "2026-11-16", "controlled": asyncio.run(controlled(args.rounds))}
    if args.online:
        try:
            report["online"] = asyncio.run(online(args.rounds))
        except Exception as exc:
            # 保留已完成的受控结果，不输出可能携带凭据/响应正文的第三方异常。
            report["online"] = {"status": "not_passed", "error_type": type(exc).__name__,
                "http_status": getattr(exc, "status_code", None)}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

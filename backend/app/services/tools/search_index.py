"""短事务维护工具说明与向量，数据库只返回有界候选，不向Web搬运全目录向量。"""

from dataclasses import dataclass
import json
import re

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.core.database import SessionLocal
from app.models.tool_search import ANN_DIMENSIONS, ToolSearchEntry


def search_terms(value: str) -> list[str]:
    """英文按词、中文按双字片段；不冒充BM25或通用中文分词器。"""
    terms = []
    stop = {"the", "and", "for", "from", "with", "this", "that", "what", "how", "please", "tool"}
    for part in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", value.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            terms.extend(part[i:i + 2] for i in range(max(1, len(part) - 1)))
        elif len(part) >= 2 and part not in stop:
            terms.append(part)
    return list(dict.fromkeys(terms))[:512]


@dataclass(frozen=True)
class ToolSearchDocument:
    tool_key: str
    category: str
    text: str

    @property
    def lexical_text(self):
        return " ".join(search_terms(f"{self.tool_key} {self.category} {self.text}"))


class PostgresToolSearchIndex:
    EXACT_THRESHOLD = 128

    def __init__(self, session_factory=SessionLocal):
        self.sessions = session_factory

    @staticmethod
    def _deadline(db):
        # to_thread取消不会中止已开始的SQL，数据库自身也必须有截止时间。
        db.execute(text("SET LOCAL statement_timeout = '2500ms'"))
        db.execute(text("SET LOCAL lock_timeout = '500ms'"))

    def prepare(self, config, documents: list[ToolSearchDocument]) -> set[str]:
        if not documents:
            return set()
        scope = config.scope()
        with self.sessions() as db:
            self._deadline(db)
            rows = db.execute(select(ToolSearchEntry.tool_key, ToolSearchEntry.metadata_text,
                ToolSearchEntry.category, ToolSearchEntry.embedding.is_not(None)).where(
                ToolSearchEntry.user_id == config.user_id, ToolSearchEntry.scope == scope,
                ToolSearchEntry.tool_key.in_([d.tool_key for d in documents]))).all()
            old = {r[0]: r[1:] for r in rows}
            ready, changed = set(), []
            for document in documents:
                previous = old.get(document.tool_key)
                if previous and previous[0] == document.text and previous[1] == document.category:
                    if previous[2]:
                        ready.add(document.tool_key)
                else:
                    changed.append(dict(scope=scope, user_id=config.user_id, tool_key=document.tool_key,
                        category=document.category, metadata_text=document.text, lexical_text=document.lexical_text,
                        dimensions=config.dimensions, embedding=None))
            # 固定顺序缩短同目录并发更新时的锁等待；同说明不触碰向量。
            changed.sort(key=lambda row: row["tool_key"])
            for offset in range(0, len(changed), 200):
                statement = insert(ToolSearchEntry).values(changed[offset:offset + 200])
                db.execute(statement.on_conflict_do_update(index_elements=["scope", "tool_key"],
                    set_={"metadata_text": statement.excluded.metadata_text,
                          "lexical_text": statement.excluded.lexical_text, "category": statement.excluded.category,
                          "embedding": None, "updated_at": func.now()},
                    where=(ToolSearchEntry.metadata_text != statement.excluded.metadata_text)
                          | (ToolSearchEntry.category != statement.excluded.category)))
            db.commit()
        return ready

    def save(self, config, documents, vectors):
        saved = set()
        with self.sessions() as db:
            self._deadline(db)
            for document, vector in zip(documents, vectors):
                # 远端Embedding可能迟到：只填仍对应同一份说明的条目，不覆盖新说明。
                result = db.execute(update(ToolSearchEntry).where(ToolSearchEntry.user_id == config.user_id,
                    ToolSearchEntry.scope == config.scope(), ToolSearchEntry.tool_key == document.tool_key,
                    ToolSearchEntry.metadata_text == document.text, ToolSearchEntry.category == document.category
                ).values(embedding=vector, updated_at=func.now()))
                if result.rowcount:
                    saved.add(document.tool_key)
            db.commit()
        return saved

    @staticmethod
    def _parameters(config, documents):
        return {"user": config.user_id, "scope": config.scope(), "keys": [d.tool_key for d in documents],
                "expected": json.dumps({d.tool_key: d.text for d in documents}, ensure_ascii=False)}

    _WHERE = ("user_id = :user AND scope = :scope AND tool_key = ANY(:keys) "
              "AND metadata_text = (CAST(:expected AS jsonb) ->> tool_key)")

    @staticmethod
    def _configure_ann(db):
        db.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
        db.execute(text("SET LOCAL hnsw.ef_search = 100"))
        # 当前说明匹配的选择率难估计，会让优化器误选全表排序；只对明确ANN路径
        # 降低显式排序优先级，事务结束自动恢复，不影响其他SQL或小目录。
        db.execute(text("SET LOCAL enable_sort = off"))

    def lexical(self, config, documents, query, categories=(), limit=30):
        if not documents:
            return []
        terms = search_terms(query)[:64]
        with self.sessions() as db:
            self._deadline(db)
            parameters = {**self._parameters(config, documents), "query": " | ".join(terms),
                          "categories": list(categories), "limit": limit}
            rows = db.execute(text("SELECT tool_key, ts_rank_cd(search_vector, to_tsquery('simple', :query)) AS rank "
                "FROM tool_search_entries WHERE " + self._WHERE +
                " AND (search_vector @@ to_tsquery('simple', :query) OR category = ANY(:categories)) "
                "ORDER BY (CASE WHEN category = ANY(:categories) THEN 2 ELSE 0 END + "
                "ts_rank_cd(search_vector, to_tsquery('simple', :query))) DESC, tool_key LIMIT :limit"), parameters).all()
        return [row[0] for row in rows]

    def semantic(self, config, documents, vector, limit=30, force_exact=False):
        if not documents:
            return {}, {"search_mode": "empty", "exact_topup": False}
        dimensions = config.dimensions
        use_ann = len(documents) > self.EXACT_THRESHOLD and dimensions in ANN_DIMENSIONS and not force_exact
        kind = "vector" if dimensions <= 2000 else "halfvec"
        expression = f"embedding::{kind}({dimensions})" if use_ann else "embedding"
        vector_text = "[" + ",".join(str(float(x)) for x in vector) + "]"
        distance = f"{expression} <=> CAST(:vector AS {kind if use_ann else 'vector'})"
        sql = (f"SELECT tool_key, {distance} AS distance FROM tool_search_entries WHERE " + self._WHERE
               + f" AND dimensions = {int(dimensions)} AND embedding IS NOT NULL ORDER BY {distance} LIMIT :limit")
        parameters = {**self._parameters(config, documents), "vector": vector_text, "limit": limit}
        with self.sessions() as db:
            self._deadline(db)
            if use_ann:
                self._configure_ann(db)
            rows = db.execute(text(sql), parameters).all()
            topup = use_ann and len(rows) < min(limit, len(documents))
            if topup:
                # 严格权限过滤后近似候选可能不足；补齐走显式精确路径，不关闭权限条件。
                db.execute(text("SET LOCAL enable_sort = on"))
                exact_distance = "embedding <=> CAST(:vector AS vector)"
                exact_sql = (f"SELECT tool_key, {exact_distance} AS distance FROM tool_search_entries WHERE "
                    + self._WHERE + f" AND dimensions = {int(dimensions)} AND embedding IS NOT NULL "
                    + f"ORDER BY ({exact_distance}) + 0 LIMIT :limit")
                rows = db.execute(text(exact_sql), parameters).all()
        return {row[0]: max(-1., min(1., 1. - row[1])) for row in rows}, {
            "search_mode": "hnsw" if use_ann else "exact", "exact_topup": topup,
            "ann_dimension_supported": dimensions in ANN_DIMENSIONS}

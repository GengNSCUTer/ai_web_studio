import json
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models.knowledge import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEvalCase,
    KnowledgeEvalResult,
    KnowledgeEvalRun,
    KnowledgeEvalSet,
    KnowledgeJob,
    KnowledgeRetrievalLog,
)


class KnowledgeBaseRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_user(self, user_id: str) -> list[KnowledgeBase]:
        stmt = (
            select(KnowledgeBase)
            .where(KnowledgeBase.user_id == user_id)
            .order_by(KnowledgeBase.updated_at.desc(), KnowledgeBase.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def get_by_user(self, knowledge_base_id: str, user_id: str) -> KnowledgeBase | None:
        stmt = (
            select(KnowledgeBase)
            .where(KnowledgeBase.id == knowledge_base_id, KnowledgeBase.user_id == user_id)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def save(self, knowledge_base: KnowledgeBase) -> KnowledgeBase:
        self.db.add(knowledge_base)
        self.db.commit()
        self.db.refresh(knowledge_base)
        return knowledge_base

    def delete(self, knowledge_base: KnowledgeBase) -> None:
        self.db.execute(
            delete(KnowledgeRetrievalLog).where(
                KnowledgeRetrievalLog.knowledge_base_id == knowledge_base.id,
                KnowledgeRetrievalLog.user_id == knowledge_base.user_id,
            )
        )
        self.db.delete(knowledge_base)
        self.db.commit()

    def document_count(self, knowledge_base_id: str, user_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(
                KnowledgeDocument.knowledge_base_id == knowledge_base_id,
                KnowledgeDocument.user_id == user_id,
            )
        )
        return int(self.db.scalar(stmt) or 0)


class KnowledgeDocumentRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_knowledge_base(self, knowledge_base_id: str, user_id: str) -> list[KnowledgeDocument]:
        stmt = (
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.knowledge_base_id == knowledge_base_id,
                KnowledgeDocument.user_id == user_id,
            )
            .order_by(KnowledgeDocument.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def get_by_user(self, document_id: str, user_id: str) -> KnowledgeDocument | None:
        stmt = (
            select(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id, KnowledgeDocument.user_id == user_id)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def save(self, document: KnowledgeDocument) -> KnowledgeDocument:
        self.db.add(document)
        self.db.commit()
        self.db.refresh(document)
        return document

    def delete(self, document: KnowledgeDocument) -> None:
        self.db.delete(document)
        self.db.commit()


class KnowledgeChunkRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_document(
        self,
        document_id: str,
        user_id: str,
        *,
        index_generation: str | None = None,
    ) -> list[KnowledgeChunk]:
        conditions = [KnowledgeChunk.document_id == document_id, KnowledgeChunk.user_id == user_id]
        if index_generation is not None:
            conditions.append(KnowledgeChunk.index_generation == index_generation)
        stmt = select(KnowledgeChunk).where(*conditions).order_by(KnowledgeChunk.chunk_index.asc())
        return list(self.db.scalars(stmt).all())

    def get_by_user(self, chunk_id: str, user_id: str) -> KnowledgeChunk | None:
        stmt = (
            select(KnowledgeChunk)
            .where(KnowledgeChunk.id == chunk_id, KnowledgeChunk.user_id == user_id)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def list_by_knowledge_base(
        self,
        knowledge_base_id: str,
        user_id: str,
        *,
        index_generation: str | None = None,
    ) -> list[KnowledgeChunk]:
        conditions = [KnowledgeChunk.knowledge_base_id == knowledge_base_id, KnowledgeChunk.user_id == user_id]
        if index_generation is not None:
            conditions.append(KnowledgeChunk.index_generation == index_generation)
        stmt = select(KnowledgeChunk).where(*conditions).order_by(KnowledgeChunk.vector_id.asc())
        return list(self.db.scalars(stmt).all())

    def list_by_vector_ids_and_knowledge_base(
        self,
        *,
        knowledge_base_id: str,
        user_id: str,
        vector_ids: list[int],
        index_generation: str | None = None,
    ) -> list[KnowledgeChunk]:
        # 查询期通常只需要 FAISS/BM25 命中的少量 vector_id，不应该每次把整个知识库 chunks 全量拉出。
        unique_vector_ids = list(dict.fromkeys(vector_ids))
        if not unique_vector_ids:
            return []
        conditions = [
            KnowledgeChunk.knowledge_base_id == knowledge_base_id,
            KnowledgeChunk.user_id == user_id,
            KnowledgeChunk.vector_id.in_(unique_vector_ids),
        ]
        if index_generation is not None:
            conditions.append(KnowledgeChunk.index_generation == index_generation)
        stmt = select(KnowledgeChunk).where(*conditions).order_by(KnowledgeChunk.vector_id.asc())
        return list(self.db.scalars(stmt).all())

    def search_by_cosine_distance(
        self,
        *,
        knowledge_base_id: str,
        user_id: str,
        index_generation: str,
        query_vector: list[float],
        embedding_provider: str,
        embedding_model: str,
        embedding_dimensions: int,
        embedding_version: str,
        top_k: int,
    ) -> list[tuple[KnowledgeChunk, float]]:
        if top_k <= 0:
            return []
        distance = KnowledgeChunk.embedding.cosine_distance(query_vector)
        similarity = (1.0 - distance).label("similarity")
        statement = (
            select(KnowledgeChunk, similarity)
            .where(
                KnowledgeChunk.knowledge_base_id == knowledge_base_id,
                KnowledgeChunk.user_id == user_id,
                KnowledgeChunk.index_generation == index_generation,
                KnowledgeChunk.embedding.is_not(None),
                KnowledgeChunk.embedding_provider == embedding_provider,
                KnowledgeChunk.embedding_model == embedding_model,
                KnowledgeChunk.embedding_dimensions == embedding_dimensions,
                KnowledgeChunk.embedding_version == embedding_version,
            )
            .order_by(distance.asc(), KnowledgeChunk.vector_id.asc())
            .limit(top_k)
        )
        return [(chunk, float(score)) for chunk, score in self.db.execute(statement).all()]

    def count_by_knowledge_base(
        self,
        knowledge_base_id: str,
        user_id: str,
        *,
        index_generation: str | None = None,
    ) -> int:
        conditions = [KnowledgeChunk.knowledge_base_id == knowledge_base_id, KnowledgeChunk.user_id == user_id]
        if index_generation is not None:
            conditions.append(KnowledgeChunk.index_generation == index_generation)
        stmt = select(func.count()).select_from(KnowledgeChunk).where(*conditions)
        return int(self.db.scalar(stmt) or 0)

    def max_vector_id(
        self,
        knowledge_base_id: str,
        user_id: str,
        *,
        index_generation: str | None = None,
    ) -> int:
        conditions = [KnowledgeChunk.knowledge_base_id == knowledge_base_id, KnowledgeChunk.user_id == user_id]
        if index_generation is not None:
            conditions.append(KnowledgeChunk.index_generation == index_generation)
        stmt = select(func.max(KnowledgeChunk.vector_id)).where(*conditions)
        return int(self.db.scalar(stmt) or 0)

    def replace_document_chunks(
        self,
        document_id: str,
        user_id: str,
        chunks: list[KnowledgeChunk],
        *,
        index_generation: str = "legacy",
    ) -> list[KnowledgeChunk]:
        stmt = select(KnowledgeChunk).where(
            KnowledgeChunk.document_id == document_id,
            KnowledgeChunk.user_id == user_id,
            KnowledgeChunk.index_generation == index_generation,
        )
        existing = list(self.db.scalars(stmt).all())
        for item in existing:
            self.db.delete(item)
        for chunk in chunks:
            self.db.add(chunk)
        self.db.commit()
        for chunk in chunks:
            self.db.refresh(chunk)
        return chunks

    def save_embeddings(self, chunks: list[KnowledgeChunk]) -> None:
        try:
            self.db.add_all(chunks)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise


class KnowledgeJobRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_knowledge_base(self, knowledge_base_id: str, user_id: str) -> list[KnowledgeJob]:
        stmt = (
            select(KnowledgeJob)
            .where(KnowledgeJob.knowledge_base_id == knowledge_base_id, KnowledgeJob.user_id == user_id)
            .order_by(KnowledgeJob.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def latest_by_document_type(
        self,
        *,
        document_id: str,
        user_id: str,
        job_type: str,
    ) -> KnowledgeJob | None:
        stmt = (
            select(KnowledgeJob)
            .where(
                KnowledgeJob.document_id == document_id,
                KnowledgeJob.user_id == user_id,
                KnowledgeJob.job_type == job_type,
            )
            .order_by(KnowledgeJob.created_at.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def save(self, job: KnowledgeJob) -> KnowledgeJob:
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


class KnowledgeRetrievalLogRepository:
    def __init__(self, db: Session):
        self.db = db

    def save(self, log: KnowledgeRetrievalLog) -> KnowledgeRetrievalLog:
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        return log

    def create(
        self,
        *,
        user_id: str,
        knowledge_base_id: str,
        query: str,
        retrieval_mode: str,
        top_k: int,
        rerank_enabled: bool,
        rerank_model: str | None,
        candidates: list[dict[str, Any]],
        selected: list[dict[str, Any]],
        diagnostics: dict[str, Any],
        sources: list[dict[str, Any]],
        status: str = "success",
        error_message: str | None = None,
        elapsed_ms: int | None = None,
        conversation_id: str | None = None,
        user_message_id: str | None = None,
        assistant_message_id: str | None = None,
    ) -> KnowledgeRetrievalLog:
        return self.save(
            KnowledgeRetrievalLog(
                user_id=user_id,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                knowledge_base_id=knowledge_base_id,
                query=query,
                retrieval_mode=retrieval_mode,
                top_k=top_k,
                rerank_enabled=rerank_enabled,
                rerank_model=rerank_model,
                candidates_json=_json_dumps(candidates),
                selected_json=_json_dumps(selected),
                diagnostics_json=_json_dumps(diagnostics),
                sources_json=_json_dumps(sources),
                status=status,
                error_message=error_message,
                elapsed_ms=elapsed_ms,
            )
        )

    def update_message_links(
        self,
        *,
        log_id: str,
        user_id: str,
        conversation_id: str | None,
        user_message_id: str | None,
        assistant_message_id: str | None,
        sources: list[dict[str, Any]] | None = None,
    ) -> KnowledgeRetrievalLog | None:
        log = self.get_by_user(log_id, user_id)
        if not log:
            return None
        log.conversation_id = conversation_id
        log.user_message_id = user_message_id
        log.assistant_message_id = assistant_message_id
        if sources is not None:
            log.sources_json = _json_dumps(sources)
        return self.save(log)

    def detach_conversation_links(
        self,
        *,
        conversation_id: str,
        user_id: str | None = None,
        commit: bool = True,
    ) -> int:
        conditions = [KnowledgeRetrievalLog.conversation_id == conversation_id]
        if user_id is not None:
            conditions.append(KnowledgeRetrievalLog.user_id == user_id)
        result = self.db.execute(
            update(KnowledgeRetrievalLog)
            .where(*conditions)
            .values(
                conversation_id=None,
                user_message_id=None,
                assistant_message_id=None,
            )
        )
        if commit:
            self.db.commit()
        return int(result.rowcount or 0)

    def detach_message_links(
        self,
        *,
        message_ids: list[str],
        user_id: str | None = None,
        commit: bool = True,
    ) -> int:
        if not message_ids:
            return 0
        target_ids = set(message_ids)
        conditions = [
            (KnowledgeRetrievalLog.user_message_id.in_(target_ids))
            | (KnowledgeRetrievalLog.assistant_message_id.in_(target_ids))
        ]
        if user_id is not None:
            conditions.append(KnowledgeRetrievalLog.user_id == user_id)
        logs = list(self.db.scalars(select(KnowledgeRetrievalLog).where(*conditions)).all())
        for log in logs:
            if log.user_message_id in target_ids:
                log.user_message_id = None
            if log.assistant_message_id in target_ids:
                log.assistant_message_id = None
            self.db.add(log)
        if commit:
            self.db.commit()
        return len(logs)

    def delete_by_knowledge_base(self, *, knowledge_base_id: str, user_id: str | None = None) -> int:
        conditions = [KnowledgeRetrievalLog.knowledge_base_id == knowledge_base_id]
        if user_id is not None:
            conditions.append(KnowledgeRetrievalLog.user_id == user_id)
        result = self.db.execute(delete(KnowledgeRetrievalLog).where(*conditions))
        self.db.commit()
        return int(result.rowcount or 0)

    def get_by_user(self, log_id: str, user_id: str) -> KnowledgeRetrievalLog | None:
        stmt = (
            select(KnowledgeRetrievalLog)
            .where(KnowledgeRetrievalLog.id == log_id, KnowledgeRetrievalLog.user_id == user_id)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def latest_by_assistant_message(self, assistant_message_id: str, user_id: str) -> KnowledgeRetrievalLog | None:
        stmt = (
            select(KnowledgeRetrievalLog)
            .where(
                KnowledgeRetrievalLog.assistant_message_id == assistant_message_id,
                KnowledgeRetrievalLog.user_id == user_id,
            )
            .order_by(KnowledgeRetrievalLog.created_at.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def list_by_knowledge_base(self, knowledge_base_id: str, user_id: str, limit: int = 20) -> list[KnowledgeRetrievalLog]:
        stmt = (
            select(KnowledgeRetrievalLog)
            .where(
                KnowledgeRetrievalLog.knowledge_base_id == knowledge_base_id,
                KnowledgeRetrievalLog.user_id == user_id,
            )
            .order_by(KnowledgeRetrievalLog.created_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        return list(self.db.scalars(stmt).all())

    @staticmethod
    def to_public_dict(log: KnowledgeRetrievalLog) -> dict[str, Any]:
        return {
            "id": log.id,
            "user_id": log.user_id,
            "conversation_id": log.conversation_id,
            "user_message_id": log.user_message_id,
            "assistant_message_id": log.assistant_message_id,
            "knowledge_base_id": log.knowledge_base_id,
            "query": log.query,
            "retrieval_mode": log.retrieval_mode,
            "top_k": log.top_k,
            "rerank_enabled": log.rerank_enabled,
            "rerank_model": log.rerank_model,
            "candidates": _json_loads(log.candidates_json, []),
            "selected": _json_loads(log.selected_json, []),
            "diagnostics": _json_loads(log.diagnostics_json, {}),
            "sources": _json_loads(log.sources_json, []),
            "status": log.status,
            "error_message": log.error_message,
            "elapsed_ms": log.elapsed_ms,
            "created_at": log.created_at,
        }


class KnowledgeEvalSetRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_knowledge_base(self, knowledge_base_id: str, user_id: str) -> list[KnowledgeEvalSet]:
        stmt = (
            select(KnowledgeEvalSet)
            .where(
                KnowledgeEvalSet.knowledge_base_id == knowledge_base_id,
                KnowledgeEvalSet.user_id == user_id,
            )
            .order_by(KnowledgeEvalSet.updated_at.desc(), KnowledgeEvalSet.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def get_by_user(self, eval_set_id: str, user_id: str) -> KnowledgeEvalSet | None:
        stmt = (
            select(KnowledgeEvalSet)
            .where(KnowledgeEvalSet.id == eval_set_id, KnowledgeEvalSet.user_id == user_id)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def save(self, eval_set: KnowledgeEvalSet) -> KnowledgeEvalSet:
        self.db.add(eval_set)
        self.db.commit()
        self.db.refresh(eval_set)
        return eval_set

    def delete(self, eval_set: KnowledgeEvalSet) -> None:
        run_ids = [
            row[0]
            for row in self.db.execute(
                select(KnowledgeEvalRun.id).where(KnowledgeEvalRun.eval_set_id == eval_set.id)
            ).all()
        ]
        case_ids = [
            row[0]
            for row in self.db.execute(
                select(KnowledgeEvalCase.id).where(KnowledgeEvalCase.eval_set_id == eval_set.id)
            ).all()
        ]
        if run_ids:
            self.db.execute(delete(KnowledgeEvalResult).where(KnowledgeEvalResult.run_id.in_(run_ids)))
        if case_ids:
            self.db.execute(delete(KnowledgeEvalResult).where(KnowledgeEvalResult.case_id.in_(case_ids)))
            self.db.execute(delete(KnowledgeEvalCase).where(KnowledgeEvalCase.id.in_(case_ids)))
        if run_ids:
            self.db.execute(delete(KnowledgeEvalRun).where(KnowledgeEvalRun.id.in_(run_ids)))
        self.db.delete(eval_set)
        self.db.commit()


class KnowledgeEvalCaseRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_eval_set(self, eval_set_id: str, user_id: str) -> list[KnowledgeEvalCase]:
        stmt = (
            select(KnowledgeEvalCase)
            .where(
                KnowledgeEvalCase.eval_set_id == eval_set_id,
                KnowledgeEvalCase.user_id == user_id,
            )
            .order_by(KnowledgeEvalCase.created_at.asc())
        )
        return list(self.db.scalars(stmt).all())

    def count_by_expected_document(self, document_id: str, user_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(KnowledgeEvalCase)
            .where(
                KnowledgeEvalCase.expected_document_id == document_id,
                KnowledgeEvalCase.user_id == user_id,
            )
        )
        return int(self.db.scalar(stmt) or 0)

    def save(self, eval_case: KnowledgeEvalCase) -> KnowledgeEvalCase:
        self.db.add(eval_case)
        self.db.commit()
        self.db.refresh(eval_case)
        return eval_case


class KnowledgeEvalRunRepository:
    def __init__(self, db: Session):
        self.db = db

    def save(self, run: KnowledgeEvalRun) -> KnowledgeEvalRun:
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def list_by_eval_set(self, eval_set_id: str, user_id: str) -> list[KnowledgeEvalRun]:
        stmt = (
            select(KnowledgeEvalRun)
            .where(
                KnowledgeEvalRun.eval_set_id == eval_set_id,
                KnowledgeEvalRun.user_id == user_id,
            )
            .order_by(KnowledgeEvalRun.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def create_result(self, result: KnowledgeEvalResult) -> KnowledgeEvalResult:
        self.db.add(result)
        self.db.commit()
        self.db.refresh(result)
        return result


class KnowledgeEvalResultRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_run(self, run_id: str, user_id: str) -> list[KnowledgeEvalResult]:
        stmt = (
            select(KnowledgeEvalResult)
            .where(
                KnowledgeEvalResult.run_id == run_id,
                KnowledgeEvalResult.user_id == user_id,
            )
            .order_by(KnowledgeEvalResult.created_at.asc())
        )
        return list(self.db.scalars(stmt).all())

    def save(self, result: KnowledgeEvalResult) -> KnowledgeEvalResult:
        self.db.add(result)
        self.db.commit()
        self.db.refresh(result)
        return result

    def create_result(self, result: KnowledgeEvalResult) -> KnowledgeEvalResult:
        return self.save(result)

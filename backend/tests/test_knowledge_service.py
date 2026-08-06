from __future__ import annotations

import io
import asyncio
import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.routes.chat import _stringify_stats
from app.core.config import settings
from app.core.database import Base
from app.models import *  # noqa: F403 - ensure all metadata is registered.
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.models.project import Project
from app.models.user import User
from app.repositories.knowledge_repo import (
    KnowledgeBaseRepository,
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeEvalCaseRepository,
    KnowledgeEvalResultRepository,
    KnowledgeEvalRunRepository,
    KnowledgeEvalSetRepository,
    KnowledgeJobRepository,
    KnowledgeRetrievalLogRepository,
)
from app.repositories.project_repo import ProjectRepository
from app.repositories.setting_repo import UserSettingRepository
from app.repositories.tool_config_repo import ToolConfigRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageRepository
from app.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeCredentialUpdate,
    KnowledgeDocumentCreate,
    KnowledgeEvalCaseCreate,
    KnowledgeEvalRunRequest,
    KnowledgeEvalSetCreate,
)
from app.schemas.setting import UserSettingUpdate
from app.services.knowledge_service import (
    KnowledgeBaseService,
    KnowledgeCredentialService,
    KnowledgeDocumentService,
    KnowledgeJobService,
)
from app.services.knowledge_vector_search import KnowledgeVectorSearchHit
from app.services.conversation_service import ConversationService
from app.services.message_service import MessageService
from app.services.knowledge_index_service import (
    KnowledgeEmbeddingService,
    KnowledgeIndexService,
    KnowledgeLexicalStore,
    KnowledgeRerankService,
    RetrievalResult,
)
from app.services.knowledge_context_service import KnowledgeContextService
from app.services.knowledge_evaluation_service import KnowledgeEvaluationService
from app.services.knowledge_model_catalog_service import KnowledgeModelCatalogService
from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalFilter, KnowledgeRetrievalPipeline
from app.services.setting_service import SettingService


class FakeKnowledgeEmbeddingService(KnowledgeEmbeddingService):
    async def embed_texts(self, *, user_id: str, knowledge_base, texts: list[str]) -> list[list[float]]:  # noqa: ANN001
        return [self._embed(text) for text in texts]

    @staticmethod
    def _embed(text: str) -> list[float]:
        lower = text.lower()
        vector = [0.0] * 128
        vector[0] = float(lower.count("adaptive") + lower.count("rag"))
        vector[1] = float(lower.count("routing") + lower.count("router"))
        vector[2] = float(lower.count("expert") + lower.count("moe"))
        vector[3] = float(len(text) % 97) / 97.0
        if not any(vector):
            vector[-1] = 0.1
        return vector


class FailingKnowledgeEmbeddingService(KnowledgeEmbeddingService):
    async def embed_texts(self, *, user_id: str, knowledge_base, texts: list[str]) -> list[list[float]]:  # noqa: ANN001, ARG002
        raise RuntimeError("fake embedding failure")


class PublishThenFailKnowledgeLexicalStore(KnowledgeLexicalStore):
    """Simulate a failure after a new BM25 file has already replaced the old file."""

    def rebuild(self, **kwargs) -> str:  # noqa: ANN003
        super().rebuild(**kwargs)
        raise RuntimeError("fake lexical publish failure")


class FakeKnowledgeRerankService(KnowledgeRerankService):
    async def rerank(self, *, user_id: str, knowledge_base, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:  # noqa: ANN001
        ranked: list[tuple[int, float]] = []
        for index, document in enumerate(documents):
            lower = document.lower()
            score = 0.95 if "expert" in lower or "moe" in lower else 0.4 - (index * 0.01)
            ranked.append((index, score))
        return sorted(ranked, key=lambda item: item[1], reverse=True)[:top_n]


class FailingKnowledgeRerankService(KnowledgeRerankService):
    async def rerank(self, *, user_id: str, knowledge_base, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:  # noqa: ANN001
        raise RuntimeError("fake rerank failure")


class SlowKnowledgeIndexService:
    async def retrieve_async(self, *, user_id: str, knowledge_base, query: str, top_k: int):  # noqa: ANN001, ARG002
        await asyncio.sleep(0.2)
        return []


class FailingKnowledgeIndexService:
    async def retrieve_async(self, *, user_id: str, knowledge_base, query: str, top_k: int):  # noqa: ANN001, ARG002
        raise RuntimeError("provider.internal/api?api_key=secret-value")


class FailingKnowledgeEvaluationPipeline:
    def retrieve(self, **_: object):
        raise RuntimeError("https://provider.internal/eval?token=secret-value")


class CapturingKnowledgeIndexService:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def retrieve_async(self, *, user_id: str, knowledge_base, query: str, top_k: int):  # noqa: ANN001, ARG002
        self.queries.append(query)
        return []


class StaticMultiKnowledgeIndexService:
    async def retrieve_async(self, *, user_id: str, knowledge_base, query: str, top_k: int):  # noqa: ANN001, ARG002
        chunks = KnowledgeChunkRepository(self.db).list_by_knowledge_base(knowledge_base.id, user_id)
        return [
            RetrievalResult(
                chunk=chunk,
                score=0.9 - (index * 0.05),
                metadata={
                    "file_name": f"{knowledge_base.name}.md",
                    "knowledge_base_id": knowledge_base.id,
                    "query": query,
                },
            )
            for index, chunk in enumerate(chunks[:top_k])
        ]

    def __init__(self, db):
        self.db = db


class GuardedKnowledgeChunkRepository(KnowledgeChunkRepository):
    def list_by_knowledge_base(self, knowledge_base_id: str, user_id: str):  # noqa: ANN201
        raise AssertionError("retrieval query path should not load all chunks")


class StaticVectorSearch:
    def __init__(self, hits: list[KnowledgeVectorSearchHit]) -> None:
        self.hits = hits

    def search(
        self,
        *,
        user_id: str,
        knowledge_base,
        index_generation: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[KnowledgeVectorSearchHit]:  # noqa: ANN001, ARG002
        return self.hits[:top_k]


class InMemoryVectorSearch:
    """Small SQLite-test implementation; production always uses pgvector."""

    def __init__(self, db):  # noqa: ANN001
        self.chunk_repo = KnowledgeChunkRepository(db)

    def search(
        self,
        *,
        user_id: str,
        knowledge_base,
        index_generation: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[KnowledgeVectorSearchHit]:  # noqa: ANN001
        chunks = self.chunk_repo.list_by_knowledge_base(
            knowledge_base.id,
            user_id,
            index_generation=index_generation,
        )
        query_norm = math.sqrt(sum(value * value for value in query_vector)) or 1.0
        hits: list[KnowledgeVectorSearchHit] = []
        for chunk in chunks:
            vector = list(chunk.embedding or [])
            if len(vector) != len(query_vector):
                continue
            vector_norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            score = sum(left * right for left, right in zip(vector, query_vector, strict=True)) / (
                vector_norm * query_norm
            )
            hits.append(KnowledgeVectorSearchHit(chunk=chunk, score=score))
        return sorted(hits, key=lambda hit: (-hit.score, hit.chunk.vector_id))[:top_k]


class KnowledgeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()
        self.user = User(email=f"knowledge-{uuid4()}@example.com", username="knowledge-user")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.project = Project(user_id=self.user.id, name="知识库工作区")
        self.db.add(self.project)
        self.db.commit()
        self.db.refresh(self.project)
        self._previous_upload_dir = settings.upload_dir
        self._previous_index_dir = settings.knowledge_index_dir
        self._previous_knowledge_context_timeout_seconds = settings.knowledge_context_timeout_seconds
        self.upload_tmp = tempfile.TemporaryDirectory()
        self.index_tmp = tempfile.TemporaryDirectory()
        object.__setattr__(settings, "upload_dir", self.upload_tmp.name)
        object.__setattr__(settings, "knowledge_index_dir", self.index_tmp.name)

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()
        object.__setattr__(settings, "upload_dir", self._previous_upload_dir)
        object.__setattr__(settings, "knowledge_index_dir", self._previous_index_dir)
        object.__setattr__(
            settings,
            "knowledge_context_timeout_seconds",
            self._previous_knowledge_context_timeout_seconds,
        )
        self.upload_tmp.cleanup()
        self.index_tmp.cleanup()

    def _create_indexed_markdown_knowledge_base(
        self,
        *,
        name: str,
        file_name: str,
        content: str,
        retrieval_mode: str = "vector",
        chunk_size: int = 120,
        chunk_overlap: int = 10,
    ):
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_repo = KnowledgeDocumentRepository(self.db)
        chunk_repo = KnowledgeChunkRepository(self.db)
        setting_service = SettingService(UserSettingRepository(self.db))
        document_service = KnowledgeDocumentService(
            document_repo,
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name=name,
                parser_provider="local_basic",
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                rerank_enabled=False,
                retrieval_mode=retrieval_mode,
                retrieval_top_k=5,
                rerank_top_n=3,
                score_threshold=0,
            ),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        source_file = user_dir / file_name
        source_file.write_text(content, encoding="utf-8")
        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name=file_name,
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/{file_name}",
            ),
        )
        assert document is not None
        document_service.parse_document(knowledge_base.id, document.id, self.user.id)
        parsed_document = document_repo.get_by_user(document.id, self.user.id)
        assert parsed_document is not None
        index_service = KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=document_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
            lexical_store=KnowledgeLexicalStore(index_root=settings.knowledge_index_dir),
        )
        index_service.index_document(user_id=self.user.id, knowledge_base=knowledge_base, document=parsed_document)
        return knowledge_base, parsed_document, chunk_repo, setting_service

    def test_legacy_generation_keeps_database_and_index_reads_on_the_same_version(self) -> None:
        knowledge_base, parsed_document, chunk_repo, setting_service = self._create_indexed_markdown_knowledge_base(
            name="Legacy generation 兼容测试",
            file_name="legacy-generation.md",
            content="LEGACY-CONTENT describes stable retrieval behavior.",
        )
        legacy_chunks = chunk_repo.list_by_knowledge_base(
            knowledge_base.id,
            self.user.id,
            index_generation="legacy",
        )
        self.assertGreaterEqual(len(legacy_chunks), 1)
        self.assertEqual(knowledge_base.active_index_generation, "legacy")
        self.assertTrue(all(chunk.index_generation == "legacy" for chunk in legacy_chunks))

        future_chunk = KnowledgeChunk(
            user_id=self.user.id,
            knowledge_base_id=knowledge_base.id,
            document_id=parsed_document.id,
            index_generation="future-generation",
            chunk_index=0,
            vector_id=legacy_chunks[0].vector_id,
            content="FUTURE-CONTENT must not leak into legacy retrieval.",
            content_hash="future-generation-hash",
            char_count=52,
            token_estimate=13,
        )
        self.db.add(future_chunk)
        self.db.commit()

        matching_legacy_chunks = chunk_repo.list_by_vector_ids_and_knowledge_base(
            knowledge_base_id=knowledge_base.id,
            user_id=self.user.id,
            vector_ids=[legacy_chunks[0].vector_id],
            index_generation=knowledge_base.active_index_generation,
        )
        self.assertEqual([chunk.id for chunk in matching_legacy_chunks], [legacy_chunks[0].id])

        lexical_store = KnowledgeLexicalStore(index_root=settings.knowledge_index_dir)
        legacy_dir = Path(settings.knowledge_index_dir) / knowledge_base.id
        self.assertEqual(
            lexical_store.index_path(knowledge_base.id, generation_id="legacy"),
            legacy_dir / "lexical_index.json",
        )

        results = KnowledgeRetrievalPipeline(
            chunk_repo=chunk_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
            lexical_store=lexical_store,
        ).retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="stable retrieval behavior",
            top_k=3,
        )
        self.assertGreaterEqual(len(results), 1)
        self.assertTrue(all(result.chunk.index_generation == "legacy" for result in results))
        self.assertTrue(all("FUTURE-CONTENT" not in result.chunk.content for result in results))

    def test_create_knowledge_base_and_add_document_creates_pending_job(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_service = KnowledgeDocumentService(
            KnowledgeDocumentRepository(self.db),
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        job_service = KnowledgeJobService(KnowledgeJobRepository(self.db), KnowledgeBaseRepository(self.db))

        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="课程资料",
                description="实验报告和课程材料",
                project_id=self.project.id,
                parser_provider="local_basic",
            ),
        )

        self.assertIsNotNone(knowledge_base)
        assert knowledge_base is not None
        self.assertEqual(knowledge_base.project_id, self.project.id)
        self.assertEqual(knowledge_base.embedding_model, "BAAI/bge-m3")
        self.assertEqual(knowledge_base.rerank_model, "BAAI/bge-reranker-v2-m3")
        self.assertEqual(knowledge_base.document_count, 0)

        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="paper.md",
                mime_type="text/markdown",
                file_size=128,
                storage_key=f"{self.user.id}/paper.md",
            ),
        )

        self.assertIsNotNone(document)
        assert document is not None
        self.assertEqual(document.parse_status, "pending")
        self.assertEqual(document.index_status, "pending")
        self.assertEqual(document.project_id, self.project.id)

        jobs = job_service.list_jobs(knowledge_base.id, self.user.id)
        self.assertIsNotNone(jobs)
        assert jobs is not None
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_type, "parse_document")
        self.assertEqual(jobs[0].status, "pending")
        self.assertEqual(jobs[0].document_id, document.id)

        listed = base_service.list_knowledge_bases(self.user.id)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].document_count, 1)

    def test_add_document_rolls_back_document_when_job_commit_fails(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_service = KnowledgeDocumentService(
            KnowledgeDocumentRepository(self.db),
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(name="文档任务原子创建测试", parser_provider="local_basic"),
        )
        assert knowledge_base is not None

        with patch.object(self.db, "commit", side_effect=RuntimeError("commit failed")):
            with self.assertRaisesRegex(RuntimeError, "commit failed"):
                document_service.add_document(
                    knowledge_base.id,
                    self.user.id,
                    KnowledgeDocumentCreate(
                        file_name="atomic.md",
                        mime_type="text/markdown",
                        file_size=6,
                        storage_key=f"{self.user.id}/atomic.md",
                    ),
                )

        documents = KnowledgeDocumentRepository(self.db).list_by_knowledge_base(
            knowledge_base.id,
            self.user.id,
        )
        jobs = KnowledgeJobRepository(self.db).list_by_knowledge_base(
            knowledge_base.id,
            self.user.id,
        )
        self.assertEqual(documents, [])
        self.assertEqual(jobs, [])

    def test_rejects_foreign_storage_key(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_service = KnowledgeDocumentService(
            KnowledgeDocumentRepository(self.db),
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(name="安全测试"),
        )
        assert knowledge_base is not None

        with self.assertRaises(ValueError):
            document_service.add_document(
                knowledge_base.id,
                self.user.id,
                KnowledgeDocumentCreate(
                    file_name="x.md",
                    mime_type="text/markdown",
                    file_size=1,
                    storage_key="other-user/x.md",
                ),
            )

    def test_create_knowledge_base_accepts_local_or_openai_compatible_model_providers(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))

        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="本地模型知识库",
                embedding_provider="ollama",
                embedding_model="bge-m3:latest",
                embedding_dimensions=1024,
                rerank_enabled=True,
                rerank_provider="openai-compatible",
                rerank_model="BAAI/bge-reranker-v2-m3",
            ),
        )

        self.assertIsNotNone(knowledge_base)
        assert knowledge_base is not None
        self.assertEqual(knowledge_base.embedding_provider, "ollama")
        self.assertEqual(knowledge_base.embedding_model, "bge-m3:latest")
        self.assertEqual(knowledge_base.rerank_provider, "openai-compatible")

    def test_embedding_dimensions_are_derived_from_known_models(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))

        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="维度修正知识库",
                embedding_model="Qwen/Qwen3-Embedding-4B",
                embedding_dimensions=1024,
            ),
        )

        self.assertIsNotNone(knowledge_base)
        assert knowledge_base is not None
        self.assertEqual(knowledge_base.embedding_dimensions, 2560)

        setting = SettingService(UserSettingRepository(self.db)).update_user_settings(
            self.user.id,
            UserSettingUpdate(
                knowledge_embedding_model="Qwen/Qwen3-Embedding-8B",
                knowledge_embedding_dimensions=1024,
            ),
        )
        self.assertEqual(setting.knowledge_embedding_dimensions, 4096)

    def test_knowledge_embedding_and_rerank_api_keys_are_separate(self) -> None:
        service = SettingService(UserSettingRepository(self.db))
        embedding_key = "embedding-key-1234567890"
        rerank_key = "rerank-key-1234567890"

        response = service.update_user_settings(
            self.user.id,
            UserSettingUpdate(
                knowledge_embedding_api_key=embedding_key,
                knowledge_rerank_api_key=rerank_key,
            ),
        )

        self.assertTrue(response.knowledge_embedding_has_api_key)
        self.assertTrue(response.knowledge_rerank_has_api_key)
        self.assertNotEqual(response.knowledge_embedding_api_key_masked, embedding_key)
        self.assertNotEqual(response.knowledge_rerank_api_key_masked, rerank_key)
        self.assertEqual(service.resolve_knowledge_model_api_key(self.user.id, "embedding"), embedding_key)
        self.assertEqual(service.resolve_knowledge_model_api_key(self.user.id, "rerank"), rerank_key)

        response = service.update_user_settings(
            self.user.id,
            UserSettingUpdate(clear_knowledge_embedding_api_key=True),
        )

        self.assertFalse(response.knowledge_embedding_has_api_key)
        self.assertTrue(response.knowledge_rerank_has_api_key)
        self.assertIsNone(service.resolve_knowledge_model_api_key(self.user.id, "embedding"))
        self.assertEqual(service.resolve_knowledge_model_api_key(self.user.id, "rerank"), rerank_key)

    def test_knowledge_model_catalog_uses_remote_models_without_builtin_fallback(self) -> None:
        service = KnowledgeModelCatalogService()

        with patch(
            "app.services.knowledge_model_catalog_service.ChatProviderService.list_models",
            new=AsyncMock(return_value=["Qwen/Qwen3-Embedding-0.6B", "Qwen/Qwen3.5-35B-A3B"]),
        ):
            models, source = asyncio.run(
                service.list_options(
                    provider="siliconflow",
                    base_url="https://api.example/v1",
                    api_key="test-key",
                    model_kind="embedding",
                    strict=True,
                )
            )

        self.assertEqual(source, "remote")
        self.assertEqual(models, ["Qwen/Qwen3-Embedding-0.6B"])
        self.assertNotIn("BAAI/bge-m3", models)

        with patch(
            "app.services.knowledge_model_catalog_service.ChatProviderService.list_models",
            new=AsyncMock(side_effect=RuntimeError("unavailable")),
        ):
            models, source = asyncio.run(
                service.list_options(
                    provider="siliconflow",
                    base_url="https://api.example/v1",
                    api_key="test-key",
                    model_kind="embedding",
                    strict=False,
                )
            )

        self.assertEqual(source, "remote-unavailable")
        self.assertEqual(models, [])

    def test_parse_document_with_local_basic_generates_markdown_preview(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_service = KnowledgeDocumentService(
            KnowledgeDocumentRepository(self.db),
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(name="解析测试", parser_provider="local_basic"),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        source_file = user_dir / "notes.md"
        source_file.write_text("LangChain LCEL 是 Runnable 组合表达式。", encoding="utf-8")

        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="notes.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/notes.md",
            ),
        )
        assert document is not None

        result = document_service.parse_document(knowledge_base.id, document.id, self.user.id)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.document.parse_status, "parsed")
        self.assertEqual(result.document.index_status, "pending")
        self.assertIsNotNone(result.document.parsed_markdown_path)
        self.assertEqual(result.job.status, "succeeded")
        self.assertIn("LangChain LCEL", result.markdown_preview or "")

        preview = document_service.preview_markdown(knowledge_base.id, document.id, self.user.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn("# notes", preview.markdown)
        self.assertIn("Runnable", preview.markdown)

    def test_parse_document_is_idempotent_after_success(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_service = KnowledgeDocumentService(
            KnowledgeDocumentRepository(self.db),
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(name="解析幂等测试", parser_provider="local_basic"),
        )
        assert knowledge_base is not None

        source_file = Path(settings.upload_dir) / self.user.id / "idempotent.md"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("同一文档不应被重复解析。", encoding="utf-8")
        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="idempotent.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/idempotent.md",
            ),
        )
        assert document is not None

        first = document_service.parse_document(knowledge_base.id, document.id, self.user.id)
        second = document_service.parse_document(knowledge_base.id, document.id, self.user.id)

        assert first is not None
        assert second is not None
        self.assertEqual(first.job.id, second.job.id)
        self.assertEqual(second.job.status, "succeeded")
        self.assertEqual(second.job.retry_count, 1)
        self.assertIn("不应被重复解析", second.markdown_preview or "")

    def test_parse_job_failure_is_classified_and_explicit_retry_increments_count(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_service = KnowledgeDocumentService(
            KnowledgeDocumentRepository(self.db),
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(name="解析失败状态测试", parser_provider="local_basic"),
        )
        assert knowledge_base is not None

        source_file = Path(settings.upload_dir) / self.user.id / "failed.md"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("failure", encoding="utf-8")
        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="failed.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/failed.md",
            ),
        )
        assert document is not None

        with patch(
            "app.services.knowledge_service.KnowledgeParserService.parse",
            side_effect=RuntimeError("provider secret should never be persisted"),
        ):
            first = document_service.parse_document(knowledge_base.id, document.id, self.user.id)
            second = document_service.parse_document(knowledge_base.id, document.id, self.user.id)

        assert first is not None
        assert second is not None
        self.assertEqual(first.job.id, second.job.id)
        self.assertEqual(first.job.status, "failed")
        self.assertEqual(first.job.error_code, "job_failed")
        self.assertEqual(first.job.retry_count, 1)
        self.assertNotIn("provider secret", first.job.error_message or "")
        self.assertEqual(second.job.retry_count, 2)

    def test_index_document_reuses_successful_job_for_same_request(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_repo = KnowledgeDocumentRepository(self.db)
        document_service = KnowledgeDocumentService(
            document_repo,
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="索引幂等测试",
                parser_provider="local_basic",
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
            ),
        )
        assert knowledge_base is not None

        source_file = Path(settings.upload_dir) / self.user.id / "index-idempotent.md"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("index once", encoding="utf-8")
        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="index-idempotent.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/index-idempotent.md",
            ),
        )
        assert document is not None
        parsed = document_service.parse_document(knowledge_base.id, document.id, self.user.id)
        assert parsed is not None
        parsed_document = document_repo.get_by_user(document.id, self.user.id)
        assert parsed_document is not None

        fake_index_service = Mock()
        fake_index_service.index_document.return_value = SimpleNamespace(
            chunk_count=1,
            index_path="/tmp/index-idempotent.json",
        )
        with patch(
            "app.services.knowledge_service.KnowledgeIndexService",
            return_value=fake_index_service,
        ):
            first = document_service.index_document(knowledge_base.id, parsed_document.id, self.user.id)
            second = document_service.index_document(knowledge_base.id, parsed_document.id, self.user.id)

        assert first is not None
        assert second is not None
        self.assertEqual(first.job.id, second.job.id)
        self.assertEqual(second.job.status, "succeeded")
        self.assertEqual(second.job.retry_count, 1)
        self.assertEqual(second.chunk_count, 1)
        fake_index_service.index_document.assert_called_once()

    def test_markdown_preview_endpoint_returns_full_markdown(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_service = KnowledgeDocumentService(
            KnowledgeDocumentRepository(self.db),
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(name="完整预览测试", parser_provider="local_basic"),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        long_tail = "TAIL_MARKER_FOR_FULL_MARKDOWN_PREVIEW"
        source_file = user_dir / "long.md"
        source_file.write_text(
            "A" * 26000 + "\n\n" + long_tail,
            encoding="utf-8",
        )

        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="long.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/long.md",
            ),
        )
        assert document is not None

        parse_result = document_service.parse_document(knowledge_base.id, document.id, self.user.id)
        self.assertIsNotNone(parse_result)
        assert parse_result is not None
        self.assertNotIn(long_tail, parse_result.markdown_preview or "")

        preview = document_service.preview_markdown(knowledge_base.id, document.id, self.user.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn(long_tail, preview.markdown)

    def test_markdown_preview_returns_chunk_locations_after_indexing(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_repo = KnowledgeDocumentRepository(self.db)
        chunk_repo = KnowledgeChunkRepository(self.db)
        document_service = KnowledgeDocumentService(
            document_repo,
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="来源定位测试",
                parser_provider="local_basic",
                chunk_size=120,
                chunk_overlap=20,
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                rerank_enabled=False,
            ),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        source_file = user_dir / "locate.md"
        source_file.write_text(
            "\n\n".join(
                [
                    "Adaptive RAG uses routing to choose retrieval strategy.",
                    "Skill Router selects useful tools and routes requests.",
                    "This paragraph is only filler text for chunking.",
                ]
            ),
            encoding="utf-8",
        )
        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="locate.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/locate.md",
            ),
        )
        assert document is not None
        document_service.parse_document(knowledge_base.id, document.id, self.user.id)
        parsed_document = document_repo.get_by_user(document.id, self.user.id)
        assert parsed_document is not None
        KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=document_repo,
            setting_service=SettingService(UserSettingRepository(self.db)),
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        ).index_document(user_id=self.user.id, knowledge_base=knowledge_base, document=parsed_document)

        preview = document_service.preview_markdown(knowledge_base.id, document.id, self.user.id)

        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertGreaterEqual(len(preview.chunks), 1)
        located_chunk = preview.chunks[0]
        self.assertIsNotNone(located_chunk.source_start)
        self.assertIsNotNone(located_chunk.source_end)
        assert located_chunk.source_start is not None
        assert located_chunk.source_end is not None
        self.assertEqual(preview.markdown[located_chunk.source_start : located_chunk.source_end], located_chunk.content)

    def test_mineru_credential_is_encrypted_and_masked(self) -> None:
        service = KnowledgeCredentialService(ToolConfigRepository(self.db))
        fake_token = "mineru-test-token-1234567890"

        response = service.update_mineru_credential(
            self.user.id,
            KnowledgeCredentialUpdate(api_key=fake_token, is_enabled=True),
        )

        self.assertTrue(response.has_api_key)
        self.assertEqual(response.provider_key, "mineru")
        self.assertNotEqual(response.api_key_masked, fake_token)
        credential = ToolConfigRepository(self.db).get_credential(self.user.id, "mineru")
        self.assertIsNotNone(credential)
        assert credential is not None
        self.assertNotIn(fake_token, credential.api_key or "")

        test_result = service.test_mineru_credential(self.user.id)
        self.assertTrue(test_result.ok)
        self.assertIn("已配置", test_result.message)

    def test_parse_document_with_mineru_generates_markdown_preview(self) -> None:
        KnowledgeCredentialService(ToolConfigRepository(self.db)).update_mineru_credential(
            self.user.id,
            KnowledgeCredentialUpdate(api_key="mineru-test-token-1234567890", is_enabled=True),
        )
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_service = KnowledgeDocumentService(
            KnowledgeDocumentRepository(self.db),
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(name="MinerU 解析测试", parser_provider="mineru"),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        source_file = user_dir / "mineru.pdf"
        source_file.write_bytes(b"%PDF-1.4 fake pdf bytes")

        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="mineru.pdf",
                mime_type="application/pdf",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/mineru.pdf",
            ),
        )
        assert document is not None

        zip_buffer = io.BytesIO()
        with ZipFile(zip_buffer, "w") as archive:
            archive.writestr(
                "mineru/full.md",
                "# MinerU Result\n\n这是 MinerU 返回的 Markdown。\n\n![Figure 1](images/fig.png)",
            )
            archive.writestr("mineru/images/fig.png", b"fake-png-bytes")

        post_response = Mock(status_code=200)
        post_response.json.return_value = {
            "code": 0,
            "data": {"batch_id": "batch-1", "file_urls": ["https://upload.example/mineru.pdf"]},
        }
        put_response = Mock(status_code=200)
        get_result_response = Mock(status_code=200)
        get_result_response.json.return_value = {
            "code": 0,
            "data": {
                "extract_result": [
                    {
                        "state": "done",
                        "full_zip_url": "https://download.example/result.zip",
                    }
                ]
            },
        }
        get_zip_response = Mock(status_code=200, content=zip_buffer.getvalue())

        with patch("app.services.knowledge_parser_service.requests.post", return_value=post_response), patch(
            "app.services.knowledge_parser_service.requests.put", return_value=put_response
        ), patch(
            "app.services.knowledge_parser_service.requests.get",
            side_effect=[get_result_response, get_zip_response],
        ):
            result = document_service.parse_document(knowledge_base.id, document.id, self.user.id)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.document.parse_status, "parsed")
        self.assertEqual(result.job.status, "succeeded")
        self.assertIn("MinerU Result", result.markdown_preview or "")
        self.assertIsNotNone(result.document.parsed_assets_json)

        preview = document_service.preview_markdown(knowledge_base.id, document.id, self.user.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn("这是 MinerU 返回的 Markdown", preview.markdown)
        self.assertIn("/api/backend/uploads/file?storage_key=", preview.markdown)

        saved_document = KnowledgeDocumentRepository(self.db).get_by_user(document.id, self.user.id)
        self.assertIsNotNone(saved_document)
        assert saved_document is not None
        self.assertIn("fig.png", saved_document.parsed_assets_json or "")
        saved_asset = (
            Path(settings.upload_dir)
            / self.user.id
            / "knowledge"
            / knowledge_base.id
            / "assets"
            / document.id
            / "images"
            / "fig.png"
        )
        self.assertTrue(saved_asset.exists())

    def test_index_document_and_retrieve_chunks_with_fake_embedding(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_repo = KnowledgeDocumentRepository(self.db)
        chunk_repo = KnowledgeChunkRepository(self.db)
        document_service = KnowledgeDocumentService(
            document_repo,
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="索引测试",
                parser_provider="local_basic",
                chunk_size=120,
                chunk_overlap=20,
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                rerank_enabled=False,
            ),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        source_file = user_dir / "adaptive.md"
        source_file.write_text(
            "\n\n".join(
                [
                    "Adaptive RAG uses routing to select retrieval strategy.",
                    "Mixture of experts and MoE routing can improve specialization.",
                    "This unrelated paragraph discusses user interface details.",
                ]
            ),
            encoding="utf-8",
        )

        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="adaptive.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/adaptive.md",
            ),
        )
        assert document is not None
        parse_result = document_service.parse_document(knowledge_base.id, document.id, self.user.id)
        self.assertIsNotNone(parse_result)
        assert parse_result is not None

        parsed_document = document_repo.get_by_user(document.id, self.user.id)
        assert parsed_document is not None
        index_service = KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=document_repo,
            setting_service=SettingService(UserSettingRepository(self.db)),
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        )
        index_result = index_service.index_document(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            document=parsed_document,
        )

        self.assertGreater(index_result.chunk_count, 0)
        self.assertIsNone(index_result.index_path)
        self.assertEqual(chunk_repo.count_by_knowledge_base(knowledge_base.id, self.user.id), index_result.chunk_count)
        persisted_chunks = chunk_repo.list_by_knowledge_base(knowledge_base.id, self.user.id)
        self.assertTrue(all(chunk.embedding is not None for chunk in persisted_chunks))
        self.assertTrue(all(len(chunk.embedding or []) == 128 for chunk in persisted_chunks))
        self.assertTrue(
            all(chunk.embedding_provider == knowledge_base.embedding_provider for chunk in persisted_chunks)
        )
        self.assertTrue(all(chunk.embedding_model == knowledge_base.embedding_model for chunk in persisted_chunks))
        self.assertTrue(
            all(chunk.embedding_dimensions == knowledge_base.embedding_dimensions for chunk in persisted_chunks)
        )

        results = index_service.retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="adaptive rag routing",
            top_k=3,
        )

        self.assertGreaterEqual(len(results), 1)
        self.assertIn("Adaptive RAG", results[0].chunk.content)

    def test_reindex_embedding_failure_preserves_previous_chunks_and_indexes(self) -> None:
        knowledge_base, parsed_document, chunk_repo, setting_service = self._create_indexed_markdown_knowledge_base(
            name="索引失败一致性测试",
            file_name="stable-index.md",
            content="The stable index contains the ORIGINAL-CONTENT marker.",
            retrieval_mode="hybrid",
        )
        old_chunks = chunk_repo.list_by_document(parsed_document.id, self.user.id)
        old_chunk_snapshot = [(chunk.id, chunk.vector_id, chunk.content) for chunk in old_chunks]
        lexical_path = KnowledgeLexicalStore(index_root=settings.knowledge_index_dir).index_path(knowledge_base.id)
        old_lexical_bytes = lexical_path.read_bytes()

        assert parsed_document.parsed_markdown_path is not None
        (Path(settings.upload_dir) / parsed_document.parsed_markdown_path).write_text(
            "The replacement text contains a NEW-CONTENT marker.",
            encoding="utf-8",
        )
        failing_index_service = KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=KnowledgeDocumentRepository(self.db),
            setting_service=setting_service,
            embedding_service=FailingKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
            lexical_store=KnowledgeLexicalStore(index_root=settings.knowledge_index_dir),
        )

        with self.assertRaisesRegex(RuntimeError, "fake embedding failure"):
            failing_index_service.index_document(
                user_id=self.user.id,
                knowledge_base=knowledge_base,
                document=parsed_document,
            )

        preserved_chunks = chunk_repo.list_by_document(parsed_document.id, self.user.id)
        self.assertEqual(
            [(chunk.id, chunk.vector_id, chunk.content) for chunk in preserved_chunks],
            old_chunk_snapshot,
        )
        self.assertEqual(lexical_path.read_bytes(), old_lexical_bytes)

    def test_reindex_lexical_failure_restores_previous_chunks_and_bm25(self) -> None:
        knowledge_base, parsed_document, chunk_repo, setting_service = self._create_indexed_markdown_knowledge_base(
            name="BM25 发布失败补偿测试",
            file_name="lexical-rollback.md",
            content="The stable lexical index contains the ORIGINAL-LEXICAL marker.",
            retrieval_mode="hybrid",
        )
        old_chunks = chunk_repo.list_by_document(parsed_document.id, self.user.id)
        old_chunk_snapshot = [
            (
                chunk.id,
                chunk.vector_id,
                chunk.content,
                list(chunk.embedding or []),
                chunk.embedding_model,
                chunk.embedding_version,
            )
            for chunk in old_chunks
        ]
        lexical_path = KnowledgeLexicalStore(index_root=settings.knowledge_index_dir).index_path(knowledge_base.id)
        old_lexical_bytes = lexical_path.read_bytes()

        assert parsed_document.parsed_markdown_path is not None
        (Path(settings.upload_dir) / parsed_document.parsed_markdown_path).write_text(
            "The replacement text contains a NEW-LEXICAL marker.",
            encoding="utf-8",
        )
        failing_index_service = KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=KnowledgeDocumentRepository(self.db),
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
            lexical_store=PublishThenFailKnowledgeLexicalStore(index_root=settings.knowledge_index_dir),
        )

        with self.assertRaisesRegex(RuntimeError, "fake lexical publish failure"):
            failing_index_service.index_document(
                user_id=self.user.id,
                knowledge_base=knowledge_base,
                document=parsed_document,
            )

        restored_chunks = chunk_repo.list_by_document(parsed_document.id, self.user.id)
        self.assertEqual(
            [
                (
                    chunk.id,
                    chunk.vector_id,
                    chunk.content,
                    list(chunk.embedding or []),
                    chunk.embedding_model,
                    chunk.embedding_version,
                )
                for chunk in restored_chunks
            ],
            old_chunk_snapshot,
        )
        self.assertEqual(lexical_path.read_bytes(), old_lexical_bytes)

    def test_reindex_unchanged_chunks_reuses_persisted_embeddings_without_api_call(self) -> None:
        knowledge_base, parsed_document, chunk_repo, setting_service = self._create_indexed_markdown_knowledge_base(
            name="Embedding 复用测试",
            file_name="embedding-reuse.md",
            content="The unchanged document keeps the same chunk content and embedding signature.",
        )
        old_chunks = chunk_repo.list_by_document(parsed_document.id, self.user.id)
        old_embeddings_by_hash = {chunk.content_hash: chunk.embedding for chunk in old_chunks}

        # 如果重索引仍调用 Embedding API，这个 fake 会立即让测试失败。
        index_service = KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=KnowledgeDocumentRepository(self.db),
            setting_service=setting_service,
            embedding_service=FailingKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
            lexical_store=KnowledgeLexicalStore(index_root=settings.knowledge_index_dir),
        )
        index_service.index_document(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            document=parsed_document,
        )

        rebuilt_chunks = chunk_repo.list_by_document(parsed_document.id, self.user.id)
        self.assertEqual(
            {chunk.content_hash: chunk.embedding for chunk in rebuilt_chunks},
            old_embeddings_by_hash,
        )

    def test_reindex_model_change_invalidates_persisted_embeddings(self) -> None:
        knowledge_base, parsed_document, chunk_repo, setting_service = self._create_indexed_markdown_knowledge_base(
            name="Embedding 签名失效测试",
            file_name="embedding-signature.md",
            content="The content stays unchanged while the embedding model changes.",
        )
        persisted_knowledge_base = KnowledgeBaseRepository(self.db).get_by_user(
            knowledge_base.id,
            self.user.id,
        )
        assert persisted_knowledge_base is not None
        persisted_knowledge_base.embedding_model = "another-embedding-model"
        self.db.add(persisted_knowledge_base)
        self.db.commit()

        index_service = KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=KnowledgeDocumentRepository(self.db),
            setting_service=setting_service,
            embedding_service=FailingKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
            lexical_store=KnowledgeLexicalStore(index_root=settings.knowledge_index_dir),
        )

        with self.assertRaisesRegex(RuntimeError, "fake embedding failure"):
            index_service.index_document(
                user_id=self.user.id,
                knowledge_base=persisted_knowledge_base,
                document=parsed_document,
            )

    def test_embedding_backfill_only_updates_missing_or_stale_chunks(self) -> None:
        knowledge_base, parsed_document, chunk_repo, setting_service = self._create_indexed_markdown_knowledge_base(
            name="Embedding backfill 测试",
            file_name="embedding-backfill.md",
            content="Backfill should update only chunks whose persisted vectors are missing.",
        )
        chunks = chunk_repo.list_by_document(parsed_document.id, self.user.id)
        self.assertGreaterEqual(len(chunks), 1)
        chunks[0].embedding = None
        chunks[0].embedding_provider = None
        chunks[0].embedding_model = None
        chunks[0].embedding_dimensions = None
        chunks[0].embedding_version = None
        self.db.add(chunks[0])
        self.db.commit()

        service = KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=KnowledgeDocumentRepository(self.db),
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
        )
        self.assertEqual(
            service.backfill_active_generation_embeddings(
                user_id=self.user.id,
                knowledge_base=knowledge_base,
            ),
            1,
        )
        self.assertEqual(
            service.backfill_active_generation_embeddings(
                user_id=self.user.id,
                knowledge_base=knowledge_base,
            ),
            0,
        )

        rebuilt_chunk = chunk_repo.get_by_user(chunks[0].id, self.user.id)
        assert rebuilt_chunk is not None
        self.assertIsNotNone(rebuilt_chunk.embedding)
        self.assertEqual(rebuilt_chunk.embedding_model, knowledge_base.embedding_model)
        self.assertEqual(rebuilt_chunk.embedding_dimensions, knowledge_base.embedding_dimensions)
        self.assertEqual(rebuilt_chunk.embedding_version, KnowledgeIndexService.EMBEDDING_VERSION)

    def test_embedding_validation_rejects_non_finite_values(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "非有限数值"):
            KnowledgeIndexService._validate_vectors(
                vectors=[[1.0, float("nan")]],
                expected_count=1,
                dimensions=2,
            )

    def test_openai_embedding_client_is_closed_after_request(self) -> None:
        setting_service = SettingService(UserSettingRepository(self.db))
        setting_service.update_user_settings(
            self.user.id,
            UserSettingUpdate(knowledge_embedding_api_key="embedding-test-key"),
        )
        knowledge_base = SimpleNamespace(
            embedding_provider="openai-compatible",
            embedding_model="fake-embedding",
        )
        client = Mock()
        client.embeddings = SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    data=[SimpleNamespace(embedding=[0.1, 0.2])],
                )
            )
        )
        client.close = AsyncMock()

        with patch("app.services.knowledge_index_service.AsyncOpenAI", return_value=client):
            vectors = asyncio.run(
                KnowledgeEmbeddingService(setting_service).embed_texts(
                    user_id=self.user.id,
                    knowledge_base=knowledge_base,
                    texts=["query"],
                )
            )

        self.assertEqual(vectors, [[0.1, 0.2]])
        client.close.assert_awaited_once()

    def test_rag6_eval_set_runs_retrieval_baseline(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_repo = KnowledgeDocumentRepository(self.db)
        chunk_repo = KnowledgeChunkRepository(self.db)
        setting_service = SettingService(UserSettingRepository(self.db))
        document_service = KnowledgeDocumentService(
            document_repo,
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="RAG6 评测测试",
                parser_provider="local_basic",
                chunk_size=120,
                chunk_overlap=20,
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                rerank_enabled=False,
                retrieval_top_k=3,
                rerank_top_n=3,
                score_threshold=0,
            ),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        source_file = user_dir / "rag6.md"
        source_file.write_text(
            "\n\n".join(
                [
                    "Adaptive RAG uses routing to select retrieval strategy.",
                    "Mixture of experts and MoE routing can improve specialization.",
                    "This unrelated paragraph discusses user interface details.",
                ]
            ),
            encoding="utf-8",
        )
        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="rag6.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/rag6.md",
            ),
        )
        assert document is not None
        document_service.parse_document(knowledge_base.id, document.id, self.user.id)
        parsed_document = document_repo.get_by_user(document.id, self.user.id)
        assert parsed_document is not None
        KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=document_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        ).index_document(user_id=self.user.id, knowledge_base=knowledge_base, document=parsed_document)

        expected_chunk = chunk_repo.list_by_knowledge_base(knowledge_base.id, self.user.id)[0]
        service = KnowledgeEvaluationService(
            base_repo=KnowledgeBaseRepository(self.db),
            chunk_repo=chunk_repo,
            eval_set_repo=KnowledgeEvalSetRepository(self.db),
            eval_case_repo=KnowledgeEvalCaseRepository(self.db),
            eval_run_repo=KnowledgeEvalRunRepository(self.db),
            eval_result_repo=KnowledgeEvalResultRepository(self.db),
            setting_service=setting_service,
            retrieval_pipeline=KnowledgeRetrievalPipeline(
                chunk_repo=chunk_repo,
                setting_service=setting_service,
                embedding_service=FakeKnowledgeEmbeddingService(),
                vector_search=InMemoryVectorSearch(self.db),
            ),
        )

        eval_set = service.create_eval_set(
            knowledge_base.id,
            self.user.id,
            KnowledgeEvalSetCreate(name="核心问题"),
        )
        self.assertIsNotNone(eval_set)
        assert eval_set is not None
        eval_case = service.add_eval_case(
            knowledge_base.id,
            eval_set.id,
            self.user.id,
            KnowledgeEvalCaseCreate(
                query="adaptive rag routing",
                expected_chunk_id=expected_chunk.id,
            ),
        )
        self.assertIsNotNone(eval_case)

        outcome = service.run_eval(
            knowledge_base.id,
            eval_set.id,
            self.user.id,
            KnowledgeEvalRunRequest(top_k=3),
        )

        self.assertIsNotNone(outcome)
        assert outcome is not None
        self.assertEqual(outcome.run.status, "succeeded")
        self.assertEqual(outcome.run.metrics["case_count"], 1)
        self.assertEqual(outcome.run.metrics["hit_at_k"], 1.0)
        self.assertGreater(float(outcome.run.metrics["mrr"]), 0)
        self.assertGreaterEqual(float(outcome.run.metrics["avg_elapsed_ms"]), 0)
        self.assertEqual(outcome.run.metrics["failure_count"], 0)
        self.assertEqual(outcome.run.metrics["fallback_count"], 0)
        self.assertEqual(
            outcome.run.metrics["config_snapshot"]["embedding"]["version"],
            "l2-normalized-v1",
        )
        self.assertEqual(len(outcome.results), 1)
        self.assertTrue(outcome.results[0].hit_at_k)
        saved_eval_set = KnowledgeEvalSetRepository(self.db).get_by_user(eval_set.id, self.user.id)
        assert saved_eval_set is not None
        KnowledgeEvalSetRepository(self.db).delete(saved_eval_set)
        self.assertEqual(len(KnowledgeEvalCaseRepository(self.db).list_by_eval_set(eval_set.id, self.user.id)), 0)
        self.assertEqual(len(KnowledgeEvalRunRepository(self.db).list_by_eval_set(eval_set.id, self.user.id)), 0)

    def test_eval_chunk_target_does_not_degrade_to_same_document_match(self) -> None:
        document_id = str(uuid4())
        expected_chunk_id = str(uuid4())
        same_document_wrong_chunk = KnowledgeChunk(
            id=str(uuid4()),
            user_id=self.user.id,
            knowledge_base_id=str(uuid4()),
            document_id=document_id,
            index_generation="legacy",
            vector_id=0,
            chunk_index=0,
            content="Same document, but not the expected evidence chunk.",
        )

        metrics = KnowledgeEvaluationService._score_case(
            retrieved=[RetrievalResult(chunk=same_document_wrong_chunk, score=0.9, metadata={})],
            expected_chunk_id=expected_chunk_id,
            expected_document_id=document_id,
        )

        self.assertFalse(metrics["hit_at_k"])
        self.assertEqual(metrics["mrr"], 0.0)
        self.assertEqual(metrics["context_precision"], 0.0)
        self.assertEqual(metrics["context_recall"], 0.0)

    def test_eval_document_only_target_matches_any_chunk_from_document(self) -> None:
        document_id = str(uuid4())
        document_chunk = KnowledgeChunk(
            id=str(uuid4()),
            user_id=self.user.id,
            knowledge_base_id=str(uuid4()),
            document_id=document_id,
            index_generation="legacy",
            vector_id=0,
            chunk_index=0,
            content="A chunk from the expected document.",
        )

        metrics = KnowledgeEvaluationService._score_case(
            retrieved=[RetrievalResult(chunk=document_chunk, score=0.9, metadata={})],
            expected_chunk_id=None,
            expected_document_id=document_id,
        )

        self.assertTrue(metrics["hit_at_k"])
        self.assertEqual(metrics["mrr"], 1.0)

    def test_eval_document_target_uses_first_match_and_all_relevant_chunks(self) -> None:
        document_id = str(uuid4())
        first_chunk = KnowledgeChunk(
            id=str(uuid4()),
            user_id=self.user.id,
            knowledge_base_id=str(uuid4()),
            document_id=document_id,
            index_generation="legacy",
            vector_id=0,
            chunk_index=0,
            content="First relevant chunk.",
        )
        second_chunk = KnowledgeChunk(
            id=str(uuid4()),
            user_id=self.user.id,
            knowledge_base_id=first_chunk.knowledge_base_id,
            document_id=document_id,
            index_generation="legacy",
            vector_id=1,
            chunk_index=1,
            content="Second relevant chunk.",
        )

        metrics = KnowledgeEvaluationService._score_case(
            retrieved=[
                RetrievalResult(chunk=first_chunk, score=0.9, metadata={}),
                RetrievalResult(chunk=second_chunk, score=0.8, metadata={}),
            ],
            expected_chunk_id=None,
            expected_document_id=document_id,
        )

        self.assertEqual(metrics["mrr"], 1.0)
        self.assertEqual(metrics["context_precision"], 1.0)
        self.assertEqual(metrics["ndcg_at_k"], 1.0)

    def test_eval_multiple_chunk_targets_measure_recall_and_return_matches(self) -> None:
        knowledge_base_id = str(uuid4())
        document_a = str(uuid4())
        document_b = str(uuid4())
        first_target = KnowledgeChunk(
            id=str(uuid4()),
            user_id=self.user.id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_a,
            index_generation="legacy",
            vector_id=0,
            chunk_index=0,
            content="Evidence A",
        )
        second_target = KnowledgeChunk(
            id=str(uuid4()),
            user_id=self.user.id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_b,
            index_generation="legacy",
            vector_id=1,
            chunk_index=1,
            content="Evidence B",
        )
        distractor = KnowledgeChunk(
            id=str(uuid4()),
            user_id=self.user.id,
            knowledge_base_id=knowledge_base_id,
            document_id=str(uuid4()),
            index_generation="legacy",
            vector_id=2,
            chunk_index=2,
            content="Distractor",
        )

        metrics = KnowledgeEvaluationService._score_case(
            retrieved=[
                RetrievalResult(chunk=first_target, score=0.9, metadata={}),
                RetrievalResult(chunk=distractor, score=0.8, metadata={}),
            ],
            expected_chunk_ids=[first_target.id, second_target.id],
            expected_document_ids=[document_a, document_b],
        )

        self.assertTrue(metrics["hit_at_k"])
        self.assertEqual(metrics["matched_chunk_ids"], [first_target.id])
        self.assertEqual(metrics["ground_truth_target_count"], 2)
        self.assertEqual(metrics["context_recall"], 0.5)
        self.assertEqual(metrics["context_precision"], 0.5)

    def test_eval_score_includes_ndcg_and_keyword_coverage(self) -> None:
        expected_chunk = KnowledgeChunk(
            id=str(uuid4()),
            user_id=self.user.id,
            knowledge_base_id=str(uuid4()),
            document_id=str(uuid4()),
            index_generation="legacy",
            vector_id=0,
            chunk_index=0,
            content="Adaptive retrieval uses routing and reranking.",
        )
        unrelated_chunk = KnowledgeChunk(
            id=str(uuid4()),
            user_id=self.user.id,
            knowledge_base_id=expected_chunk.knowledge_base_id,
            document_id=expected_chunk.document_id,
            index_generation="legacy",
            vector_id=1,
            chunk_index=1,
            content="This chunk is unrelated.",
        )

        metrics = KnowledgeEvaluationService._score_case(
            retrieved=[
                RetrievalResult(chunk=expected_chunk, score=0.9, metadata={}),
                RetrievalResult(chunk=unrelated_chunk, score=0.1, metadata={}),
            ],
            expected_chunk_id=expected_chunk.id,
            expected_document_id=expected_chunk.document_id,
            expected_answer_keywords=["adaptive", "routing", "missing"],
        )

        self.assertEqual(metrics["ndcg_at_k"], 1.0)
        self.assertAlmostEqual(metrics["expected_keyword_recall"], 2 / 3)
        self.assertEqual(metrics["expected_keyword_hits"], ["adaptive", "routing"])

    def test_score_answer_is_explicitly_deterministic(self) -> None:
        result = KnowledgeEvaluationService.score_answer(
            answer_text="Adaptive routing is enabled.",
            expected_answer_keywords=["adaptive", "routing", "rerank"],
        )

        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["matched_keywords"], ["adaptive", "routing"])
        self.assertAlmostEqual(result["keyword_recall"], 2 / 3)

    def test_eval_empty_set_is_rejected_before_creating_run(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(name="空评测集知识库"),
        )
        assert knowledge_base is not None
        service = KnowledgeEvaluationService(
            base_repo=KnowledgeBaseRepository(self.db),
            chunk_repo=KnowledgeChunkRepository(self.db),
            eval_set_repo=KnowledgeEvalSetRepository(self.db),
            eval_case_repo=KnowledgeEvalCaseRepository(self.db),
            eval_run_repo=KnowledgeEvalRunRepository(self.db),
            eval_result_repo=KnowledgeEvalResultRepository(self.db),
            setting_service=SettingService(UserSettingRepository(self.db)),
        )
        eval_set = service.create_eval_set(
            knowledge_base.id,
            self.user.id,
            KnowledgeEvalSetCreate(name="没有 Case"),
        )
        assert eval_set is not None

        with self.assertRaisesRegex(ValueError, "还没有 Case"):
            service.run_eval(
                knowledge_base.id,
                eval_set.id,
                self.user.id,
                KnowledgeEvalRunRequest(),
            )

        self.assertEqual(
            KnowledgeEvalRunRepository(self.db).list_by_eval_set(eval_set.id, self.user.id),
            [],
        )

    def test_eval_run_sanitizes_case_exception(self) -> None:
        knowledge_base, document, chunk_repo, setting_service = self._create_indexed_markdown_knowledge_base(
            name="评测异常脱敏",
            file_name="eval-sanitized.md",
            content="Evaluation errors must not expose provider details.",
        )
        expected_chunk = chunk_repo.list_by_document(document.id, self.user.id)[0]
        service = KnowledgeEvaluationService(
            base_repo=KnowledgeBaseRepository(self.db),
            chunk_repo=chunk_repo,
            eval_set_repo=KnowledgeEvalSetRepository(self.db),
            eval_case_repo=KnowledgeEvalCaseRepository(self.db),
            eval_run_repo=KnowledgeEvalRunRepository(self.db),
            eval_result_repo=KnowledgeEvalResultRepository(self.db),
            setting_service=setting_service,
            retrieval_pipeline=FailingKnowledgeEvaluationPipeline(),  # type: ignore[arg-type]
        )
        eval_set = service.create_eval_set(
            knowledge_base.id,
            self.user.id,
            KnowledgeEvalSetCreate(name="异常脱敏集"),
        )
        assert eval_set is not None
        service.add_eval_case(
            knowledge_base.id,
            eval_set.id,
            self.user.id,
            KnowledgeEvalCaseCreate(query="evaluation error", expected_chunk_id=expected_chunk.id),
        )

        outcome = service.run_eval(
            knowledge_base.id,
            eval_set.id,
            self.user.id,
            KnowledgeEvalRunRequest(top_k=3),
        )

        assert outcome is not None
        self.assertEqual(outcome.run.status, "partial")
        self.assertEqual(outcome.run.metrics["case_errors"][0]["error_code"], "knowledge_retrieval_failed")
        self.assertNotIn("provider.internal", outcome.run.metrics["case_errors"][0]["message"])
        self.assertNotIn("secret-value", outcome.run.metrics["case_errors"][0]["message"])
        self.assertNotIn("provider.internal", outcome.run.error_message or "")

    def test_eval_run_overrides_are_detached_and_snapshotted(self) -> None:
        knowledge_base, document, chunk_repo, setting_service = self._create_indexed_markdown_knowledge_base(
            name="评测临时配置测试",
            file_name="eval-override.md",
            content="Adaptive retrieval uses both lexical and semantic evidence.",
            retrieval_mode="vector",
        )
        expected_chunk = chunk_repo.list_by_document(document.id, self.user.id)[0]
        captured_configs: list[object] = []
        pipeline = Mock()

        def retrieve(**kwargs):  # noqa: ANN003, ANN202
            captured_configs.append(kwargs["knowledge_base"])
            return [RetrievalResult(chunk=expected_chunk, score=0.9, metadata={})]

        pipeline.retrieve.side_effect = retrieve
        service = KnowledgeEvaluationService(
            base_repo=KnowledgeBaseRepository(self.db),
            chunk_repo=chunk_repo,
            eval_set_repo=KnowledgeEvalSetRepository(self.db),
            eval_case_repo=KnowledgeEvalCaseRepository(self.db),
            eval_run_repo=KnowledgeEvalRunRepository(self.db),
            eval_result_repo=KnowledgeEvalResultRepository(self.db),
            setting_service=setting_service,
            retrieval_pipeline=pipeline,
        )
        eval_set = service.create_eval_set(
            knowledge_base.id,
            self.user.id,
            KnowledgeEvalSetCreate(name="临时配置对照组"),
        )
        assert eval_set is not None
        service.add_eval_case(
            knowledge_base.id,
            eval_set.id,
            self.user.id,
            KnowledgeEvalCaseCreate(query="What is adaptive retrieval?", expected_chunk_id=expected_chunk.id),
        )

        outcome = service.run_eval(
            knowledge_base.id,
            eval_set.id,
            self.user.id,
            KnowledgeEvalRunRequest(
                top_k=7,
                retrieval_mode="hybrid",
                rerank_enabled=True,
            ),
        )

        assert outcome is not None
        self.assertEqual(outcome.run.retrieval_mode, "hybrid")
        self.assertEqual(outcome.run.top_k, 7)
        self.assertTrue(outcome.run.rerank_enabled)
        self.assertEqual(len(captured_configs), 1)
        self.assertEqual(captured_configs[0].retrieval_mode, "hybrid")
        self.assertTrue(captured_configs[0].rerank_enabled)
        snapshot = outcome.run.metrics["config_snapshot"]
        self.assertEqual(snapshot["active_index_generation"], knowledge_base.active_index_generation)
        self.assertEqual(snapshot["retrieval_mode"], "hybrid")
        self.assertEqual(snapshot["top_k"], 7)
        self.assertEqual(snapshot["rrf_k"], 60)
        self.assertEqual(snapshot["chunking"]["size"], knowledge_base.chunk_size)

        self.db.expire_all()
        persisted_base = KnowledgeBaseRepository(self.db).get_by_user(knowledge_base.id, self.user.id)
        assert persisted_base is not None
        self.assertEqual(persisted_base.retrieval_mode, "vector")
        self.assertFalse(persisted_base.rerank_enabled)

    def test_reindex_clears_stale_expected_chunk_but_preserves_eval_case_document_target(self) -> None:
        self.db.execute(text("PRAGMA foreign_keys = ON"))
        self.db.commit()
        knowledge_base, parsed_document, chunk_repo, setting_service = self._create_indexed_markdown_knowledge_base(
            name="评测引用重索引测试",
            file_name="eval-reference.md",
            content="The ORIGINAL evaluation chunk describes adaptive retrieval.",
        )
        expected_chunk = chunk_repo.list_by_document(parsed_document.id, self.user.id)[0]
        stable_document = KnowledgeDocument(
            id=str(uuid4()),
            knowledge_base_id=knowledge_base.id,
            user_id=self.user.id,
            file_name="stable-evidence.md",
            mime_type="text/markdown",
            file_size=32,
            storage_key=f"{self.user.id}/stable-evidence.md",
            parser_provider="local_basic",
            parse_status="parsed",
            index_status="indexed",
            document_version=1,
        )
        stable_chunk = KnowledgeChunk(
            id=str(uuid4()),
            user_id=self.user.id,
            knowledge_base_id=knowledge_base.id,
            document_id=stable_document.id,
            index_generation="legacy",
            chunk_index=0,
            vector_id=100,
            content="Stable evidence chunk.",
            content_hash=hashlib.sha256(b"Stable evidence chunk.").hexdigest(),
            char_count=22,
            token_estimate=6,
        )
        self.db.add_all([stable_document, stable_chunk])
        self.db.commit()
        eval_service = KnowledgeEvaluationService(
            base_repo=KnowledgeBaseRepository(self.db),
            chunk_repo=chunk_repo,
            eval_set_repo=KnowledgeEvalSetRepository(self.db),
            eval_case_repo=KnowledgeEvalCaseRepository(self.db),
            eval_run_repo=KnowledgeEvalRunRepository(self.db),
            eval_result_repo=KnowledgeEvalResultRepository(self.db),
            setting_service=setting_service,
        )
        eval_set = eval_service.create_eval_set(
            knowledge_base.id,
            self.user.id,
            KnowledgeEvalSetCreate(name="重索引回归集"),
        )
        assert eval_set is not None
        eval_case = eval_service.add_eval_case(
            knowledge_base.id,
            eval_set.id,
            self.user.id,
            KnowledgeEvalCaseCreate(
                query="What is adaptive retrieval?",
                expected_chunk_id=expected_chunk.id,
            ),
        )
        assert eval_case is not None
        self.assertEqual(eval_case.expected_document_id, parsed_document.id)
        persisted_eval_case = self.db.get(KnowledgeEvalCase, eval_case.id)
        assert persisted_eval_case is not None
        persisted_eval_case.expected_chunk_ids_json = json.dumps([expected_chunk.id, stable_chunk.id])
        self.db.commit()

        assert parsed_document.parsed_markdown_path is not None
        (Path(settings.upload_dir) / parsed_document.parsed_markdown_path).write_text(
            "The NEW evaluation chunk describes adaptive retrieval after an update.",
            encoding="utf-8",
        )
        KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=KnowledgeDocumentRepository(self.db),
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
            lexical_store=KnowledgeLexicalStore(index_root=settings.knowledge_index_dir),
        ).index_document(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            document=parsed_document,
        )

        self.db.expire_all()
        preserved_case = KnowledgeEvalCaseRepository(self.db).list_by_eval_set(eval_set.id, self.user.id)[0]
        self.assertEqual(preserved_case.expected_document_id, parsed_document.id)
        self.assertEqual(preserved_case.expected_chunk_id, stable_chunk.id)
        self.assertEqual(json.loads(preserved_case.expected_chunk_ids_json or "[]"), [stable_chunk.id])

    def test_eval_case_rejects_expected_targets_from_another_knowledge_base(self) -> None:
        knowledge_base, _, chunk_repo, setting_service = self._create_indexed_markdown_knowledge_base(
            name="当前评测知识库",
            file_name="current-eval.md",
            content="Current knowledge base content.",
        )
        other_base, other_document, _, _ = self._create_indexed_markdown_knowledge_base(
            name="其他评测知识库",
            file_name="other-eval.md",
            content="Other knowledge base content.",
        )
        other_chunk = chunk_repo.list_by_knowledge_base(other_base.id, self.user.id)[0]
        eval_service = KnowledgeEvaluationService(
            base_repo=KnowledgeBaseRepository(self.db),
            chunk_repo=chunk_repo,
            eval_set_repo=KnowledgeEvalSetRepository(self.db),
            eval_case_repo=KnowledgeEvalCaseRepository(self.db),
            eval_run_repo=KnowledgeEvalRunRepository(self.db),
            eval_result_repo=KnowledgeEvalResultRepository(self.db),
            setting_service=setting_service,
        )
        eval_set = eval_service.create_eval_set(
            knowledge_base.id,
            self.user.id,
            KnowledgeEvalSetCreate(name="归属边界测试集"),
        )
        assert eval_set is not None

        with self.assertRaisesRegex(ValueError, "期望 Chunk"):
            eval_service.add_eval_case(
                knowledge_base.id,
                eval_set.id,
                self.user.id,
                KnowledgeEvalCaseCreate(query="invalid chunk", expected_chunk_id=other_chunk.id),
            )
        with self.assertRaisesRegex(ValueError, "期望文档"):
            eval_service.add_eval_case(
                knowledge_base.id,
                eval_set.id,
                self.user.id,
                KnowledgeEvalCaseCreate(query="invalid document", expected_document_id=other_document.id),
            )

    def test_delete_document_reports_conflict_when_evaluation_case_still_references_it(self) -> None:
        self.db.execute(text("PRAGMA foreign_keys = ON"))
        self.db.commit()
        knowledge_base, parsed_document, chunk_repo, setting_service = self._create_indexed_markdown_knowledge_base(
            name="文档删除冲突测试",
            file_name="protected-eval.md",
            content="This document is protected by an evaluation case.",
        )
        expected_chunk = chunk_repo.list_by_document(parsed_document.id, self.user.id)[0]
        eval_service = KnowledgeEvaluationService(
            base_repo=KnowledgeBaseRepository(self.db),
            chunk_repo=chunk_repo,
            eval_set_repo=KnowledgeEvalSetRepository(self.db),
            eval_case_repo=KnowledgeEvalCaseRepository(self.db),
            eval_run_repo=KnowledgeEvalRunRepository(self.db),
            eval_result_repo=KnowledgeEvalResultRepository(self.db),
            setting_service=setting_service,
        )
        eval_set = eval_service.create_eval_set(
            knowledge_base.id,
            self.user.id,
            KnowledgeEvalSetCreate(name="保护文档的评测集"),
        )
        assert eval_set is not None
        eval_case = eval_service.add_eval_case(
            knowledge_base.id,
            eval_set.id,
            self.user.id,
            KnowledgeEvalCaseCreate(
                query="Why is this document protected?",
                expected_chunk_id=expected_chunk.id,
            ),
        )
        assert eval_case is not None

        # Exercise the new multi-document JSON protection independently of the
        # legacy scalar column.  The document must remain undeletable even when
        # an older client did not populate ``expected_document_id``.
        eval_case.expected_document_id = None
        self.db.commit()
        document_service = KnowledgeDocumentService(
            KnowledgeDocumentRepository(self.db),
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )

        with self.assertRaisesRegex(RuntimeError, "评测用例"):
            document_service.delete_document(
                knowledge_base.id,
                parsed_document.id,
                self.user.id,
            )

        self.assertIsNotNone(
            KnowledgeDocumentRepository(self.db).get_by_user(parsed_document.id, self.user.id)
        )
        self.assertEqual(
            len(KnowledgeEvalCaseRepository(self.db).list_by_eval_set(eval_set.id, self.user.id)),
            1,
        )

    def test_rag61_retrieval_test_supports_metadata_filters(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_repo = KnowledgeDocumentRepository(self.db)
        chunk_repo = KnowledgeChunkRepository(self.db)
        setting_service = SettingService(UserSettingRepository(self.db))
        document_service = KnowledgeDocumentService(
            document_repo,
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="RAG6.1 过滤测试",
                parser_provider="local_basic",
                chunk_size=120,
                chunk_overlap=10,
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                rerank_enabled=False,
                retrieval_top_k=5,
                rerank_top_n=3,
                score_threshold=0,
            ),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        adaptive_file = user_dir / "adaptive.md"
        expert_file = user_dir / "expert.txt"
        adaptive_file.write_text("Adaptive RAG uses routing to select retrieval strategy.", encoding="utf-8")
        expert_file.write_text("Mixture of experts and MoE routing can improve specialization.", encoding="utf-8")

        adaptive_document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="adaptive.md",
                mime_type="text/markdown",
                file_size=adaptive_file.stat().st_size,
                storage_key=f"{self.user.id}/adaptive.md",
            ),
        )
        expert_document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="expert.txt",
                mime_type="text/plain",
                file_size=expert_file.stat().st_size,
                storage_key=f"{self.user.id}/expert.txt",
            ),
        )
        assert adaptive_document is not None
        assert expert_document is not None
        document_service.parse_document(knowledge_base.id, adaptive_document.id, self.user.id)
        document_service.parse_document(knowledge_base.id, expert_document.id, self.user.id)

        index_service = KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=document_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        )
        parsed_adaptive = document_repo.get_by_user(adaptive_document.id, self.user.id)
        parsed_expert = document_repo.get_by_user(expert_document.id, self.user.id)
        assert parsed_adaptive is not None
        assert parsed_expert is not None
        index_service.index_document(user_id=self.user.id, knowledge_base=knowledge_base, document=parsed_adaptive)
        index_service.index_document(user_id=self.user.id, knowledge_base=knowledge_base, document=parsed_expert)

        filtered_by_document = KnowledgeRetrievalPipeline(
            chunk_repo=chunk_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        ).retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="adaptive rag routing",
            top_k=5,
            filters=KnowledgeRetrievalFilter(document_ids=[expert_document.id]),
        )

        self.assertGreaterEqual(len(filtered_by_document), 1)
        self.assertTrue(all(result.chunk.document_id == expert_document.id for result in filtered_by_document))

        filtered_by_type = KnowledgeRetrievalPipeline(
            chunk_repo=chunk_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        ).retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="adaptive rag routing",
            top_k=5,
            filters=KnowledgeRetrievalFilter(file_types=["markdown"]),
        )

        self.assertGreaterEqual(len(filtered_by_type), 1)
        self.assertTrue(all(result.chunk.document_id == adaptive_document.id for result in filtered_by_type))

        with self.assertRaises(ValueError):
            document_service.test_retrieval(
                knowledge_base.id,
                self.user.id,
                query="adaptive rag routing",
                document_ids=[str(uuid4())],
            )

    def test_rag63_lexical_retrieval_uses_bm25(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_repo = KnowledgeDocumentRepository(self.db)
        chunk_repo = KnowledgeChunkRepository(self.db)
        setting_service = SettingService(UserSettingRepository(self.db))
        document_service = KnowledgeDocumentService(
            document_repo,
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="RAG6.3 BM25 测试",
                parser_provider="local_basic",
                chunk_size=120,
                chunk_overlap=10,
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                rerank_enabled=False,
                retrieval_mode="lexical",
                retrieval_top_k=5,
                rerank_top_n=3,
                score_threshold=0,
            ),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        source_file = user_dir / "lexical.md"
        source_file.write_text(
            "\n\n".join(
                [
                    "This paragraph describes generic adaptive retrieval.",
                    "The exact internal code ZXQ-42 appears only in this paragraph.",
                    "This paragraph discusses user interface rendering.",
                ]
            ),
            encoding="utf-8",
        )
        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="lexical.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/lexical.md",
            ),
        )
        assert document is not None
        document_service.parse_document(knowledge_base.id, document.id, self.user.id)
        parsed_document = document_repo.get_by_user(document.id, self.user.id)
        assert parsed_document is not None
        KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=document_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        ).index_document(user_id=self.user.id, knowledge_base=knowledge_base, document=parsed_document)

        results = KnowledgeRetrievalPipeline(
            chunk_repo=chunk_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        ).retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="ZXQ-42",
            top_k=5,
        )

        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].rank_source, "lexical")
        self.assertIn("ZXQ-42", results[0].chunk.content)
        self.assertIn("lexical_score", results[0].metadata)

    def test_vector_retrieval_consumes_search_hits_without_repository_rescan(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="按命中向量取 chunk",
                parser_provider="local_basic",
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                rerank_enabled=False,
                retrieval_mode="vector",
                retrieval_top_k=2,
                rerank_top_n=2,
                score_threshold=0,
            ),
        )
        assert knowledge_base is not None
        knowledge_base_model = KnowledgeBaseRepository(self.db).get_by_user(knowledge_base.id, self.user.id)
        assert knowledge_base_model is not None
        document = KnowledgeDocument(
            knowledge_base_id=knowledge_base.id,
            user_id=self.user.id,
            file_name="vector-hit.md",
            mime_type="text/markdown",
            file_size=100,
            storage_key=f"{self.user.id}/vector-hit.md",
            parse_status="parsed",
            index_status="indexed",
        )
        self.db.add(document)
        self.db.commit()
        self.db.refresh(document)
        chunks = []
        for vector_id in range(1, 5):
            content = f"vector chunk {vector_id} adaptive rag routing"
            chunks.append(
                KnowledgeChunk(
                    user_id=self.user.id,
                    knowledge_base_id=knowledge_base.id,
                    document_id=document.id,
                    chunk_index=vector_id - 1,
                    vector_id=vector_id,
                    content=content,
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    char_count=len(content),
                    token_estimate=max(1, len(content) // 2),
                    source_start=vector_id * 10,
                    source_end=vector_id * 10 + len(content),
                    metadata_json=json.dumps({"file_name": "vector-hit.md"}, ensure_ascii=False),
                )
            )
        self.db.add_all(chunks)
        self.db.commit()

        results = KnowledgeRetrievalPipeline(
            chunk_repo=GuardedKnowledgeChunkRepository(self.db),
            setting_service=SettingService(UserSettingRepository(self.db)),
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=StaticVectorSearch(
                hits=[
                    KnowledgeVectorSearchHit(chunk=chunks[2], score=0.91),
                    KnowledgeVectorSearchHit(chunk=chunks[0], score=0.82),
                ]
            ),
        ).retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base_model,
            query="adaptive rag routing",
            top_k=2,
        )

        self.assertEqual([result.chunk.vector_id for result in results], [3, 1])
        self.assertEqual([result.score for result in results], [0.91, 0.82])

    def test_rag64_hybrid_retrieval_uses_rrf_fusion(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_repo = KnowledgeDocumentRepository(self.db)
        chunk_repo = KnowledgeChunkRepository(self.db)
        setting_service = SettingService(UserSettingRepository(self.db))
        document_service = KnowledgeDocumentService(
            document_repo,
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="RAG6.4 Hybrid 测试",
                parser_provider="local_basic",
                chunk_size=120,
                chunk_overlap=10,
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                rerank_enabled=False,
                retrieval_mode="hybrid",
                retrieval_top_k=5,
                rerank_top_n=3,
                score_threshold=0,
            ),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        source_file = user_dir / "hybrid.md"
        source_file.write_text(
            "\n\n".join(
                [
                    "Adaptive RAG uses routing to select retrieval strategy.",
                    "The exact internal code ZXQ-42 appears only in this paragraph.",
                    "Mixture of experts and MoE routing can improve specialization.",
                ]
            ),
            encoding="utf-8",
        )
        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="hybrid.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/hybrid.md",
            ),
        )
        assert document is not None
        document_service.parse_document(knowledge_base.id, document.id, self.user.id)
        parsed_document = document_repo.get_by_user(document.id, self.user.id)
        assert parsed_document is not None
        KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=document_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        ).index_document(user_id=self.user.id, knowledge_base=knowledge_base, document=parsed_document)

        results = KnowledgeRetrievalPipeline(
            chunk_repo=chunk_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        ).retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="adaptive ZXQ-42",
            top_k=5,
        )

        self.assertGreaterEqual(len(results), 1)
        self.assertTrue(all(result.rank_source == "hybrid_rrf" for result in results))
        self.assertIn("rrf_score", results[0].metadata)
        self.assertTrue(
            any(result.metadata.get("vector_rank") is not None for result in results)
            and any(result.metadata.get("lexical_rank") is not None for result in results)
        )

    def test_rag66_index_document_persists_bm25_inverted_index(self) -> None:
        knowledge_base, _document, _chunk_repo, _setting_service = self._create_indexed_markdown_knowledge_base(
            name="RAG6.6 BM25 持久化索引",
            file_name="persistent-bm25.md",
            retrieval_mode="lexical",
            content="\n\n".join(
                [
                    "Adaptive RAG uses semantic routing.",
                    "The unique marker PERSISTENT-BM25-42 is stored in the lexical index.",
                    "A different paragraph talks about frontend styling.",
                ]
            ),
        )

        lexical_store = KnowledgeLexicalStore(index_root=settings.knowledge_index_dir)
        index_path = lexical_store.index_path(knowledge_base.id)
        self.assertTrue(index_path.exists())
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("version"), KnowledgeLexicalStore.VERSION)
        self.assertEqual(payload.get("knowledge_base_id"), knowledge_base.id)
        self.assertGreater(int(payload.get("doc_count") or 0), 0)
        self.assertIn("persistent-bm25-42", payload.get("postings") or {})

    def test_rag66_lexical_retrieval_reads_persistent_bm25_index(self) -> None:
        knowledge_base, _document, chunk_repo, setting_service = self._create_indexed_markdown_knowledge_base(
            name="RAG6.6 BM25 持久化检索",
            file_name="persistent-search.md",
            retrieval_mode="lexical",
            content="\n\n".join(
                [
                    "General retrieval discussion appears here.",
                    "The exact identifier LEXICAL-PERSIST-99 should be the top lexical hit.",
                    "Model settings and layout are unrelated.",
                ]
            ),
        )

        results = KnowledgeRetrievalPipeline(
            chunk_repo=chunk_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
            lexical_store=KnowledgeLexicalStore(index_root=settings.knowledge_index_dir),
        ).retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="LEXICAL-PERSIST-99",
            top_k=5,
        )

        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].rank_source, "lexical")
        self.assertIn("LEXICAL-PERSIST-99", results[0].chunk.content)
        self.assertEqual(results[0].metadata.get("lexical_index"), "persistent")
        self.assertIn("lexical_score", results[0].metadata)

    def test_rag66_lexical_retrieval_lazy_rebuilds_missing_index(self) -> None:
        knowledge_base, _document, chunk_repo, setting_service = self._create_indexed_markdown_knowledge_base(
            name="RAG6.6 BM25 懒重建",
            file_name="lazy-bm25.md",
            retrieval_mode="lexical",
            content="\n\n".join(
                [
                    "This paragraph is not important.",
                    "The token LAZY-BM25-REBUILD appears after the original index is deleted.",
                    "Other content is used as noise.",
                ]
            ),
        )
        lexical_store = KnowledgeLexicalStore(index_root=settings.knowledge_index_dir)
        index_path = lexical_store.index_path(knowledge_base.id)
        self.assertTrue(index_path.exists())
        index_path.unlink()
        self.assertFalse(index_path.exists())

        results = KnowledgeRetrievalPipeline(
            chunk_repo=chunk_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
            lexical_store=lexical_store,
        ).retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="LAZY-BM25-REBUILD",
            top_k=5,
        )

        self.assertGreaterEqual(len(results), 1)
        self.assertTrue(index_path.exists())
        self.assertEqual(results[0].metadata.get("lexical_index"), "persistent")
        self.assertIn("LAZY-BM25-REBUILD", results[0].chunk.content)

    def test_rag66_hybrid_retrieval_preserves_persistent_bm25_metadata(self) -> None:
        knowledge_base, _document, chunk_repo, setting_service = self._create_indexed_markdown_knowledge_base(
            name="RAG6.6 Hybrid BM25 持久化",
            file_name="hybrid-persistent.md",
            retrieval_mode="hybrid",
            content="\n\n".join(
                [
                    "Adaptive RAG routing provides the vector-side signal.",
                    "The unique lexical marker HYBRID-PERSIST-77 appears only here.",
                    "Other paragraphs discuss unrelated UI polish.",
                ]
            ),
        )

        results = KnowledgeRetrievalPipeline(
            chunk_repo=chunk_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
            lexical_store=KnowledgeLexicalStore(index_root=settings.knowledge_index_dir),
        ).retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="adaptive HYBRID-PERSIST-77",
            top_k=5,
        )

        self.assertGreaterEqual(len(results), 1)
        self.assertTrue(all(result.rank_source == "hybrid_rrf" for result in results))
        self.assertTrue(any(result.metadata.get("lexical_index") == "persistent" for result in results))
        self.assertTrue(any(result.metadata.get("lexical_rank") is not None for result in results))

    def test_rag65_parent_child_retrieval_expands_context_window(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_repo = KnowledgeDocumentRepository(self.db)
        chunk_repo = KnowledgeChunkRepository(self.db)
        setting_service = SettingService(UserSettingRepository(self.db))
        document_service = KnowledgeDocumentService(
            document_repo,
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="RAG6.5 Parent Child 测试",
                parser_provider="local_basic",
                chunk_mode="parent_child",
                chunk_size=500,
                chunk_overlap=50,
                parent_chunk_size=500,
                child_chunk_size=100,
                child_chunk_overlap=0,
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                rerank_enabled=False,
                retrieval_mode="vector",
                retrieval_top_k=5,
                rerank_top_n=3,
                score_threshold=0,
            ),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        source_file = user_dir / "parent-child.md"
        parent_context_marker = "PARENT_CONTEXT_ONLY: this surrounding paragraph explains why the route matters."
        source_file.write_text(
            "\n\n".join(
                [
                    "Adaptive RAG uses routing to choose retrieval strategy for each request.",
                    parent_context_marker,
                    "Additional details describe evaluation signals and failure analysis.",
                ]
            ),
            encoding="utf-8",
        )
        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="parent-child.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/parent-child.md",
            ),
        )
        assert document is not None
        document_service.parse_document(knowledge_base.id, document.id, self.user.id)
        parsed_document = document_repo.get_by_user(document.id, self.user.id)
        assert parsed_document is not None
        KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=document_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        ).index_document(user_id=self.user.id, knowledge_base=knowledge_base, document=parsed_document)

        chunks = chunk_repo.list_by_knowledge_base(knowledge_base.id, self.user.id)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(json.loads(chunk.metadata_json or "{}").get("chunk_mode") == "parent_child" for chunk in chunks))

        results = KnowledgeRetrievalPipeline(
            chunk_repo=chunk_repo,
            setting_service=setting_service,
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        ).retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="adaptive routing",
            top_k=3,
        )

        self.assertGreaterEqual(len(results), 1)
        hit = results[0]
        self.assertEqual(hit.metadata.get("chunk_mode"), "parent_child")
        self.assertEqual(hit.metadata.get("retrieval_unit"), "child")
        self.assertLess(len(hit.chunk.content), int(hit.metadata.get("parent_char_count") or 0))
        self.assertIn(parent_context_marker, str(hit.metadata.get("parent_content") or ""))

        context_text = KnowledgeContextService._format_context(
            knowledge_base_name=knowledge_base.name,
            results=[hit],
            max_chars=2000,
        )
        self.assertIsNotNone(context_text)
        assert context_text is not None
        self.assertIn(parent_context_marker, context_text)
        self.assertIn("parent-child：已扩展", context_text)

    def test_retrieve_uses_rerank_when_enabled(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_repo = KnowledgeDocumentRepository(self.db)
        chunk_repo = KnowledgeChunkRepository(self.db)
        document_service = KnowledgeDocumentService(
            document_repo,
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="Rerank 测试",
                parser_provider="local_basic",
                chunk_size=100,
                chunk_overlap=10,
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                rerank_enabled=True,
                rerank_provider="openai-compatible",
                rerank_model="fake-rerank",
                retrieval_top_k=3,
                rerank_top_n=3,
                score_threshold=0,
            ),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        source_file = user_dir / "rerank.md"
        source_file.write_text(
            "\n\n".join(
                [
                    "Adaptive RAG uses routing to select retrieval strategy.",
                    "This paragraph is about interface rendering and layout.",
                    "Mixture of experts and MoE routing can improve specialization.",
                ]
            ),
            encoding="utf-8",
        )
        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="rerank.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/rerank.md",
            ),
        )
        assert document is not None
        document_service.parse_document(knowledge_base.id, document.id, self.user.id)
        parsed_document = document_repo.get_by_user(document.id, self.user.id)
        assert parsed_document is not None

        index_service = KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=document_repo,
            setting_service=SettingService(UserSettingRepository(self.db)),
            embedding_service=FakeKnowledgeEmbeddingService(),
            rerank_service=FakeKnowledgeRerankService(),
            vector_search=InMemoryVectorSearch(self.db),
        )
        index_service.index_document(user_id=self.user.id, knowledge_base=knowledge_base, document=parsed_document)

        results = index_service.retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="adaptive rag routing",
            top_k=3,
        )

        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].rank_source, "rerank")
        self.assertIsNotNone(results[0].rerank_score)
        self.assertIn("experts", results[0].chunk.content.lower())

    def test_retrieve_falls_back_to_vector_when_rerank_fails(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_repo = KnowledgeDocumentRepository(self.db)
        chunk_repo = KnowledgeChunkRepository(self.db)
        document_service = KnowledgeDocumentService(
            document_repo,
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="Rerank 回退测试",
                parser_provider="local_basic",
                chunk_size=100,
                chunk_overlap=10,
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                rerank_enabled=True,
                rerank_provider="openai-compatible",
                rerank_model="fake-rerank",
                retrieval_top_k=3,
                rerank_top_n=3,
                score_threshold=0,
            ),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        source_file = user_dir / "fallback.md"
        source_file.write_text(
            "\n\n".join(
                [
                    "Adaptive RAG uses routing to select retrieval strategy.",
                    "Mixture of experts and MoE routing can improve specialization.",
                    "This unrelated paragraph discusses user interface details.",
                ]
            ),
            encoding="utf-8",
        )
        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="fallback.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/fallback.md",
            ),
        )
        assert document is not None
        document_service.parse_document(knowledge_base.id, document.id, self.user.id)
        parsed_document = document_repo.get_by_user(document.id, self.user.id)
        assert parsed_document is not None

        index_service = KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=document_repo,
            setting_service=SettingService(UserSettingRepository(self.db)),
            embedding_service=FakeKnowledgeEmbeddingService(),
            rerank_service=FailingKnowledgeRerankService(),
            vector_search=InMemoryVectorSearch(self.db),
        )
        index_service.index_document(user_id=self.user.id, knowledge_base=knowledge_base, document=parsed_document)

        results = index_service.retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="adaptive rag routing",
            top_k=3,
        )

        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].rank_source, "vector_fallback")
        self.assertTrue(results[0].metadata.get("rerank_fallback"))
        self.assertEqual(results[0].metadata.get("rerank_error_code"), "knowledge_retrieval_failed")
        self.assertNotIn("fake rerank failure", str(results[0].metadata))

    def test_parse_pdf_from_adaptive_rag_fixture_for_rag3_smoke(self) -> None:
        pdf_path = Path("/disk2/gengnan/Adaptive-RAG/training_free_grpo/pdf/Adaptive_RAG.pdf")
        if not pdf_path.exists():
            self.skipTest("Adaptive-RAG PDF fixture not found")

        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_repo = KnowledgeDocumentRepository(self.db)
        chunk_repo = KnowledgeChunkRepository(self.db)
        document_service = KnowledgeDocumentService(
            document_repo,
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="PDF 冒烟测试",
                parser_provider="local_basic",
                chunk_size=900,
                chunk_overlap=120,
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                rerank_enabled=False,
            ),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        target_pdf = user_dir / pdf_path.name
        target_pdf.write_bytes(pdf_path.read_bytes())

        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name=pdf_path.name,
                mime_type="application/pdf",
                file_size=target_pdf.stat().st_size,
                storage_key=f"{self.user.id}/{pdf_path.name}",
            ),
        )
        assert document is not None

        result = document_service.parse_document(knowledge_base.id, document.id, self.user.id)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.document.parse_status, "parsed")
        self.assertIn("Adaptive", result.markdown_preview or "")

        parsed_document = document_repo.get_by_user(document.id, self.user.id)
        assert parsed_document is not None
        index_service = KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=document_repo,
            setting_service=SettingService(UserSettingRepository(self.db)),
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        )
        index_result = index_service.index_document(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            document=parsed_document,
        )
        retrieval_results = index_service.retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="adaptive rag",
            top_k=3,
        )

        self.assertGreater(index_result.chunk_count, 1)
        self.assertGreaterEqual(len(retrieval_results), 1)
        self.assertIn("Adaptive", retrieval_results[0].chunk.content)

    def test_knowledge_context_service_builds_prompt_context_and_sources(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_repo = KnowledgeDocumentRepository(self.db)
        chunk_repo = KnowledgeChunkRepository(self.db)
        document_service = KnowledgeDocumentService(
            document_repo,
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="聊天接入测试",
                parser_provider="local_basic",
                chunk_size=120,
                chunk_overlap=20,
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                rerank_enabled=False,
                max_context_chunks=2,
                max_context_chars=1200,
                score_threshold=0,
            ),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        source_file = user_dir / "chat-rag.md"
        source_file.write_text(
            "\n\n".join(
                [
                    "Adaptive RAG uses routing to choose retrieval strategy.",
                    "This paragraph explains unrelated rendering details.",
                ]
            ),
            encoding="utf-8",
        )
        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="chat-rag.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/chat-rag.md",
            ),
        )
        assert document is not None
        document_service.parse_document(knowledge_base.id, document.id, self.user.id)
        parsed_document = document_repo.get_by_user(document.id, self.user.id)
        assert parsed_document is not None
        index_service = KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=document_repo,
            setting_service=SettingService(UserSettingRepository(self.db)),
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        )
        index_service.index_document(user_id=self.user.id, knowledge_base=knowledge_base, document=parsed_document)

        result = asyncio.run(
            KnowledgeContextService(db=self.db, user_id=self.user.id, index_service=index_service).build_context(
                knowledge_base_id=knowledge_base.id,
                query="adaptive rag routing",
            )
        )

        self.assertIsNotNone(result.context_text)
        self.assertIn("知识库：聊天接入测试", result.context_text or "")
        self.assertIn("[KB1]", result.context_text or "")
        self.assertEqual(result.diagnostics["knowledge_retrieval_enabled"], 1)
        self.assertGreaterEqual(result.diagnostics["knowledge_chunks_injected"], 1)
        self.assertGreaterEqual(len(result.sources), 1)
        self.assertEqual(result.sources[0].source_type, "knowledge")
        self.assertEqual(result.sources[0].metadata["knowledge_base_id"], knowledge_base.id)
        self.assertTrue(result.retrieval_log_id)
        self.assertEqual(result.sources[0].metadata["retrieval_log_id"], result.retrieval_log_id)
        self.assertIn("chat-rag.md", result.sources[0].title)

        assert result.retrieval_log_id is not None
        retrieval_log = KnowledgeRetrievalLogRepository(self.db).get_by_user(result.retrieval_log_id, self.user.id)
        self.assertIsNotNone(retrieval_log)
        assert retrieval_log is not None
        public_log = KnowledgeRetrievalLogRepository.to_public_dict(retrieval_log)
        self.assertEqual(public_log["query"], "adaptive rag routing")
        self.assertEqual(public_log["knowledge_base_id"], knowledge_base.id)
        self.assertGreaterEqual(len(public_log["candidates"]), 1)
        self.assertGreaterEqual(len(public_log["selected"]), 1)
        self.assertEqual(public_log["selected"][0]["chunk_id"], result.sources[0].metadata["chunk_id"])
        self.assertEqual(public_log["status"], "success")

    def test_knowledge_context_rewrites_coreference_only_for_retrieval(self) -> None:
        knowledge_base, _, _, _ = self._create_indexed_markdown_knowledge_base(
            name="Query Rewrite 测试",
            file_name="query-rewrite.md",
            content="KnowledgeIndexGeneration uses compare-and-swap activation.",
        )
        index_service = CapturingKnowledgeIndexService()

        result = asyncio.run(
            KnowledgeContextService(
                db=self.db,
                user_id=self.user.id,
                index_service=index_service,
            ).build_context(
                knowledge_base_id=knowledge_base.id,
                query="它为什么需要 CAS 激活？",
                recent_messages=[
                    SimpleNamespace(
                        id="previous-user-message",
                        role="user",
                        content="请解释 KnowledgeIndexGeneration。",
                    ),
                    SimpleNamespace(
                        id="previous-assistant-message",
                        role="assistant",
                        content="不应使用这条模型回答改写检索词。",
                    ),
                ],
            )
        )

        self.assertEqual(
            index_service.queries,
            ["请解释 KnowledgeIndexGeneration。；追问：它为什么需要 CAS 激活？"],
        )
        self.assertEqual(result.diagnostics["knowledge_query_rewrite_used"], 1)
        self.assertEqual(
            result.details["knowledge_query_rewrite"]["context_message_id"],
            "previous-user-message",
        )
        retrieval_log = self.db.query(KnowledgeRetrievalLog).order_by(KnowledgeRetrievalLog.created_at.desc()).first()
        self.assertIsNotNone(retrieval_log)
        assert retrieval_log is not None
        public_log = KnowledgeRetrievalLogRepository.to_public_dict(retrieval_log)
        self.assertEqual(public_log["query"], "它为什么需要 CAS 激活？")
        self.assertEqual(
            public_log["diagnostics"]["knowledge_retrieval_query"],
            "请解释 KnowledgeIndexGeneration。；追问：它为什么需要 CAS 激活？",
        )

    def test_knowledge_context_sources_match_chunks_injected_into_prompt(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="来源一致性测试",
                parser_provider="local_basic",
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                retrieval_top_k=3,
                rerank_top_n=3,
                max_context_chunks=3,
                max_context_chars=1000,
                score_threshold=0,
                rerank_enabled=False,
            ),
        )
        assert knowledge_base is not None
        knowledge_base_model = KnowledgeBaseRepository(self.db).get_by_user(knowledge_base.id, self.user.id)
        assert knowledge_base_model is not None
        knowledge_base_model.max_context_chars = 260
        self.db.add(knowledge_base_model)
        self.db.commit()
        self.db.refresh(knowledge_base_model)
        document = KnowledgeDocument(
            knowledge_base_id=knowledge_base.id,
            user_id=self.user.id,
            file_name="source-alignment.md",
            mime_type="text/markdown",
            file_size=100,
            storage_key=f"{self.user.id}/source-alignment.md",
            parse_status="parsed",
            index_status="indexed",
        )
        self.db.add(document)
        self.db.commit()
        self.db.refresh(document)

        chunks = []
        for index in range(3):
            content = f"chunk {index} adaptive rag routing " + ("details " * 30)
            chunks.append(
                KnowledgeChunk(
                    user_id=self.user.id,
                    knowledge_base_id=knowledge_base.id,
                    document_id=document.id,
                    chunk_index=index,
                    vector_id=index + 1,
                    content=content,
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    char_count=len(content),
                    token_estimate=max(1, len(content) // 2),
                    source_start=index * 100,
                    source_end=(index + 1) * 100,
                    metadata_json=json.dumps(
                        {
                            "file_name": "source-alignment.md",
                            "file_type": "markdown",
                        },
                        ensure_ascii=False,
                    ),
                )
            )
        self.db.add_all(chunks)
        self.db.commit()

        result = asyncio.run(
            KnowledgeContextService(
                db=self.db,
                user_id=self.user.id,
                index_service=StaticMultiKnowledgeIndexService(self.db),
            ).build_context(
                knowledge_base_id=knowledge_base.id,
                query="adaptive rag routing",
            )
        )

        injected_labels = [
            line for line in (result.context_text or "").splitlines() if line.startswith("[KB")
        ]
        self.assertEqual(len(injected_labels), 1)
        self.assertEqual(result.diagnostics["knowledge_chunks_retrieved"], 3)
        self.assertEqual(result.diagnostics["knowledge_chunks_injected"], len(injected_labels))
        self.assertEqual(len(result.sources), len(injected_labels))

        assert result.retrieval_log_id is not None
        retrieval_log = KnowledgeRetrievalLogRepository(self.db).get_by_user(result.retrieval_log_id, self.user.id)
        self.assertIsNotNone(retrieval_log)
        assert retrieval_log is not None
        public_log = KnowledgeRetrievalLogRepository.to_public_dict(retrieval_log)
        self.assertEqual(len(public_log["candidates"]), 3)
        self.assertEqual(len(public_log["selected"]), len(injected_labels))
        self.assertEqual(public_log["selected"][0]["chunk_id"], result.sources[0].metadata["chunk_id"])

    def test_knowledge_context_service_skips_unindexed_knowledge_base(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(name="未索引知识库", parser_provider="local_basic"),
        )
        assert knowledge_base is not None

        result = asyncio.run(
            KnowledgeContextService(db=self.db, user_id=self.user.id).build_context(
                knowledge_base_id=knowledge_base.id,
                query="adaptive rag",
            )
        )

        self.assertIsNone(result.context_text)
        self.assertEqual(result.sources, [])
        self.assertEqual(result.diagnostics["knowledge_retrieval_enabled"], 1)
        self.assertEqual(result.diagnostics["knowledge_chunks_injected"], 0)
        self.assertIn("尚未生成索引", result.notices[0])

    def test_knowledge_context_service_times_out_slow_retrieval(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        document_repo = KnowledgeDocumentRepository(self.db)
        chunk_repo = KnowledgeChunkRepository(self.db)
        document_service = KnowledgeDocumentService(
            document_repo,
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        knowledge_base = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="超时知识库",
                parser_provider="local_basic",
                chunk_size=120,
                chunk_overlap=20,
                embedding_provider="openai-compatible",
                embedding_model="fake-embedding",
                embedding_dimensions=128,
                rerank_enabled=False,
            ),
        )
        assert knowledge_base is not None

        user_dir = Path(settings.upload_dir) / self.user.id
        user_dir.mkdir(parents=True, exist_ok=True)
        source_file = user_dir / "slow.md"
        source_file.write_text("Adaptive RAG timeout test.", encoding="utf-8")
        document = document_service.add_document(
            knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="slow.md",
                mime_type="text/markdown",
                file_size=source_file.stat().st_size,
                storage_key=f"{self.user.id}/slow.md",
            ),
        )
        assert document is not None
        document_service.parse_document(knowledge_base.id, document.id, self.user.id)
        parsed_document = document_repo.get_by_user(document.id, self.user.id)
        assert parsed_document is not None
        KnowledgeIndexService(
            chunk_repo=chunk_repo,
            document_repo=document_repo,
            setting_service=SettingService(UserSettingRepository(self.db)),
            embedding_service=FakeKnowledgeEmbeddingService(),
            vector_search=InMemoryVectorSearch(self.db),
        ).index_document(user_id=self.user.id, knowledge_base=knowledge_base, document=parsed_document)

        object.__setattr__(settings, "knowledge_context_timeout_seconds", 0.01)
        result = asyncio.run(
            KnowledgeContextService(
                db=self.db,
                user_id=self.user.id,
                index_service=SlowKnowledgeIndexService(),  # type: ignore[arg-type]
            ).build_context(
                knowledge_base_id=knowledge_base.id,
                query="adaptive rag",
            )
        )

        self.assertIsNone(result.context_text)
        self.assertEqual(result.sources, [])
        self.assertEqual(result.diagnostics["knowledge_retrieval_error"], 1)
        self.assertGreaterEqual(result.diagnostics["knowledge_retrieval_latency_ms"], 1)
        self.assertIn("检索超过", result.notices[0])

    def test_knowledge_context_service_sanitizes_retrieval_exception(self) -> None:
        knowledge_base, _, _, _ = self._create_indexed_markdown_knowledge_base(
            name="检索异常脱敏",
            file_name="sanitized.md",
            content="A retrieval error must not expose provider details.",
        )

        result = asyncio.run(
            KnowledgeContextService(
                db=self.db,
                user_id=self.user.id,
                index_service=FailingKnowledgeIndexService(),  # type: ignore[arg-type]
            ).build_context(
                knowledge_base_id=knowledge_base.id,
                query="retrieval error",
            )
        )

        self.assertEqual(result.diagnostics["knowledge_retrieval_error"], 1)
        self.assertIn("检索失败", result.notices[0])
        self.assertNotIn("provider.internal", result.notices[0])
        self.assertNotIn("secret-value", result.notices[0])

    def test_context_stats_header_supports_unicode_values(self) -> None:
        encoded = _stringify_stats(
            {
                "knowledge_retrieval_enabled": 1,
                "knowledge_base_name": "中文知识库",
                "knowledge_chunks_injected": 2,
            }
        )

        self.assertTrue(encoded.startswith("json64:"))
        encoded.encode("latin-1")

    def test_delete_conversation_detaches_knowledge_retrieval_logs(self) -> None:
        conversation_repo = ConversationRepository(self.db)
        message_repo = MessageRepository(self.db)
        message_service = MessageService(message_repo)
        conversation_service = ConversationService(conversation_repo)

        conversation = conversation_repo.create(
            self._create_conversation("日志解绑测试")
        )
        user_message = message_service.create_system_message(
            conversation_id=conversation.id,
            role="user",
            content="test",
            status="done",
        )
        assistant_message = message_service.create_system_message(
            conversation_id=conversation.id,
            role="assistant",
            content="answer",
            status="done",
        )
        kb = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db)).create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(name="日志解绑知识库", parser_provider="local_basic"),
        )
        assert kb is not None
        log = KnowledgeRetrievalLogRepository(self.db).create(
            user_id=self.user.id,
            knowledge_base_id=kb.id,
            query="what is test",
            retrieval_mode="vector",
            top_k=3,
            rerank_enabled=False,
            rerank_model=None,
            candidates=[],
            selected=[],
            diagnostics={},
            sources=[],
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
        )

        deleted = conversation_service.delete_conversation(conversation.id, self.user.id)
        self.assertTrue(deleted)
        refreshed = KnowledgeRetrievalLogRepository(self.db).get_by_user(log.id, self.user.id)
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertIsNone(refreshed.conversation_id)
        self.assertIsNone(refreshed.user_message_id)
        self.assertIsNone(refreshed.assistant_message_id)

    def test_delete_knowledge_base_removes_retrieval_logs(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        kb = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(name="删除日志测试库", parser_provider="local_basic"),
        )
        assert kb is not None
        log = KnowledgeRetrievalLogRepository(self.db).create(
            user_id=self.user.id,
            knowledge_base_id=kb.id,
            query="delete me",
            retrieval_mode="vector",
            top_k=3,
            rerank_enabled=False,
            rerank_model=None,
            candidates=[],
            selected=[],
            diagnostics={},
            sources=[],
        )

        deleted = base_service.delete_knowledge_base(kb.id, self.user.id)
        self.assertTrue(deleted)
        self.assertIsNone(KnowledgeRetrievalLogRepository(self.db).get_by_user(log.id, self.user.id))

    def test_delete_knowledge_base_removes_index_directory(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        kb = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(name="删除索引目录测试库", parser_provider="local_basic"),
        )
        assert kb is not None
        index_dir = Path(settings.knowledge_index_dir) / kb.id
        index_dir.mkdir(parents=True, exist_ok=True)
        (index_dir / "lexical_index.json").write_text("{}", encoding="utf-8")

        deleted = base_service.delete_knowledge_base(kb.id, self.user.id)

        self.assertTrue(deleted)
        self.assertFalse(index_dir.exists())

    def test_build_context_merges_multiple_knowledge_bases(self) -> None:
        base_service = KnowledgeBaseService(KnowledgeBaseRepository(self.db), ProjectRepository(self.db))
        kb_a = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="Skill Router",
                parser_provider="local_basic",
                retrieval_top_k=3,
                rerank_top_n=3,
                max_context_chunks=2,
                max_context_chars=4000,
                rerank_enabled=False,
            ),
        )
        kb_b = base_service.create_knowledge_base(
            self.user.id,
            KnowledgeBaseCreate(
                name="Adaptive RAG",
                parser_provider="local_basic",
                retrieval_top_k=3,
                rerank_top_n=3,
                max_context_chunks=2,
                max_context_chars=4000,
                rerank_enabled=False,
            ),
        )
        assert kb_a is not None
        assert kb_b is not None
        for index, (kb, file_name, content) in enumerate(
            [
                (kb_a, "skill-router.md", "Skill Router learns to route user requests to suitable tools."),
                (kb_b, "adaptive-rag.md", "Adaptive RAG adjusts retrieval strategy according to query difficulty."),
            ],
            start=1,
        ):
            document = KnowledgeDocument(
                knowledge_base_id=kb.id,
                user_id=self.user.id,
                project_id=self.project.id,
                file_name=file_name,
                mime_type="text/markdown",
                file_size=len(content),
                storage_key=f"{self.user.id}/{file_name}",
                parser_provider="local_basic",
                parse_status="done",
                index_status="done",
            )
            self.db.add(document)
            self.db.flush()
            self.db.add(
                KnowledgeChunk(
                    user_id=self.user.id,
                    knowledge_base_id=kb.id,
                    document_id=document.id,
                    chunk_index=0,
                    vector_id=index,
                    content=content,
                    content_hash=f"hash-{index}",
                    char_count=len(content),
                    token_estimate=max(1, len(content) // 4),
                    metadata_json=json.dumps({"file_name": file_name}, ensure_ascii=False),
                )
            )
        self.db.commit()

        result = asyncio.run(
            KnowledgeContextService(
                db=self.db,
                user_id=self.user.id,
                index_service=StaticMultiKnowledgeIndexService(self.db),
            ).build_context(
                knowledge_base_id=None,
                knowledge_base_ids=[kb_a.id, kb_b.id],
                query="介绍一下 routing 和 adaptive rag",
            )
        )

        self.assertIsNotNone(result.context_text)
        assert result.context_text is not None
        self.assertIn("多知识库检索", result.context_text)
        self.assertIn("Skill Router", result.context_text)
        self.assertIn("Adaptive RAG", result.context_text)
        self.assertEqual(result.diagnostics["knowledge_base_count"], 2)
        self.assertEqual(result.diagnostics["knowledge_chunks_injected"], 2)
        self.assertEqual(len(result.sources), 2)
        self.assertEqual(len(result.retrieval_log_ids), 2)
        self.assertEqual(len(result.details["knowledge_retrieval_log_ids"]), 2)
        self.assertEqual(result.sources[0].citation_label, "[KB1]")
        self.assertEqual(result.sources[1].citation_label, "[KB2]")

    def _create_conversation(self, title: str):
        from app.models.conversation import Conversation

        return Conversation(
            user_id=self.user.id,
            title=title,
            model_name="deepseek-ai/DeepSeek-V4-Flash",
            system_prompt=None,
        )


if __name__ == "__main__":
    unittest.main()

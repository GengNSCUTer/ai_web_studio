from __future__ import annotations

import io
import asyncio
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes.chat import _stringify_stats
from app.core.config import settings
from app.core.database import Base
from app.models import *  # noqa: F403 - ensure all metadata is registered.
from app.models.project import Project
from app.models.user import User
from app.repositories.knowledge_repo import (
    KnowledgeBaseRepository,
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeJobRepository,
    KnowledgeRetrievalLogRepository,
)
from app.repositories.project_repo import ProjectRepository
from app.repositories.setting_repo import UserSettingRepository
from app.repositories.tool_config_repo import ToolConfigRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageRepository
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeCredentialUpdate, KnowledgeDocumentCreate
from app.schemas.setting import UserSettingUpdate
from app.services.knowledge_service import (
    KnowledgeBaseService,
    KnowledgeCredentialService,
    KnowledgeDocumentService,
    KnowledgeJobService,
)
from app.services.conversation_service import ConversationService
from app.services.message_service import MessageService
from app.services.knowledge_index_service import (
    KnowledgeEmbeddingService,
    KnowledgeFaissStore,
    KnowledgeIndexService,
    KnowledgeRerankService,
)
from app.services.knowledge_context_service import KnowledgeContextService
from app.services.knowledge_model_catalog_service import KnowledgeModelCatalogService
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
            faiss_store=KnowledgeFaissStore(index_root=settings.knowledge_index_dir),
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
            faiss_store=KnowledgeFaissStore(index_root=settings.knowledge_index_dir),
        )
        index_result = index_service.index_document(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            document=parsed_document,
        )

        self.assertGreater(index_result.chunk_count, 0)
        self.assertTrue(Path(index_result.index_path).exists())
        self.assertEqual(chunk_repo.count_by_knowledge_base(knowledge_base.id, self.user.id), index_result.chunk_count)

        results = index_service.retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="adaptive rag routing",
            top_k=3,
        )

        self.assertGreaterEqual(len(results), 1)
        self.assertIn("Adaptive RAG", results[0].chunk.content)

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
            faiss_store=KnowledgeFaissStore(index_root=settings.knowledge_index_dir),
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
            faiss_store=KnowledgeFaissStore(index_root=settings.knowledge_index_dir),
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
        self.assertIn("fake rerank failure", str(results[0].metadata.get("rerank_error")))

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
            faiss_store=KnowledgeFaissStore(index_root=settings.knowledge_index_dir),
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
            faiss_store=KnowledgeFaissStore(index_root=settings.knowledge_index_dir),
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
            faiss_store=KnowledgeFaissStore(index_root=settings.knowledge_index_dir),
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

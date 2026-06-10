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

from app.core.config import settings
from app.core.database import Base
from app.models import *  # noqa: F403 - ensure all metadata is registered.
from app.models.project import Project
from app.models.user import User
from app.repositories.knowledge_repo import (
    KnowledgeBaseRepository,
    KnowledgeDocumentRepository,
    KnowledgeJobRepository,
)
from app.repositories.project_repo import ProjectRepository
from app.repositories.setting_repo import UserSettingRepository
from app.repositories.tool_config_repo import ToolConfigRepository
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeCredentialUpdate, KnowledgeDocumentCreate
from app.schemas.setting import UserSettingUpdate
from app.services.knowledge_service import (
    KnowledgeBaseService,
    KnowledgeCredentialService,
    KnowledgeDocumentService,
    KnowledgeJobService,
)
from app.services.knowledge_model_catalog_service import KnowledgeModelCatalogService
from app.services.setting_service import SettingService


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
        self.upload_tmp = tempfile.TemporaryDirectory()
        object.__setattr__(settings, "upload_dir", self.upload_tmp.name)

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()
        object.__setattr__(settings, "upload_dir", self._previous_upload_dir)
        self.upload_tmp.cleanup()

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
            archive.writestr("mineru/full.md", "# MinerU Result\n\n这是 MinerU 返回的 Markdown。")

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

        preview = document_service.preview_markdown(knowledge_base.id, document.id, self.user.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn("这是 MinerU 返回的 Markdown", preview.markdown)


if __name__ == "__main__":
    unittest.main()

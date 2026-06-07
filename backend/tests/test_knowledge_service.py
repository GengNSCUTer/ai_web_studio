from __future__ import annotations

import unittest
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeDocumentCreate
from app.services.knowledge_service import KnowledgeBaseService, KnowledgeDocumentService, KnowledgeJobService


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

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

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


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from unittest.mock import Mock
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument, User
from app.repositories.knowledge_repo import KnowledgeChunkRepository
from app.services.knowledge_index_service import KnowledgeIndexService
from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalPipeline


TEST_POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")


class StaticEmbeddingService:
    async def embed_texts(self, *, user_id: str, knowledge_base, texts: list[str]) -> list[list[float]]:  # noqa: ANN001, ARG002
        return [[1.0, 0.0, 0.0] for _ in texts]


class FailIfFaissIsUsed:
    def search(self, **kwargs):  # noqa: ANN003, ANN201
        raise AssertionError(f"PostgreSQL retrieval must not call FAISS: {kwargs}")


@unittest.skipUnless(TEST_POSTGRES_URL, "set TEST_POSTGRES_URL to run PostgreSQL integration tests")
class PgvectorKnowledgeChunkIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_POSTGRES_URL is not None
        source_url = make_url(TEST_POSTGRES_URL)
        cls.database_name = f"aiws_pgvector_test_{uuid4().hex}"
        cls.admin_url = source_url.set(database="postgres")
        cls.database_url = source_url.set(database=cls.database_name)
        cls.admin_engine = create_engine(cls.admin_url, isolation_level="AUTOCOMMIT")
        with cls.admin_engine.connect() as connection:
            # database_name is generated from a fixed prefix and uuid hex, not from user input.
            connection.execute(text(f'create database "{cls.database_name}"'))

        cls.engine = create_engine(cls.database_url)
        with cls.engine.begin() as connection:
            connection.execute(text("create extension vector"))
        Base.metadata.create_all(bind=cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        with cls.admin_engine.connect() as connection:
            connection.execute(text(f'drop database if exists "{cls.database_name}" with (force)'))
        cls.admin_engine.dispose()

    def setUp(self) -> None:
        self.db = Session(self.engine)
        self.user = User(email=f"pgvector-{uuid4()}@example.com", username=f"pgvector-{uuid4()}")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def _add_chunk(
        self,
        *,
        knowledge_base: KnowledgeBase,
        document: KnowledgeDocument,
        vector_id: int,
        content: str,
        embedding: list[float],
    ) -> KnowledgeChunk:
        chunk = KnowledgeChunk(
            user_id=self.user.id,
            knowledge_base_id=knowledge_base.id,
            document_id=document.id,
            index_generation="legacy",
            chunk_index=vector_id - 1,
            vector_id=vector_id,
            content=content,
            content_hash=uuid4().hex,
            embedding=embedding,
            embedding_provider=knowledge_base.embedding_provider,
            embedding_model=knowledge_base.embedding_model,
            embedding_dimensions=knowledge_base.embedding_dimensions,
            embedding_version=KnowledgeIndexService.EMBEDDING_VERSION,
            char_count=len(content),
            token_estimate=1,
        )
        self.db.add(chunk)
        return chunk

    def _add_knowledge_base_and_document(
        self,
        *,
        name: str,
        dimensions: int,
    ) -> tuple[KnowledgeBase, KnowledgeDocument]:
        knowledge_base = KnowledgeBase(
            user_id=self.user.id,
            name=name,
            embedding_dimensions=dimensions,
            active_index_generation="legacy",
        )
        self.db.add(knowledge_base)
        self.db.flush()
        document = KnowledgeDocument(
            knowledge_base_id=knowledge_base.id,
            user_id=self.user.id,
            file_name=f"{name}.md",
            storage_key=f"{name}.md",
        )
        self.db.add(document)
        self.db.flush()
        return knowledge_base, document

    def test_variable_dimension_embeddings_round_trip(self) -> None:
        three_dimensional_base, three_dimensional_document = self._add_knowledge_base_and_document(
            name="three-dimensional",
            dimensions=3,
        )
        four_dimensional_base, four_dimensional_document = self._add_knowledge_base_and_document(
            name="four-dimensional",
            dimensions=4,
        )
        three_dimensional_chunk = self._add_chunk(
            knowledge_base=three_dimensional_base,
            document=three_dimensional_document,
            vector_id=1,
            content="three",
            embedding=[1.0, 0.0, 0.0],
        )
        four_dimensional_chunk = self._add_chunk(
            knowledge_base=four_dimensional_base,
            document=four_dimensional_document,
            vector_id=1,
            content="four",
            embedding=[1.0, 0.0, 0.0, 0.0],
        )
        self.db.commit()

        self.db.refresh(three_dimensional_chunk)
        self.db.refresh(four_dimensional_chunk)
        self.assertEqual(three_dimensional_chunk.embedding, [1.0, 0.0, 0.0])
        self.assertEqual(four_dimensional_chunk.embedding, [1.0, 0.0, 0.0, 0.0])

    def test_cosine_query_filters_knowledge_base_and_generation_before_ranking(self) -> None:
        knowledge_base, document = self._add_knowledge_base_and_document(name="ranking", dimensions=3)
        self._add_chunk(
            knowledge_base=knowledge_base,
            document=document,
            vector_id=1,
            content="exact",
            embedding=[1.0, 0.0, 0.0],
        )
        self._add_chunk(
            knowledge_base=knowledge_base,
            document=document,
            vector_id=2,
            content="near",
            embedding=[0.9, 0.1, 0.0],
        )
        self._add_chunk(
            knowledge_base=knowledge_base,
            document=document,
            vector_id=3,
            content="far",
            embedding=[0.0, 1.0, 0.0],
        )
        self.db.commit()

        rows = KnowledgeChunkRepository(self.db).search_by_cosine_distance(
            knowledge_base_id=knowledge_base.id,
            user_id=self.user.id,
            index_generation="legacy",
            query_vector=[1.0, 0.0, 0.0],
            embedding_provider=knowledge_base.embedding_provider,
            embedding_model=knowledge_base.embedding_model,
            embedding_dimensions=knowledge_base.embedding_dimensions,
            embedding_version=KnowledgeIndexService.EMBEDDING_VERSION,
            top_k=3,
        )

        self.assertEqual([chunk.content for chunk, _ in rows], ["exact", "near", "far"])
        self.assertAlmostEqual(rows[0][1], 1.0, places=6)
        self.assertGreater(rows[1][1], rows[2][1])

    def test_retrieval_pipeline_uses_pgvector_instead_of_faiss_on_postgresql(self) -> None:
        knowledge_base, document = self._add_knowledge_base_and_document(name="pipeline", dimensions=3)
        knowledge_base.rerank_enabled = False
        knowledge_base.score_threshold = 0.0
        self._add_chunk(
            knowledge_base=knowledge_base,
            document=document,
            vector_id=1,
            content="exact",
            embedding=[1.0, 0.0, 0.0],
        )
        self._add_chunk(
            knowledge_base=knowledge_base,
            document=document,
            vector_id=2,
            content="near",
            embedding=[0.9, 0.1, 0.0],
        )
        self.db.commit()

        pipeline = KnowledgeRetrievalPipeline(
            chunk_repo=KnowledgeChunkRepository(self.db),
            setting_service=Mock(),
            embedding_service=StaticEmbeddingService(),
            faiss_store=FailIfFaissIsUsed(),
        )
        results = pipeline.retrieve(
            user_id=self.user.id,
            knowledge_base=knowledge_base,
            query="query",
            top_k=2,
        )

        self.assertEqual([result.chunk.content for result in results], ["exact", "near"])
        self.assertAlmostEqual(results[0].score, 1.0, places=6)

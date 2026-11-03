"""验证父块单份存储、版本隔离、去重和命中区域预算。"""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models
from app.core.database import Base
from app.models.knowledge import KnowledgeBase, KnowledgeDocument, KnowledgeChunk
from app.models.user import User
from app.repositories.knowledge_repo import KnowledgeChunkRepository
from app.services.knowledge_context_service import KnowledgeContextService
from app.services.knowledge_index_service import KnowledgeChunker, RetrievalResult
from app.services.knowledge_parent_context import unique_parent_results, compact_parent_metadata
from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalPipeline
from app.services.knowledge_e2e_evaluation_service import KnowledgeE2EEvaluationService, KnowledgeE2EEvaluationConfig


def hit(identifier, parent_index=0, parent=None, doc="doc", generation="g", child="命中证据"):
    chunk = SimpleNamespace(id=identifier, user_id="u", knowledge_base_id="kb", document_id=doc,
        index_generation=generation, chunk_index=int(identifier[-1]) if identifier[-1].isdigit() else 0,
        content=child, source_start=0, source_end=len(child))
    metadata = {"chunk_mode": "parent_child", "parent_index": parent_index, "parent_content": parent or child,
                "file_name": "source.md"}
    return RetrievalResult(chunk=chunk, score=0.9, metadata=metadata)


class ParentContextTest(unittest.TestCase):
    def test_legacy_compaction_preview_idempotency_and_conflicts(self):
        rows = [SimpleNamespace(id=str(i), user_id="u", knowledge_base_id="kb", document_id="d", index_generation="g",
            chunk_index=i, metadata_json=json.dumps({"chunk_mode": "parent_child", "parent_index": 0,
                "parent_content": "完整父块" * 100})) for i in range(3)]
        original = [item.metadata_json for item in rows]
        report = compact_parent_metadata(rows)
        self.assertGreater(report["metadata_bytes_saved"], 0)
        self.assertEqual([item.metadata_json for item in rows], original)
        compact_parent_metadata(rows, apply=True)
        self.assertEqual(sum("parent_content" in json.loads(item.metadata_json) for item in rows), 1)
        self.assertEqual(compact_parent_metadata(rows)["updated_chunks"], 0)
        rows[-1].metadata_json = json.dumps({"chunk_mode": "parent_child", "parent_index": 0, "parent_content": "不一致"})
        self.assertEqual(compact_parent_metadata(rows, apply=True)["conflicting_groups_skipped"], 1)

    def test_parent_text_stored_once_for_each_parent(self):
        chunks = KnowledgeChunker().split_parent_child(markdown="甲" * 1800, parent_chunk_size=900,
                                                       child_chunk_size=180, child_chunk_overlap=30)
        parents = {}
        for chunk in chunks:
            meta = chunk.metadata
            parents.setdefault(meta["parent_index"], []).append(chunk)
        self.assertGreater(len(parents), 1)
        for children in parents.values():
            self.assertEqual(sum("parent_content" in child.metadata for child in children), 1)
            self.assertTrue(all(child.metadata["parent_chunk_index"] == children[0].chunk_index for child in children))

    def test_parent_dedup_keeps_provenance_and_distinct_documents_generations(self):
        results = [hit("c1"), hit("c2"), hit("c3", doc="other"), hit("c4", generation="old")]
        unique = unique_parent_results(results)
        self.assertEqual([item.chunk.id for item in unique], ["c1", "c3", "c4"])
        self.assertEqual(unique[0].metadata["matched_child_ids"], ["c1", "c2"])
        self.assertEqual(unique_parent_results(unique)[0].metadata["matched_child_ids"], ["c1", "c2"])

    def test_budget_preserves_late_hit_other_evidence_and_actual_sources(self):
        parent = "无关前文" * 1200 + "命中证据" + "结尾" * 200
        results = [hit("c1", parent=parent), hit("c2", parent=parent),
                   hit("c3", parent_index=1, parent="第二项资料" * 40, child="第二项资料")]
        selected = KnowledgeContextService._prepare_context_results(knowledge_base_name="test", results=results, max_chars=1400)
        self.assertEqual(len(selected), 2)
        context = KnowledgeContextService._format_context(knowledge_base_name="test", results=selected, max_chars=1400)
        self.assertLessEqual(len(context), 1400)
        self.assertEqual(context.count("命中证据"), 1)
        self.assertIn("第二项资料", context)
        sources = KnowledgeContextService._build_sources(knowledge_base_id="kb", knowledge_base_name="test", results=selected)
        for source in sources:
            self.assertIn(source.display_text, context)
            self.assertNotIn("parent_content", source.metadata)
        self.assertEqual(sources[0].metadata["matched_child_ids"], ["c1", "c2"])
        multi = KnowledgeContextService._prepare_multi_sources(sources, 950)
        merged = KnowledgeContextService._format_multi_context(results=multi, max_chars=950)
        self.assertLessEqual(len(merged), 950)
        self.assertIn("命中证据", merged)
        self.assertIn("第二项资料", merged)
        self.assertTrue(all(source.display_text in merged for source in multi))

    def test_e2e_parent_sources_dedup_and_keep_hit_under_budget(self):
        parent = "前文" * 500 + "命中证据"
        sources, _ = KnowledgeE2EEvaluationService._serialize_sources(
            retrieved=[hit("c1", parent=parent), hit("c2", parent=parent), hit("c3", parent_index=1)],
            config=KnowledgeE2EEvaluationConfig(max_context_chars=300, max_chunk_chars=200))
        self.assertEqual(len(sources), 2)
        self.assertLessEqual(sum(len(item["content"]) for item in sources), 300)
        self.assertIn("命中证据", sources[0]["content"])
        self.assertEqual(sources[0]["matched_child_ids"], ["c1", "c2"])

    def test_resolve_anchor_scopes_user_document_and_index_generation(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        try:
            with sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(User(id="u", email="parent@example.test")); db.flush()
                db.add(KnowledgeBase(id="kb", user_id="u", name="parents")); db.flush()
                db.add(KnowledgeDocument(id="doc", user_id="u", knowledge_base_id="kb", file_name="a.md",
                    mime_type="text/markdown", file_size=20, storage_key="u/a.md")); db.flush()
                for version, text in [("g", "当前父块"), ("old", "旧父块")]:
                    db.add(KnowledgeChunk(id=version, user_id="u", knowledge_base_id="kb", document_id="doc",
                        index_generation=version, chunk_index=0, vector_id=0, content="锚点", content_hash="hash",
                        char_count=2, token_estimate=1, source_start=0, source_end=2,
                        metadata_json=json.dumps({"parent_content": text})))
                db.commit()
                repo = KnowledgeChunkRepository(db)
                self.assertEqual(repo.parent_anchors(user_id="other", keys=[("kb", "doc", "g", 0)]), [])
                self.assertEqual(repo.parent_anchors(user_id="u", keys=[("kb", "other", "g", 0)]), [])
                item = hit("c1")
                item.metadata.pop("parent_content")
                item.metadata["parent_chunk_index"] = 0
                pipeline = KnowledgeRetrievalPipeline(chunk_repo=repo, setting_service=SimpleNamespace())
                pipeline._retrieve_async = AsyncMock(return_value=[item])
                resolved = asyncio.run(pipeline.retrieve_async(user_id="u", knowledge_base=SimpleNamespace(), query="q", top_k=1))
                self.assertEqual(resolved[0].metadata["parent_content"], "当前父块")
                self.assertNotIn("parent_content", item.metadata)
        finally:
            engine.dispose()

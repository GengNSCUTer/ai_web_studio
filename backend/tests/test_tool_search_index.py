"""独立PostgreSQL验证工具持久索引；不触碰用户的知识库和工具配置。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import math
import os
import random
import statistics
import time
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.migrations import upgrade_database
from app.services.tools.catalog import ToolCatalog
from app.services.tools.search_index import PostgresToolSearchIndex, ToolSearchDocument, search_terms
from app.services.tools.semantic_retrieval import ToolEmbeddingConfig, ToolSemanticRetriever


@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'), '需要独立PostgreSQL工具索引测试库')
class ToolSearchIndexPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = make_url(os.environ['TEST_POSTGRES_URL'])
        cls.name = 'aiws_tool_index_test_' + uuid4().hex[:12]
        cls.admin = create_engine(source.set(database='postgres'), isolation_level='AUTOCOMMIT')
        with cls.admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{cls.name}"'))
        cls.engine = create_engine(source.set(database=cls.name))
        upgrade_database(cls.engine)
        cls.sessions = sessionmaker(cls.engine)
        with cls.engine.begin() as c:
            c.execute(text("INSERT INTO users(id,email) VALUES ('tool-a','tool-a@example.test'),('tool-b','tool-b@example.test')"))

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        with cls.admin.connect() as c:
            c.execute(text(f'DROP DATABASE "{cls.name}" WITH (FORCE)'))
        cls.admin.dispose()

    def setUp(self):
        with self.engine.begin() as c:
            c.execute(text('TRUNCATE tool_search_entries'))
        self.index = PostgresToolSearchIndex(self.sessions)
        self.config = ToolEmbeddingConfig('tool-a', 'siliconflow', 'unit', 2, 'https://example.test/v1', 'test-key')
        self.docs = [ToolSearchDocument('mcp.weather', 'weather', 'weather 天气 气温 umbrella'),
                     ToolSearchDocument('mcp.read', 'workspace_file', 'read file 正文 保留期限')]

    def populate(self, config=None, docs=None, vectors=None):
        config, docs = config or self.config, docs or self.docs
        self.index.prepare(config, docs)
        self.index.save(config, docs, vectors or [[1., 0.], [0., 1.]])

    def test_persistence_reopen_only_embeds_query(self):
        self.populate()
        adapter = AsyncMock()
        adapter.embed_texts.return_value = [[1., 0.]]
        tool = replace(ToolCatalog().get('amap.maps.weather'), tool_key='mcp.weather', category='weather',
                       display_name='weather', description='天气', when_to_use=[], input_schema={})
        retriever = ToolSemanticRetriever(config=self.config, embedding_service=adapter,
            index_store=PostgresToolSearchIndex(self.sessions), timeout_seconds=3)
        asyncio.run(retriever.warmup([tool]))
        adapter.reset_mock()
        _, diag = asyncio.run(ToolSemanticRetriever(config=self.config, embedding_service=adapter,
            index_store=PostgresToolSearchIndex(self.sessions), timeout_seconds=3).scores(query='umbrella', definitions=[tool]))
        self.assertEqual(diag['persistent_hits'], 1)
        self.assertEqual(diag['embedded_tools'], 0)
        self.assertEqual(adapter.embed_texts.call_args.kwargs['texts'], ['umbrella'])

    def test_user_model_dimension_and_credential_scope(self):
        self.populate()
        for changed in (replace(self.config, user_id='tool-b'), replace(self.config, model='other'),
                        replace(self.config, dimensions=3), replace(self.config, api_key='rotated')):
            scores, _ = self.index.semantic(changed, self.docs, [1.] * changed.dimensions)
            self.assertEqual(scores, {})
        self.assertEqual(self.index.prepare(self.config, self.docs), {'mcp.weather', 'mcp.read'})

    def test_changed_description_invalidates_and_late_embedding_cannot_overwrite(self):
        self.populate()
        changed = [replace(self.docs[0], text='weather 新的字段与语义'), self.docs[1]]
        self.assertEqual(self.index.prepare(self.config, changed), {'mcp.read'})
        self.assertEqual(self.index.save(self.config, [self.docs[0]], [[1., 0.]]), set())
        scores, _ = self.index.semantic(self.config, changed, [1., 0.])
        self.assertNotIn('mcp.weather', scores)
        self.index.save(self.config, [changed[0]], [[1., 0.]])
        self.assertIn('mcp.weather', self.index.semantic(self.config, changed, [1., 0.])[0])

    def test_disabled_deleted_and_unapproved_keys_are_not_recalled(self):
        self.populate()
        # 历史索引条目仍存在，当前调用者只允许read，不得复活weather。
        scores, _ = self.index.semantic(self.config, [self.docs[1]], [1., 0.])
        self.assertEqual(set(scores), {'mcp.read'})
        self.assertEqual(self.index.lexical(self.config, [self.docs[1]], 'weather 天气', ['weather']), [])
        stale = [replace(self.docs[0], text='new text not yet indexed')]
        self.assertEqual(self.index.semantic(self.config, stale, [1., 0.])[0], {})

    def test_chinese_lexical_and_category_recall_without_embedding(self):
        self.index.prepare(self.config, self.docs)
        self.assertEqual(self.index.lexical(self.config, self.docs, '看看保留期限'), ['mcp.read'])
        self.assertEqual(self.index.lexical(self.config, self.docs, 'nonexistent', ['weather']), ['mcp.weather'])
        self.assertEqual(self.index.lexical(self.config, self.docs, "';DROP TABLE users;--"), [])
        self.assertEqual(search_terms('API timeout、保留期限'), ['api', 'timeout', '保留', '留期', '期限'])

    def test_concurrent_prepare_same_metadata_does_not_clear_ready_vector(self):
        self.populate()
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(lambda _: self.index.prepare(self.config, self.docs), range(12)))
        self.assertTrue(all(row == {'mcp.weather', 'mcp.read'} for row in rows))
        self.assertEqual(len(self.index.semantic(self.config, self.docs, [1., 0.])[0]), 2)

    def test_hnsw_large_catalog_matches_exact_and_has_real_query_plan(self):
        rng = random.Random(20261103)
        config = replace(self.config, dimensions=256)
        docs = [ToolSearchDocument(f'mcp.item.{i:04}', 'synthetic', f'synthetic capability {i}') for i in range(1000)]
        def unit():
            vector = [rng.gauss(0, 1) for _ in range(256)]
            norm = math.hypot(*vector)
            return [value / norm for value in vector]
        vectors = [unit() for _ in docs]
        self.populate(config, docs, vectors)
        with self.engine.begin() as c:
            c.execute(text('ANALYZE tool_search_entries'))
        overlaps, elapsed = [], []
        for _ in range(10):
            query = unit()
            start = time.perf_counter()
            ann, diag = self.index.semantic(config, docs, query)
            elapsed.append((time.perf_counter() - start) * 1000)
            exact, _ = self.index.semantic(config, docs, query, force_exact=True)
            overlaps.append(len(set(ann).intersection(exact)) / 30)
            self.assertEqual(diag['search_mode'], 'hnsw')
            self.assertEqual(len(ann), 30)
            self.assertFalse(diag['exact_topup'])
        parameters = {**self.index._parameters(config, docs), 'vector': '[' + ','.join(map(str, vectors[0])) + ']'}
        with self.engine.connect() as c:
            self.index._configure_ann(c)
            plan = c.execute(text('EXPLAIN (FORMAT JSON) SELECT tool_key FROM tool_search_entries WHERE '
                + self.index._WHERE + ' AND dimensions = 256 AND embedding IS NOT NULL '
                + 'ORDER BY embedding::vector(256) <=> CAST(:vector AS vector) LIMIT 30'), parameters).scalar()
        def names(value):
            if isinstance(value, list):
                return [name for item in value for name in names(item)]
            if isinstance(value, dict):
                return [value['Index Name']] if 'Index Name' in value else [name for item in value.values() for name in names(item)]
            return []
        indexes = names(plan)
        self.assertIn('ix_tool_search_hnsw_256', indexes)
        self.assertGreaterEqual(statistics.mean(overlaps), .95)
        print(json.dumps(dict(benchmark='tool-index-1000-256', queries=10, ann_exact_overlap_at_30=statistics.mean(overlaps),
                             mean_ms=round(statistics.mean(elapsed), 2), indexes=indexes)))

    def test_filtered_ann_underfill_exact_topup_keeps_current_scope(self):
        config = replace(self.config, dimensions=256)
        docs = [ToolSearchDocument(f'only.{i}', 'synthetic', f'synthetic {i}') for i in range(150)]
        self.populate(config, docs, [[1.] + [0.] * 255 for _ in docs])
        # 模拟大目录大部分尚未嵌入，不能因近似返回不足而越权补外部条目。
        with self.engine.begin() as c:
            c.execute(text("UPDATE tool_search_entries SET embedding = NULL WHERE tool_key != 'only.0'"))
        scores, diag = self.index.semantic(config, docs, [1.] + [0.] * 255)
        self.assertEqual(set(scores), {'only.0'})
        self.assertTrue(diag['exact_topup'])

    def test_high_dimension_uses_halfvec_index_and_unknown_dimension_uses_exact(self):
        for dimensions in (2048, 3):
            config = replace(self.config, dimensions=dimensions)
            docs = [ToolSearchDocument(f'dim.{i}', 'synthetic', f'synthetic {i}') for i in range(129)]
            vector = [1.] + [0.] * (dimensions - 1)
            self.populate(config, docs, [vector for _ in docs])
            scores, diag = self.index.semantic(config, docs, vector)
            self.assertEqual(len(scores), 30)
            self.assertEqual(diag['search_mode'], 'hnsw' if dimensions == 2048 else 'exact')

    def test_ann_never_returns_other_user_or_currently_excluded_tools(self):
        config = replace(self.config, dimensions=256)
        documents = [ToolSearchDocument(f'allowed.{i}', 'synthetic', f'allowed capability {i}') for i in range(200)]
        foreign = replace(config, user_id='tool-b')
        vector = [1.] + [0.] * 255
        self.populate(config, documents, [vector for _ in documents])
        self.populate(foreign, documents, [vector for _ in documents])
        # 另一个用户有相同tool_key和向量，当前项目又禁用了后50项。
        result, _ = self.index.semantic(config, documents[:150], vector)
        self.assertEqual(len(result), 30)
        self.assertTrue(set(result).issubset({d.tool_key for d in documents[:150]}))
        new_model = replace(config, model='unindexed-model')
        self.assertEqual(self.index.semantic(new_model, documents, vector)[0], {})


if __name__ == '__main__':
    unittest.main()

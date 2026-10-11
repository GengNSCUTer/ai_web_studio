"""混合候选筛选的故障、权限边界与模型缓存行为。"""

import asyncio
import math
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from app.services.tools.catalog import ToolCatalog
from app.services.tools.selector import ToolCandidateSelector
from app.services.tools.semantic_retrieval import ToolEmbeddingConfig, ToolSemanticRetriever


class ToolSemanticSelectorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ToolSemanticRetriever._cache.clear()
        self.catalog = ToolCatalog()
        self.config = ToolEmbeddingConfig('user-a', 'siliconflow', 'test-embedding', 2, 'https://example.org/v1', 'test-key')
        self.adapter = AsyncMock()

    def retriever(self, config=None, timeout=.3):
        return ToolSemanticRetriever(config=config or self.config, embedding_service=self.adapter, timeout_seconds=timeout)

    async def test_real_vector_path_recalls_weather_without_keyword(self):
        weather = self.catalog.get('amap.maps.weather')
        self.adapter.embed_texts.return_value = [[1., 0.], [1., 0.]]
        self.catalog._definitions = {weather.tool_key: weather}
        tools, trace = await ToolCandidateSelector(self.catalog, semantic_retriever=self.retriever()).select_async(
            query='Should I carry an umbrella?', enabled=True)
        self.assertEqual([tool.tool_key for tool in tools], [weather.tool_key])
        self.assertEqual(trace['retrieval']['mode'], 'hybrid')
        self.assertEqual(trace['candidates'][0]['semantic_score'], 1.0)

    async def test_disabled_empty_skill_and_no_tools_do_not_call_embedding(self):
        selector = ToolCandidateSelector(self.catalog, semantic_retriever=self.retriever())
        for args in (dict(query='weather', enabled=False), dict(query='天气', enabled=True, allowed_tool_keys=set()),
                     dict(query='Do not use any tools', enabled=True), dict(query='谢谢你', enabled=True)):
            tools, trace = await selector.select_async(**args)
            self.assertEqual(tools, [])
            self.assertEqual(trace['retrieval']['mode'], 'skipped')
            self.assertNotEqual(trace['retrieval']['fallback_reason'], 'semantic_not_configured')
        self.adapter.embed_texts.assert_not_awaited()

    async def test_permission_filters_apply_before_semantic_call(self):
        def vectors(**kwargs):
            return [[1., 0.] for _ in kwargs['texts']]
        self.adapter.embed_texts.side_effect = vectors
        selector = ToolCandidateSelector(self.catalog, semantic_retriever=self.retriever())
        tools, _ = await selector.select_async(query='Read note.md. Do not search the web. Do not modify files.',
            enabled=True, allowed_tool_keys={'workspace.files.read','workspace.files.apply_edit','web.tavily.search'})
        self.assertEqual([tool.tool_key for tool in tools], ['workspace.files.read'])
        texts = self.adapter.embed_texts.call_args.kwargs['texts']
        self.assertEqual(len(texts), 2)
        self.assertNotIn('联网', texts[1])

    async def test_local_file_exclusion_cannot_be_revived_by_semantic(self):
        self.adapter.embed_texts.side_effect = lambda **kwargs: [[1., 0.] for _ in kwargs['texts']]
        tools, _ = await ToolCandidateSelector(self.catalog, semantic_retriever=self.retriever()).select_async(
            query='Search online only. Do not access local files.', enabled=True)
        self.assertFalse(any(tool.category == 'workspace_file' for tool in tools))

    async def test_timeout_uses_same_rule_candidates_and_does_not_leak_exception(self):
        async def slow(**kwargs):
            await asyncio.sleep(.05)
        self.adapter.embed_texts.side_effect = slow
        selector = ToolCandidateSelector(self.catalog, semantic_retriever=self.retriever(timeout=.001))
        expected, _ = selector.select(query='深圳天气', enabled=True)
        tools, trace = await selector.select_async(query='深圳天气', enabled=True)
        self.assertEqual(tools, expected)
        self.assertEqual(trace['retrieval']['fallback_reason'], 'embedding_timeout')
        self.adapter.embed_texts.side_effect = RuntimeError('secret-value')
        _, trace = await ToolCandidateSelector(self.catalog, semantic_retriever=self.retriever()).select_async(query='天气', enabled=True)
        self.assertNotIn('secret-value', str(trace))

    async def test_malformed_embeddings_fail_closed_to_rules(self):
        for vector in ([0., 0.], [1.], [math.nan, 1.], [True, 1.], [math.inf, 1.], 'wrong'):
            with self.subTest(vector=vector):
                self.adapter.embed_texts.side_effect = lambda **kwargs: [vector for _ in kwargs['texts']]
                _, trace = await ToolCandidateSelector(self.catalog, semantic_retriever=self.retriever()).select_async(
                    query='深圳天气', enabled=True)
                self.assertEqual(trace['retrieval']['fallback_reason'], 'embedding_unavailable')
        self.adapter.embed_texts.side_effect = None
        self.adapter.embed_texts.return_value = []
        scores, _ = await self.retriever().scores(query='天气', definitions=[self.catalog.get('amap.maps.weather')])
        self.assertEqual(scores, {})

    async def test_cancelled_request_propagates_without_fallback(self):
        self.adapter.embed_texts.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.retriever().scores(query='天气', definitions=[self.catalog.get('amap.maps.weather')])

    async def test_cache_reuses_tool_but_not_query_and_invalidates_on_changes(self):
        self.assertNotIn('test-key', repr(self.config))
        self.adapter.embed_texts.side_effect = lambda **kwargs: [[1., 0.] for _ in kwargs['texts']]
        tool = self.catalog.get('amap.maps.weather')
        retriever = self.retriever()
        await retriever.scores(query='第一问', definitions=[tool])
        _, diag = await retriever.scores(query='第二问', definitions=[tool])
        self.assertEqual(diag['cache_hits'], 1)
        self.assertEqual(self.adapter.embed_texts.call_args.kwargs['texts'], ['第二问'])
        for config in (replace(self.config,user_id='user-b'), replace(self.config,model='new-model'),
                       replace(self.config,api_key='rotated'), replace(self.config,base_url='https://other.org/v1')):
            _, diag = await self.retriever(config).scores(query='第三问', definitions=[tool])
            self.assertEqual(diag['cache_hits'], 0)
        _, diag = await retriever.scores(query='第三问', definitions=[replace(tool,description='new capability')])
        self.assertEqual(diag['cache_hits'], 0)

    async def test_cache_capacity_and_expiration_are_bounded(self):
        with unittest.mock.patch.object(ToolSemanticRetriever, 'CACHE_SIZE', 2):
            for i in range(3):
                ToolSemanticRetriever._put(str(i), [1., 0.])
            self.assertEqual(len(ToolSemanticRetriever._cache), 2)
            self.assertIsNone(ToolSemanticRetriever._get('0'))
        with unittest.mock.patch.object(ToolSemanticRetriever, 'CACHE_TTL_SECONDS', -1):
            self.assertIsNone(ToolSemanticRetriever._get('2'))

    async def test_new_category_works_without_rule_keyword_table(self):
        new = replace(self.catalog.get('workspace.files.read'), tool_key='mcp.language.detect', category='language_analysis',
                      display_name='语言识别', description='识别西班牙语或葡萄牙语', when_to_use=[], input_schema={})
        self.catalog._definitions[new.tool_key] = new
        retriever = AsyncMock()
        retriever.scores.return_value = ({new.tool_key: .8}, {'mode':'hybrid'})
        tools, _ = await ToolCandidateSelector(self.catalog, semantic_retriever=retriever).select_async(
            query='这段话是哪种语言？', enabled=True)
        self.assertIn(new.tool_key, {tool.tool_key for tool in tools})

    async def test_negative_metadata_and_short_english_words_are_not_positive_matches(self):
        tool = replace(self.catalog.get('workspace.files.read'), category='unknown', description='process directions',
                       when_to_use=[], when_not_to_use=['unique_forbidden_term'])
        selector = ToolCandidateSelector(self.catalog)
        for query in ('it is in the', 'unique_forbidden_term'):
            candidate = selector._score_tool(tool=tool, query=query)
            self.assertEqual(candidate.score, 0)

    async def test_capacity_across_skills_and_semantic_rankings(self):
        retriever = AsyncMock()
        retriever.scores.return_value = ({tool.tool_key:.8 for tool in self.catalog.list_definitions()}, {'mode':'hybrid'})
        for capacity in (1,2,4,6):
            tools, _ = await ToolCandidateSelector(self.catalog, max_candidates=capacity, semantic_retriever=retriever).select_async(
                query='路线天气文件', enabled=True)
            self.assertLessEqual(len(tools), capacity)
            self.assertEqual(len(tools), len({tool.tool_key for tool in tools}))
            self.assertIn('web.tavily.search', {tool.tool_key for tool in tools})

    async def test_online_config_missing_or_ollama_never_loads_model(self):
        scores, trace = await self.retriever(replace(self.config, provider='ollama')).scores(query='天气', definitions=[])
        self.assertEqual(scores, {})
        self.adapter.embed_texts.assert_not_awaited()
        scores, trace = await ToolSemanticRetriever().scores(query='天气', definitions=[])
        self.assertEqual(trace['fallback_reason'], 'online_embedding_not_configured')

    async def test_bounded_catalog_degrades_without_network(self):
        self.adapter.embed_texts.side_effect = lambda **kwargs: [[1., 0.] for _ in kwargs['texts']]
        tools = [replace(self.catalog.get('workspace.files.read'), tool_key=f'mcp.read.{i}') for i in range(129)]
        scores, trace = await self.retriever().scores(query='read', definitions=tools)
        self.assertEqual(trace['fallback_reason'], 'catalog_exceeds_semantic_limit')
        self.assertEqual(scores, {})
        self.adapter.embed_texts.assert_not_awaited()

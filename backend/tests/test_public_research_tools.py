"""验证公开资料接入的实际边界，而非仅检查 Manifest 字段。"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.evidence_sufficiency import assess_evidence_sufficiency
from app.services.skill_catalog import SkillCatalog, SkillExecutionContext
from app.services.tools.adapters import ToolAdapterRunner
from app.services.tools.catalog import ToolCatalog
from app.services.tools.completion_contract import augment_plan, needs_followup
from app.services.tools.public_research import map_public_research_text, public_url, validate_public_lookup_arguments
from app.services.tools.quality import evaluate_tool_result_quality
from app.services.tools.result_mappers import map_mcp_result
from app.services.tools.schemas import PlannedToolCall, ToolExecutionFeedbackError, ToolPlan
from app.services.tools.selector import ToolCandidateSelector


URL = "https://docs.sqlalchemy.org/en/20/orm/session_basics.html"
BODY = "Session.flush sends SQL to the database while the transaction remains open. Session.commit commits the transaction and releases its connection."


class PublicResearchMappingTest(unittest.TestCase):
    def map(self, text, profile="web_pages", **kwargs):
        return map_public_research_text(payload=text, provider="test", profile=profile, **kwargs)

    def test_real_exa_page_layout_preserves_citable_body(self):
        sources = self.map(f"# Session Basics\n —\n SQLAlchemy Documentation\nURL: {URL}\n\n{BODY}")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].url, URL)
        self.assertIn("Session.commit", sources[0].display_text)
        self.assertEqual(sources[0].title, "Session Basics")

    def test_search_titles_and_context7_source_markers_share_parser(self):
        for profile, text in (
            ("web_pages", f"Title: Session Basics\nURL: {URL}\nHighlights:\n{BODY}"),
            ("documentation", f"### Flush\n\nSource: {URL}\n\n{BODY}"),
        ):
            with self.subTest(profile=profile):
                sources = self.map(text, profile, arguments={"libraryId": "/websites/sqlalchemy_en_20"})
                self.assertEqual(len(sources), 1)
                definition = ToolCatalog().get("docs.context7.query" if profile == "documentation" else "web.exa.search")
                self.assertEqual(evaluate_tool_result_quality(sources=sources, contract=definition.quality_contract).status, "valid")

    def test_fetch_rejects_wrong_source_and_accepts_fragment_equivalent(self):
        text = f"Title: Docs\nURL: {URL}\n{BODY}"
        self.assertEqual(self.map(text, "fetched_pages", arguments={"urls": ["https://example.com/other"]}), [])
        self.assertEqual(len(self.map(text, "fetched_pages", arguments={"urls": [URL + "#flushing"]})), 1)

    def test_errors_unknown_json_and_uncitable_text_are_not_evidence(self):
        for text in ("No results found", "Rate limit exceeded", "{" + '"content":"unknown"}', {"content": BODY}, f"URL: {URL}\nFailed to fetch the page", "URL: http://localhost/x\n" + BODY):
            with self.subTest(text_type=type(text).__name__):
                self.assertEqual(self.map(text), [])

    def test_body_is_bounded_without_losing_all_content_after_old_1600_limit(self):
        body = BODY + "x" * 2_000 + "important later evidence" + "x" * 10_000
        source = self.map(f"URL: {URL}\n{body}")[0]
        self.assertIn("important later evidence", source.display_text)
        self.assertLessEqual(len(source.display_text), 8_000)
        self.assertTrue(source.metadata["truncated"])

    def test_total_budget_and_duplicates_are_bounded(self):
        text = "\n".join(f"Title: page {i}\nURL: https://example.com/{i}\n" + "x" * 9_000 for i in range(8))
        sources = self.map(text)
        self.assertLessEqual(len(sources), 5)
        self.assertLessEqual(sum(len(source.display_text) for source in sources), 24_000)

    def test_secret_in_title_and_body_is_redacted_before_metadata(self):
        source = self.map(f"Title: API_KEY=fixture-secret\nURL: {URL}\n{BODY}\npassword=fixture-password")[0]
        self.assertNotIn("fixture-secret", str(source))
        self.assertNotIn("fixture-password", str(source))

    def test_library_catalog_requires_real_id_title_and_description(self):
        text = "Available Libraries:\n- Title: SQLAlchemy\n- Context7-compatible library ID: /websites/sqlalchemy_en_20\n- Description: Official SQLAlchemy documentation."
        source = self.map(text, "library_catalog")[0]
        self.assertEqual(source.metadata["raw"]["library_id"], "/websites/sqlalchemy_en_20")
        self.assertEqual(source.source_type, "documentation_library")
        self.assertEqual(self.map("Error: rate limit exceeded", "library_catalog"), [])
        self.assertEqual(self.map(text.replace("/websites/sqlalchemy_en_20", "/../../secret"), "library_catalog"), [])

    def test_mcp_envelope_uses_the_production_mapper(self):
        raw = {"result": {"content": [{"type": "text", "text": f"URL: {URL}\n{BODY}"}]}}
        sources = map_mcp_result(mapper="public_fetched_pages", provider="exa", category="web_fetch", display_name="Fetch", query="", raw=raw, request_arguments={"urls": [URL]})
        self.assertEqual(sources[0].url, URL)

    def test_next_snippet_title_is_not_evidence_for_previous_source(self):
        text = f"### First API\nSource: {URL}\n{BODY}\n\n### Next API\n\nSource: https://example.com/next\n" + BODY
        sources = self.map(text, "documentation")
        self.assertEqual(len(sources), 2)
        self.assertNotIn("Next API", sources[0].display_text)
        self.assertEqual(sources[1].title, "Next API")
        self.assertIn("Session.commit", sources[0].display_text)

    def test_unknown_heading_layout_does_not_cut_previous_body(self):
        text = f"URL: {URL}\n{BODY}\n### Condition\nOnly when a later statement is executed.\nURL: https://example.com/next\n" + BODY
        self.assertIn("Only when", self.map(text)[0].display_text)


class PublicResearchPolicyTest(unittest.TestCase):
    def test_public_address_syntax_rejects_private_credentials_and_non_web(self):
        for url in ("file:///etc/passwd", "http://localhost/x", "https://127.0.0.1/x", "http://169.254.169.254/x", "https://10.1.2.3/x", "https://user:pass@example.com/x", "https://example.com/?token=fixture-secret", "https://example.com:8443/x"):
            with self.subTest(url=url):
                self.assertIsNone(public_url(url))
        self.assertEqual(public_url(URL), URL)

    def test_query_secrets_are_rejected_but_technical_terms_are_allowed(self):
        self.assertEqual(validate_public_lookup_arguments({"query": "how API keys are authenticated"}, url_fields=[]), [])
        with self.assertRaises(ToolExecutionFeedbackError):
            validate_public_lookup_arguments({"query": "API_KEY=fixture-secret"}, url_fields=[])

    def test_multiple_urls_are_checked_not_only_first(self):
        with self.assertRaises(ToolExecutionFeedbackError):
            validate_public_lookup_arguments({"urls": [URL, "http://127.0.0.1/x"]}, url_fields=["urls"])


class PublicResearchAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_optional_keys_use_correct_headers_and_never_endpoint_or_trace(self):
        from types import SimpleNamespace
        for tool_key, headers in (("web.exa.search", {"X-API-Key": "fixture-key"}),
                                  ("docs.context7.resolve", {"Authorization": "Bearer fixture-key"})):
            definition = ToolCatalog().get(tool_key)
            arguments = {"query": "SQLAlchemy public docs", "objective": "Find docs", "libraryName": "SQLAlchemy"}
            call = PlannedToolCall(call_id="auth", tool_key=tool_key, provider=definition.provider,
                                   category=definition.category, display_name=definition.display_name,
                                   confidence=1.0, reason="验证可选认证", arguments=arguments)
            raw = {"result": {"content": [{"type": "text", "text": f"URL: {URL}\n{BODY}"}]}}
            with patch("app.services.tools.adapters.McpHttpClient") as client:
                client.return_value.call_tool = AsyncMock(return_value=SimpleNamespace(raw=raw))
                _, metadata = await ToolAdapterRunner().run(definition=definition, call=call, api_key="fixture-key")
                client.assert_called_once_with(endpoint=definition.adapter["endpoint_template"], extra_headers=headers)
                self.assertNotIn("fixture-key", str(metadata))
            with patch("app.services.tools.adapters.McpHttpClient") as client:
                client.return_value.call_tool = AsyncMock(return_value=SimpleNamespace(raw=raw))
                await ToolAdapterRunner().run(definition=definition, call=call, api_key=None)
                self.assertEqual(client.call_args.kwargs["extra_headers"], {})

    async def test_optional_credentials_allow_missing_but_respect_explicit_disable(self):
        from backend.tests.test_tool_executor import FakeCredentialResolver
        from app.services.tools.credentials import ToolCredential
        from app.services.tools.executor import ToolExecutor
        from app.services.tools.schemas import ExternalSource
        definition = ToolCatalog().get("web.exa.search")
        call = PlannedToolCall(call_id="optional", tool_key=definition.tool_key, provider="exa",
                               category=definition.category, display_name=definition.display_name,
                               confidence=1.0, reason="验证匿名与关闭策略", arguments={"query": "public docs", "objective": "Find docs"})
        for source, key, enabled, expected_calls in (("missing", None, False, 1),
                                                     ("user", "fixture-key", True, 1),
                                                     ("user_disabled", None, False, 0)):
            resolver = FakeCredentialResolver()
            resolver.resolve = lambda **kwargs: ToolCredential("exa", key, source, enabled)
            adapter = AsyncMock()
            adapter.run.return_value = ([ExternalSource(source_type="web", provider="exa", title="Docs",
                                                        display_text=BODY, url=URL)], {})
            result, events = await ToolExecutor(credential_resolver=resolver, adapter_runner=adapter).execute(call)
            self.assertEqual(adapter.run.await_count, expected_calls)
            if expected_calls:
                self.assertEqual(result.status, "success")
                self.assertEqual(adapter.run.call_args.kwargs["api_key"], key)
            else:
                self.assertEqual(result.status, "skipped")
            self.assertNotIn("fixture-key", str(events))

    async def test_private_dns_is_blocked_before_mcp_client_is_created(self):
        from app.services.tools.mcp_security import McpEndpointPolicyError
        definition = ToolCatalog().get("web.exa.fetch")
        call = PlannedToolCall(call_id="fetch", tool_key=definition.tool_key, provider=definition.provider, category=definition.category, display_name=definition.display_name, confidence=1.0, reason="测试公开地址检查", arguments={"urls": ["https://private.example.com/x"]})
        with patch("app.services.tools.adapters.enforce_mcp_endpoint_target_policy", new=AsyncMock(side_effect=McpEndpointPolicyError("private"))) as guard, patch("app.services.tools.adapters.McpHttpClient") as client:
            with self.assertRaises(ToolExecutionFeedbackError):
                await ToolAdapterRunner().run(definition=definition, call=call, api_key=None)
            guard.assert_awaited_once_with("https://private.example.com/x", allow_private=False)
            client.assert_not_called()


class PublicResearchSkillTest(unittest.TestCase):
    def test_public_skill_prompt_is_compact_and_keeps_execution_boundaries(self):
        from app.services.tools.planner import LLMToolPlanner
        definition = SkillCatalog()._definitions["knowledge.technical-docs"]
        context = SkillExecutionContext(skill_key=definition["skill_key"], version=definition["version"],
            display_name="查证", description="测试", planner_instructions=tuple(definition["instructions"]),
            output_contract=tuple(definition["output_contract"]), allowed_tool_keys=tuple(definition["required_tool_keys"]),
            required_tool_keys=tuple(definition["required_tool_keys"]), optional_tool_keys=(), requires_tool_execution=True)
        planner = LLMToolPlanner()
        candidates = [ToolCatalog().get(key) for key in definition["required_tool_keys"]]
        plain = planner._build_planner_messages(query="查证公开文档", recent_messages=[], candidate_tools=candidates, observations=[])
        scoped = planner._build_planner_messages(query="查证公开文档", recent_messages=[], candidate_tools=candidates,
                                                  observations=[], skill_context=context)
        self.assertLess(len(scoped[0]["content"]), len(plain[0]["content"]))
        self.assertNotIn("amap.maps", scoped[0]["content"])
        for boundary in ("input_schema", "depends_on", "result_bindings", "审批", "候选", "不外发", "tool_quality_feedback"):
            self.assertIn(boundary, scoped[0]["content"])

    def test_factual_constraints_are_sent_to_final_model_not_only_planner(self):
        definition = SkillCatalog()._definitions["knowledge.technical-docs"]
        context = SkillExecutionContext(skill_key=definition["skill_key"], version=definition["version"],
            display_name="查证", description="测试", planner_instructions=tuple(definition["instructions"]),
            output_contract=tuple(definition["output_contract"]), allowed_tool_keys=(), required_tool_keys=(),
            optional_tool_keys=(), requires_tool_execution=True)
        self.assertIn("触发条件", context.final_answer_instructions)
        self.assertIn("后续操作", context.final_answer_instructions)
        self.assertIn("正文", context.final_answer_instructions)

    def test_new_skills_limit_tools_and_writes_remain_explicit(self):
        definitions = SkillCatalog()._definitions
        self.assertEqual(set(definitions["knowledge.technical-docs"]["required_tool_keys"]), {"docs.context7.resolve", "docs.context7.query"})
        draft = definitions["workspace.doc-coauthoring"]
        self.assertNotIn("workspace.files.apply_edit", draft["required_tool_keys"])
        self.assertFalse(draft["durable_eligible"])
        self.assertTrue(all(ToolCatalog().get(key).read_only for key in definitions["research.source-brief"]["required_tool_keys"]))

    def test_web_disallowed_also_blocks_docs_and_fetch_candidates(self):
        tools, _ = ToolCandidateSelector().select(query="读取 https://example.com/ 官方文档，但不要联网", enabled=True)
        self.assertFalse(any(tool.category in {"web_search", "web_fetch", "documentation"} for tool in tools))

    def test_explicit_skills_recall_their_evidence_steps(self):
        catalog = SkillCatalog()._definitions
        for key, query in (("research.source-brief", "阅读网页 https://example.com/"), ("knowledge.technical-docs", "查询 SQLAlchemy 官方文档")):
            allowed = set(catalog[key]["required_tool_keys"] + catalog[key]["optional_tool_keys"])
            tools, _ = ToolCandidateSelector().select(query=query, enabled=True, allowed_tool_keys=allowed)
            self.assertTrue(set(catalog[key]["required_tool_keys"]).issubset({tool.tool_key for tool in tools}))
            self.assertTrue({tool.tool_key for tool in tools}.issubset(allowed))

    def test_library_candidate_is_not_sufficient_documentation_evidence(self):
        contract = SkillCatalog()._definitions["knowledge.technical-docs"]["completion_contract"]
        source = map_public_research_text(payload="- Title: SQLAlchemy\n- Context7-compatible library ID: /websites/sqlalchemy_en_20\n- Description: Official SQLAlchemy documentation.", provider="context7", profile="library_catalog")[0]
        source.metadata["tool_key"] = "docs.context7.resolve"
        assessment = assess_evidence_sufficiency(query="查询 SQLAlchemy 文档", sources=[source], completion_contract=contract)
        self.assertEqual(assessment.status, "insufficient")
        self.assertTrue(needs_followup(query="查询 SQLAlchemy 文档", contract=contract, observations=[source.to_public_dict()]))
        self.assertIn("missing_required_tool:docs.context7.query", assessment.reasons)

    def test_docs_must_be_nonempty_and_actually_injected(self):
        contract = SkillCatalog()._definitions["knowledge.technical-docs"]["completion_contract"]
        source = self._doc_source()
        self.assertEqual(assess_evidence_sufficiency(query="查证文档", sources=[source], completion_contract=contract).status, "sufficient")
        source.used_in_prompt = False
        self.assertEqual(assess_evidence_sufficiency(query="查证文档", sources=[source], completion_contract=contract).status, "insufficient")

    def test_search_snippet_cannot_complete_webpage_reading(self):
        contract = SkillCatalog()._definitions["research.source-brief"]["completion_contract"]
        source = map_public_research_text(payload=f"URL: {URL}\n{BODY}", provider="exa", profile="web_pages")[0]
        source.metadata["tool_key"] = "web.exa.search"
        self.assertTrue(needs_followup(query="读取网页正文", contract=contract, observations=[source.to_public_dict()]))
        self.assertEqual(assess_evidence_sufficiency(query="读取网页正文", sources=[source], completion_contract=contract).status, "insufficient")

    def test_generic_completion_does_not_invent_parameters(self):
        contract = SkillCatalog()._definitions["knowledge.technical-docs"]["completion_contract"]
        context = SkillExecutionContext(skill_key="knowledge.technical-docs", version="1.0.0", display_name="查证",
            description="测试", planner_instructions=(), output_contract=(),
            allowed_tool_keys=("docs.context7.resolve", "docs.context7.query"),
            required_tool_keys=("docs.context7.query",), optional_tool_keys=(),
            requires_tool_execution=True, completion_contract=contract)
        plan = ToolPlan(plan_id="test", router="test", external_context_allowed=True, should_use_tools=False, calls=[])
        self.assertFalse(augment_plan(plan=plan, query="查证文档", observations=[self._doc_source().to_public_dict()], skill_context=context, catalog=ToolCatalog()))
        self.assertFalse(plan.should_use_tools)
        self.assertEqual(plan.calls, [])

    def test_plain_weather_keeps_one_search_fallback(self):
        tools, _ = ToolCandidateSelector().select(query="深圳今天气温怎么样", enabled=True)
        self.assertEqual({tool.tool_key for tool in tools}, {"amap.maps.weather", "web.tavily.search"})

    def test_selected_docs_skill_requires_evidence_without_keyword(self):
        contract = SkillCatalog()._definitions["knowledge.technical-docs"]["completion_contract"]
        self.assertEqual(assess_evidence_sufficiency(query="Explain the difference", sources=[], completion_contract=contract).status, "insufficient")

    def test_drafting_cannot_claim_a_file_excluded_from_prompt(self):
        from app.services.tools.schemas import ExternalSource
        contract = SkillCatalog()._definitions["workspace.doc-coauthoring"]["completion_contract"]
        source = ExternalSource(source_type="workspace_file_read", provider="workspace", title="资料", display_text=BODY, rank=1, citation_label="[T1]", used_in_prompt=False)
        self.assertEqual(assess_evidence_sufficiency(query="根据资料起草说明", sources=[source], completion_contract=contract).status, "insufficient")

    @staticmethod
    def _doc_source():
        source = map_public_research_text(payload=f"### Flush\nSource: {URL}\n{BODY}", provider="context7", profile="documentation", arguments={"libraryId": "/websites/sqlalchemy_en_20"})[0]
        source.metadata["tool_key"] = "docs.context7.query"
        return source


if __name__ == "__main__":
    unittest.main()

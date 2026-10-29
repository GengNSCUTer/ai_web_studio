"""真实任务失败暴露出的正文缺口、实况/预报和来源交付回归。"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.tools.adapters import ToolAdapterRunner
from app.services.tools.catalog import ToolCatalog
from app.services.tools.completion_contract import infer_read_completion, needs_followup
from app.services.tools.formatter import ExternalContextAssembler
from app.services.tools.planner import LLMToolPlanner
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolExecutionFeedbackError
from app.services.external_context_service import ExternalContextService
from app.services.tools.schemas import ToolPlan, ToolResultBinding
from app.repositories.tool_trace_repo import ToolTraceRepository


class ReadCompletionTests(unittest.TestCase):
    def test_round_local_ids_cannot_overwrite_other_tool_or_lose_bindings(self):
        def call(key):
            return PlannedToolCall(call_id="call_1",tool_key=key,provider="test",category="test",
                                   display_name=key,confidence=1,reason="测试")
        first=ToolPlan(plan_id="p1",router="test",external_context_allowed=True,should_use_tools=True,calls=[call("weather")])
        second=ToolPlan(plan_id="p2",router="test",external_context_allowed=True,should_use_tools=True,calls=[call("search")])
        second.trace_events=[{"type":"tool_call_start","call_id":"call_1","arguments":{"call_id":"call_1"}}]
        second.calls += [PlannedToolCall(call_id="call_2",tool_key="read",provider="test",category="test",
            display_name="read",confidence=1,reason="测试",depends_on=["call_1"],result_bindings=[
                ToolResultBinding(source_call_id="call_1",source_path="/sources/0/metadata/raw/file_id",target_argument="file_id")])]
        used=set()
        a=ExternalContextService._scope_plan_call_ids(first,1,used)
        b=ExternalContextService._scope_plan_call_ids(second,2,used)
        self.assertNotEqual(a.calls[0].call_id,b.calls[0].call_id)
        self.assertEqual(b.calls[1].depends_on,[b.calls[0].call_id])
        self.assertEqual(b.calls[1].result_bindings[0].source_call_id,b.calls[0].call_id)
        self.assertEqual(first.calls[0].call_id,"call_1")
        self.assertEqual(b.trace_events[0]["call_id"],b.calls[0].call_id)
        self.assertEqual(b.trace_events[0]["arguments"]["call_id"],"call_1")
        events=[dict(type="tool_call_end",call_id=c.call_id,tool_key=c.tool_key) for c in [*a.calls,*b.calls]]
        rows=ToolTraceRepository._build_call_runs("route",events,[])
        self.assertEqual(len(rows),3)
        self.assertEqual({r.tool_key for r in rows},{"weather","search","read"})

    def test_duplicate_within_single_plan_is_still_duplicate(self):
        c=PlannedToolCall(call_id="dup",tool_key="read",provider="test",category="test",display_name="r",confidence=1,reason="测试")
        p=ToolPlan(plan_id="p",router="test",external_context_allowed=True,should_use_tools=True,calls=[c,c])
        scoped=ExternalContextService._scope_plan_call_ids(p,1)
        self.assertEqual(scoped.calls[0].call_id,scoped.calls[1].call_id)
    def test_fallback_reads_only_verified_file_id(self):
        planner=LLMToolPlanner();query="读取 notes.md 告诉我保留多久"
        plan=planner._verified_read_fallback(query=query,candidate_keys={"workspace.files.read"},
            observations=[{"source_type":"workspace_file_search","metadata":{"file_id":"opaque-file"}}])
        self.assertEqual(plan.calls[0].arguments,{"file_id":"opaque-file"})
        self.assertIsNone(planner._verified_read_fallback(query=query,candidate_keys=set(),
            observations=[{"source_type":"workspace_file_search","metadata":{"file_id":"opaque-file"}}]))

    def test_context_fallback_uses_actual_library_and_keeps_original_query(self):
        query="使用 Context7 查询 httpx 的 Timeout 配置"
        planner=LLMToolPlanner()
        plan=planner._verified_read_fallback(query=query,candidate_keys={"docs.context7.query"},
            observations=[{"source_type":"documentation_library","metadata":{"library_id":"/encode/httpx","title":"HTTPX"}}])
        self.assertEqual(plan.calls[0].arguments,{"libraryId":"/encode/httpx","query":query})
        empty=planner._verified_read_fallback(query=query,candidate_keys={"web.tavily.search"},observations=[])
        self.assertFalse(empty.calls)

    def test_library_resolution_is_not_documentation(self):
        contract = infer_read_completion("使用 Context7 查 httpx Timeout，不读本地文件")
        obs = [{"metadata": {"tool_key": "docs.context7.resolve"}}]
        self.assertTrue(needs_followup(query="Context7 httpx", observations=obs, contract=contract))
        obs += [{"metadata": {"tool_key": "docs.context7.query"}}]
        self.assertFalse(needs_followup(query="Context7 httpx", observations=obs, contract=contract))

    def test_empty_file_search_does_not_finish_content_task(self):
        query = "找一下上传的备份保留笔记，项目代号和周期是什么？"
        contract = infer_read_completion(query)
        obs = [{"source_type": "workspace_file_search", "metadata": {"tool_key": "workspace.files.search"}}]
        self.assertTrue(needs_followup(query=query, observations=obs, contract=contract))
        obs += [{"source_type": "workspace_file_read", "metadata": {"tool_key": "workspace.files.read"}}]
        self.assertFalse(needs_followup(query=query, observations=obs, contract=contract))

    def test_listing_only_and_general_questions_do_not_force_read(self):
        self.assertIsNone(infer_read_completion("列出当前工作区有哪些文件"))
        self.assertIsNone(infer_read_completion("只回复已收到"))

    def test_actual_file_evidence_precedes_miss_but_preserves_search_scope(self):
        miss = ExternalSource(source_type="workspace_file_search", provider="workspace", title="搜索",
            display_text="未找到", metadata={"empty_reason": "no_matching_files"})
        found = ExternalSource(source_type="workspace_file_read", provider="workspace", title="notes.md",
            display_text="Project CedarLedger, retention 17 days.")
        text = ExternalContextAssembler.format_sources_for_prompt([miss, found],max_chars=2000)
        self.assertIn("未找到",text)
        self.assertIn("CedarLedger",text)
        self.assertLess(text.index("CedarLedger"),text.index("内容：未找到"))
        self.assertTrue(miss.used_in_prompt)
        self.assertTrue(found.used_in_prompt)


class WeatherModeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.definition = ToolCatalog().get("amap.maps.weather")
        self.runner = ToolAdapterRunner()

    def call(self, arguments):
        return PlannedToolCall(call_id="weather",tool_key=self.definition.tool_key,provider="amap",
            category="weather",display_name="天气",confidence=1,reason="测试",arguments=arguments)

    async def test_default_live_uses_fixed_real_time_api_and_canonical_fields(self):
        source = ExternalSource(source_type="weather",provider="amap",title="天气",display_text="当前20度",
            metadata={"city":"深圳市","weather":"晴","temperature":"20"})
        with patch("app.services.tools.adapters.AmapToolProvider.query_weather",new=AsyncMock(return_value=[source])) as live, \
             patch.object(self.runner,"_run_mcp_http",new=AsyncMock()) as mcp:
            sources,metadata=await self.runner.run(definition=self.definition,call=self.call({"city":"深圳"}),api_key="fixture")
        live.assert_awaited_once_with("", city="深圳"); mcp.assert_not_called()
        self.assertEqual(sources[0].metadata["raw"]["weather_data_type"],"live")
        self.assertEqual(metadata["weather_data_type"],"live")

    async def test_explicit_forecast_keeps_forecast_path(self):
        with patch.object(self.runner,"_run_mcp_http",new=AsyncMock(return_value=([],{}))) as mcp, \
             patch("app.services.tools.adapters.AmapToolProvider.query_weather",new=AsyncMock()) as live:
            await self.runner.run(definition=self.definition,call=self.call({"city":"深圳","mode":"forecast"}),api_key="fixture")
        mcp.assert_awaited_once();live.assert_not_called()

    async def test_forecast_does_not_forward_platform_mode_to_mcp(self):
        with patch("app.services.tools.adapters.McpHttpClient.call_tool",new=AsyncMock(return_value=SimpleNamespace(raw={}))) as call:
            await self.runner.run(definition=self.definition,call=self.call({"city":"深圳","mode":"forecast"}),api_key="fixture")
        self.assertEqual(call.call_args.kwargs["arguments"],{"city":"深圳"})

    async def test_live_error_cannot_silently_become_forecast(self):
        with patch("app.services.tools.adapters.AmapToolProvider.query_weather",new=AsyncMock(side_effect=RuntimeError("无实况"))), \
             patch.object(self.runner,"_run_mcp_http",new=AsyncMock()) as mcp:
            with self.assertRaises(RuntimeError):
                await self.runner.run(definition=self.definition,call=self.call({"city":"深圳"}),api_key="fixture")
        mcp.assert_not_called()

    async def test_missing_credentials_still_rejected(self):
        with self.assertRaises(ToolExecutionFeedbackError):
            await self.runner.run(definition=self.definition,call=self.call({"city":"深圳"}),api_key=None)

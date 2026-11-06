"""验证上下文与流式图的实际阶段、取消、背压和请求隔离。"""

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.agent_graph.context_graph import build_context_graph, run_context_graph
from app.services.agent_graph.chat_stream_graph import ChatStreamGraphContext, stream_chat_graph
from app.services.chat_assembly_runtime import ChatAssemblyRuntime
from app.services.chat_provider_service import ChatStreamEvent


class StageService:
    def __init__(self):
        self.thread = threading.get_ident()
        self.calls = []

    def mark(self, name):
        assert threading.get_ident() == self.thread, "共享Session步骤不能被送到线程池"
        self.calls.append(name)

    def snapshot(self, assembly):
        self.mark("snapshot")

    async def retrieve_memory(self, assembly):
        self.mark("memory")
        assembly.memory_bundle = SimpleNamespace(count=0)

    async def execute_tools(self, assembly):
        self.mark("tools")
        assembly.external_context_result = SimpleNamespace(sources=[])

    async def retrieve_knowledge(self, assembly):
        self.mark("rag")
        assembly.knowledge_context_result = SimpleNamespace(sources=[])

    async def refresh_summary(self, assembly):
        self.mark("summary")

    async def govern_prompt(self, assembly):
        self.mark("govern")
        assembly.governed_context = SimpleNamespace(
            stats=SimpleNamespace(total_tokens_estimate=30), final_source_map={"[KB1]": {}}
        )

    def finalize_context(self, assembly):
        self.mark("ready")
        return SimpleNamespace(context_details={}, context_stats={})


def assembly():
    return ChatAssemblyRuntime(None, [], None, None, None, False, None, False)


class ChatGraphStagesTest(unittest.IsolatedAsyncioTestCase):
    async def test_actual_context_stages_run_once_on_same_thread(self):
        service = StageService()
        result = await run_context_graph(service, assembly())
        self.assertEqual(service.calls, ["snapshot", "memory", "tools", "rag", "summary", "govern", "ready"])
        self.assertEqual(result.context_details["context_graph_phases"],
                         ["snapshot", "memory", "tools_subgraph", "rag", "summary", "govern", "context_ready"])
        self.assertIsNone(build_context_graph().checkpointer)

    async def test_context_error_does_not_replay_tools_or_enter_later_nodes(self):
        service = StageService()
        service.retrieve_knowledge = AsyncMock(side_effect=RuntimeError("controlled"))
        with self.assertRaisesRegex(RuntimeError, "controlled"):
            await run_context_graph(service, assembly())
        self.assertEqual(service.calls, ["snapshot", "memory", "tools"])

    async def test_context_instances_never_share_phases(self):
        first, second = assembly(), assembly()
        await asyncio.gather(run_context_graph(StageService(), first), run_context_graph(StageService(), second))
        self.assertEqual(first.phases, second.phases)
        self.assertIsNot(first.phases, second.phases)

    def stream_context(self, source, persist=None):
        return ChatStreamGraphContext(source, persist or AsyncMock(return_value=True), [], [], {}, {})

    async def test_stream_is_incremental_and_persists_after_usage_consumed(self):
        release = asyncio.Event()

        async def source():
            yield ChatStreamEvent("answer_delta", "first")
            await release.wait()
            yield ChatStreamEvent("provider_usage", data={"input_tokens": 7})

        consumed_usage = []

        async def persist(status):
            self.assertEqual(consumed_usage, [7])
            self.assertEqual(status, "done")
            return True

        context = self.stream_context(source(), persist)
        stream = stream_chat_graph(context)
        first = await asyncio.wait_for(stream.__anext__(), 1)
        self.assertEqual(first.text, "first")
        self.assertIsNone(context.persisted)
        release.set()
        async for event in stream:
            consumed_usage.append(event.data["input_tokens"])
        self.assertTrue(context.persisted)
        self.assertEqual(context.content_parts, ["first"])
        self.assertEqual(context.phases, ["generate", "persist_terminal"])

    async def test_stream_provider_error_keeps_partial_and_saves_failed_once(self):
        async def source():
            yield ChatStreamEvent("answer_delta", "已生成")
            raise RuntimeError("controlled")

        context = self.stream_context(source())
        events = [event async for event in stream_chat_graph(context)]
        self.assertEqual([event.text for event in events], ["已生成"])
        context.persist.assert_awaited_once_with("failed")
        self.assertIsInstance(context.error, RuntimeError)

    async def test_stream_internal_cancel_is_not_swallowed_by_graph(self):
        async def source():
            yield ChatStreamEvent("answer_delta", "partial")
            raise asyncio.CancelledError

        context = self.stream_context(source())
        with self.assertRaises(asyncio.CancelledError):
            async for _ in stream_chat_graph(context):
                pass
        self.assertEqual(context.content_parts, ["partial"])
        context.persist.assert_not_awaited()

    async def test_consumer_close_stops_provider_and_does_not_finalize_done(self):
        closed = asyncio.Event()

        async def source():
            try:
                yield ChatStreamEvent("answer_delta", "first")
                await asyncio.sleep(10)
            finally:
                closed.set()

        context = self.stream_context(source())
        stream = stream_chat_graph(context)
        await stream.__anext__()
        await stream.aclose()
        await asyncio.wait_for(closed.wait(), 1)
        context.persist.assert_not_awaited()

    async def test_external_cancel_releases_provider_and_rethrows(self):
        started, closed = asyncio.Event(), asyncio.Event()

        async def source():
            try:
                started.set()
                await asyncio.sleep(10)
                yield ChatStreamEvent("answer_delta", "late")
            finally:
                closed.set()

        context = self.stream_context(source())

        async def consume():
            async for _ in stream_chat_graph(context):
                pass

        task = asyncio.create_task(consume())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())
        context.persist.assert_not_awaited()

    async def test_stream_rejected_generation_does_not_retry_persistence(self):
        async def source():
            yield ChatStreamEvent("answer_delta", "old")

        context = self.stream_context(source(), AsyncMock(return_value=False))
        async for _ in stream_chat_graph(context):
            pass
        self.assertFalse(context.persisted)
        context.persist.assert_awaited_once_with("done")

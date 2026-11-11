"""Chat生成与保存终态的图；内部传递业务事件，HTTP边界统一编码SSE。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context


class ChatStreamGraphState(TypedDict, total=False):
    status: str
    answer_chars: int
    reasoning_chars: int
    persisted: bool
    error_type: str


@dataclass
class ChatStreamGraphContext:
    # 只在当前流中存在；不把回答正文、客户端、回调或凭据写入State。
    source: AsyncIterator[Any]
    persist: Callable[[str], Awaitable[bool]]
    content_parts: list[str]
    reasoning_parts: list[str]
    context_details: dict
    context_stats: dict
    error: Exception | None = None
    persisted: bool | None = None
    phases: list[str] = field(default_factory=list)
    emit: Callable[[Any], Awaitable[None]] | None = None


async def _generate(state: ChatStreamGraphState, runtime: Runtime[ChatStreamGraphContext]):
    context = runtime.context
    context.phases.append("generate")
    iterator = context.source.__aiter__()
    try:
        async for event in iterator:
            if event.type == "answer_delta":
                context.content_parts.append(event.text)
            elif event.type == "reasoning_delta":
                context.reasoning_parts.append(event.text)
            assert context.emit is not None
            await context.emit(event)
    except Exception as exc:
        # 只记录错误类型；原异常留在本请求，交给现有公开错误分类器处理。
        # CancelledError不在这里吞掉，路由会在屏蔽取消的短作用域中保存partial。
        context.error = exc
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()
    return {
        "status": "failed" if context.error else "done",
        "answer_chars": sum(map(len, context.content_parts)),
        "reasoning_chars": sum(map(len, context.reasoning_parts)),
        "error_type": type(context.error).__name__ if context.error else "",
    }


async def _finalize(state: ChatStreamGraphState, runtime: Runtime[ChatStreamGraphContext]):
    context = runtime.context
    context.phases.append("persist_terminal")
    context.context_details["chat_stream_graph_phases"] = list(context.phases)
    context.context_stats["chat_stream_orchestrator"] = "langgraph"
    context.context_stats["chat_stream_graph_phases"] = list(context.phases)
    context.persisted = await context.persist(state["status"])
    return {"persisted": context.persisted}


@lru_cache(maxsize=1)
def build_chat_stream_graph():
    graph = StateGraph(ChatStreamGraphState, context_schema=ChatStreamGraphContext)
    graph.add_node("generate", _generate)
    graph.add_node("persist_terminal", _finalize)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", "persist_terminal")
    graph.add_edge("persist_terminal", END)
    return graph.compile(name="aiws_chat_stream")


async def stream_chat_graph(context: ChatStreamGraphContext):
    # Python3.10不能自动传播节点Task的Runnable ContextVar；框架custom writer
    # 在本版本会报outside runnable。使用请求级单槽桥接，不读框架私有上下文。
    # 每个事件等路由消费后再继续，防止慢客户端积压、usage尚未处理便提前落库。
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)

    async def emit(event):
        delivered = asyncio.get_running_loop().create_future()
        await queue.put((event, delivered))
        await delivered

    context.emit = emit

    async def produce():
        with tracing_context(enabled=False):
            await build_chat_stream_graph().ainvoke(
                {"status": "streaming"}, context=context, config={"recursion_limit": 8},
            )

    producer = asyncio.create_task(produce())
    pending = None
    try:
        while True:
            pending = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({producer, pending}, return_when=asyncio.FIRST_COMPLETED)
            if pending not in done:
                # 内部取消、持久化异常和正常无事件结束都必须传回HTTP层。
                await producer
                break
            event, delivered = pending.result()
            pending = None
            yield event
            if not delivered.done():
                delivered.set_result(None)
    finally:
        if pending is not None:
            pending.cancel()
        if not producer.done():
            producer.cancel()
        await asyncio.gather(*(task for task in (pending, producer) if task is not None),
                             return_exceptions=True)

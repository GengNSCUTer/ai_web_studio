"""工具循环使用原生图检查点；业务事务独立提交工具结果。"""

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from weakref import WeakKeyDictionary

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context

from app.services.chat_tool_snapshot import CHANNEL_FIELDS, ChatToolCheckpointState
from app.services.tools.loop_runtime import SyncToolRun
from app.services.tools.schemas import ToolTraceEvent


class SyncToolGraphState(ChatToolCheckpointState, total=False):
    route: str
    # 只读取升级前的检查点；有新节点输出时标记为已迁移。
    tool_state: str | None


@dataclass(frozen=True)
class SyncToolGraphContext:
    run: SyncToolRun
    persistence: object = None


async def _update(state, context, route):
    updates = {"route": route}
    if context.persistence is not None:
        channels = await asyncio.to_thread(context.persistence.checkpoint_state, context.run)
        updates.update({name: value for name, value in channels.items() if state.get(name) != value})
        if state.get("tool_state") is not None:
            updates["tool_state"] = None
    return updates


def _record(run: SyncToolRun, node: str):
    run.events.append(ToolTraceEvent(type="tool_graph_node", payload={
        "graph": "sync_tool_v1", "node": node, "round": run.round_index,
        "tool_calls_used": run.budget.tool_calls_used,
    }))


async def _reserve(state, runtime: Runtime[SyncToolGraphContext]):
    context = runtime.context
    return await _update(state, context, "plan" if context.run.reserve_round() else "finalize")


async def _plan(state, runtime: Runtime[SyncToolGraphContext]):
    context = runtime.context
    ready = await context.run.plan_round()
    _record(context.run, "plan")
    return await _update(state, context, "execute" if ready else "finalize")


async def _execute(state, runtime: Runtime[SyncToolGraphContext]):
    context = runtime.context
    if context.persistence is not None:
        # 计划已进入图状态；业务步骤仍记录实际调用、依赖阻断和产物关系。
        await asyncio.to_thread(context.persistence.record_plan, context.run)
    ready = await context.run.execute_round()
    if ready and context.persistence is not None:
        await asyncio.to_thread(context.persistence.record_outcomes, context.run)
    _record(context.run, "execute")
    return await _update(state, context, "observe" if ready else "finalize")


async def _observe(state, runtime: Runtime[SyncToolGraphContext]):
    context = runtime.context
    run = context.run
    if context.persistence is not None:
        # 已发出但未返回的真实调用也占额度，不依赖图回放次数。
        run.budget.tool_calls_used = await asyncio.to_thread(context.persistence.attempts_used)
    again = run.observe_round()
    _record(run, "observe")
    return await _update(state, context, "reserve" if again else "finalize")


async def _finalize(state, runtime: Runtime[SyncToolGraphContext]):
    context = runtime.context
    if context.persistence is not None:
        await asyncio.to_thread(context.persistence.finish)
    _record(context.run, "finalize")
    return await _update(state, context, "done")


def _build_sync_tool_graph(checkpointer=None):
    graph = StateGraph(SyncToolGraphState, context_schema=SyncToolGraphContext)
    graph.add_edge(START, "reserve")
    for name, node, targets in (
        ("reserve", _reserve, ("plan", "finalize")),
        ("plan", _plan, ("execute", "finalize")),
        ("execute", _execute, ("observe", "finalize")),
        ("observe", _observe, ("reserve", "finalize")),
    ):
        graph.add_node(name, node)
        graph.add_conditional_edges(name, lambda state: state["route"], {target: target for target in targets})
    graph.add_node("finalize", _finalize)
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer, name="aiws_chat_tool")


@lru_cache(maxsize=1)
def build_sync_tool_graph():
    return _build_sync_tool_graph()


# SQLite仅供单进程测试；生产由带租约保护的PostgreSQL Saver跨进程恢复。
_test_savers = WeakKeyDictionary()


async def _invoke(graph, config, context):
    from app.services.chat_tool_runtime import ChatToolStateUnavailable

    run, persistence = context.run, context.persistence
    previous = await graph.aget_state(config)
    values = previous.values
    # 新格式有任何字段就只走新格式校验，不能回退到过时的字符串继续执行。
    structured = "checkpoint_schema" in values or any(channel in values for channel in CHANNEL_FIELDS)
    packed = values if structured else values.get("tool_state")
    try:
        if packed is not None:
            # Pending Writes已完成的节点可更新route，但在下一次super-step前next仍为空。
            node = previous.next[0] if previous.next else previous.values.get("route")
            if ((not structured and not isinstance(packed, str)) or len(previous.next) > 1
                    or node not in {"reserve", "plan", "execute", "observe", "finalize", "done"}):
                raise ValueError("原生工具图状态不完整，不能重新执行旧任务")
            await asyncio.to_thread(persistence.restore, run, snapshot=packed, next_node=node)
        else:
            if values.get("route") is not None:
                raise ValueError("工具图有进度但缺少恢复字段")
            node = await asyncio.to_thread(persistence.restore, run)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ChatToolStateUnavailable(str(exc)) from exc
    run.events.append(ToolTraceEvent(type="tool_persistence", payload={
        "agent_run_id": persistence.agent_id, "chat_run_id": persistence.claim.run_id,
        "recovered": persistence.claim.version > 1, "resume_node": node,
        "checkpoint_source": "langgraph" if packed is not None else "initial",
    }))
    # 终态只重新渲染证据，不再执行节点。
    if node == "done" and packed is not None:
        return
    if packed is not None:
        await graph.ainvoke(None, context=context, config=config, durability="sync")
        return
    initial = {"route": "reserve", **await asyncio.to_thread(persistence.checkpoint_state, run)}
    await graph.ainvoke(initial, context=context, config=config, durability="sync")


async def run_sync_tool_graph(run: SyncToolRun):
    from app.services.chat_tool_runtime import current_tool_persistence

    persistence = current_tool_persistence.get()
    context = SyncToolGraphContext(run, persistence)
    recursion = run.service.run_policy.max_planning_rounds * 5 + 10
    with tracing_context(enabled=False):
        if persistence is None:
            await build_sync_tool_graph().ainvoke({}, context=context, config={"recursion_limit": recursion})
        else:
            from app.services.chat_tool_checkpoint import ChatToolCheckpointIdentity, ChatToolPostgresSaver
            from app.services.durable_checkpoint_service import durable_checkpointer
            from app.services.tools.db_boundary import release_read_transaction

            bind = run.service.db.get_bind()
            release_read_transaction(run.service.db)
            identity = ChatToolCheckpointIdentity(run.service.executor.user_id, run.service.executor.project_id,
                persistence.agent_id, persistence.coordinator_id, "", persistence.claim.owner,
                persistence.claim.version, persistence.claim.run_id)
            config = {**identity.config, "recursion_limit": recursion}
            if bind.dialect.name == "postgresql":
                async with durable_checkpointer(bind.url, identity, saver_type=ChatToolPostgresSaver) as saver:
                    await _invoke(_build_sync_tool_graph(saver), config, context)
            else:
                saver = _test_savers.setdefault(bind, InMemorySaver())
                await _invoke(_build_sync_tool_graph(saver), config, context)
    return run.finish()

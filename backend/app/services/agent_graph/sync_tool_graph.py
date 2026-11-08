"""Chat工具图：Worker路径保存业务进度和原生PG检查点，独立调用保留无状态图。"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
from functools import lru_cache
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context

from app.services.tools.loop_runtime import SyncToolRun
from app.services.tools.schemas import ToolTraceEvent


class SyncToolGraphState(TypedDict, total=False):
    """仅记录编排进展，不把凭据、数据库Session或客户端放入State。"""

    round_index: int
    has_plan: bool
    execution_ready: bool
    continue_loop: bool
    next_action: str
    terminal_reason: str
    source_count: int
    tool_calls_used: int
    next_node: str


@dataclass(frozen=True)
class SyncToolGraphContext:
    # 业务对象走Runtime Context，PG图State只存节点名和安全计数。
    run: SyncToolRun
    persistence: object = None


async def _save(runtime, next_node):
    context = runtime.context
    if context.persistence is not None:
        await asyncio.to_thread(context.persistence.save, context.run, next_node)


def _dispatch(state, runtime):
    return {"next_node": "finalize" if state.get("next_node") == "done" else state.get("next_node", "plan")}


def _record(run: SyncToolRun, node: str):
    run.events.append(ToolTraceEvent(type="tool_graph_node", payload={
        "graph": "sync_tool_v1", "node": node, "round": run.round_index,
        "tool_calls_used": run.budget.tool_calls_used,
    }))


async def _plan(state: SyncToolGraphState, runtime: Runtime[SyncToolGraphContext]):
    run = runtime.context.run
    ready = await run.plan_round()
    await _save(runtime, "execute" if ready else "finalize")
    _record(run, "plan")
    return {"round_index": run.round_index, "has_plan": ready}


async def _execute(state: SyncToolGraphState, runtime: Runtime[SyncToolGraphContext]):
    run = runtime.context.run
    ready = await run.execute_round()
    await _save(runtime, "observe" if ready else "finalize")
    _record(run, "execute")
    return {"execution_ready": ready}


async def _observe(state: SyncToolGraphState, runtime: Runtime[SyncToolGraphContext]):
    run = runtime.context.run
    if runtime.context.persistence is not None:
        # 以真实尝试次数计算总额，包括崩溃前已发出、但还没有结果的请求。
        run.budget.tool_calls_used = await asyncio.to_thread(runtime.context.persistence.attempts_used)
    again = run.observe_round()
    await _save(runtime, "plan" if again else "finalize")
    _record(run, "observe")
    return {"continue_loop": again, "next_action": run.next_action,
        "terminal_reason": run.terminal_reason, "source_count": len(run.sources),
        "tool_calls_used": run.budget.tool_calls_used}


async def _finalize(state: SyncToolGraphState, runtime: Runtime[SyncToolGraphContext]):
    run = runtime.context.run
    _record(run, "finalize")
    run.result = run.finish()
    await _save(runtime, "done")
    return {"next_action": run.next_action, "terminal_reason": run.terminal_reason,
        "source_count": len(run.sources), "tool_calls_used": run.budget.tool_calls_used}


def _build_sync_tool_graph(checkpointer=None):
    """编译结构可复用；所有可变业务值必须来自每次调用的Context。"""
    graph = StateGraph(SyncToolGraphState, context_schema=SyncToolGraphContext)
    graph.add_node("dispatch", _dispatch)
    graph.add_node("plan", _plan)
    graph.add_node("execute", _execute)
    graph.add_node("observe", _observe)
    graph.add_node("finalize", _finalize)
    graph.add_edge(START, "dispatch")
    graph.add_conditional_edges("dispatch", lambda state: state["next_node"],
        {name: name for name in ("plan", "execute", "observe", "finalize")})
    graph.add_conditional_edges("plan", lambda state: "execute" if state["has_plan"] else "finalize",
                               {"execute": "execute", "finalize": "finalize"})
    graph.add_conditional_edges("execute", lambda state: "observe" if state["execution_ready"] else "finalize",
                               {"observe": "observe", "finalize": "finalize"})
    graph.add_conditional_edges("observe", lambda state: "plan" if state["continue_loop"] else "finalize",
                               {"plan": "plan", "finalize": "finalize"})
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer, name="aiws_chat_tool")


@lru_cache(maxsize=1)
def build_sync_tool_graph():
    return _build_sync_tool_graph()


async def run_sync_tool_graph(run: SyncToolRun):
    from app.services.chat_tool_runtime import current_tool_persistence
    persistence = current_tool_persistence.get()
    next_node = await asyncio.to_thread(persistence.restore, run) if persistence else "plan"
    if persistence:
        run.events.append(ToolTraceEvent(type="tool_persistence", payload={
            "agent_run_id": persistence.agent_id, "chat_run_id": persistence.claim.run_id,
            "recovered": persistence.claim.version > 1, "resume_node": next_node,
        }))
    context = SyncToolGraphContext(run, persistence)
    config = {"recursion_limit": run.service.run_policy.max_planning_rounds * 4 + 10}
    # 业务轮次与图superstep不是一回事；递归保护不能抢先截断合法业务轮次。
    # 禁止因宿主遗留的 LANGSMITH_TRACING 环境变量将用户输入/节点内容外传。
    with tracing_context(enabled=False):
        initial = {"round_index": run.round_index, "continue_loop": False, "next_node": next_node}
        if persistence and run.service.db.get_bind().dialect.name == "postgresql":
            from app.services.chat_tool_checkpoint import ChatToolCheckpointIdentity, ChatToolPostgresSaver
            from app.services.durable_checkpoint_service import durable_checkpointer
            from app.services.tools.db_boundary import release_read_transaction
            release_read_transaction(run.service.db)
            identity = ChatToolCheckpointIdentity(run.service.executor.user_id, run.service.executor.project_id,
                persistence.agent_id, persistence.coordinator_id, "", persistence.claim.owner,
                persistence.claim.version, persistence.claim.run_id)
            config.update(identity.config)
            config["recursion_limit"] = run.service.run_policy.max_planning_rounds * 4 + 10
            async with durable_checkpointer(run.service.db.get_bind().url, identity, saver_type=ChatToolPostgresSaver) as saver:
                # 新租约从业务提交的位置重新分派。图检查点可能晚于业务产物，不能盲目重跑旧next。
                await _build_sync_tool_graph(saver).ainvoke(initial, context=context, config=config, durability="sync")
        else:
            await build_sync_tool_graph().ainvoke(initial, context=context, config=config)
    if run.result is None:
        raise RuntimeError("工具图未进入收尾节点")
    return run.result

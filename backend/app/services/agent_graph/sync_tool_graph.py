"""用真实图节点接管同步工具循环；本阶段不创建跨请求检查点。"""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class SyncToolGraphContext:
    # 请求级业务对象走 Runtime Context，本阶段不序列化/持久化。
    run: SyncToolRun


def _record(run: SyncToolRun, node: str):
    run.events.append(ToolTraceEvent(type="tool_graph_node", payload={
        "graph": "sync_tool_v1", "node": node, "round": run.round_index,
        "tool_calls_used": run.budget.tool_calls_used,
    }))


async def _plan(state: SyncToolGraphState, runtime: Runtime[SyncToolGraphContext]):
    run = runtime.context.run
    ready = await run.plan_round()
    _record(run, "plan")
    return {"round_index": run.round_index, "has_plan": ready}


async def _execute(state: SyncToolGraphState, runtime: Runtime[SyncToolGraphContext]):
    run = runtime.context.run
    ready = await run.execute_round()
    _record(run, "execute")
    return {"execution_ready": ready}


def _observe(state: SyncToolGraphState, runtime: Runtime[SyncToolGraphContext]):
    run = runtime.context.run
    again = run.observe_round()
    _record(run, "observe")
    return {"continue_loop": again, "next_action": run.next_action,
        "terminal_reason": run.terminal_reason, "source_count": len(run.sources),
        "tool_calls_used": run.budget.tool_calls_used}


def _finalize(state: SyncToolGraphState, runtime: Runtime[SyncToolGraphContext]):
    run = runtime.context.run
    _record(run, "finalize")
    run.result = run.finish()
    return {"next_action": run.next_action, "terminal_reason": run.terminal_reason,
        "source_count": len(run.sources), "tool_calls_used": run.budget.tool_calls_used}


@lru_cache(maxsize=1)
def build_sync_tool_graph():
    """编译结构可复用；所有可变业务值必须来自每次调用的Context。"""
    graph = StateGraph(SyncToolGraphState, context_schema=SyncToolGraphContext)
    graph.add_node("plan", _plan)
    graph.add_node("execute", _execute)
    graph.add_node("observe", _observe)
    graph.add_node("finalize", _finalize)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", lambda state: "execute" if state["has_plan"] else "finalize",
                               {"execute": "execute", "finalize": "finalize"})
    graph.add_conditional_edges("execute", lambda state: "observe" if state["execution_ready"] else "finalize",
                               {"observe": "observe", "finalize": "finalize"})
    graph.add_conditional_edges("observe", lambda state: "plan" if state["continue_loop"] else "finalize",
                               {"plan": "plan", "finalize": "finalize"})
    graph.add_edge("finalize", END)
    return graph.compile(name="aiws_sync_tool")


async def run_sync_tool_graph(run: SyncToolRun):
    # 业务轮次与图superstep不是一回事；递归保护不能抢先截断合法业务轮次。
    # 禁止因宿主遗留的 LANGSMITH_TRACING 环境变量将用户输入/节点内容外传。
    with tracing_context(enabled=False):
        await build_sync_tool_graph().ainvoke(
            {"round_index": 0, "continue_loop": False},
            context=SyncToolGraphContext(run),
            config={"recursion_limit": run.service.run_policy.max_planning_rounds * 4 + 8},
        )
    if run.result is None:
        raise RuntimeError("工具图未进入收尾节点")
    return run.result

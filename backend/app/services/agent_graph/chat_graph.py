"""Chat 准备主图。

本阶段先把“创建/复用本轮消息 → 解析运行时 → 组装上下文 → 交给流式层”纳入
LangGraph。最终模型流式输出仍由现有 Chat 路由负责，以保持 NDJSON、取消、超时和
generation CAS 的原有行为；后续阶段再把上下文内部的检索、工具和预算拆成子图节点。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context

from app.repositories.message_repo import ContextHistory, MessageGenerationConflict
from app.services.agent_graph.chat_graph_state import ChatGraphContext, ChatGraphState


def _record_phase(context: ChatGraphContext, phase: str) -> None:
    """把图阶段写入上下文诊断，避免把用户输入或凭据写入事件。"""

    if context.phase_log is None:
        context.phase_log = []
    if phase not in context.phase_log:
        context.phase_log.append(phase)
    result = context.result
    if result is None:
        return
    details = getattr(result, "context_details", None)
    if not isinstance(details, dict):
        return
    phases = details.setdefault("chat_graph_phases", [])
    for item in context.phase_log:
        if item not in phases:
            phases.append(item)


async def _bootstrap(state: ChatGraphState, runtime: Runtime[ChatGraphContext]) -> dict[str, Any]:
    context = runtime.context
    service = context.service
    if context.turn_kind == "new":
        context.default_settings = service.setting_service.get_or_create_user_settings(
            service.current_user.id
        )
        context.turn = service.turn_bootstrapper.bootstrap_new_turn(
            payload=context.payload,
            default_settings=context.default_settings,
        )
        context.conversation = context.turn.conversation
        context.user_message = context.turn.user_message
        context.assistant_message = context.turn.assistant_message
        context.history_rows = context.turn.history_rows
    else:
        execution_input = context.execution_input
        context.conversation = execution_input.conversation
        context.user_message = execution_input.user_message
        context.assistant_message = execution_input.assistant_message
        history_rows = execution_input.history_rows
        if isinstance(history_rows, ContextHistory):
            history_rows = service.message_repo.freeze_context_history(history_rows)
        service.turn_bootstrapper.apply_turn_overrides(
            conversation=context.conversation,
            model_name=execution_input.model_name,
            system_prompt=execution_input.system_prompt,
        )
        service.validate_attachment_context_inputs(
            list(getattr(context.user_message, "attachments", []) or [])
        )
        # 这里是准备阶段最后一个短事务；外部检索和模型调用不持有该事务。
        service.db.commit()
        context.history_rows = history_rows

    generation_id = getattr(context.assistant_message, "generation_id", None)
    context.generation_id = generation_id
    _record_phase(context, "bootstrap")
    return {
        "phase": "bootstrap",
        "turn_kind": context.turn_kind,
        "conversation_id": str(getattr(context.conversation, "id", "")),
        "user_message_id": str(getattr(context.user_message, "id", "")),
        "assistant_message_id": str(getattr(context.assistant_message, "id", "")),
        "generation_id": generation_id,
        "history_count": len(context.history_rows or []),
    }


async def _resolve_runtime(state: ChatGraphState, runtime: Runtime[ChatGraphContext]) -> dict[str, Any]:
    context = runtime.context
    service = context.service
    context.runtime = service._build_runtime_config(context.conversation)
    if context.turn_kind == "new":
        skill_key = context.payload.skill_key
    else:
        skill_key = context.execution_input.skill_key
    context.skill_context = service._resolve_skill_execution_context(
        skill_key=skill_key,
        conversation=context.conversation,
    )
    _record_phase(context, "runtime")
    return {"phase": "runtime"}


async def _assemble_context(state: ChatGraphState, runtime: Runtime[ChatGraphContext]) -> dict[str, Any]:
    context = runtime.context
    service = context.service
    if context.turn_kind == "new":
        payload = context.payload
        values = {
            "thinking_enabled": payload.thinking_enabled,
            "thinking_budget": payload.thinking_budget,
            "web_search_enabled": payload.web_search_enabled,
            "knowledge_base_id": payload.knowledge_base_id,
            "knowledge_base_ids": payload.knowledge_base_ids,
            "tool_run_mode": payload.tool_run_mode,
        }
    else:
        payload = context.execution_input
        values = {
            "thinking_enabled": payload.thinking_enabled,
            "thinking_budget": payload.thinking_budget,
            "web_search_enabled": payload.web_search_enabled,
            "knowledge_base_id": payload.knowledge_base_id,
            "knowledge_base_ids": payload.knowledge_base_ids,
            "tool_run_mode": payload.tool_run_mode,
        }
    context.result = await service.context_assembly_service.build_execution_context(
        conversation=context.conversation,
        history_rows=context.history_rows or [],
        user_message=context.user_message,
        assistant_message=context.assistant_message,
        runtime=context.runtime,
        skill_context=context.skill_context,
        **values,
    )
    _record_phase(context, "assemble")
    return {
        "phase": "assemble",
        "context_ready": True,
        "source_count": len(getattr(context.result, "external_sources", []) or []),
        "tool_event_count": len(getattr(context.result, "tool_events", []) or []),
        "context_token_count": int((getattr(context.result, "context_stats", {}) or {}).get(
            "total_tokens_estimate", 0
        ) or 0),
    }


def _finalize(state: ChatGraphState, runtime: Runtime[ChatGraphContext]) -> dict[str, Any]:
    context = runtime.context
    _record_phase(context, "finalize")
    return {"phase": "finalize", "terminal_reason": "prepared_for_stream"}


@lru_cache(maxsize=1)
def build_chat_graph():
    """编译可复用的图结构；请求数据只从 Runtime Context 读取。"""

    graph = StateGraph(ChatGraphState, context_schema=ChatGraphContext)
    graph.add_node("bootstrap", _bootstrap)
    graph.add_node("resolve_runtime", _resolve_runtime)
    graph.add_node("assemble_context", _assemble_context)
    graph.add_node("finalize", _finalize)
    graph.add_edge(START, "bootstrap")
    graph.add_edge("bootstrap", "resolve_runtime")
    graph.add_edge("resolve_runtime", "assemble_context")
    graph.add_edge("assemble_context", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(name="aiws_chat_prepare")


async def run_chat_graph(context: ChatGraphContext):
    """执行一次 Chat 准备图，不创建跨请求检查点。"""

    # 不让宿主已有 LANGSMITH_TRACING 环境变量把用户输入发送到外部服务。
    with tracing_context(enabled=False):
        await build_chat_graph().ainvoke(
            {"phase": "start", "turn_kind": context.turn_kind},
            context=context,
            config={"recursion_limit": 16},
        )
    if context.result is None:
        raise RuntimeError("Chat 图未完成上下文组装")
    return context.result

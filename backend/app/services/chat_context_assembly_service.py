from __future__ import annotations

import hashlib
import json
from time import perf_counter
from dataclasses import replace
from typing import Any
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import ContextHistory, MessageRepository
from app.repositories.tool_trace_repo import ToolTraceRepository
from app.repositories.knowledge_repo import KnowledgeRetrievalLogRepository
from app.services.attachment_context_service import AttachmentContextService
from app.services.chat_execution_models import (
    ChatExecutionContext,
    ChatRuntimeConfig,
    PromptDiagnosticsBundle,
    SummaryRefreshBundle,
)
from app.services.chat_assembly_runtime import ChatAssemblyRuntime
from app.services.chat_summary_coverage import ChatSummaryCoverage
from app.services.external_context_service import ExternalContextService
from app.services.evidence_sufficiency import assess_evidence_sufficiency
from app.services.file_edit_response import build_file_edit_response
from app.services.knowledge_context_service import KnowledgeContextResult, KnowledgeContextService
from app.services.message_service import MessageService
from app.services.memory_retrieval_service import MemoryRetrievalResult, MemoryRetrievalService
from app.services.memory_injection_policy import decide_memory_read
from app.services.prompt_builder_service import ContextPromptBuilder
from app.services.provider_capabilities import resolve_provider_capabilities
from app.services.skill_catalog import SkillExecutionContext
from app.services.tools.planner import PlannerRuntime
from app.services.tools.run_policy import resolve_tool_run_policy
from app.services.citation_audit_service import is_smalltalk_query
from app.services.mcp_resource_service import McpResourceService
from app.services.tools.formatter import ExternalContextAssembler
from app.services.tools.task_spec import build_task_spec
from app.services.tools.completion_verifier import verify_final_completion, sources_from_final_map, FINAL_COMPLETION_GUIDANCE
from app.services.tools.schemas import ExternalContextResult, ToolTraceEvent


def clean_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class ChatContextAssemblyService:
    """上下文组装层。

    这一层不创建消息，也不调用最终回答模型。它负责把所有上下文来源合并成模型可消费的 messages：
    长期记忆、当前轮附件、联网/工具结果、知识库片段、滚动摘要、最近历史、上下文预算治理。
    """

    def __init__(
        self,
        *,
        db: object,
        user_id: str,
        conversation_repo: ConversationRepository,
        message_service: MessageService,
        tool_trace_repo: ToolTraceRepository,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_repo = conversation_repo
        self.message_service = message_service
        self.tool_trace_repo = tool_trace_repo

    async def build_memory_context(
        self,
        settings: object,
        *,
        query: str | None = None,
        project_id: str | None = None,
        prior_user_text: str = "",
    ) -> MemoryRetrievalResult:
        started = perf_counter()
        decision = decide_memory_read(query or "", enabled=getattr(settings, "memory_enabled", True),
                                      prior_user_text=prior_user_text)
        if decision.mode in {"disabled", "skipped"}:
            return MemoryRetrievalResult(diagnostics={"mode": decision.mode, "read_decision": decision.mode,
                "read_reason": decision.reason, "read_rows": 0, "embedding_calls": 0, "elapsed_ms": 0})

        max_chars = int(getattr(settings, "memory_max_chars", 4000) or 4000)
        # 快照阶段已结束主事务。记忆召回自行管理短会话。
        retrieval = MemoryRetrievalService(session_factory=sessionmaker(bind=self.db.get_bind(), autoflush=False))
        result = await retrieval.retrieve(user_id=self.user_id, max_chars=max_chars, query=query or "",
                                          project_id=project_id, decision=decision)
        result.diagnostics["elapsed_ms"] = round((perf_counter() - started) * 1000, 2)
        return result

    async def build_execution_context(
        self,
        *,
        conversation: object,
        history_rows: list[object],
        user_message: object,
        assistant_message: object,
        runtime: ChatRuntimeConfig,
        thinking_enabled: bool,
        thinking_budget: int | None,
        web_search_enabled: bool,
        knowledge_base_id: str | None = None,
        knowledge_base_ids: list[str] | None = None,
        skill_context: SkillExecutionContext | None = None,
        tool_run_mode: str = "quick_chat",
        generation_id: str | None = None,
        history_mode: str = "complete",
    ) -> ChatExecutionContext:
        # 独立图节点调用原业务步骤，不再在一个函数里顺序编排所有上下文来源。
        from app.services.agent_graph.context_graph import run_context_graph

        assembly = ChatAssemblyRuntime(
            conversation=conversation, history_rows=history_rows,
            user_message=user_message, assistant_message=assistant_message,
            runtime=runtime, thinking_enabled=thinking_enabled,
            thinking_budget=thinking_budget, web_search_enabled=web_search_enabled,
            knowledge_base_id=knowledge_base_id, knowledge_base_ids=knowledge_base_ids,
            skill_context=skill_context, tool_run_mode=tool_run_mode,
            assistant_generation_id=generation_id, history_mode=history_mode,
        )
        return await run_context_graph(self, assembly)

    def snapshot(self, assembly: ChatAssemblyRuntime) -> None:
        """固定原文、历史边界和生成版本，防止后续提交刷新ORM属性。"""
        # 这是 Chat prepare 阶段的核心方法：收集所有上下文来源，构造最终 prompt，并返回给流式执行层。
        assembly.query = getattr(assembly.user_message, "content", "") or ""
        assembly.conversation_id = assembly.conversation.id
        assembly.user_message_id = assembly.user_message.id
        assembly.project_id = getattr(assembly.conversation, "project_id", None)
        assembly.system_prompt = clean_optional_str(assembly.conversation.system_prompt) or clean_optional_str(
            getattr(assembly.runtime.settings, "system_prompt", None))
        assembly.memory_read_settings = SimpleNamespace(
            memory_enabled=getattr(assembly.runtime.settings, "memory_enabled", True),
            memory_max_chars=getattr(assembly.runtime.settings, "memory_max_chars", 4000),
        )
        self._preflight_context(assembly)
        # 选择只保存标识；本轮和重生成均重新读取，不能用上一轮已撤销授权的正文。
        assembly.resource_selections = json.loads(getattr(assembly.user_message, "mcp_resources_json", None) or "[]")
        # Prepare 过程中会有外部调用和多次 commit；必须固定本次 generation 的 token，
        # 避免 ORM 刷新后读到后来 generation 的新 token，削弱流式收口的 CAS 保护。
        if assembly.assistant_generation_id is None:
            assembly.assistant_generation_id = getattr(assembly.assistant_message, "generation_id", None)
        assembly.assistant_id = assembly.assistant_message.id
        assembly.bounded_history = isinstance(assembly.history_rows, ContextHistory)
        assembly.history_through_message_id = getattr(assembly.history_rows, "through_message_id", None)
        # 重生成入口也可能传入当前空占位；它不属于可摘要、可发送的历史。
        assembly.history_rows = [row for row in assembly.history_rows if row.id != assembly.assistant_id]
        # 必须在任何 await/commit 之前固定原文，ORM 对象可能在后续提交后被刷新。
        assembly.history_snapshot = [
            (row.id, row.role, row.content, row.generation_id) for row in assembly.history_rows
        ]
        # 后续检索和 Prompt 也只读值副本，避免等待期间被删除的 ORM 对象触发懒加载异常。
        assembly.history_rows = [
            SimpleNamespace(
                id=row.id, role=row.role, content=row.content, generation_id=row.generation_id,
                status=row.status,
                attachments=[SimpleNamespace(
                    kind=attachment.kind, storage_path=attachment.storage_path, mime_type=attachment.mime_type,
                ) for attachment in row.attachments],
            )
            for row in assembly.history_rows
        ]

        # 提交后并发检索只读快照和各自会话。
        # 文件片段准备也在原始附件仍已加载时完成。
        assembly.attachment_context_result = AttachmentContextService().build_context(
            attachments=list(getattr(assembly.user_message, "attachments", []) or []), query=assembly.query,
            max_chars=assembly.runtime.budget.max_attachment_chars)
        self.db.commit()

    @staticmethod
    def _preflight_context(assembly: ChatAssemblyRuntime) -> None:
        current = ContextPromptBuilder().build_chat_messages(
            messages=[assembly.user_message], system_prompt=assembly.system_prompt,
            memory_context=None, context_summary=None, summary_boundary_message_id=None, external_context=None,
            attachment_context=None, provider_type=assembly.runtime.provider_type,
            skill_instructions=assembly.skill_context.final_answer_instructions if assembly.skill_context else None)
        assembly.runtime.governance_service.require_fit(current.messages)
        assembly.current_user_payload = current.messages[-1]

    async def summary_preflight(self, assembly: ChatAssemblyRuntime) -> None:
        if assembly.history_mode == "current_turn_only":
            ChatSummaryCoverage(self.db).prepare_current_turn(assembly)
            assembly.summary_bundle = SummaryRefreshBundle(None, None, {"history_coverage": "current_turn_only"})
        else:
            await ChatSummaryCoverage(self.db).prepare(assembly)

    async def retrieve_memory(self, assembly: ChatAssemblyRuntime) -> None:
        """使用独立短 Session 召回长期记忆。"""
        user_message_id = assembly.user_message_id
        if assembly.history_mode == "current_turn_only":
            assembly.memory_bundle = MemoryRetrievalResult(diagnostics={"mode": "skipped"})
            return
        prior_user_text = next((content for message_id, role, content, _ in reversed(assembly.history_snapshot)
                                if role == "user" and message_id != user_message_id and content), "")
        assembly.memory_bundle = await self.build_memory_context(
            assembly.memory_read_settings,
            query=assembly.query,
            project_id=assembly.project_id,
            prior_user_text=prior_user_text,
        )

    async def execute_tools(self, assembly: ChatAssemblyRuntime) -> None:
        """调用已有工具图和MCP资料接口，汇总已校验的工具证据。"""
        # 外部上下文包括联网搜索、地图、天气等工具。只有 web_search_enabled=True 时才会真正规划/调用工具。
        assembly.resource_sources, assembly.resource_notices = [], []
        assembly.resource_service = None
        if assembly.history_mode == "current_turn_only":
            assembly.external_context_result = ExternalContextResult(context_text=None, sources=[], notices=[], diagnostics={}, details={})
            assembly.evidence_sufficiency = assess_evidence_sufficiency(query=assembly.query, sources=[])
            return
        if assembly.resource_selections:
            resource_project_id = getattr(assembly.conversation, "project_id", None)
            self.db.commit()
            assembly.resource_service = McpResourceService(user_id=self.user_id, project_id=resource_project_id)
            assembly.resource_sources, assembly.resource_notices = await assembly.resource_service.build_sources(assembly.resource_selections)
        assembly.external_context_result = await self._build_external_context(
            conversation=assembly.conversation,
            assistant_message=assembly.assistant_message,
            user_message=assembly.user_message,
            query=assembly.query,
            history_rows=assembly.history_rows,
            web_search_enabled=assembly.web_search_enabled,
            max_attachment_chars=assembly.runtime.budget.max_attachment_chars,
            runtime=assembly.runtime,
            skill_context=assembly.skill_context,
            tool_run_mode=assembly.tool_run_mode,
        )
        if assembly.resource_selections:
            all_sources = [*assembly.external_context_result.sources, *assembly.resource_sources]
            status_text = str(assembly.external_context_result.details.get("execution_status_text") or "")
            if assembly.resource_notices:
                status_text += "\n所选MCP资料未成功读取。不要声称已查阅这些资料，相关问题应说明资料不足。"
            section = ExternalContextAssembler.build_section(all_sources,
                max_chars=max(1200, min(assembly.runtime.budget.max_attachment_chars, 6000)), execution_status=status_text)
            assembly.external_context_result = replace(assembly.external_context_result, sources=all_sources,
                evidence_section=section, context_text=section.render() or None,
                notices=[*assembly.external_context_result.notices, *assembly.resource_notices])
        # 证据充分性是代码侧的最终回答约束。它仅依据已通过执行边界的 Tool
        # 来源类型和状态生成，不读取自由文本，也不授予模型额外权限。
        assembly.evidence_sufficiency = assess_evidence_sufficiency(
            query=assembly.query,
            sources=assembly.external_context_result.sources,
            skill_key=assembly.skill_context.skill_key if assembly.skill_context else None,
            completion_contract=assembly.skill_context.completion_contract if assembly.skill_context else None,
        )

    async def retrieve_knowledge(self, assembly: ChatAssemblyRuntime) -> None:
        """检索知识库并将原始召回日志绑定到本轮消息。"""
        if assembly.history_mode == "current_turn_only":
            assembly.knowledge_context_result = KnowledgeContextResult(context_text=None)
            return
        sessions = sessionmaker(bind=self.db.get_bind(), autoflush=False)
        with sessions() as db:
            assembly.knowledge_context_result = await KnowledgeContextService(db=db, user_id=self.user_id).build_context(
                knowledge_base_id=assembly.knowledge_base_id,
                knowledge_base_ids=assembly.knowledge_base_ids,
                query=assembly.query,
                rewrite_provider={
                    "provider_type": assembly.runtime.provider_type,
                    "base_url": assembly.runtime.base_url,
                    "api_key": assembly.runtime.provider_api_key,
                    "model_name": assembly.runtime.resolved_model,
                },
                recent_messages=[row for row in assembly.history_rows if row.id != assembly.user_message_id],
            )
            self._link_retrieval_logs(db, assembly)

    def _link_retrieval_logs(self, db, assembly):
        assembly.retrieval_log_ids = assembly.knowledge_context_result.retrieval_log_ids or (
            [assembly.knowledge_context_result.retrieval_log_id] if assembly.knowledge_context_result.retrieval_log_id else []
        )
        if assembly.retrieval_log_ids:
            # RAG 来源定位需要把检索日志绑定到本轮消息，否则前端只能看到片段，不能跳回对应回答。
            knowledge_log_repo = KnowledgeRetrievalLogRepository(db)
            sources_by_log_id: dict[str, list[dict[str, Any]]] = {}
            for source in assembly.knowledge_context_result.sources:
                source_dict = source.to_public_dict()
                log_id = str((source.metadata or {}).get("retrieval_log_id") or "")
                if log_id:
                    sources_by_log_id.setdefault(log_id, []).append(source_dict)
            for log_id in assembly.retrieval_log_ids:
                knowledge_log_repo.update_message_links(
                    log_id=log_id,
                    user_id=self.user_id,
                    conversation_id=assembly.conversation_id,
                    user_message_id=assembly.user_message_id,
                    assistant_message_id=assembly.assistant_id,
                    sources=sources_by_log_id.get(log_id, []),
                )

    async def refresh_summary(self, assembly: ChatAssemblyRuntime) -> None:
        """刷新摘要并复核历史版本及MCP资料授权。"""
        if assembly.resource_service:
            self.db.commit()
            await assembly.resource_service.revalidate()

    async def govern_prompt(self, assembly: ChatAssemblyRuntime) -> None:
        """构建分层Prompt，并按完整证据单元执行预算治理。"""
        if assembly.coverage_through is not None:
            ChatSummaryCoverage(self.db).verify(assembly)
        # PromptBuilder携带结构化证据；Governance按保留单元渲染最终输入。
        completion = assembly.external_context_result.details.get("tool_task_completion", {})
        evidence_guidance = assembly.evidence_sufficiency.guidance
        # 活动Skill的完整合同优先；没有适用合同时仍保留原问题的读取要求。
        spec = build_task_spec(query=getattr(assembly, "query", ""), skill_context=assembly.skill_context) or build_task_spec(
            query=getattr(assembly, "query", ""))
        if spec:
            # 预先计入保守约束，治理后只作等预算内替换，不追加超预算消息或反复裁剪。
            evidence_guidance = FINAL_COMPLETION_GUIDANCE
        elif completion.get("status") in {"partial", "clarify", "blocked"}:
            evidence_guidance += "\n工具证据检查未全部满足：仅回答已核实部分，说明缺口，不得声称任务全部完成。等待审批不等于文件已写入。"
        assembly.memory_bundle.recheck_for_prompt(self.db, user_id=self.user_id,
            project_id=assembly.conversation.project_id)
        assembly.prompt_result = ContextPromptBuilder().build_chat_messages(
            messages=assembly.history_rows,
            system_prompt=assembly.system_prompt,
            memory_context=assembly.memory_bundle.context_text,
            context_summary=assembly.summary_bundle.summary,
            summary_boundary_message_id=assembly.summary_bundle.boundary_message_id,
            external_context=assembly.external_context_result.context_text,
            attachment_context=assembly.attachment_context_result.context_text,
            knowledge_context=assembly.knowledge_context_result.context_text,
            provider_type=assembly.runtime.provider_type,
            model_name=assembly.runtime.resolved_model,
            skill_instructions=(
                assembly.skill_context.final_answer_instructions if assembly.skill_context else None
            ),
            evidence_guidance=evidence_guidance,
            current_user_payload=assembly.current_user_payload,
            evidence_sections={section.layer: section for section in (
                assembly.external_context_result.evidence_section, assembly.knowledge_context_result.evidence_section) if section is not None},
        )
        preferred = self._preferred_reference_layers(assembly)
        assembly.governed_context = assembly.runtime.governance_service.govern_messages(
            assembly.prompt_result.messages, preferred_reference_layers=preferred,
            preserve_history=assembly.coverage_state is not None)
        assembly.prompt_result.diagnostics["prompt_preferred_reference_layers"] = ",".join(preferred)
        if spec:
            self._recheck_final_completion(assembly, spec)
        retained_memory = assembly.governed_context.retained_reference_texts.get("long_term_memory", "")
        assembly.memory_diagnostics = assembly.memory_bundle.after_governance(retained_memory)
        assembly.memory_diagnostics["injected_tokens_estimate"] = assembly.runtime.tokenizer.estimate_text_tokens(retained_memory)
        assembly.prompt_diagnostics = self._build_prompt_diagnostics(
            conversation=assembly.conversation,
            prompt_result=assembly.prompt_result,
            governed_messages=assembly.governed_context.messages,
            runtime=assembly.runtime,
        )

        assembly.conversation.last_prompt_prefix_hash = assembly.prompt_diagnostics.prompt_prefix_hash or None
        assembly.conversation.last_prompt_prefix_token_count = assembly.prompt_diagnostics.prompt_prefix_tokens or None
        # prefix hash 只是观测/缓存命中诊断字段，不影响业务权限。
        self.conversation_repo.save(assembly.conversation)
        # ConversationRepository 只 flush；摘要边界与 prefix 诊断在 Context Assembly 用例末尾统一提交。
        self.db.commit()

    @staticmethod
    def _preferred_reference_layers(assembly: ChatAssemblyRuntime) -> tuple[str, ...]:
        # 依据本轮选择和已有召回判断保护资料，无额外模型分类。
        layers = []
        memory_reason = assembly.memory_bundle.diagnostics.get("read_reason")
        if memory_reason == "user_requested_preferences":
            layers.append("long_term_memory")
        if assembly.skill_context or assembly.resource_selections:
            layers.append("external_context")
        if assembly.attachment_context_result.context_text:
            layers.append("attachment_context")
        if assembly.knowledge_base_id or assembly.knowledge_base_ids:
            layers.append("knowledge_context")
        if assembly.web_search_enabled and "external_context" not in layers:
            layers.append("external_context")
        if memory_reason == "contextual_follow_up" or assembly.summary_bundle.summary:
            layers.append("conversation_summary")
        return tuple(layers)

    def _recheck_final_completion(self, assembly, spec) -> None:
        """最终Prompt与判定共用同一来源清单；不重新调用模型或触发工具。"""
        external = assembly.external_context_result
        prior = external.details.get("tool_task_completion", {})
        verdict = verify_final_completion(spec=spec, final_source_map=assembly.governed_context.final_source_map,
            terminal_reason=external.diagnostics.get("external_agent_terminal_reason", "no_tool_needed"),
            next_action=external.diagnostics.get("external_tool_next_action", "stop"))
        payload = verdict.to_public_dict()
        assembly.evidence_sufficiency = assess_evidence_sufficiency(query=spec.goal,
            sources=sources_from_final_map(assembly.governed_context.final_source_map),
            skill_key=assembly.skill_context.skill_key if assembly.skill_context else None,
            completion_contract=assembly.skill_context.completion_contract if assembly.skill_context else None)
        external.details.setdefault("tool_task_completion_before_governance", prior)
        external.details["tool_task_completion"] = payload
        external.diagnostics["external_tool_task_completion"] = payload
        external.tool_events = [e for e in external.tool_events if e.type != "tool_task_completion"]
        external.tool_events.append(ToolTraceEvent(type="tool_task_completion", payload=payload))
        external.details["tool_events"] = [e.to_public_dict() for e in external.tool_events]
        status_text = ("最终必要工具证据完整保留；仅根据所给正文回答，不宣称已验证答案语义或整份原文。"
                       if verdict.status == "complete" else
                       "最终必要工具证据未齐全或尚待确认；仅回答已核实部分，明确缺口，不得声称任务全部完成。节选不等于全文，等待审批不等于文件已写入。")
        governed = assembly.governed_context
        candidate = [{**m, "content": m["content"].replace(FINAL_COMPLETION_GUIDANCE, status_text, 1)}
                     if m.get("role") == "system" and isinstance(m.get("content"), str) else m
                     for m in governed.messages]
        governance = assembly.runtime.governance_service
        # 替换不能挤占已分配证据预算；自定义Tokenizer下不满足则保留原保守约束。
        if (governance._estimate_messages_chars(candidate) <= governance._estimate_messages_chars(governed.messages)
                and governance._estimate_messages_tokens(candidate) <= governance._estimate_messages_tokens(governed.messages)):
            governed.messages = candidate
            governed.stats.total_chars_estimate = governance._estimate_messages_chars(candidate)
            governed.stats.total_tokens_estimate = governance._estimate_messages_tokens(candidate)
            governed.stats.system_chars = sum(governance._estimate_message_chars(m) for m in candidate if m.get("role") == "system")
        self.tool_trace_repo.update_final_completion(user_id=self.user_id,
            conversation_id=assembly.conversation.id, assistant_message_id=assembly.assistant_id,
            generation_id=assembly.assistant_generation_id, verdict=payload)

    def finalize_context(self, assembly: ChatAssemblyRuntime) -> ChatExecutionContext:
        """从同一份治理结果生成可调用上下文和最终来源映射。"""
        summary_text = assembly.summary_bundle.summary or ""
        summary_tokens = assembly.runtime.tokenizer.estimate_text_tokens(summary_text)
        summary_source_tokens = int(assembly.summary_bundle.stats.get("summary_refresh_source_tokens", 0) or 0)
        summary_compression_ratio = (
            round(summary_tokens / summary_source_tokens, 4) if summary_source_tokens > 0 else 0
        )
        attachment_context_tokens = assembly.runtime.tokenizer.estimate_text_tokens(assembly.attachment_context_result.context_text or "")
        knowledge_context_tokens = assembly.runtime.tokenizer.estimate_text_tokens(assembly.knowledge_context_result.context_text or "")
        original_sources = [*assembly.external_context_result.sources, *assembly.knowledge_context_result.sources]
        final_source_map = assembly.governed_context.final_source_map
        provider_capabilities = resolve_provider_capabilities(
            provider_type=assembly.runtime.provider_type,
            base_url=assembly.runtime.base_url,
        )
        combined_public_sources = list(final_source_map.values())
        verified_file_edit_answer = build_file_edit_response(query=assembly.query, mode=assembly.tool_run_mode,
            sources=assembly.external_context_result.sources, final_source_map=final_source_map,
            events=assembly.external_context_result.tool_events)
        # 候选/选择记录不变；展示来源只取最终证据，另存排除原因而非伪装成可引用来源。
        if assembly.retrieval_log_ids:
            log_repo = KnowledgeRetrievalLogRepository(self.db)
            for log_id in assembly.retrieval_log_ids:
                log = log_repo.update_message_links(log_id=log_id, user_id=self.user_id,
                    conversation_id=getattr(assembly.conversation, "id", None), user_message_id=getattr(assembly.user_message, "id", None),
                    assistant_message_id=getattr(assembly.assistant_message, "id", None),
                    sources=[source for source in combined_public_sources
                        if (source.get("metadata") or {}).get("retrieval_log_id") == log_id])
                if log:
                    diagnostics = json.loads(log.diagnostics_json or "{}")
                    diagnostics["knowledge_sources_in_final_prompt"] = sum(
                        source["metadata"].get("retrieval_log_id") == log_id for source in combined_public_sources)
                    diagnostics["excluded_source_labels"] = [source.citation_label for source in original_sources
                        if source.metadata.get("retrieval_log_id") == log_id and source.citation_label not in final_source_map]
                    log.diagnostics_json = json.dumps(diagnostics, ensure_ascii=False)
                    log_repo.save(log)
            # 流式层会关闭请求Session，最终来源/排除原因必须在交接前提交，
            # 否则日志会退回预算治理前的来源集合。
            self.db.commit()
        context_details = {
            "citation_smalltalk": is_smalltalk_query(assembly.query),
            "memory_retrieval": assembly.memory_diagnostics,
            "attachment_chunks": assembly.attachment_context_result.details.get("attachment_chunks", []),
            "external_sources": combined_public_sources,
            "final_source_map": final_source_map,
            "known_source_labels": [source.citation_label for source in original_sources if source.citation_label],
            "knowledge_sources": [source for source in combined_public_sources if source["source_type"] == "knowledge"],
            "knowledge_query_rewrite": assembly.knowledge_context_result.details.get("knowledge_query_rewrite"),
            "tool_plan": assembly.external_context_result.details.get("tool_plan"),
            "tool_events": assembly.external_context_result.details.get("tool_events", []),
            "tool_task_completion": assembly.external_context_result.details.get("tool_task_completion"),
            "tool_task_completion_before_governance": assembly.external_context_result.details.get("tool_task_completion_before_governance"),
            "active_skill": assembly.external_context_result.details.get("active_skill"),
            "tool_run_policy": assembly.external_context_result.details.get("tool_run_policy"),
            "tool_run_budget": assembly.external_context_result.details.get("tool_run_budget"),
            "evidence_sufficiency": assembly.evidence_sufficiency.to_public_dict(),
        }

        return ChatExecutionContext(
            conversation_repo=self.conversation_repo,
            message_service=self.message_service,
            conversation=assembly.conversation,
            user_message=assembly.user_message,
            assistant_message=assembly.assistant_message,
            history_messages=assembly.governed_context.messages,
            resolved_model=assembly.runtime.resolved_model,
            provider_type=assembly.runtime.provider_type,
            base_url=assembly.runtime.base_url,
            api_key=assembly.runtime.provider_api_key,
            temperature=assembly.runtime.settings.temperature,
            top_p=assembly.runtime.settings.top_p,
            # Provider 输出上限必须与预算预留一致，否则输入合规后仍可能在生成阶段挤爆窗口。
            max_tokens=assembly.runtime.budget.reserved_output_tokens,
            # cache key 只用于 Provider 路由/缓存分片，不暴露原始 user/conversation id。
            prompt_cache_key=hashlib.sha256(
                f"{self.user_id}:{assembly.conversation.id}:{ContextPromptBuilder.TEMPLATE_VERSION}".encode("utf-8")
            ).hexdigest()[:32],
            # Govern 后最后一条是包含当前 query/evidence 的 user message；之前的连续消息
            # 是 Anthropic 可设置 cache_control 的稳定断点。
            prompt_cache_breakpoint=max(
                0,
                len(assembly.governed_context.messages)
                - int(bool(assembly.governed_context.messages and assembly.governed_context.messages[-1].get("role") == "user")),
            ),
            context_notices=[
                *assembly.external_context_result.notices,
                *assembly.knowledge_context_result.notices,
                *assembly.governed_context.notices,
            ],
            context_stats={
                "context_mode": assembly.runtime.budget.context_mode,
                "model_context_window": assembly.runtime.budget.model_context_window,
                "budget_reserved_output_tokens": assembly.runtime.budget.reserved_output_tokens,
                "budget_max_total_chars": assembly.runtime.budget.max_total_chars,
                "budget_max_total_tokens": assembly.runtime.budget.max_total_tokens,
                "budget_max_attachment_chars": assembly.runtime.budget.max_attachment_chars,
                "budget_max_attachment_tokens": assembly.runtime.budget.max_attachment_tokens,
                "total_chars_estimate": assembly.governed_context.stats.total_chars_estimate,
                "total_tokens_estimate": assembly.governed_context.stats.total_tokens_estimate,
                "truncated_history_messages": assembly.governed_context.stats.truncated_history_messages,
                # 裁剪计数针对本轮已加载窗口，不冒充整段会话的总消息统计。
                "history_rows_loaded": len(assembly.history_rows),
                "history_read_bounded": int(assembly.bounded_history),
                "history_recent_read_limit": MessageRepository.RECENT_CONTEXT_LIMIT if assembly.bounded_history else 0,
                "history_summary_batch_limit": MessageRepository.SUMMARY_BATCH_LIMIT if assembly.bounded_history else 0,
                "dropped_reference_layers": assembly.governed_context.stats.dropped_reference_layers,
                "truncated_reference_layers": assembly.governed_context.stats.truncated_reference_layers,
                "retained_reference_tokens": assembly.governed_context.stats.retained_reference_tokens,
                "summary_chars": len(
                    assembly.summary_bundle.summary or ""
                ),
                "summary_tokens": summary_tokens,
                "summary_triggered": int(assembly.governed_context.summary_triggered),
                "summary_refresh_triggered": assembly.summary_bundle.stats.get("summary_refresh_triggered", 0),
                "summary_refresh_source_messages": assembly.summary_bundle.stats.get("summary_refresh_source_messages", 0),
                "summary_refresh_source_chars": assembly.summary_bundle.stats.get("summary_refresh_source_chars", 0),
                "summary_refresh_model_used": assembly.summary_bundle.stats.get("summary_refresh_model_used", 0),
                "summary_remaining_backlog": assembly.summary_bundle.stats.get("summary_remaining_backlog", 0),
                "summary_mode": assembly.summary_bundle.stats.get("summary_mode", "unknown"),
                "summary_repair_status": assembly.summary_bundle.stats.get("summary_repair_status", "ready"),
                "history_coverage": assembly.summary_bundle.stats.get("history_coverage", "unknown"),
                "summary_preflight_ms": assembly.summary_bundle.stats.get("summary_preflight_ms", 0),
                "summary_refresh_ms": assembly.summary_bundle.stats.get("summary_refresh_ms", 0),
                "summary_refresh_input_tokens": assembly.summary_bundle.stats.get("summary_refresh_input_tokens", 0),
                "summary_refresh_source_tokens": summary_source_tokens,
                "summary_compression_ratio": summary_compression_ratio,
                "attachment_context_tokens": attachment_context_tokens,
                "knowledge_context_tokens": knowledge_context_tokens,
                "memory_enabled": int(bool(getattr(assembly.runtime.settings, "memory_enabled", True))),
                "memory_injected": int(bool(assembly.memory_diagnostics["injected_count"])),
                "memory_count": assembly.memory_diagnostics["injected_count"],
                "memory_chars": assembly.memory_diagnostics["injected_chars"],
                "memory_selected_count": assembly.memory_bundle.count,
                "memory_retrieval_mode": assembly.memory_diagnostics.get("mode", "lexical"),
                "memory_fallback_reason": assembly.memory_diagnostics.get("fallback_reason") or "none",
                "memory_retrieval": assembly.memory_diagnostics,
                "thinking_enabled": int(bool(assembly.thinking_enabled)),
                "skill_active": int(bool(assembly.skill_context)),
                "skill_key": assembly.skill_context.skill_key if assembly.skill_context else "none",
                "skill_version": assembly.skill_context.version if assembly.skill_context else "none",
                "evidence_sufficiency_status": assembly.evidence_sufficiency.status,
                "evidence_sufficiency_reasons": ",".join(assembly.evidence_sufficiency.reasons),
                "tokenizer_encoding": assembly.runtime.tokenizer.estimate.encoding_name,
                "prompt_prefix_hash": assembly.prompt_diagnostics.prompt_prefix_hash,
                "prompt_prefix_tokens": assembly.prompt_diagnostics.prompt_prefix_tokens,
                "prompt_total_tokens": assembly.prompt_diagnostics.prompt_total_tokens,
                "prompt_recent_history_tokens": assembly.prompt_diagnostics.prompt_recent_history_tokens,
                "prompt_prefix_reused_last_turn": assembly.prompt_diagnostics.prompt_prefix_reused_last_turn,
                "prompt_cache_provider_enabled": int(provider_capabilities.prompt_cache_mode != "none"),
                "prompt_cache_mode": provider_capabilities.prompt_cache_mode,
                "prompt_cache_provider_family": provider_capabilities.family,
                "prompt_cache_usage_support": provider_capabilities.cache_usage_support,
                **assembly.attachment_context_result.diagnostics,
                **assembly.external_context_result.diagnostics,
                **assembly.knowledge_context_result.diagnostics,
                **assembly.prompt_result.diagnostics,
                # 原阶段计数保留；另列最终预算之后实际可引用的证据数。
                "citation_sources_in_final_prompt": len(final_source_map),
                "citation_sources_excluded": sum(source.citation_label not in final_source_map for source in original_sources),
            },
            context_details=context_details,
            verified_file_edit_answer=verified_file_edit_answer,
            context_summary=assembly.summary_bundle.summary,
            thinking_enabled=assembly.thinking_enabled,
            thinking_budget=assembly.thinking_budget,
            tool_events=[event.to_public_dict() for event in assembly.external_context_result.tool_events],
            external_sources=combined_public_sources,
            generation_id=assembly.assistant_generation_id,
        )

    async def _build_external_context(
        self,
        *,
        conversation: object,
        assistant_message: object,
        user_message: object,
        query: str,
        history_rows: list[object],
        web_search_enabled: bool,
        max_attachment_chars: int,
        runtime: ChatRuntimeConfig,
        skill_context: SkillExecutionContext | None = None,
        tool_run_mode: str = "quick_chat",
    ) -> object:
        # 工具层的输入不只是当前 query，还包含 recent_messages 和 planner_runtime。
        # 这让 LLM planner 可以根据上下文判断是否需要多工具调用，而不是纯正则匹配。
        # 真实服务支持运行模式；测试替身和第三方扩展可能仍是旧构造协议，保留兼容入口。
        try:
            external_service = ExternalContextService(
                db=self.db,
                user_id=self.user_id,
                project_id=getattr(conversation, "project_id", None),
                run_policy=resolve_tool_run_policy(tool_run_mode),
            )
        except TypeError as exc:
            if "run_policy" not in str(exc):
                raise
            external_service = ExternalContextService(
                db=self.db,
                user_id=self.user_id,
                project_id=getattr(conversation, "project_id", None),
            )
        # 将聊天归属附加到真实 Executor；测试替身和自定义 ExternalContextService
        # 可以不实现该内部属性，保持旧扩展接口兼容。
        executor = getattr(external_service, "executor", None)
        if executor is not None:
            executor.conversation_id = getattr(conversation, "id", None)
            executor.assistant_message_id = getattr(assistant_message, "id", None)
        external_context_kwargs = {
            "query": query,
            "enabled": bool(web_search_enabled or skill_context),
            "max_chars": max(1200, min(max_attachment_chars, 6000)),
            "recent_messages": list(history_rows),
            "planner_runtime": PlannerRuntime(
                provider_type=runtime.provider_type,
                base_url=runtime.base_url,
                api_key=runtime.provider_api_key,
                model_name=runtime.resolved_model,
            ),
        }
        if skill_context:
            external_context_kwargs["skill_context"] = skill_context
        external_context_result = await external_service.build_context(
            **external_context_kwargs,
        )
        self.tool_trace_repo.replace_for_assistant_message(
            user_id=self.user_id,
            conversation_id=conversation.id,
            user_message_id=getattr(user_message, "id", None),
            assistant_message_id=getattr(assistant_message, "id"),
            query=query,
            external_context=external_context_result,
        )
        return external_context_result

    @staticmethod
    def _build_prompt_diagnostics(
        *,
        conversation: object,
        prompt_result: object,
        governed_messages: list[dict[str, Any]],
        runtime: ChatRuntimeConfig,
    ) -> PromptDiagnosticsBundle:
        # stable_prefix_messages 只包含系统层上下文，不包含最近用户历史。
        # hash 相同表示 prefix 可能复用，有利于观察 prompt cache 命中潜力。
        stable_prefix_text = json.dumps(
            prompt_result.stable_prefix_messages,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        prompt_prefix_hash = (
            hashlib.sha256(stable_prefix_text.encode("utf-8")).hexdigest()[:16]
            if stable_prefix_text
            else ""
        )
        prompt_prefix_tokens = runtime.tokenizer.estimate_messages_tokens(
            prompt_result.stable_prefix_messages,
            image_equiv_tokens=runtime.budget.max_image_equiv_tokens,
        )
        prompt_total_tokens = runtime.tokenizer.estimate_messages_tokens(
            governed_messages,
            image_equiv_tokens=runtime.budget.max_image_equiv_tokens,
        )
        prompt_recent_history_tokens = max(0, prompt_total_tokens - prompt_prefix_tokens)
        previous_prompt_prefix_hash = clean_optional_str(getattr(conversation, "last_prompt_prefix_hash", None))
        prompt_prefix_reused_last_turn = int(
            bool(prompt_prefix_hash and prompt_prefix_hash == previous_prompt_prefix_hash)
        )
        return PromptDiagnosticsBundle(
            prompt_prefix_hash=prompt_prefix_hash,
            prompt_prefix_tokens=prompt_prefix_tokens,
            prompt_total_tokens=prompt_total_tokens,
            prompt_recent_history_tokens=prompt_recent_history_tokens,
            prompt_prefix_reused_last_turn=prompt_prefix_reused_last_turn,
        )

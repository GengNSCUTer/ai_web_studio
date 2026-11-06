from __future__ import annotations

from copy import deepcopy
import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.services.skill_catalog import SkillExecutionContext
from app.services.tools.credentials import ToolCredentialResolver
from app.services.tools.catalog import ToolCatalog
from app.services.tools.executor import ToolExecutor
from app.services.tools.formatter import ExternalContextAssembler
from app.services.tools.planner import DeterministicToolPlanner, LLMToolPlanner, PlannerRuntime
from app.services.tools.query_rewriter import QueryRewriteService
from app.services.tools.run_policy import ToolRunBudget, ToolRunPolicy, resolve_tool_run_policy
from app.services.tools.schemas import (
    ExternalContextResult,
    ToolTraceEvent,
)
from app.services.tools.workflow import ToolWorkflowService
from app.services.tools.observation_projection import PlannerObservationProjection
from app.services.tools.selector import ToolCandidateSelector
from app.services.tools.semantic_retrieval import ToolSemanticRetriever


class ExternalContextService:
    """Chat 侧外部上下文门面。

    Chat 只依赖这一层；Catalog、Planner、Workflow、Executor 和结果组装都收在 tools 包内。
    每轮先规划再执行，并把结果作为 observations 交给下一轮；实际轮数和调用
    预算由代码侧 ToolRunPolicy 限定，避免开放式 Agent 无限循环。
    """

    def __init__(
        self,
        *,
        db: Session | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        conversation_id: str | None = None,
        assistant_message_id: str | None = None,
        registry: ToolCatalog | None = None,
        router: object | None = None,
        executor: ToolExecutor | None = None,
        assembler: ExternalContextAssembler | None = None,
        query_rewriter: QueryRewriteService | None = None,
        planner: LLMToolPlanner | None = None,
        planner_runtime: PlannerRuntime | None = None,
        workflow: ToolWorkflowService | None = None,
        run_policy: ToolRunPolicy | None = None,
    ) -> None:
        self.db = db
        self.registry = registry or ToolCatalog(db=db, user_id=user_id, project_id=project_id)
        deterministic = router or DeterministicToolPlanner(self.registry)
        self.planner = planner or LLMToolPlanner(
            catalog=self.registry,
            fallback_planner=deterministic,
            candidate_selector=ToolCandidateSelector(self.registry,
                semantic_retriever=ToolSemanticRetriever(user_id=user_id) if user_id else None),
        )
        self.planner_runtime = planner_runtime
        self.executor = executor or ToolExecutor(
            credential_resolver=ToolCredentialResolver(db),
            catalog=self.registry,
            db=db,
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
        )
        self.assembler = assembler or ExternalContextAssembler()
        self.query_rewriter = query_rewriter or QueryRewriteService()
        self.workflow = workflow or ToolWorkflowService(executor=self.executor, registry=self.registry)
        self.run_policy = run_policy or resolve_tool_run_policy()

    @staticmethod
    def _scope_plan_call_ids(plan, round_index, used_call_ids=None):
        """模型call_id只在单计划内唯一；按轮次隔离，避免审计和来源被后轮覆盖。"""
        plan = deepcopy(plan)
        used_call_ids = used_call_ids if used_call_ids is not None else set()
        original_ids = [call.call_id for call in plan.calls]
        mapping = {}
        for index,original_id in enumerate(original_ids,1):
            if original_id in mapping:
                continue
            scoped_id = original_id
            if original_id in used_call_ids:
                scoped_id = f"round_{round_index}_{index}"
                while scoped_id in used_call_ids or scoped_id in original_ids or scoped_id in mapping.values():
                    scoped_id += "_"
            mapping[original_id] = scoped_id
        for call, original_id in zip(plan.calls,original_ids):
            call.call_id = mapping[original_id]
            call.depends_on = [mapping.get(dependency,dependency) for dependency in call.depends_on]
            for binding in call.result_bindings:
                binding.source_call_id = mapping.get(binding.source_call_id,binding.source_call_id)
        # 原始ID留在映射审计里，计划及执行事件统一使用运行级ID。
        def rewrite(value):
            if isinstance(value,list):return [rewrite(item) for item in value]
            if not isinstance(value,dict):return value
            return {key:(item if key in {"arguments","input_schema","output_schema"}
                         else mapping.get(item,item) if key in {"call_id","source_call_id"} and isinstance(item,str)
                         else [mapping.get(v,v) for v in item] if key=="depends_on" and isinstance(item,list)
                         else rewrite(item)) for key,item in value.items()}
        plan.trace_events = rewrite(plan.trace_events)
        used_call_ids.update(mapping.values())
        if any(k!=v for k,v in mapping.items()):
            plan.trace_events.append({"type":"tool_call_identity_scope","round":round_index,
                                      "original_to_execution":mapping})
        return plan

    async def build_context(
        self,
        *,
        query: str,
        enabled: bool,
        max_chars: int,
        recent_messages: list[object] | None = None,
        planner_runtime: PlannerRuntime | None = None,
        skill_context: SkillExecutionContext | None = None,
    ) -> ExternalContextResult:
        if not enabled:
            return ExternalContextResult(
                context_text=None,
                sources=[],
                notices=[],
                diagnostics={
                    "external_context_enabled": 0,
                    "external_tool_called": "none",
                    "external_sources_total": 0,
                    "external_sources_included": 0,
                    "external_sources_raw_total": 0,
                    "external_sources_duplicate_count": 0,
                    "external_sources_dedup_strategy": "normalized_url_or_canonical_content_hash",
                    "external_evidence_projection": {
                        "rounds": [],
                        "totals": PlannerObservationProjection.aggregate_diagnostics([]),
                    },
                    "external_context_chars": 0,
                    "external_context_latency_ms": 0,
                    "external_context_error": 0,
                    "external_tool_events_total": 0,
                    "external_tool_next_action": "stop",
                },
                details={
                    "external_sources": [],
                    "tool_plan": None,
                    "tool_events": [],
                    "tool_workflow_next_action": "stop",
                },
                tool_plan=None,
                tool_events=[],
            )

        from app.services.tools.loop_runtime import SyncToolRun
        from app.services.agent_graph.sync_tool_graph import run_sync_tool_graph

        # 后续commit会使ORM对象过期；规划只需要角色/正文，先复制为请求级值。
        history = [{"role": message.get("role"), "content": message.get("content")} if isinstance(message, dict)
                   else {"role": getattr(message, "role", None), "content": getattr(message, "content", None)}
                   for message in recent_messages or []]
        run = SyncToolRun(self, query=query, enabled=enabled, max_chars=max_chars,
            recent_messages=history, planner_runtime=planner_runtime, skill_context=skill_context,
            budget=ToolRunBudget(policy=self.run_policy))
        result = await run_sync_tool_graph(run)
        result.diagnostics["external_orchestrator"] = "langgraph"
        return result

    @staticmethod
    def _budget_terminal_action(*, reason: str, has_sources: bool):
        """预算不足时只能部分收口或停止，不能隐式扩轮。"""

        from app.services.tools.workflow import ToolWorkflowAction

        if has_sources:
            return ToolWorkflowAction(
                action="finalize_partial",
                reason=reason,
                notice="工具运行预算已用尽，回答仅使用已通过质量校验的结果。",
            )
        return ToolWorkflowAction(
            action="stop",
            reason=reason,
            notice="工具运行预算已用尽，未获得可用于回答的外部结果。",
        )

    @staticmethod
    def _budget_exhausted_notice(reason: str, *, has_sources: bool) -> str:
        if has_sources:
            return "工具运行预算已用尽，回答仅使用已通过质量校验的结果。"
        if reason == "tool_wall_clock_budget_exhausted":
            return "工具运行超过当前模式允许时长，未继续执行后续调用。"
        return "工具运行预算已用尽，未继续执行后续调用。"

    @staticmethod
    def _merge_sources(target: list, incoming: list) -> tuple[list, int]:
        """合并同一请求内重复的 evidence，并返回新增来源与重复数量。"""

        seen = {
            ExternalContextService._source_identity(source)
            for source in target
        }
        newly_added: list = []
        duplicate_count = 0
        for source in incoming:
            identity = ExternalContextService._source_identity(source)
            if identity in seen:
                duplicate_count += 1
                continue
            target.append(source)
            newly_added.append(source)
            seen.add(identity)
        return newly_added, duplicate_count

    @staticmethod
    def _source_identity(source: object) -> tuple[str, ...]:
        """使用规范化 URL 或 canonical 内容摘要去重，不记录原始正文。"""

        url = str(getattr(source, "url", None) or "").strip()
        if url:
            return ("url", ExternalContextService._normalize_source_url(url))

        metadata = getattr(source, "metadata", {})
        # 两个空文件页可能有完全相同的说明文字，却携带不同的继续查询位置。
        # 按页和文件身份去重，避免把新 cursor 当成重复正文丢掉，卡在前一页。
        source_type = str(getattr(source, "source_type", "") or "")
        if (isinstance(metadata, dict) and metadata.get("page_id")
                and getattr(source, "provider", None) == "workspace"
                and source_type in {"workspace_file_search", "workspace_file_list"}):
            return ("workspace_page", source_type, str(metadata["page_id"]), str(metadata.get("file_id") or "list"))
        raw = metadata.get("raw") if isinstance(metadata, dict) else None
        canonical_text = raw.get("content") if isinstance(raw, dict) else None
        if not isinstance(canonical_text, str) or not canonical_text.strip():
            canonical_text = str(getattr(source, "display_text", "") or "")
        normalized_text = " ".join(canonical_text.split())
        if len(normalized_text) >= 32:
            digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
            return ("content", digest)

        title = " ".join(str(getattr(source, "title", "") or "").split())
        return (
            str(getattr(source, "provider", "") or ""),
            str(getattr(source, "source_type", "") or ""),
            title,
            # 短文本不足以作为跨 Provider 的正文摘要去重依据，但保留它可避免
            # 多个没有 URL、标题为空的不同结果被误认为同一个来源。
            normalized_text,
        )

    @staticmethod
    def _normalize_source_url(value: str) -> str:
        """去掉 URL fragment 和常见追踪参数，保留实际资源定位参数。"""

        try:
            parsed = urlsplit(value)
            query = [
                (key, item)
                for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                if not key.lower().startswith("utm_")
                and key.lower() not in {"gclid", "fbclid", "msclkid"}
            ]
            return urlunsplit(
                (
                    parsed.scheme.lower(),
                    parsed.netloc.lower(),
                    parsed.path or "/",
                    urlencode(query, doseq=True),
                    "",
                )
            )
        except ValueError:
            return value.strip()

    @staticmethod
    def _build_observations(
        *,
        round_index: int,
        sources: list,
        registry: ToolCatalog | None = None,
        projection_reports: list[dict] | None = None,
    ) -> list[dict]:
        observations, diagnostics = PlannerObservationProjection.project_sources_with_diagnostics(
            round_index=round_index,
            sources=sources,
            definition_resolver=registry.get_or_none if registry is not None else None,
        )
        if projection_reports is not None:
            projection_reports.append(diagnostics)
        return observations

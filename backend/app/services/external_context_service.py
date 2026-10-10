from __future__ import annotations

import asyncio
import hashlib
import time
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
from app.services.tools.workflow import (
    ToolRunCallLedger,
    ToolWorkflowAggregate,
    decide_workflow_action,
    ToolWorkflowService,
)
from app.services.tools.observation_projection import PlannerObservationProjection
from app.services.tools.completion_contract import needs_followup


class ExternalContextService:
    """Chat 侧外部上下文门面。

    Chat 只依赖这一层；Catalog、Planner、Workflow、Executor 和结果组装都收在 tools 包内。
    每轮先规划再执行，并把结果作为 observations 交给下一轮；实际轮数和调用
    预算由代码侧 ToolRunPolicy 限定，避免开放式 Agent 无限循环。
    """

    # 兼容历史调用方读取。默认 quick_chat 仍为五轮；实例运行时实际使用
    # self.run_policy.max_planning_rounds，而不是让这个类常量决定所有模式。
    max_agent_rounds = 5

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
        self.registry = registry or ToolCatalog(db=db, user_id=user_id, project_id=project_id)
        deterministic = router or DeterministicToolPlanner(self.registry)
        self.planner = planner or LLMToolPlanner(
            catalog=self.registry,
            fallback_planner=deterministic,
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

        rewrite = self.query_rewriter.rewrite(query=query, recent_messages=recent_messages)
        routed_query = rewrite.rewritten_query
        observations: list[dict] = []
        events: list[ToolTraceEvent] = []
        if skill_context:
            events.append(
                ToolTraceEvent(
                    type="skill_activation",
                    payload={
                        "skill_key": skill_context.skill_key,
                        "version": skill_context.version,
                        "display_name": skill_context.display_name,
                        "activation_mode": "explicit",
                        "allowed_tool_keys": list(skill_context.allowed_tool_keys),
                        "requires_tool_execution": skill_context.requires_tool_execution,
                    },
                )
            )
        sources = []
        notices: list[str] = []
        last_plan = None
        planning_elapsed_ms = 0
        execution_elapsed_ms = 0
        selected_tool = "none"
        error_message = ""
        terminal_reason = "no_tool_needed"
        next_action = "stop"
        workflow_aggregate_status = "empty"
        workflow_aggregate = ToolWorkflowAggregate()
        raw_sources_total = 0
        duplicate_sources_total = 0
        projection_reports: list[dict] = []
        budget = ToolRunBudget(policy=self.run_policy)
        # 同步请求共享一个内存账本；跨请求恢复仍由持久化 Durable Runtime 负责。
        call_ledger = ToolRunCallLedger()

        def record_budget_timeout(*, round_index: int, stage: str) -> None:
            """超时只结束工具阶段；已获得的有效证据仍可用于有限回答。"""

            nonlocal terminal_reason, next_action, error_message
            terminal_reason = "tool_wall_clock_budget_exhausted"
            next_action = "finalize_partial" if sources else "stop"
            error_message = error_message or "工具阶段超过当前模式允许时长。"
            notices.append(self._budget_exhausted_notice(terminal_reason, has_sources=bool(sources)))
            events.append(
                ToolTraceEvent(
                    type="tool_agent_budget_timeout",
                    payload={
                        "reason": terminal_reason,
                        "stage": stage,
                        "round": round_index,
                        "budget": budget.to_trace_payload(),
                        "preserved_sources_count": len(sources),
                    },
                )
            )
            events.append(
                ToolTraceEvent(
                    type="tool_agent_terminal",
                    payload={
                        "reason": terminal_reason,
                        "round": round_index,
                        "max_rounds": self.run_policy.max_planning_rounds,
                        "next_action": next_action,
                        "budget": budget.to_trace_payload(),
                    },
                )
            )

        events.append(
            ToolTraceEvent(
                type="tool_agent_budget_start",
                payload={"policy": self.run_policy.to_public_dict(), "budget": budget.to_trace_payload()},
            )
        )
        # 这是受模式和多维 Budget 约束的 observe -> re-plan，而不是可无限自主运行的 ReAct loop。
        for round_index in range(1, self.run_policy.max_planning_rounds + 1):
            try:
                budget.begin_round()
            except ValueError as exc:
                terminal_reason = str(exc)
                if terminal_reason == "tool_wall_clock_budget_exhausted":
                    record_budget_timeout(round_index=round_index - 1, stage="before_planner")
                    break
                next_action = "stop"
                notices.append(self._budget_exhausted_notice(terminal_reason, has_sources=bool(sources)))
                events.append(
                    ToolTraceEvent(
                        type="tool_agent_terminal",
                        payload={
                            "reason": terminal_reason,
                            "round": round_index - 1,
                            "max_rounds": self.run_policy.max_planning_rounds,
                            "next_action": next_action,
                            "budget": budget.to_trace_payload(),
                        },
                    )
                )
                break
            planner_kwargs = {
                "query": routed_query,
                "enabled": enabled,
                "runtime": planner_runtime or self.planner_runtime,
                "recent_messages": recent_messages,
                "observations": observations,
            }
            if skill_context:
                planner_kwargs["skill_context"] = skill_context
            planning_started = time.perf_counter()
            try:
                plan = await asyncio.wait_for(
                    self.planner.plan(**planner_kwargs),
                    timeout=max(0.0, budget.deadline - time.perf_counter()),
                )
            except asyncio.TimeoutError:
                record_budget_timeout(round_index=round_index, stage="planner")
                break
            finally:
                planning_elapsed_ms += max(0, int((time.perf_counter() - planning_started) * 1000))
            # 同步处理或取消清理可能越过截止时间，不能将晚到的计划记成正常结束。
            if time.perf_counter() >= budget.deadline:
                record_budget_timeout(round_index=round_index, stage="planner")
                break
            last_plan = plan
            plan.original_query = rewrite.original_query
            plan.rewritten_query = routed_query if rewrite.did_rewrite else None
            plan_events = [
                ToolTraceEvent(
                    type=str(event.get("type")),
                    payload={key: value for key, value in event.items() if key != "type"},
                )
                for event in plan.trace_events
                if isinstance(event, dict) and event.get("type")
            ]
            events.append(
                ToolTraceEvent(
                    type="tool_agent_round_start",
                    payload={
                        "round": round_index,
                        "max_rounds": self.run_policy.max_planning_rounds,
                        "observations_count": len(observations),
                        "budget": budget.to_trace_payload(),
                    },
                )
            )
            events.extend(plan_events)
            if not enabled or not plan.should_use_tools:
                # 规划失败且规则无法给出候选内调用，不等于模型确认无需工具。
                # 明确不用工具的空候选仍正常结束，避免把用户的禁止要求误记成失败。
                planning_failed = any(event.type == "tool_fallback" for event in plan_events) and any(
                    event.type == "tool_candidate_selection" and bool(event.payload.get("candidates"))
                    for event in plan_events
                )
                if enabled and planning_failed:
                    terminal_reason = "tool_planning_unavailable"
                    next_action = "finalize_partial" if sources else "stop"
                    error_message = "工具规划失败，候选内规则兜底没有产生可执行调用。"
                    notices.append("本次工具核验未完成，请稍后重试或明确要查询的对象。")
                    events.append(ToolTraceEvent(type="tool_agent_terminal", payload={
                        "reason": terminal_reason, "round": round_index, "next_action": next_action,
                        "preserved_sources_count": len(sources),
                    }))
                else:
                    terminal_reason = "no_tool_needed"
                    next_action = "stop"
                break

            events.append(
                ToolTraceEvent(
                    type="tool_plan",
                    payload={"round": round_index, "plan": plan.to_public_dict()},
                )
            )
            if rewrite.did_rewrite and round_index == 1:
                events.append(
                    ToolTraceEvent(
                        type="tool_query_rewrite",
                        payload={
                            "original_query": rewrite.original_query,
                            "rewritten_query": rewrite.rewritten_query,
                            "reason": rewrite.reason,
                            "extracted_places": rewrite.extracted_places or [],
                        },
                    )
                )

            workflow_kwargs = {
                "plan": plan,
                "query": routed_query,
                "call_ledger": call_ledger,
            }
            # 保持测试替身和旧扩展 Workflow 的最小 `run(plan, query,
            # call_ledger)` 协议。正式 ToolWorkflowService 才接受并执行
            # Policy 的单计划调用/并发额度，不能靠替身绕过生产路径。
            if isinstance(self.workflow, ToolWorkflowService):
                workflow_kwargs.update(
                    {
                        "max_tool_calls": min(
                            self.run_policy.max_calls_per_plan,
                            budget.remaining_tool_calls,
                        ),
                        "max_parallel_calls": self.run_policy.max_parallel_calls,
                        "deadline": budget.deadline,
                    }
                )
            execution_started = time.perf_counter()
            try:
                if isinstance(self.workflow, ToolWorkflowService):
                    # 正式 Workflow 在截止时间内收集部分结果；外层不再次整体取消它。
                    workflow_result = await self.workflow.run(**workflow_kwargs)
                else:
                    workflow_result = await asyncio.wait_for(
                        self.workflow.run(**workflow_kwargs),
                        timeout=max(0.0, budget.deadline - time.perf_counter()),
                    )
            except asyncio.TimeoutError:
                record_budget_timeout(round_index=round_index, stage="execution")
                break
            finally:
                execution_elapsed_ms += max(0, int((time.perf_counter() - execution_started) * 1000))
            budget.record_attempted_tool_calls(workflow_result.aggregate.attempted_steps)
            events.extend(workflow_result.events)
            raw_sources_total += len(workflow_result.sources)
            newly_added_sources, duplicate_count = self._merge_sources(sources, workflow_result.sources)
            duplicate_sources_total += duplicate_count
            notices.extend(workflow_result.notices)
            selected_tool = workflow_result.selected_tool
            error_message = workflow_result.error_message or error_message
            workflow_aggregate_status = workflow_result.aggregate_status
            workflow_aggregate = workflow_result.aggregate
            observations.extend(
                self._build_observations(
                    round_index=round_index,
                    # 同一来源在多轮重规划中只投影一次，避免重复占用 Planner 观察预算。
                    sources=newly_added_sources,
                    registry=self.registry,
                    projection_reports=projection_reports,
                )
            )
            quality_feedback = list(getattr(workflow_result, "feedback", []) or [])
            quality_observations = [
                feedback.to_planner_observation(round_index=round_index)
                for feedback in quality_feedback
            ]
            observations.extend(quality_observations)
            if workflow_result.error_message:
                # Expected tool failures are useful observations. They allow the
                # bounded follow-up planning round to repair an ambiguous file edit
                # or stale file id instead of turning it into an opaque app error.
                observations.append(
                    {
                        "round": round_index,
                        "source_type": "tool_error_feedback",
                        "provider": "tool_runtime",
                        "title": "工具执行反馈",
                        "display_text": workflow_result.error_message[:500],
                        "metadata": {},
                    }
                )
            if getattr(workflow_result, "deadline_exhausted", False) or time.perf_counter() >= budget.deadline:
                # 先合并有效证据、计数和终态，再停止；不因超时重新执行已完成步骤。
                record_budget_timeout(round_index=round_index, stage="execution")
                events.append(
                    ToolTraceEvent(
                        type="tool_agent_round_end",
                        payload={
                            "round": round_index,
                            "next_action": next_action,
                            "decision_reason": terminal_reason,
                            "sources_count": len(workflow_result.sources),
                            "budget": budget.to_trace_payload(),
                        },
                    )
                )
                break
            quality_replan_required = any(
                observation.get("next_action") == "replan"
                for observation in quality_observations
            )
            completion_replan_required = needs_followup(
                query=routed_query,
                observations=observations,
                contract=skill_context.completion_contract if skill_context else None,
            )
            if completion_replan_required:
                events.append(
                    ToolTraceEvent(
                        type="tool_agent_replan_required",
                        payload={
                            "round": round_index,
                            "reason": "skill_completion_contract",
                            "skill_key": skill_context.skill_key if skill_context else None,
                        },
                    )
                )
            if quality_replan_required:
                events.append(
                    ToolTraceEvent(
                        type="tool_agent_replan_required",
                        payload={
                            "round": round_index,
                            "reason": "tool_result_quality",
                            "failed_call_ids": [
                                observation["call_id"]
                                for observation in quality_observations
                                if observation.get("next_action") == "replan"
                            ],
                        },
                    )
                )
            workflow_action = decide_workflow_action(
                aggregate=workflow_result.aggregate,
                # 自定义 Workflow 可能未填充 aggregate；正式 Workflow 的 sources
                # 已经由质量门筛选，这个参数只用于兼容测试和扩展实现。
                has_sources=bool(workflow_result.sources),
                has_error=bool(workflow_result.error_message),
                planner_need_more_rounds=plan.need_more_rounds,
                quality_replan_required=quality_replan_required,
                round_index=round_index,
                max_rounds=self.run_policy.max_planning_rounds,
                completion_replan_required=completion_replan_required,
            )
            if workflow_action.action == "replan" and not budget.can_replan():
                budget_reason = budget.replan_limit_reason() or "tool_replan_budget_exhausted"
                workflow_action = self._budget_terminal_action(
                    reason=budget_reason,
                    has_sources=bool(workflow_result.sources),
                )
            elif workflow_action.action == "replan":
                budget.consume_replan()
            next_action = workflow_action.action
            if workflow_action.notice:
                notices.append(workflow_action.notice)
            events.append(
                ToolTraceEvent(
                    type="tool_agent_round_end",
                    payload={
                        "round": round_index,
                        "need_more_rounds": plan.need_more_rounds,
                        "quality_replan_required": quality_replan_required,
                        "completion_replan_required": completion_replan_required,
                        "next_action": workflow_action.action,
                        "decision_reason": workflow_action.reason,
                        "budget": budget.to_trace_payload(),
                        "sources_count": len(workflow_result.sources),
                        "observations_count": len(observations),
                    },
                )
            )
            if workflow_action.action != "replan":
                terminal_reason = workflow_action.reason
                events.append(
                    ToolTraceEvent(
                        type="tool_agent_terminal",
                        payload={
                            "reason": terminal_reason,
                            "round": round_index,
                            "max_rounds": self.run_policy.max_planning_rounds,
                            "next_action": workflow_action.action,
                            "need_more_rounds": plan.need_more_rounds,
                            "budget": budget.to_trace_payload(),
                        },
                    )
                )
                break

        if error_message and not sources and not notices:
            # Provider/Adapter 错误可能携带 URL、响应正文或凭据片段；外层只
            # 给用户稳定的脱敏说明，详细原因留在受保护的服务端日志中。
            notices.append("外部信息工具调用未获得可用结果，请检查输入或稍后重试。")

        if skill_context:
            events.append(
                ToolTraceEvent(
                    type="skill_result",
                    payload={
                        "skill_key": skill_context.skill_key,
                        "version": skill_context.version,
                        "status": (
                            "partial"
                            if sources and next_action == "finalize_partial"
                            else "success"
                            if sources
                            else "error"
                            if error_message
                            else "empty"
                        ),
                        "planner": last_plan.router if last_plan else "none",
                        "planned_tool_keys": [call.tool_key for call in last_plan.calls] if last_plan else [],
                        "sources_count": len(sources),
                        "rounds_observed": sum(
                            1 for event in events if event.type == "tool_agent_round_end"
                        ),
                        "terminal_reason": terminal_reason,
                    },
                )
            )

        execution_status_text = ""
        if terminal_reason == "tool_wall_clock_budget_exhausted":
            # 状态来自代码，不是工具正文；最终模型也需知道本次任务没有全部完成。
            execution_status_text = (
                "[工具执行状态（系统记录）]\n"
                "本次工具阶段达到总时长限制，部分计划可能未完成。"
                "仅根据下列已获得的证据回答；缺少结果的部分请明确说明未完成，不能猜测工具输出。"
            )[:max(0, max_chars)]
        elif terminal_reason == "tool_planning_unavailable":
            execution_status_text = (
                "[工具执行状态（系统记录）]\n"
                "本轮工具规划未完成，未取得本轮要求核验的新结果。"
                "只可使用下列已有证据；没有取得的原文、行内容或外部事实应明确说明无法核实，"
                "不得根据文件名、历史摘要或模型记忆编造，也不得声称已读取或已完成。"
            )[:max(0, max_chars)]
        evidence_text = self.assembler.format_sources_for_prompt(
            sources,
            max_chars=max(0, max_chars - len(execution_status_text)),
        )
        context_text = "\n\n".join(part for part in (execution_status_text, evidence_text) if part) or None
        included_sources = [source for source in sources if source.used_in_prompt]
        public_sources = [source.to_public_dict() for source in sources]
        public_events = [event.to_public_dict() for event in events]

        return ExternalContextResult(
            context_text=context_text,
            sources=sources,
            notices=notices,
            diagnostics={
                "external_context_enabled": int(enabled),
                "external_tool_called": selected_tool,
                "external_sources_total": len(sources),
                "external_sources_included": len(included_sources),
                "external_sources_raw_total": raw_sources_total,
                "external_sources_duplicate_count": duplicate_sources_total,
                "external_sources_dedup_strategy": "normalized_url_or_canonical_content_hash",
                "external_evidence_projection": {
                    "rounds": projection_reports,
                    "totals": PlannerObservationProjection.aggregate_diagnostics(projection_reports),
                },
                "external_context_chars": len(context_text or ""),
                "external_context_latency_ms": budget.elapsed_ms,
                "external_planning_latency_ms": planning_elapsed_ms,
                "external_execution_latency_ms": execution_elapsed_ms,
                "external_context_error": int(bool(error_message and not sources)),
                "external_tool_events_total": len(events),
                "external_agent_terminal_reason": terminal_reason,
                "external_tool_next_action": next_action,
                "external_tool_run_policy": self.run_policy.to_public_dict(),
                "external_tool_run_budget": budget.to_trace_payload(),
                "external_tool_workflow_aggregate_status": workflow_aggregate_status,
                "external_tool_workflow_aggregate": workflow_aggregate.to_trace_payload(),
                "skill_active": int(bool(skill_context)),
                "skill_key": skill_context.skill_key if skill_context else "none",
                "skill_version": skill_context.version if skill_context else "none",
            },
            details={
                "external_sources": public_sources,
                "tool_plan": last_plan.to_public_dict() if last_plan else None,
                "tool_events": public_events,
                "active_skill": skill_context.to_public_dict() if skill_context else None,
                "tool_workflow_aggregate_status": workflow_aggregate_status,
                "tool_workflow_aggregate": workflow_aggregate.to_trace_payload(),
                "tool_workflow_next_action": next_action,
                "tool_run_policy": self.run_policy.to_public_dict(),
                "tool_run_budget": budget.to_trace_payload(),
            },
            tool_plan=last_plan,
            tool_events=events,
        )

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

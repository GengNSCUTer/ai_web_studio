from __future__ import annotations

import asyncio

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
        total_elapsed_ms = 0
        selected_tool = "none"
        error_message = ""
        terminal_reason = "no_tool_needed"
        next_action = "stop"
        workflow_aggregate_status = "empty"
        workflow_aggregate = ToolWorkflowAggregate()
        budget = ToolRunBudget(policy=self.run_policy)
        # One request owns one in-memory ledger. It deliberately ends with this
        # synchronous Chat request; durable retries/replay keep using the
        # persisted AgentRun/Step runtime instead.
        call_ledger = ToolRunCallLedger()

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
            plan = await self.planner.plan(
                **planner_kwargs,
            )
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
                    }
                )
            try:
                workflow_result = await asyncio.wait_for(
                    self.workflow.run(**workflow_kwargs),
                    timeout=max(0.001, budget.remaining_wall_clock_ms / 1000),
                )
            except asyncio.TimeoutError:
                terminal_reason = "tool_wall_clock_budget_exhausted"
                next_action = "stop"
                notices.append(self._budget_exhausted_notice(terminal_reason, has_sources=bool(sources)))
                events.append(
                    ToolTraceEvent(
                        type="tool_agent_budget_timeout",
                        payload={
                            "reason": terminal_reason,
                            "round": round_index,
                            "budget": budget.to_trace_payload(),
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
                break
            budget.record_attempted_tool_calls(workflow_result.aggregate.attempted_steps)
            events.extend(workflow_result.events)
            self._merge_sources(sources, workflow_result.sources)
            notices.extend(workflow_result.notices)
            total_elapsed_ms += workflow_result.elapsed_ms
            selected_tool = workflow_result.selected_tool
            error_message = workflow_result.error_message or error_message
            workflow_aggregate_status = workflow_result.aggregate_status
            workflow_aggregate = workflow_result.aggregate
            observations.extend(
                self._build_observations(
                    round_index=round_index,
                    sources=workflow_result.sources,
                    registry=self.registry,
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
            quality_replan_required = any(
                observation.get("next_action") == "replan"
                for observation in quality_observations
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

        if not last_plan:
            public_events = [event.to_public_dict() for event in events]
            return ExternalContextResult(
                context_text=None,
                sources=[],
                notices=[],
                diagnostics={
                    "external_context_enabled": 0,
                    "external_tool_called": "none",
                    "external_sources_total": 0,
                    "external_sources_included": 0,
                    "external_context_chars": 0,
                    "external_context_error": 0,
                    "external_tool_next_action": next_action,
                },
                details={
                    "external_sources": [],
                    "tool_plan": None,
                    "tool_events": public_events,
                    "tool_workflow_next_action": next_action,
                },
                tool_plan=None,
                tool_events=events,
            )
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
                            "success"
                            if sources
                            else "error"
                            if error_message
                            else "empty"
                        ),
                        "planner": last_plan.router,
                        "planned_tool_keys": [call.tool_key for call in last_plan.calls],
                        "sources_count": len(sources),
                        "rounds_observed": sum(
                            1 for event in events if event.type == "tool_agent_round_end"
                        ),
                        "terminal_reason": terminal_reason,
                    },
                )
            )

        context_text = self.assembler.format_sources_for_prompt(sources, max_chars=max_chars)
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
                "external_context_chars": len(context_text or ""),
                "external_context_latency_ms": total_elapsed_ms,
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
                "tool_plan": last_plan.to_public_dict(),
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
    def _merge_sources(target: list, incoming: list) -> None:
        """合并同一请求内重复的 evidence，避免受控重规划放大上下文。"""

        seen = {
            ExternalContextService._source_identity(source)
            for source in target
        }
        for source in incoming:
            identity = ExternalContextService._source_identity(source)
            if identity in seen:
                continue
            target.append(source)
            seen.add(identity)

    @staticmethod
    def _source_identity(source: object) -> tuple[str, ...]:
        """使用稳定的公开字段去重，不读取或记录 Provider 原始响应。"""

        url = str(getattr(source, "url", None) or "").strip()
        title = " ".join(str(getattr(source, "title", "") or "").split())
        display_text = " ".join(str(getattr(source, "display_text", "") or "").split())
        if url:
            return (str(getattr(source, "provider", "") or ""), url)
        return (
            str(getattr(source, "provider", "") or ""),
            str(getattr(source, "source_type", "") or ""),
            title,
            display_text,
        )

    @staticmethod
    def _build_observations(
        *,
        round_index: int,
        sources: list,
        registry: ToolCatalog | None = None,
    ) -> list[dict]:
        return PlannerObservationProjection.project_sources(
            round_index=round_index,
            sources=sources,
            definition_resolver=registry.get_or_none if registry is not None else None,
        )

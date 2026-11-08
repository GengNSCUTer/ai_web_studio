"""同步工具循环的业务步骤；编排器只决定步骤之间的跳转。"""

from __future__ import annotations

import asyncio
import time

from app.services.tools.action_guard import ActionScope, ToolActionGuard
from app.services.tools.schemas import ExternalContextResult, ToolTraceEvent
from app.services.tools.run_policy import ToolRunBudget
from app.services.tools.workflow import (ToolRunCallLedger, ToolWorkflowAggregate,
    ToolWorkflowAction, ToolWorkflowService, decide_workflow_action)
from app.services.tools.observation_projection import PlannerObservationProjection
from app.services.tools.completion_contract import infer_read_completion, needs_followup, required_tool_keys
from app.services.tools.edit_preview_completion import (PREVIEW_GUIDANCE, APPROVAL_GUIDANCE,
    preview_requirement, approval_requirement, missing_preview_tools, missing_approval_tools)
from app.services.tools.db_boundary import release_read_transaction


class SyncToolRun:
    """每次执行独占的业务对象；Chat Worker可由业务快照重建，不跨用户复用对象。"""

    def __init__(self, service, *, query, enabled, max_chars, recent_messages=None,
                 planner_runtime=None, skill_context=None, budget=None):
        self.service = service
        self.query, self.enabled, self.max_chars = query, enabled, max_chars
        self.recent_messages = recent_messages
        self.planner_runtime, self.skill_context = planner_runtime, skill_context
        self.round_index = 0
        self.result = None
        self.service.executor.action_guard = ToolActionGuard(ActionScope(self.query))
        self.rewrite = self.service.query_rewriter.rewrite(query=self.query, recent_messages=self.recent_messages)
        self.routed_query = self.rewrite.rewritten_query
        self.observations: list[dict] = []
        self.used_call_ids: set[str] = set()
        self.events: list[ToolTraceEvent] = []
        if self.skill_context:
            self.events.append(
                ToolTraceEvent(
                    type="skill_activation",
                    payload={
                        "skill_key": self.skill_context.skill_key,
                        "version": self.skill_context.version,
                        "display_name": self.skill_context.display_name,
                        "activation_mode": "explicit",
                        "allowed_tool_keys": list(self.skill_context.allowed_tool_keys),
                        "requires_tool_execution": self.skill_context.requires_tool_execution,
                    },
                )
            )
        self.sources = []
        self.notices: list[str] = []
        self.last_plan = None
        self.planning_elapsed_ms = 0
        self.execution_elapsed_ms = 0
        self.selected_tool = "none"
        self.error_message = ""
        self.terminal_reason = "no_tool_needed"
        self.next_action = "stop"
        self.workflow_aggregate_status = "empty"
        self.workflow_aggregate = ToolWorkflowAggregate()
        self.raw_sources_total = 0
        self.duplicate_sources_total = 0
        self.projection_reports: list[dict] = []
        self.budget = budget if budget is not None else ToolRunBudget(policy=self.service.run_policy)
        self.call_ledger = ToolRunCallLedger()
        self.events.append(
            ToolTraceEvent(
                type="tool_agent_budget_start",
                payload={"policy": self.service.run_policy.to_public_dict(), "budget": self.budget.to_trace_payload()},
            )
        )

    def record_budget_timeout(self, *, round_index: int, stage: str) -> None:
        """超时只结束工具阶段；已获得的有效证据仍可用于有限回答。"""

        self.terminal_reason = "tool_wall_clock_budget_exhausted"
        self.next_action = "finalize_partial" if self.sources else "stop"
        self.error_message = self.error_message or "工具阶段超过当前模式允许时长。"
        self.notices.append(self.service._budget_exhausted_notice(self.terminal_reason, has_sources=bool(self.sources)))
        self.events.append(
            ToolTraceEvent(
                type="tool_agent_budget_timeout",
                payload={
                    "reason": self.terminal_reason,
                    "stage": stage,
                    "round": round_index,
                    "budget": self.budget.to_trace_payload(),
                    "preserved_sources_count": len(self.sources),
                },
            )
        )
        self.events.append(
            ToolTraceEvent(
                type="tool_agent_terminal",
                payload={
                    "reason": self.terminal_reason,
                    "round": round_index,
                    "max_rounds": self.service.run_policy.max_planning_rounds,
                    "next_action": self.next_action,
                    "budget": self.budget.to_trace_payload(),
                },
            )
        )

    async def plan_round(self) -> bool:
        """检查预算并规划；返回是否有可执行计划。"""
        self.round_index = self.budget.planning_rounds_used + 1
        try:
            self.budget.begin_round()
        except ValueError as exc:
            self.terminal_reason = str(exc)
            if self.terminal_reason == "tool_wall_clock_budget_exhausted":
                self.record_budget_timeout(round_index=self.round_index - 1, stage="before_planner")
                return False
            self.next_action = "stop"
            self.notices.append(self.service._budget_exhausted_notice(self.terminal_reason, has_sources=bool(self.sources)))
            self.events.append(
                ToolTraceEvent(
                    type="tool_agent_terminal",
                    payload={
                        "reason": self.terminal_reason,
                        "round": self.round_index - 1,
                        "max_rounds": self.service.run_policy.max_planning_rounds,
                        "next_action": self.next_action,
                        "budget": self.budget.to_trace_payload(),
                    },
                )
            )
            return False
        planner_kwargs = {
            "query": self.routed_query,
            "enabled": self.enabled,
            "runtime": self.planner_runtime or self.service.planner_runtime,
            "recent_messages": self.recent_messages,
            "observations": self.observations,
        }
        if self.skill_context:
            planner_kwargs["skill_context"] = self.skill_context
        # Catalog/权限查询及上一轮只读收尾不能持有连接等待Embedding或Planner。
        release_read_transaction(self.service.db)
        from app.services.chat_tool_runtime import current_tool_persistence
        persistence = current_tool_persistence.get()
        if persistence is not None:
            # 规划请求也会耗时和失败，崩溃不能把已消耗轮次和工具截止时间归零。
            await asyncio.to_thread(persistence.save, self, "plan")
        planning_started = time.perf_counter()
        try:
            self.plan = await asyncio.wait_for(
                self.service.planner.plan(**planner_kwargs),
                timeout=max(0.0, self.budget.deadline - time.perf_counter()),
            )
        except asyncio.TimeoutError:
            self.record_budget_timeout(round_index=self.round_index, stage="planner")
            return False
        finally:
            self.planning_elapsed_ms += max(0, int((time.perf_counter() - planning_started) * 1000))
        # 同步处理或取消清理可能越过截止时间，不能将晚到的计划记成正常结束。
        if time.perf_counter() >= self.budget.deadline:
            self.record_budget_timeout(round_index=self.round_index, stage="planner")
            return False
        self.plan = self.service._scope_plan_call_ids(self.plan, self.round_index, self.used_call_ids)
        self.last_plan = self.plan
        self.plan.original_query = self.rewrite.original_query
        self.plan.rewritten_query = self.routed_query if self.rewrite.did_rewrite else None
        plan_events = [
            ToolTraceEvent(
                type=str(event.get("type")),
                payload={key: value for key, value in event.items() if key != "type"},
            )
            for event in self.plan.trace_events
            if isinstance(event, dict) and event.get("type")
        ]
        self.events.append(
            ToolTraceEvent(
                type="tool_agent_round_start",
                payload={
                    "round": self.round_index,
                    "max_rounds": self.service.run_policy.max_planning_rounds,
                    "observations_count": len(self.observations),
                    "budget": self.budget.to_trace_payload(),
                },
            )
        )
        self.events.extend(plan_events)
        if not self.enabled or not self.plan.should_use_tools:
            # 规划失败且规则无法给出候选内调用，不等于模型确认无需工具。
            # 明确不用工具的空候选仍正常结束，避免把用户的禁止要求误记成失败。
            planning_failed = any(event.type == "tool_fallback" for event in plan_events) and any(
                event.type == "tool_candidate_selection" and bool(event.payload.get("candidates"))
                for event in plan_events
            )
            if self.enabled and planning_failed:
                self.terminal_reason = "tool_planning_unavailable"
                self.next_action = "finalize_partial" if self.sources else "stop"
                self.error_message = "工具规划失败，候选内规则兜底没有产生可执行调用。"
                self.notices.append("本次工具核验未完成，请稍后重试或明确要查询的对象。")
                self.events.append(ToolTraceEvent(type="tool_agent_terminal", payload={
                    "reason": self.terminal_reason, "round": self.round_index, "next_action": self.next_action,
                    "preserved_sources_count": len(self.sources),
                }))
            else:
                missing_edit = (missing_preview_tools(self.routed_query, self.observations) if preview_requirement(self.routed_query)
                                else missing_approval_tools(self.observations) if approval_requirement(self.routed_query) else ())
                if self.enabled and missing_edit:
                    self.terminal_reason = "task_evidence_incomplete"
                    self.next_action = "finalize_partial" if self.sources else "clarify"
                    self.notices.append("尚未完成本轮文件预览或修改审批，请核实目标与替换内容；不能视为已写入。")
                    self.events.append(ToolTraceEvent(type="tool_agent_terminal", payload={
                        "reason": self.terminal_reason, "round": self.round_index, "next_action": self.next_action,
                        "missing_tool_keys": list(missing_edit),
                    }))
                else:
                    self.terminal_reason = "no_tool_needed"
                    self.next_action = "stop"
            return False

        self.events.append(
            ToolTraceEvent(
                type="tool_plan",
                payload={"round": self.round_index, "plan": self.plan.to_public_dict()},
            )
        )
        if self.rewrite.did_rewrite and self.round_index == 1:
            self.events.append(
                ToolTraceEvent(
                    type="tool_query_rewrite",
                    payload={
                        "original_query": self.rewrite.original_query,
                        "rewritten_query": self.rewrite.rewritten_query,
                        "reason": self.rewrite.reason,
                        "extracted_places": self.rewrite.extracted_places or [],
                    },
                )
            )

        return True

    async def execute_round(self) -> bool:
        """执行当前计划；超时保留既有证据，取消继续向上传递。"""
        workflow_kwargs = {
            "plan": self.plan,
            "query": self.routed_query,
            "call_ledger": self.call_ledger,
        }
        # 保持测试替身和旧扩展 Workflow 的最小 `run(plan, query,
        # call_ledger)` 协议。正式 ToolWorkflowService 才接受并执行
        # Policy 的单计划调用/并发额度，不能靠替身绕过生产路径。
        if isinstance(self.service.workflow, ToolWorkflowService):
            from app.services.chat_tool_runtime import current_tool_persistence
            persistence = current_tool_persistence.get()
            workflow_kwargs.update(
                {
                    "max_tool_calls": min(
                        self.service.run_policy.max_calls_per_plan,
                        # 恢复计划包含已提交调用；其重放不占外部请求额度，逐调用由PG重新计数。
                        self.service.run_policy.max_calls_per_plan if persistence else self.budget.remaining_tool_calls,
                    ),
                    "max_parallel_calls": self.service.run_policy.max_parallel_calls,
                    "deadline": self.budget.deadline,
                }
            )
        execution_started = time.perf_counter()
        try:
            if isinstance(self.service.workflow, ToolWorkflowService):
                # 正式 Workflow 在截止时间内收集部分结果；外层不再次整体取消它。
                self.workflow_result = await self.service.workflow.run(**workflow_kwargs)
            else:
                self.workflow_result = await asyncio.wait_for(
                    self.service.workflow.run(**workflow_kwargs),
                    timeout=max(0.0, self.budget.deadline - time.perf_counter()),
                )
        except asyncio.TimeoutError:
            self.record_budget_timeout(round_index=self.round_index, stage="execution")
            return False
        finally:
            self.execution_elapsed_ms += max(0, int((time.perf_counter() - execution_started) * 1000))

        return True

    def observe_round(self) -> bool:
        """合并质量、依赖和完成反馈；返回是否需要下一轮。"""
        from app.services.chat_tool_runtime import current_tool_persistence
        persistence = current_tool_persistence.get()
        if persistence is None:
            self.budget.record_attempted_tool_calls(self.workflow_result.aggregate.attempted_steps)
        self.events.extend(self.workflow_result.events)
        self.used_call_ids.update(event.payload["call_id"] for event in self.workflow_result.events
            if isinstance(event.payload.get("call_id"),str) and event.payload["call_id"])
        self.raw_sources_total += len(self.workflow_result.sources)
        newly_added_sources, duplicate_count = self.service._merge_sources(self.sources, self.workflow_result.sources)
        self.duplicate_sources_total += duplicate_count
        self.notices.extend(self.workflow_result.notices)
        self.selected_tool = self.workflow_result.selected_tool
        self.error_message = self.workflow_result.error_message or self.error_message
        self.workflow_aggregate_status = self.workflow_result.aggregate_status
        self.workflow_aggregate = self.workflow_result.aggregate
        self.observations.extend(
            self.service._build_observations(
                round_index=self.round_index,
                # 同一来源在多轮重规划中只投影一次，避免重复占用 Planner 观察预算。
                sources=newly_added_sources,
                registry=self.service.registry,
                projection_reports=self.projection_reports,
            )
        )
        quality_feedback = list(getattr(self.workflow_result, "feedback", []) or [])
        quality_observations = [
            feedback.to_planner_observation(round_index=self.round_index)
            for feedback in quality_feedback
        ]
        self.observations.extend(quality_observations)
        if self.workflow_result.error_message:
            # 预期的工具失败作为观察反馈，允许在预算内修正歧义或过期文件ID。
            self.observations.append(
                {
                    "round": self.round_index,
                    "source_type": "tool_error_feedback",
                    "provider": "tool_runtime",
                    "title": "工具执行反馈",
                    "display_text": self.workflow_result.error_message[:500],
                    "metadata": {},
                }
            )
        if getattr(self.workflow_result, "deadline_exhausted", False) or time.perf_counter() >= self.budget.deadline:
            # 先合并有效证据、计数和终态，再停止；不因超时重新执行已完成步骤。
            self.record_budget_timeout(round_index=self.round_index, stage="execution")
            self.events.append(
                ToolTraceEvent(
                    type="tool_agent_round_end",
                    payload={
                        "round": self.round_index,
                        "next_action": self.next_action,
                        "decision_reason": self.terminal_reason,
                        "sources_count": len(self.workflow_result.sources),
                        "budget": self.budget.to_trace_payload(),
                    },
                )
            )
            return False
        quality_replan_required = any(
            observation.get("next_action") == "replan"
            for observation in quality_observations
        )
        completion = preview_requirement(self.routed_query) or approval_requirement(self.routed_query) or (self.skill_context.completion_contract if self.skill_context else infer_read_completion(self.routed_query))
        completion_replan_required = needs_followup(
            query=self.routed_query,
            observations=self.observations,
            contract=completion,
        )
        if completion_replan_required:
            # 这是代码根据用户意图生成的缺口说明，不读取外部资料中的任务指令。
            observed = {item.get("metadata", {}).get("tool_key") for item in self.observations}
            missing = list(missing_preview_tools(self.routed_query, self.observations)) if preview_requirement(self.routed_query) else [key for key in required_tool_keys(completion) if key not in observed]
            self.observations.append({"source_type": "task_completion_feedback", "round": self.round_index,
                "metadata": {"missing_tool_keys": missing},
                "display_text": PREVIEW_GUIDANCE if preview_requirement(self.routed_query) else APPROVAL_GUIDANCE if approval_requirement(self.routed_query) else "当前只有定位/搜索结果，尚缺正文核验。依据实际返回的ID继续读取；搜索未命中时可列出授权文件后定位，不能推断文件不存在。"})
            self.events.append(
                ToolTraceEvent(
                    type="tool_agent_replan_required",
                    payload={
                        "round": self.round_index,
                        "reason": "skill_completion_contract",
                        "skill_key": self.skill_context.skill_key if self.skill_context else None,
                    },
                )
            )
        if quality_replan_required:
            self.events.append(
                ToolTraceEvent(
                    type="tool_agent_replan_required",
                    payload={
                        "round": self.round_index,
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
            aggregate=self.workflow_result.aggregate,
            # 自定义 Workflow 可能未填充 aggregate；正式 Workflow 的 sources
            # 已经由质量门筛选，这个参数只用于兼容测试和扩展实现。
            has_sources=bool(self.workflow_result.sources),
            has_error=bool(self.workflow_result.error_message),
            planner_need_more_rounds=self.plan.need_more_rounds,
            quality_replan_required=quality_replan_required,
            round_index=self.round_index,
            max_rounds=self.service.run_policy.max_planning_rounds,
            completion_replan_required=completion_replan_required,
        )
        if any(event.type == 'tool_action_intent_check' and event.payload.get('status') == 'denied'
               for event in self.workflow_result.events):
            # 原任务/数据边界拒绝不是临时网络故障，不能靠换工具继续试探。
            workflow_action = ToolWorkflowAction(
                action='finalize_partial' if self.sources else 'clarify', reason='action_scope_denied',
                notice='部分工具动作超出本次任务范围或包含禁止外发的数据，已停止继续规划；已有有效证据保留。')
        if workflow_action.action == "replan" and not self.budget.can_replan():
            budget_reason = self.budget.replan_limit_reason() or "tool_replan_budget_exhausted"
            workflow_action = self.service._budget_terminal_action(
                reason=budget_reason,
                has_sources=bool(self.workflow_result.sources),
            )
        elif workflow_action.action == "replan":
            self.budget.consume_replan()
        self.next_action = workflow_action.action
        if workflow_action.notice:
            self.notices.append(workflow_action.notice)
        self.events.append(
            ToolTraceEvent(
                type="tool_agent_round_end",
                payload={
                    "round": self.round_index,
                    "need_more_rounds": self.plan.need_more_rounds,
                    "quality_replan_required": quality_replan_required,
                    "completion_replan_required": completion_replan_required,
                    "next_action": workflow_action.action,
                    "decision_reason": workflow_action.reason,
                    "budget": self.budget.to_trace_payload(),
                    "sources_count": len(self.workflow_result.sources),
                    "observations_count": len(self.observations),
                },
            )
        )
        if workflow_action.action != "replan":
            self.terminal_reason = workflow_action.reason
            self.events.append(
                ToolTraceEvent(
                    type="tool_agent_terminal",
                    payload={
                        "reason": self.terminal_reason,
                        "round": self.round_index,
                        "max_rounds": self.service.run_policy.max_planning_rounds,
                        "next_action": workflow_action.action,
                        "need_more_rounds": self.plan.need_more_rounds,
                        "budget": self.budget.to_trace_payload(),
                    },
                )
            )
            return False

        return True

    def finish(self) -> ExternalContextResult:
        """复用原证据组装和诊断，输出业务结果而非图内部状态。"""
        if self.error_message and not self.sources and not self.notices:
            # Provider/Adapter 错误可能携带 URL、响应正文或凭据片段；外层只
            # 给用户稳定的脱敏说明，详细原因留在受保护的服务端日志中。
            self.notices.append("外部信息工具调用未获得可用结果，请检查输入或稍后重试。")

        if self.skill_context:
            self.events.append(
                ToolTraceEvent(
                    type="skill_result",
                    payload={
                        "skill_key": self.skill_context.skill_key,
                        "version": self.skill_context.version,
                        "status": (
                            "partial"
                            if self.sources and self.next_action == "finalize_partial"
                            else "success"
                            if self.sources
                            else "error"
                            if self.error_message
                            else "empty"
                        ),
                        "planner": self.last_plan.router if self.last_plan else "none",
                        "planned_tool_keys": [call.tool_key for call in self.last_plan.calls] if self.last_plan else [],
                        "sources_count": len(self.sources),
                        "rounds_observed": sum(
                            1 for event in self.events if event.type == "tool_agent_round_end"
                        ),
                        "terminal_reason": self.terminal_reason,
                    },
                )
            )

        execution_status_text = ""
        if self.terminal_reason == "tool_wall_clock_budget_exhausted":
            # 状态来自代码，不是工具正文；最终模型也需知道本次任务没有全部完成。
            execution_status_text = (
                "[工具执行状态（系统记录）]\n"
                "本次工具阶段达到总时长限制，部分计划可能未完成。"
                "仅根据下列已获得的证据回答；缺少结果的部分请明确说明未完成，不能猜测工具输出。"
            )[:max(0, self.max_chars)]
        elif self.terminal_reason == "tool_planning_unavailable":
            execution_status_text = (
                "[工具执行状态（系统记录）]\n"
                "本轮工具规划未完成，未取得本轮要求核验的新结果。"
                "只可使用下列已有证据；没有取得的原文、行内容或外部事实应明确说明无法核实，"
                "不得根据文件名、历史摘要或模型记忆编造，也不得声称已读取或已完成。"
            )[:max(0, self.max_chars)]
        evidence_section = self.service.assembler.build_section(self.sources, max_chars=self.max_chars,
                                                       execution_status=execution_status_text)
        context_text = evidence_section.render() or None
        included_sources = [source for source in self.sources if source.used_in_prompt]
        public_sources = [source.to_public_dict() for source in self.sources]
        public_events = [event.to_public_dict() for event in self.events]

        return ExternalContextResult(
            context_text=context_text,
            sources=self.sources,
            notices=self.notices,
            diagnostics={
                "external_context_enabled": int(self.enabled),
                "external_tool_called": self.selected_tool,
                "external_sources_total": len(self.sources),
                "external_sources_included": len(included_sources),
                "external_sources_raw_total": self.raw_sources_total,
                "external_sources_duplicate_count": self.duplicate_sources_total,
                "external_sources_dedup_strategy": "normalized_url_or_canonical_content_hash",
                "external_evidence_projection": {
                    "rounds": self.projection_reports,
                    "totals": PlannerObservationProjection.aggregate_diagnostics(self.projection_reports),
                },
                "external_context_chars": len(context_text or ""),
                "external_context_latency_ms": self.budget.elapsed_ms,
                "external_planning_latency_ms": self.planning_elapsed_ms,
                "external_execution_latency_ms": self.execution_elapsed_ms,
                "external_context_error": int(bool(self.error_message and not self.sources)),
                "external_tool_events_total": len(self.events),
                "external_agent_terminal_reason": self.terminal_reason,
                "external_tool_next_action": self.next_action,
                "external_tool_run_policy": self.service.run_policy.to_public_dict(),
                "external_tool_run_budget": self.budget.to_trace_payload(),
                "external_tool_workflow_aggregate_status": self.workflow_aggregate_status,
                "external_tool_workflow_aggregate": self.workflow_aggregate.to_trace_payload(),
                "skill_active": int(bool(self.skill_context)),
                "skill_key": self.skill_context.skill_key if self.skill_context else "none",
                "skill_version": self.skill_context.version if self.skill_context else "none",
            },
            details={
                "external_sources": public_sources,
                "execution_status_text": execution_status_text,
                "tool_plan": self.last_plan.to_public_dict() if self.last_plan else None,
                "tool_events": public_events,
                "active_skill": self.skill_context.to_public_dict() if self.skill_context else None,
                "tool_workflow_aggregate_status": self.workflow_aggregate_status,
                "tool_workflow_aggregate": self.workflow_aggregate.to_trace_payload(),
                "tool_workflow_next_action": self.next_action,
                "tool_run_policy": self.service.run_policy.to_public_dict(),
                "tool_run_budget": self.budget.to_trace_payload(),
            },
            tool_plan=self.last_plan,
            tool_events=self.events,
            evidence_section=evidence_section,
        )

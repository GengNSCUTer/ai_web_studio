from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.skill_catalog import SkillExecutionContext
from app.services.tools.credentials import ToolCredentialResolver
from app.services.tools.catalog import ToolCatalog
from app.services.tools.executor import ToolExecutor
from app.services.tools.formatter import ExternalContextAssembler
from app.services.tools.planner import DeterministicToolPlanner, LLMToolPlanner, PlannerRuntime
from app.services.tools.query_rewriter import QueryRewriteService
from app.services.tools.schemas import (
    ExternalContextResult,
    ToolTraceEvent,
)
from app.services.tools.workflow import ToolWorkflowService


class ExternalContextService:
    """Chat 侧外部上下文门面。

    Chat 只依赖这一层；Catalog、Planner、Workflow、Executor 和结果组装都收在 tools 包内。
    每轮先规划再执行，并把结果作为 observations 交给下一轮，但最多五轮，避免开放式 Agent 无限循环。
    """

    # Chat has a hard five-round observe -> re-plan cap. Each plan remains
    # separately call-limited and every Tool still passes Executor policy.
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
    ) -> None:
        self.registry = registry or ToolCatalog(db=db, user_id=user_id)
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
                },
                details={
                    "external_sources": [],
                    "tool_plan": None,
                    "tool_events": [],
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

        # 这是有硬上限的 observe -> re-plan，而不是可无限自主运行的 ReAct loop。
        for round_index in range(1, self.max_agent_rounds + 1):
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
                        "max_rounds": self.max_agent_rounds,
                        "observations_count": len(observations),
                    },
                )
            )
            events.extend(plan_events)
            if not enabled or not plan.should_use_tools:
                terminal_reason = "no_tool_needed"
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

            workflow_result = await self.workflow.run(plan=plan, query=routed_query)
            events.extend(workflow_result.events)
            sources.extend(workflow_result.sources)
            notices.extend(workflow_result.notices)
            total_elapsed_ms += workflow_result.elapsed_ms
            selected_tool = workflow_result.selected_tool
            error_message = workflow_result.error_message or error_message
            observations.extend(self._build_observations(round_index=round_index, sources=workflow_result.sources))
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
            events.append(
                ToolTraceEvent(
                    type="tool_agent_round_end",
                    payload={
                        "round": round_index,
                        "need_more_rounds": plan.need_more_rounds,
                        "sources_count": len(workflow_result.sources),
                        "observations_count": len(observations),
                    },
                )
            )
            if not plan.need_more_rounds:
                terminal_reason = "completed_no_followup"
                break
            if not workflow_result.sources and not workflow_result.error_message:
                terminal_reason = "no_evidence_or_error"
                break
            if round_index >= self.max_agent_rounds:
                # The Planner may request another observation, but synchronous
                # Chat intentionally stops here. Long read-only workflows have
                # an explicit Durable Run path instead of silently expanding
                # this request into an autonomous loop.
                terminal_reason = "max_rounds_reached"
                notices.append("工具观察已达到同步对话轮次上限；如需更长的只读任务，请显式发起可恢复工作流。")
                events.append(
                    ToolTraceEvent(
                        type="tool_agent_terminal",
                        payload={
                            "reason": terminal_reason,
                            "round": round_index,
                            "max_rounds": self.max_agent_rounds,
                            "need_more_rounds": True,
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
                },
                details={
                    "external_sources": [],
                    "tool_plan": None,
                    "tool_events": public_events,
                },
                tool_plan=None,
                tool_events=events,
            )
        if error_message and not sources:
            notices.append(f"外部信息工具调用失败：{error_message}")

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
                "skill_active": int(bool(skill_context)),
                "skill_key": skill_context.skill_key if skill_context else "none",
                "skill_version": skill_context.version if skill_context else "none",
            },
            details={
                "external_sources": public_sources,
                "tool_plan": last_plan.to_public_dict(),
                "tool_events": public_events,
                "active_skill": skill_context.to_public_dict() if skill_context else None,
            },
            tool_plan=last_plan,
            tool_events=events,
        )

    @staticmethod
    def _build_observations(*, round_index: int, sources: list) -> list[dict]:
        allowed_metadata_keys = {
            "call_id",
            "tool_key",
            "city",
            "province",
            "district",
            "address",
            "name",
            "domain",
            "origin",
            "destination",
            "mode",
            # Workspace file tools expose an opaque ProjectFile id, never a path
            # or storage key. Keeping it in the bounded observation lets round 2
            # read a specific file after list/search discovered it.
            "file_id",
            "mime_type",
            "line_start",
            "line_end",
        }
        observations: list[dict] = []
        for index, source in enumerate(sources[:8], start=1):
            raw_metadata = getattr(source, "metadata", {})
            safe_metadata = {
                key: str(value)[:240]
                for key, value in (raw_metadata.items() if isinstance(raw_metadata, dict) else [])
                if key in allowed_metadata_keys and isinstance(value, (str, int, float, bool))
            }
            observations.append(
                {
                    "round": round_index,
                    "index": index,
                    "source_type": getattr(source, "source_type", ""),
                    "provider": getattr(source, "provider", ""),
                    "title": getattr(source, "title", ""),
                    "display_text": str(getattr(source, "display_text", ""))[:1200],
                    # Never send metadata.raw or nested remote payloads back to the
                    # Planner; observations are untrusted evidence, not instructions.
                    "metadata": safe_metadata,
                }
            )
        return observations

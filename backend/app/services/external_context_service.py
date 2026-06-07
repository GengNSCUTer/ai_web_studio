from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.tools.credentials import ToolCredentialResolver
from app.services.tools.executor import ToolExecutor
from app.services.tools.formatter import ExternalContextAssembler
from app.services.tools.planner import DeterministicToolPlanner, LLMToolPlanner, PlannerRuntime
from app.services.tools.query_rewriter import QueryRewriteService
from app.services.tools.registry import ToolRegistry
from app.services.tools.schemas import (
    ExternalContextResult,
    ToolTraceEvent,
)
from app.services.tools.workflow import ToolWorkflowService


class ExternalContextService:
    """Facade for external context retrieval.

    Chat routes should depend on this facade only. Tool definitions, routing,
    execution and prompt assembly live in app.services.tools.
    """

    max_agent_rounds = 2

    def __init__(
        self,
        *,
        db: Session | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        registry: ToolRegistry | None = None,
        router: object | None = None,
        executor: ToolExecutor | None = None,
        assembler: ExternalContextAssembler | None = None,
        query_rewriter: QueryRewriteService | None = None,
        planner: LLMToolPlanner | None = None,
        planner_runtime: PlannerRuntime | None = None,
        workflow: ToolWorkflowService | None = None,
    ) -> None:
        self.registry = registry or ToolRegistry(db=db, user_id=user_id)
        deterministic = router or DeterministicToolPlanner(self.registry)
        self.planner = planner or LLMToolPlanner(
            catalog=self.registry,
            fallback_planner=deterministic,
        )
        self.planner_runtime = planner_runtime
        self.executor = executor or ToolExecutor(
            credential_resolver=ToolCredentialResolver(db),
            catalog=self.registry,
            user_id=user_id,
            project_id=project_id,
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
    ) -> ExternalContextResult:
        rewrite = self.query_rewriter.rewrite(query=query, recent_messages=recent_messages)
        routed_query = rewrite.rewritten_query
        observations: list[dict] = []
        events: list[ToolTraceEvent] = []
        sources = []
        notices: list[str] = []
        last_plan = None
        total_elapsed_ms = 0
        selected_tool = "none"
        error_message = ""

        for round_index in range(1, self.max_agent_rounds + 1):
            plan = await self.planner.plan(
                query=routed_query,
                enabled=enabled,
                runtime=planner_runtime or self.planner_runtime,
                recent_messages=recent_messages,
                observations=observations,
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
            if not plan.need_more_rounds or not workflow_result.sources:
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

        context_text = self.assembler.format_sources_for_prompt(sources, max_chars=max_chars)
        included_sources = sources if context_text else []
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
            },
            details={
                "external_sources": public_sources,
                "tool_plan": last_plan.to_public_dict(),
                "tool_events": public_events,
            },
            tool_plan=last_plan,
            tool_events=events,
        )

    @staticmethod
    def _build_observations(*, round_index: int, sources: list) -> list[dict]:
        observations: list[dict] = []
        for index, source in enumerate(sources[:8], start=1):
            observations.append(
                {
                    "round": round_index,
                    "index": index,
                    "source_type": getattr(source, "source_type", ""),
                    "provider": getattr(source, "provider", ""),
                    "title": getattr(source, "title", ""),
                    "display_text": str(getattr(source, "display_text", ""))[:1200],
                    "metadata": getattr(source, "metadata", {}),
                }
            )
        return observations

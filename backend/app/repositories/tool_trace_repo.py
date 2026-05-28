from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.tool_trace import ToolCallRun, ToolRouteRun
from app.services.tools.schemas import ExternalContextResult


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


class ToolTraceRepository:
    def __init__(self, db: Session):
        self.db = db

    def replace_for_assistant_message(
        self,
        *,
        user_id: str,
        conversation_id: str,
        user_message_id: str | None,
        assistant_message_id: str,
        query: str,
        external_context: ExternalContextResult,
    ) -> ToolRouteRun | None:
        plan = external_context.tool_plan
        if not plan:
            return None

        old_route_ids = list(
            self.db.scalars(
                select(ToolRouteRun.id).where(ToolRouteRun.assistant_message_id == assistant_message_id)
            ).all()
        )
        if old_route_ids:
            self.db.execute(delete(ToolCallRun).where(ToolCallRun.route_run_id.in_(old_route_ids)))
            self.db.execute(delete(ToolRouteRun).where(ToolRouteRun.id.in_(old_route_ids)))

        events = [event.to_public_dict() for event in external_context.tool_events]
        sources = [source.to_public_dict() for source in external_context.sources]
        selected_tools = [call.to_public_dict() for call in plan.calls]
        status = "success"
        if external_context.diagnostics.get("external_context_error"):
            status = "error"
        elif not plan.should_use_tools:
            status = "skipped"

        route_run = ToolRouteRun(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            router_type=plan.router,
            query=query,
            external_context_allowed=plan.external_context_allowed,
            plan_json=_json_dumps(plan.to_public_dict()),
            selected_tools_json=_json_dumps(selected_tools),
            events_json=_json_dumps(events),
            sources_json=_json_dumps(sources),
            status=status,
            elapsed_ms=int(external_context.diagnostics.get("external_context_latency_ms") or 0),
        )
        self.db.add(route_run)
        self.db.flush()

        for call_run in self._build_call_runs(route_run.id, events, sources):
            self.db.add(call_run)

        self.db.commit()
        self.db.refresh(route_run)
        return route_run

    def get_events_by_assistant_message(self, assistant_message_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(ToolRouteRun)
            .where(ToolRouteRun.assistant_message_id == assistant_message_id)
            .order_by(ToolRouteRun.created_at.desc())
            .limit(1)
        )
        route_run = self.db.scalars(stmt).first()
        if not route_run:
            return []
        events = _json_loads(route_run.events_json, [])
        return events if isinstance(events, list) else []

    def get_events_by_assistant_messages(self, assistant_message_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not assistant_message_ids:
            return {}
        stmt = (
            select(ToolRouteRun)
            .where(ToolRouteRun.assistant_message_id.in_(assistant_message_ids))
            .order_by(ToolRouteRun.created_at.asc())
        )
        result: dict[str, list[dict[str, Any]]] = {}
        for route_run in self.db.scalars(stmt).all():
            if not route_run.assistant_message_id:
                continue
            events = _json_loads(route_run.events_json, [])
            result[route_run.assistant_message_id] = events if isinstance(events, list) else []
        return result

    @staticmethod
    def _build_call_runs(route_run_id: str, events: list[dict[str, Any]], sources: list[dict[str, Any]]) -> list[ToolCallRun]:
        started_at = datetime.now(timezone.utc)
        calls: dict[str, dict[str, Any]] = {}
        for event in events:
            event_type = event.get("type")
            call_id = event.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                continue

            current = calls.setdefault(call_id, {"call_id": call_id, "started_at": started_at})
            if event_type == "tool_call_start":
                current.update(
                    {
                        "tool_key": event.get("tool_key") or "",
                        "provider": event.get("provider") or "",
                        "category": event.get("category") or "",
                        "display_name": event.get("display_name"),
                        "arguments": event.get("arguments") or {},
                        "status": "running",
                    }
                )
            elif event_type == "tool_call_end":
                current.update(
                    {
                        "tool_key": event.get("tool_key") or current.get("tool_key") or "",
                        "provider": event.get("provider") or current.get("provider") or "",
                        "category": event.get("category") or current.get("category") or "",
                        "display_name": event.get("display_name") or current.get("display_name"),
                        "status": "success",
                        "elapsed_ms": event.get("elapsed_ms"),
                        "sources_count": event.get("sources_count") or 0,
                        "finished_at": datetime.now(timezone.utc),
                    }
                )
            elif event_type == "tool_call_error":
                current.update(
                    {
                        "tool_key": event.get("tool_key") or current.get("tool_key") or "",
                        "provider": event.get("provider") or current.get("provider") or "",
                        "category": event.get("category") or current.get("category") or "",
                        "display_name": event.get("display_name") or current.get("display_name"),
                        "status": "error",
                        "elapsed_ms": event.get("elapsed_ms"),
                        "error_message": event.get("error"),
                        "finished_at": datetime.now(timezone.utc),
                    }
                )

        call_runs: list[ToolCallRun] = []
        for call in calls.values():
            tool_sources = [
                source
                for source in sources
                if source.get("provider") == call.get("provider") and source.get("source_type") in {call.get("category"), "web", "weather", "map"}
            ]
            call_runs.append(
                ToolCallRun(
                    route_run_id=route_run_id,
                    call_id=call["call_id"],
                    tool_key=call.get("tool_key") or "unknown",
                    provider=call.get("provider") or "unknown",
                    category=call.get("category") or "unknown",
                    display_name=call.get("display_name"),
                    arguments_json=_json_dumps(call.get("arguments") or {}),
                    status=call.get("status") or "unknown",
                    started_at=call.get("started_at"),
                    finished_at=call.get("finished_at"),
                    elapsed_ms=call.get("elapsed_ms"),
                    error_message=call.get("error_message"),
                    sources_count=int(call.get("sources_count") or len(tool_sources)),
                    sources_json=_json_dumps(tool_sources),
                )
            )
        return call_runs

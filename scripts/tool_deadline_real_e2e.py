"""真实在线 Planner、文件读取和最终回答的超时故障注入验收。

只在当前测试进程里收窄目录并延迟一个文件读取，不修改运行中服务器配置。
复用固定账号的在线模型，创建临时项目，结束后清理；不输出凭据或模型原始异常。
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes.chat import _build_streaming_response  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.message import Message  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.project_file import ProjectFile  # noqa: E402
from app.models.tool_trace import ToolCallRun, ToolRouteRun  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.message import ChatStreamRequest  # noqa: E402
from app.services.chat_execution_service import ChatExecutionService  # noqa: E402
from app.services.chat_provider_service import ChatProviderService  # noqa: E402
from app.services.external_context_service import ExternalContextService  # noqa: E402
from app.services.tools.selector import ToolCandidateSelector  # noqa: E402
from sqlalchemy import select  # noqa: E402
from stage46a_real_e2e import cleanup, prepare_fixture  # noqa: E402


class DelayedFileExecutor:
    """只延迟第二个临时文件，其他调用仍由真实 Executor 做权限和质量检查。"""

    def __init__(self, original, slow_file_id: str) -> None:
        self.original = original
        self.slow_file_id = slow_file_id
        self.cancelled = False
        self.started = False

    async def execute(self, call):
        if call.tool_key != "workspace.files.read":
            raise AssertionError("故障验收仅允许读取临时工作区文件。")
        if call.tool_key == "workspace.files.read" and call.arguments.get("file_id") == self.slow_file_id:
            self.started = True
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return await self.original.execute(call)


class ReadOnlyFileSelector(ToolCandidateSelector):
    """测试仅收窄候选，不删除基础目录或扩大原有授权范围。"""

    def select(self, **kwargs):
        allowed = kwargs.get("allowed_tool_keys")
        kwargs["allowed_tool_keys"] = (
            {"workspace.files.read"} if allowed is None else allowed & {"workspace.files.read"}
        )
        return super().select(**kwargs)


async def run_case(*, user_id: str, project_id: str, first_file_id: str) -> dict:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        first = db.get(ProjectFile, first_file_id)
        first.file_name = "available-note.md"
        first.parsed_text = "# 可用资料\n项目资料归档周期为 7 天。\n"
        second = ProjectFile(
            user_id=user_id, project_id=project_id, kind="text", mime_type="text/markdown",
            file_name="delayed-note.md", file_size=40, storage_key=f"deadline-test/{project_id}/delayed.md",
            parsed_text="# 延迟资料\n备用资料归档周期为 41 天。\n",
        )
        conversation = Conversation(
            user_id=user_id, project_id=project_id, title="工具超时真实模型验收",
            model_name=db.get(Project, project_id).default_model,
            system_prompt="用中文回答，只根据实际获得的文件证据回答，明确指出未完成的部分。",
        )
        db.add_all([second, conversation])
        db.commit()
        slow_file_id = second.id
        conversation_id = conversation.id
        injected: list[DelayedFileExecutor] = []

        def external_service_factory(**kwargs):
            service = ExternalContextService(**kwargs)
            # 验收只检查读取与超时，目录收窄不改变生产候选规则或数据库权限。
            service.planner.candidate_selector = ReadOnlyFileSelector(service.registry)
            delayed = DelayedFileExecutor(service.executor, slow_file_id)
            service.workflow.executor = delayed
            injected.append(delayed)
            return service

        with patch(
            "app.services.chat_context_assembly_service.ExternalContextService",
            side_effect=external_service_factory,
        ):
            context = await ChatExecutionService(db=db, current_user=user).prepare_chat_execution(
                ChatStreamRequest(
                    conversation_id=conversation_id,
                    content=(
                        "请读取工作区以下两份文件，并分别报告资料归档周期。"
                        f"available-note.md 的 file_id 是 {first_file_id}；"
                        f"delayed-note.md 的 file_id 是 {slow_file_id}。"
                        "已经提供准确 ID，请直接调用 workspace.files.read 读取这两个文件，"
                        "两个读取互不依赖，可以并发；不要搜索或联网，不要修改文件。"
                        "如果某份未读取成功，明确说明未完成，不能编造周期。"
                    ),
                    web_search_enabled=True, tool_run_mode="quick_chat",
                )
            )

        if not injected or not injected[0].started or not injected[0].cancelled:
            planners = [event for event in context.tool_events if event.get("type") == "tool_fallback"]
            raise AssertionError("真实模型未触发两文件读取与超时取消，故障场景未成立："
                                 f"{[event.get('reason') for event in planners]}")
        stats = context.context_stats
        if stats.get("external_tool_next_action") != "finalize_partial":
            raise AssertionError("超时后未保留部分成功结果。")
        if stats.get("external_tool_run_budget", {}).get("tool_calls_used") != 2:
            raise AssertionError("实际尝试的两次调用没有准确计入预算。")
        planner_events = [event for event in context.tool_events if event.get("type") == "tool_planner_end"]
        if not any(event.get("strategy") == "llm_primary" for event in planner_events):
            raise AssertionError("此场景没有经过真实在线 LLM Planner。")
        if not any("工具执行状态（系统记录）" in str(message.get("content")) for message in context.history_messages):
            raise AssertionError("超时状态没有进入最终模型的实际输入。")

        assistant_id = context.assistant_message.id
        response = _build_streaming_response(context, ChatProviderService(), event_stream=True)
        events = []
        async for chunk in response.body_iterator:
            for line in chunk.splitlines():
                if line:
                    events.append(json.loads(line))
        if not any(event.get("type") == "done" for event in events):
            raise AssertionError("真实最终回答流未正常完成。")

    with SessionLocal() as db:
        assistant = db.get(Message, assistant_id)
        if not assistant or assistant.status != "done":
            raise AssertionError("部分工具超时不应使最终成功回答被标为 failed。")
        answer = assistant.content or ""
        if "7" not in answer or "41" in answer or not re.search("未|超时|无法|没有|不可", answer):
            raise AssertionError("最终回答未正确使用已获取证据或未披露缺失结果。")
        route = db.scalars(select(ToolRouteRun).where(ToolRouteRun.assistant_message_id == assistant_id)).one()
        calls = list(db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id == route.id)).all())
        statuses = sorted(call.status for call in calls)
        if route.status != "partial" or statuses != ["success", "timed_out"]:
            raise AssertionError("持久化工具轨迹没有如实记录部分成功和超时。")
        return {
            "scenario": "real_model_partial_tool_timeout_with_controlled_delay",
            "planner": "online_llm", "tool": "real_workspace_file_read",
            "assistant_status": assistant.status, "route_status": route.status,
            "call_statuses": statuses, "answer_chars": len(answer),
            "available_fact_used": True, "unavailable_fact_not_invented": True,
            "planner_latency_ms": stats["external_planning_latency_ms"],
            "execution_latency_ms": stats["external_execution_latency_ms"],
            "tool_phase_latency_ms": stats["external_context_latency_ms"],
        }


async def run_unavailable_planner_case(*, user_id: str, project_id: str) -> dict:
    """受控触发规划超时，真实最终模型应披露没有执行，而不是补写文件原文。"""

    class UnavailablePlannerProvider:
        async def complete_chat(self, **kwargs):
            await asyncio.Event().wait()

    with SessionLocal() as db:
        user = db.get(User, user_id)
        conversation = Conversation(user_id=user_id, project_id=project_id, title="规划失败真实回答验收",
                                    model_name=db.get(Project, project_id).default_model)
        db.add(conversation)
        db.commit()

        def external_service_factory(**kwargs):
            service = ExternalContextService(**kwargs)
            # 仅当前验收进程模拟不可用 Planner；最终回答仍使用账号真实在线模型。
            service.planner.chat_provider = UnavailablePlannerProvider()
            service.planner.planner_timeout_seconds = 0.01
            return service

        with patch("app.services.chat_context_assembly_service.ExternalContextService",
                   side_effect=external_service_factory):
            context = await ChatExecutionService(db=db, current_user=user).prepare_chat_execution(
                ChatStreamRequest(conversation_id=conversation.id,
                                  content="available-note.md 的第81行原文是什么？请核实，别联网。",
                                  web_search_enabled=True, tool_run_mode="workspace_review"))
        if context.context_stats.get("external_agent_terminal_reason") != "tool_planning_unavailable":
            raise AssertionError("规划失败未形成独立终态。")
        if not any("未取得本轮要求核验" in str(message.get("content")) for message in context.history_messages):
            raise AssertionError("规划未完成状态没有进入最终模型实际输入。")
        assistant_id = context.assistant_message.id
        response = _build_streaming_response(context, ChatProviderService(), event_stream=True)
        async for _ in response.body_iterator:
            pass
    with SessionLocal() as db:
        answer = db.get(Message, assistant_id)
        route = db.scalars(select(ToolRouteRun).where(ToolRouteRun.assistant_message_id == assistant_id)).one()
        calls = list(db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id == route.id)).all())
        if answer.status != "done" or not re.search("无法|未能|没有|未读取|未获取|不能|未完成", answer.content):
            raise AssertionError("真实最终模型没有明确披露核验未完成。")
        if "归档核验结束" in answer.content or "供应链溯源" in answer.content or calls or route.status != "error":
            raise AssertionError("核验失败时仍出现虚构原文、实际调用或错误任务状态。")
        return {"scenario": "unavailable_planner_without_safe_fallback", "planner": "controlled_timeout",
                "final_model": db.get(Conversation, route.conversation_id).model_name,
                "route_status": route.status, "assistant_status": answer.status,
                "tool_calls": 0, "limitation_disclosed": True, "answer_chars": len(answer.content)}


def run() -> None:
    _, user_id, project_id, file_id, _ = prepare_fixture()
    try:
        result = asyncio.run(run_case(user_id=user_id, project_id=project_id, first_file_id=file_id))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        unavailable = asyncio.run(run_unavailable_planner_case(user_id=user_id, project_id=project_id))
        print(json.dumps(unavailable, ensure_ascii=False, sort_keys=True))
    finally:
        cleanup(user_id=user_id, project_id=project_id)


if __name__ == "__main__":
    run()

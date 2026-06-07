from __future__ import annotations

import asyncio
import base64
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.core.config import settings
from fastapi.responses import PlainTextResponse
from app.models import *  # noqa: F403 - import all models so metadata contains every table.
from app.models.attachment import Attachment
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.tool_trace import ToolCallRun, ToolRouteRun
from app.models.user import User
from app.repositories.setting_repo import UserSettingRepository
from app.api.routes.chat import edit_last_user_stream, regenerate_last_answer_stream
from app.schemas.message import ChatEditLastUserRequest, ChatRegenerateRequest, ChatStreamRequest
from app.schemas.upload import UploadItemReference
from app.services.chat_execution_service import (
    ChatExecutionService,
    ExistingTurnExecutionInput,
)
from app.services.setting_service import SettingService
from app.services.tools.schemas import (
    ExternalContextResult,
    ExternalSource,
    PlannedToolCall,
    ToolPlan,
    ToolTraceEvent,
)


class FakeExternalContextService:
    def __init__(self, *, db: Session | None = None, user_id: str | None = None, project_id: str | None = None):
        self.db = db
        self.user_id = user_id
        self.project_id = project_id

    async def build_context(
        self,
        *,
        query: str,
        enabled: bool,
        max_chars: int,
        recent_messages: list[object] | None = None,
        planner_runtime: object | None = None,
    ) -> ExternalContextResult:
        if not enabled:
            plan = ToolPlan(
                plan_id="plan-skipped",
                router="test_router",
                external_context_allowed=False,
                should_use_tools=False,
                calls=[],
            )
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
                    "tool_plan": plan.to_public_dict(),
                    "tool_events": [],
                },
                tool_plan=plan,
                tool_events=[],
            )

        call = PlannedToolCall(
            call_id="call-web-1",
            tool_key="tavily.search",
            provider="tavily",
            category="web",
            display_name="网页搜索",
            confidence=0.9,
            reason="测试外部上下文",
            arguments={"query": query},
        )
        plan = ToolPlan(
            plan_id="plan-web",
            router="test_router",
            external_context_allowed=True,
            should_use_tools=True,
            calls=[call],
        )
        source = ExternalSource(
            source_type="web",
            provider="tavily",
            title="测试来源",
            display_text="这是一条测试外部来源。",
            url="https://example.com",
            rank=1,
        )
        events = [
            ToolTraceEvent(type="tool_plan", payload={"plan": plan.to_public_dict()}),
            ToolTraceEvent(
                type="tool_call_start",
                payload={
                    "call_id": call.call_id,
                    "tool_key": call.tool_key,
                    "provider": call.provider,
                    "category": call.category,
                    "display_name": call.display_name,
                    "arguments": call.arguments,
                },
            ),
            ToolTraceEvent(
                type="tool_call_end",
                payload={
                    "call_id": call.call_id,
                    "tool_key": call.tool_key,
                    "provider": call.provider,
                    "category": call.category,
                    "display_name": call.display_name,
                    "status": "success",
                    "elapsed_ms": 12,
                    "sources_count": 1,
                },
            ),
        ]
        context_text = "【外部信息源】\n测试来源：这是一条测试外部来源。"
        return ExternalContextResult(
            context_text=context_text,
            sources=[source],
            notices=[],
            diagnostics={
                "external_context_enabled": 1,
                "external_tool_called": "web",
                "external_sources_total": 1,
                "external_sources_included": 1,
                "external_context_chars": len(context_text),
                "external_context_latency_ms": 12,
                "external_context_error": 0,
                "external_tool_events_total": len(events),
            },
            details={
                "external_sources": [source.to_public_dict()],
                "tool_plan": plan.to_public_dict(),
                "tool_events": [event.to_public_dict() for event in events],
            },
            tool_plan=plan,
            tool_events=events,
        )


class ChatExecutionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()
        self.user = User(email=f"tester-{uuid4()}@example.com", username="tester")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self._previous_upload_dir = settings.upload_dir
        self.test_upload_root = Path("/tmp") / f"aiws_test_uploads_{uuid4()}"
        self.test_upload_root.mkdir(parents=True, exist_ok=True)
        object.__setattr__(settings, "upload_dir", str(self.test_upload_root))
        setting = SettingService(UserSettingRepository(self.db))._build_default_setting(self.user.id)
        setting.provider_type = "ollama"
        setting.default_model = "qwen-test"
        setting.ollama_base_url = "http://ollama.test"
        self.db.add(setting)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()
        object.__setattr__(settings, "upload_dir", self._previous_upload_dir)
        if self.test_upload_root.exists():
            for child in self.test_upload_root.rglob("*"):
                if child.is_file():
                    child.unlink()
            for child in sorted(self.test_upload_root.rglob("*"), reverse=True):
                if child.is_dir():
                    child.rmdir()
            self.test_upload_root.rmdir()

    def test_prepare_chat_execution_builds_context_without_external_services(self) -> None:
        async def run_test() -> None:
            service = ChatExecutionService(db=self.db, current_user=self.user)
            payload = ChatStreamRequest(
                content="请总结一下这个文档",
                model_name="qwen-test",
                system_prompt="用中文回答。",
                attachments=[
                    UploadItemReference(
                        id="upload-1",
                        file_name="notes.md",
                        mime_type="text/markdown",
                        file_size=128,
                        kind="file",
                        storage_key=f"{self.user.id}/notes.md",
                        parsed_text="LangChain LCEL 是 Runnable 组合表达式。",
                    )
                ],
                web_search_enabled=False,
            )

            with patch(
                "app.services.chat_context_assembly_service.ExternalContextService",
                FakeExternalContextService,
            ):
                context = await service.prepare_chat_execution(payload)

            self.assertEqual(context.resolved_model, "qwen-test")
            self.assertEqual(context.provider_type, "ollama")
            self.assertEqual(context.user_message.content, payload.content)
            self.assertEqual(context.assistant_message.status, "streaming")
            self.assertEqual(context.context_stats["prompt_attachment_context_injected"], 1)
            self.assertEqual(context.context_stats["external_context_enabled"], 0)
            self.assertGreaterEqual(context.context_stats["attachment_chunks_selected"], 1)
            self.assertTrue(context.history_messages)
            self.assertEqual(len(context.context_stats["prompt_prefix_hash"]), 16)
            self.assertEqual(len(context.user_message.attachments), 1)

            conversations = self.db.scalars(select(Conversation)).all()
            messages = self.db.scalars(select(Message)).all()
            self.assertEqual(len(conversations), 1)
            self.assertEqual(len(messages), 2)

        asyncio.run(run_test())

    def test_prepare_existing_turn_persists_tool_trace_when_external_context_enabled(self) -> None:
        async def run_test() -> None:
            conversation = Conversation(
                user_id=self.user.id,
                title="工具测试",
                model_name="qwen-test",
                system_prompt="用中文回答。",
            )
            self.db.add(conversation)
            self.db.commit()
            self.db.refresh(conversation)
            user_message = Message(
                conversation_id=conversation.id,
                role="user",
                content="美国现任总统是谁？",
                status="done",
            )
            assistant_message = Message(
                conversation_id=conversation.id,
                role="assistant",
                content="",
                status="streaming",
            )
            self.db.add_all([user_message, assistant_message])
            self.db.commit()
            self.db.refresh(user_message)
            self.db.refresh(assistant_message)

            service = ChatExecutionService(db=self.db, current_user=self.user)
            execution_input = ExistingTurnExecutionInput(
                conversation=conversation,
                history_rows=[user_message, assistant_message],
                user_message=user_message,
                assistant_message=assistant_message,
                model_name=None,
                system_prompt=None,
                thinking_enabled=False,
                thinking_budget=None,
                web_search_enabled=True,
            )

            with patch(
                "app.services.chat_context_assembly_service.ExternalContextService",
                FakeExternalContextService,
            ):
                context = await service.prepare_existing_turn_execution(execution_input)

            self.assertEqual(context.context_stats["external_context_enabled"], 1)
            self.assertEqual(context.context_stats["external_sources_total"], 1)
            self.assertEqual(len(context.tool_events), 3)
            self.assertEqual(len(context.external_sources), 1)

            route_runs = self.db.scalars(select(ToolRouteRun)).all()
            call_runs = self.db.scalars(select(ToolCallRun)).all()
            self.assertEqual(len(route_runs), 1)
            self.assertEqual(route_runs[0].assistant_message_id, assistant_message.id)
            self.assertEqual(route_runs[0].status, "success")
            self.assertEqual(len(call_runs), 1)
            self.assertEqual(call_runs[0].tool_key, "tavily.search")
            self.assertEqual(call_runs[0].status, "success")

        asyncio.run(run_test())

    def test_prepare_existing_turn_injects_multimodal_image_for_ollama(self) -> None:
        async def run_test() -> None:
            conversation = Conversation(
                user_id=self.user.id,
                title="图片测试",
                model_name="qwen-test",
                system_prompt="识别图片内容。",
            )
            self.db.add(conversation)
            self.db.commit()
            self.db.refresh(conversation)

            image_dir = self.test_upload_root / self.user.id
            image_dir.mkdir(parents=True, exist_ok=True)
            image_path = image_dir / "chart.png"
            image_bytes = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9sR4tiAAAAAASUVORK5CYII="
            )
            image_path.write_bytes(image_bytes)

            user_message = Message(
                conversation_id=conversation.id,
                role="user",
                content="请描述这张图片",
                status="done",
            )
            assistant_message = Message(
                conversation_id=conversation.id,
                role="assistant",
                content="",
                status="streaming",
            )
            self.db.add_all([user_message, assistant_message])
            self.db.commit()
            self.db.refresh(user_message)
            self.db.refresh(assistant_message)

            attachment = Attachment(
                message_id=user_message.id,
                kind="image",
                file_name="chart.png",
                file_ext="png",
                mime_type="image/png",
                file_size=len(image_bytes),
                storage_path=str(image_path),
                parsed_text=None,
            )
            self.db.add(attachment)
            self.db.commit()
            self.db.refresh(attachment)
            user_message.attachments = [attachment]

            service = ChatExecutionService(db=self.db, current_user=self.user)
            execution_input = ExistingTurnExecutionInput(
                conversation=conversation,
                history_rows=[user_message, assistant_message],
                user_message=user_message,
                assistant_message=assistant_message,
                model_name=None,
                system_prompt=None,
                thinking_enabled=False,
                thinking_budget=None,
                web_search_enabled=False,
            )

            with patch(
                "app.services.chat_context_assembly_service.ExternalContextService",
                FakeExternalContextService,
            ):
                context = await service.prepare_existing_turn_execution(execution_input)

            user_prompt = next(message for message in context.history_messages if message["role"] == "user")
            self.assertIn("images", user_prompt)
            self.assertEqual(len(user_prompt["images"]), 1)
            self.assertEqual(context.context_stats["prompt_image_messages"], 1)

        asyncio.run(run_test())

    def test_regenerate_route_reuses_last_turn_and_streams_answer(self) -> None:
        async def run_test() -> None:
            conversation = Conversation(
                user_id=self.user.id,
                title="重生成测试",
                model_name="qwen-test",
                system_prompt="用中文回答。",
            )
            user_message = Message(
                conversation_id=conversation.id,
                role="user",
                content="给我一个结论",
                status="done",
            )
            assistant_message = Message(
                conversation_id=conversation.id,
                role="assistant",
                content="旧回答",
                status="done",
            )
            self.db.add(conversation)
            self.db.commit()
            self.db.refresh(conversation)
            user_message.conversation_id = conversation.id
            assistant_message.conversation_id = conversation.id
            self.db.add_all([user_message, assistant_message])
            self.db.commit()
            self.db.refresh(user_message)
            self.db.refresh(assistant_message)

            with patch(
                "app.api.routes.chat.ChatExecutionService.prepare_existing_turn_execution",
                new=AsyncMock(return_value=SimpleNamespace()),
            ), patch(
                "app.api.routes.chat._build_streaming_response",
                return_value=PlainTextResponse("ok"),
            ):
                response = await regenerate_last_answer_stream(
                    ChatRegenerateRequest(
                        conversation_id=conversation.id,
                        assistant_message_id=assistant_message.id,
                        model_name="qwen-test",
                        system_prompt="用中文回答。",
                        thinking_enabled=False,
                        web_search_enabled=False,
                    ),
                    db=self.db,
                    current_user=self.user,
                )

            self.assertEqual(response.status_code, 200)
            refreshed = self.db.get(Message, assistant_message.id)
            self.assertEqual(refreshed.status, "streaming")
            self.assertEqual(refreshed.content, "")

        asyncio.run(run_test())

    def test_edit_last_user_route_updates_message_and_streams_answer(self) -> None:
        async def run_test() -> None:
            conversation = Conversation(
                user_id=self.user.id,
                title="编辑重答测试",
                model_name="qwen-test",
                system_prompt="用中文回答。",
            )
            self.db.add(conversation)
            self.db.commit()
            self.db.refresh(conversation)
            user_message = Message(
                conversation_id=conversation.id,
                role="user",
                content="旧问题",
                status="done",
            )
            assistant_message = Message(
                conversation_id=conversation.id,
                role="assistant",
                content="旧回答",
                status="done",
            )
            self.db.add_all([user_message, assistant_message])
            self.db.commit()
            self.db.refresh(user_message)
            self.db.refresh(assistant_message)

            with patch(
                "app.api.routes.chat.ChatExecutionService.prepare_existing_turn_execution",
                new=AsyncMock(return_value=SimpleNamespace()),
            ), patch(
                "app.api.routes.chat._build_streaming_response",
                return_value=PlainTextResponse("ok"),
            ):
                response = await edit_last_user_stream(
                    ChatEditLastUserRequest(
                        conversation_id=conversation.id,
                        user_message_id=user_message.id,
                        assistant_message_id=assistant_message.id,
                        content="新问题",
                        attachments=[],
                        model_name="qwen-test",
                        system_prompt="用中文回答。",
                        thinking_enabled=False,
                        web_search_enabled=False,
                    ),
                    db=self.db,
                    current_user=self.user,
                )

            self.assertEqual(response.status_code, 200)
            refreshed_user = self.db.get(Message, user_message.id)
            refreshed_assistant = self.db.get(Message, assistant_message.id)
            self.assertEqual(refreshed_user.content, "新问题")
            self.assertEqual(refreshed_assistant.status, "streaming")
            self.assertEqual(refreshed_assistant.content, "")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()

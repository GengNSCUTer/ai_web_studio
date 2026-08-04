from __future__ import annotations

import asyncio
import base64
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
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
from app.repositories.attachment_repo import AttachmentRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageGenerationConflict, MessageRepository
from app.repositories.setting_repo import UserSettingRepository
from app.api.routes.chat import (
    _build_streaming_response,
    _persist_stream_result,
    edit_last_user_stream,
    regenerate_last_answer_stream,
)
from app.schemas.message import ChatEditLastUserRequest, ChatRegenerateRequest, ChatStreamRequest
from app.schemas.upload import UploadItemReference
from app.services.chat_execution_service import (
    ChatExecutionContext,
    ChatExecutionService,
    ExistingTurnExecutionInput,
)
from app.services.message_service import MessageService
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

    def _create_stream_context(self) -> ChatExecutionContext:
        conversation = Conversation(
            user_id=self.user.id,
            title="流式状态测试",
            model_name="qwen-test",
            system_prompt="用中文回答。",
        )
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)

        user_message = Message(
            conversation_id=conversation.id,
            role="user",
            content="RRF 是什么？",
            status="done",
        )
        assistant_message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="",
            status="streaming",
        )
        message_repo = MessageRepository(self.db)
        message_repo.create(user_message)
        message_repo.create(assistant_message)

        return ChatExecutionContext(
            conversation_repo=ConversationRepository(self.db),
            message_service=MessageService(message_repo, AttachmentRepository(self.db)),
            conversation=conversation,
            user_message=user_message,
            assistant_message=assistant_message,
            history_messages=[{"role": "user", "content": user_message.content}],
            resolved_model="qwen-test",
            provider_type="ollama",
            base_url="http://ollama.test",
            api_key=None,
            temperature=0.2,
            top_p=0.9,
            max_tokens=None,
            context_notices=[],
            context_stats={},
            context_details={},
            context_summary=None,
            thinking_enabled=True,
            thinking_budget=None,
            tool_events=[],
            external_sources=[{"title": "RRF 资料", "url": "https://example.com/rrf"}],
        )

    def test_streaming_response_done_persists_complete_answer(self) -> None:
        class DoneProvider:
            async def stream_chat_events(self, **_: object):
                yield SimpleNamespace(type="reasoning_delta", text="先比较排名。")
                yield SimpleNamespace(type="answer_delta", text="RRF 是一种")
                yield SimpleNamespace(type="answer_delta", text="排名融合算法。")

        async def run_test() -> None:
            context = self._create_stream_context()
            response = _build_streaming_response(context, DoneProvider(), event_stream=True)
            chunks = [chunk async for chunk in response.body_iterator]

            self.db.refresh(context.assistant_message)
            self.assertEqual(context.assistant_message.status, "done")
            self.assertEqual(context.assistant_message.content, "RRF 是一种排名融合算法。")
            self.assertEqual(context.assistant_message.reasoning_content, "先比较排名。")
            self.assertIn('"title": "RRF 资料"', context.assistant_message.external_sources or "")
            self.assertIn('"type": "done"', "".join(chunks))

        asyncio.run(run_test())

    def test_streaming_response_cancelled_persists_partial_answer(self) -> None:
        class CancelledProvider:
            async def stream_chat_events(self, **_: object):
                yield SimpleNamespace(type="answer_delta", text="RRF 是一种排名融合算法")
                raise asyncio.CancelledError

        async def run_test() -> None:
            context = self._create_stream_context()
            response = _build_streaming_response(context, CancelledProvider(), event_stream=True)

            with self.assertRaises(asyncio.CancelledError):
                async for _ in response.body_iterator:
                    pass

            self.db.refresh(context.assistant_message)
            self.assertEqual(context.assistant_message.status, "cancelled")
            self.assertEqual(context.assistant_message.content, "RRF 是一种排名融合算法")

        asyncio.run(run_test())

    def test_streaming_response_cancelled_before_first_token_closes_as_cancelled(self) -> None:
        class CancelledBeforeTokenProvider:
            async def stream_chat_events(self, **_: object):
                raise asyncio.CancelledError
                yield  # pragma: no cover

        async def run_test() -> None:
            context = self._create_stream_context()
            response = _build_streaming_response(
                context,
                CancelledBeforeTokenProvider(),
                event_stream=True,
            )

            with self.assertRaises(asyncio.CancelledError):
                async for _ in response.body_iterator:
                    pass

            self.db.refresh(context.assistant_message)
            self.assertEqual(context.assistant_message.status, "cancelled")
            self.assertEqual(context.assistant_message.content, "")

        asyncio.run(run_test())

    def test_streaming_response_closes_provider_iterator_on_failure(self) -> None:
        class ClosableStream:
            def __init__(self) -> None:
                self.closed = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise RuntimeError("provider failed")

            async def aclose(self) -> None:
                self.closed = True

        class ClosableProvider:
            def __init__(self) -> None:
                self.stream = ClosableStream()

            def stream_chat_events(self, **_: object):
                return self.stream

        async def run_test() -> None:
            context = self._create_stream_context()
            provider = ClosableProvider()
            response = _build_streaming_response(context, provider, event_stream=True)
            body = "".join([chunk async for chunk in response.body_iterator])

            self.assertIn('"error_code": "provider_error"', body)
            self.assertTrue(provider.stream.closed)
            self.db.refresh(context.assistant_message)
            self.assertEqual(context.assistant_message.status, "failed")

        asyncio.run(run_test())

    def test_streaming_response_failed_persists_partial_answer_and_emits_error(self) -> None:
        class FailedProvider:
            async def stream_chat_events(self, **_: object):
                yield SimpleNamespace(type="answer_delta", text="RRF 是一种")
                raise RuntimeError("provider unavailable at https://secret.internal?api_key=hidden")

        async def run_test() -> None:
            context = self._create_stream_context()
            response = _build_streaming_response(context, FailedProvider(), event_stream=True)
            chunks = [chunk async for chunk in response.body_iterator]

            self.db.refresh(context.assistant_message)
            self.assertEqual(context.assistant_message.status, "failed")
            self.assertEqual(context.assistant_message.content, "RRF 是一种")
            body = "".join(chunks)
            self.assertIn('"type": "answer_delta"', body)
            self.assertIn('"type": "model_error"', body)
            self.assertIn('"error_code": "provider_error"', body)
            self.assertIn("模型调用失败，请稍后重试。", body)
            self.assertNotIn("secret.internal", body)

        asyncio.run(run_test())

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
            self.assertEqual(context.max_tokens, context.context_stats["budget_reserved_output_tokens"])
            self.assertLessEqual(
                context.context_stats["budget_max_total_tokens"] + context.max_tokens,
                context.context_stats["model_context_window"],
            )
            self.assertTrue(context.history_messages)
            self.assertEqual(len(context.context_stats["prompt_prefix_hash"]), 16)
            self.assertEqual(len(context.user_message.attachments), 1)

            conversations = self.db.scalars(select(Conversation)).all()
            messages = self.db.scalars(select(Message)).all()
            self.assertEqual(len(conversations), 1)
            self.assertEqual(len(messages), 2)

        asyncio.run(run_test())

    def test_turn_bootstrap_rolls_back_conversation_and_messages_when_attachment_write_fails(self) -> None:
        service = ChatExecutionService(db=self.db, current_user=self.user)
        payload = ChatStreamRequest(
            content="这轮不应留下半成品",
            model_name="qwen-test",
            attachments=[
                UploadItemReference(
                    id="upload-failure",
                    file_name="notes.md",
                    mime_type="text/markdown",
                    file_size=128,
                    kind="file",
                    storage_key=f"{self.user.id}/notes.md",
                    parsed_text="valid parsed text",
                )
            ],
            web_search_enabled=False,
        )
        default_settings = service.setting_service.get_or_create_user_settings(self.user.id)

        with patch.object(
            service.message_service.attachment_repo,
            "create_many",
            side_effect=RuntimeError("attachment write failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "attachment write failed"):
                service.turn_bootstrapper.bootstrap_new_turn(
                    payload=payload,
                    default_settings=default_settings,
                )

        self.assertEqual(list(self.db.scalars(select(Conversation)).all()), [])
        self.assertEqual(list(self.db.scalars(select(Message)).all()), [])
        self.assertEqual(list(self.db.scalars(select(Attachment)).all()), [])

    def test_edit_and_reset_rolls_back_user_text_when_attachment_replacement_fails(self) -> None:
        conversation = Conversation(user_id=self.user.id, title="Atomic edit", model_name="qwen-test")
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        user_message = Message(conversation_id=conversation.id, role="user", content="old question", status="done")
        assistant_message = Message(conversation_id=conversation.id, role="assistant", content="old answer", status="done")
        self.db.add_all([user_message, assistant_message])
        self.db.commit()
        self.db.refresh(user_message)
        self.db.refresh(assistant_message)
        message_service = MessageService(
            MessageRepository(self.db),
            AttachmentRepository(self.db),
            ConversationRepository(self.db),
        )

        with patch.object(
            message_service.attachment_repo,
            "create_many",
            side_effect=RuntimeError("replacement failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "replacement failed"):
                message_service.edit_and_reset_for_regeneration(
                    user_message=user_message,
                    assistant_message=assistant_message,
                    content="new question",
                    uploads=[
                        UploadItemReference(
                            id="replacement",
                            file_name="new.md",
                            mime_type="text/markdown",
                            file_size=64,
                            kind="file",
                            storage_key=f"{self.user.id}/new.md",
                            parsed_text="new text",
                        )
                    ],
                    user_id=self.user.id,
                )

        self.db.refresh(user_message)
        self.db.refresh(assistant_message)
        self.assertEqual(user_message.content, "old question")
        self.assertEqual(assistant_message.content, "old answer")
        self.assertEqual(assistant_message.status, "done")

    def test_prepare_failure_closes_assistant_placeholder(self) -> None:
        async def run_test() -> None:
            service = ChatExecutionService(db=self.db, current_user=self.user)
            payload = ChatStreamRequest(
                content="触发上下文组装失败",
                model_name="qwen-test",
                web_search_enabled=False,
            )

            with patch.object(
                service.context_assembly_service,
                "build_execution_context",
                new=AsyncMock(side_effect=RuntimeError("context assembly failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "context assembly failed"):
                    await service.prepare_chat_execution(payload)

            assistant_messages = self.db.scalars(
                select(Message).where(Message.role == "assistant")
            ).all()
            self.assertEqual(len(assistant_messages), 1)
            self.assertEqual(assistant_messages[0].status, "failed")

        asyncio.run(run_test())

    def test_prepare_failure_rolls_back_broken_session_before_closing_placeholder(self) -> None:
        async def run_test() -> None:
            service = ChatExecutionService(db=self.db, current_user=self.user)
            payload = ChatStreamRequest(content="触发数据库事务失败", model_name="qwen-test")

            async def fail_with_broken_session(**_: object) -> None:
                self.db.add(User(email=self.user.email, username=f"duplicate-{uuid4()}"))
                self.db.flush()

            with patch.object(
                service.context_assembly_service,
                "build_execution_context",
                new=AsyncMock(side_effect=fail_with_broken_session),
            ):
                with self.assertRaises(IntegrityError):
                    await service.prepare_chat_execution(payload)

            assistant_message = self.db.scalars(
                select(Message).where(Message.role == "assistant")
            ).one()
            self.assertEqual(assistant_message.status, "failed")

        asyncio.run(run_test())

    def test_streaming_response_enforces_first_token_timeout(self) -> None:
        class SlowFirstTokenProvider:
            async def stream_chat_events(self, **_: object):
                await asyncio.sleep(0.05)
                yield SimpleNamespace(type="answer_delta", text="too late")

        async def run_test() -> None:
            context = self._create_stream_context()
            timeout_settings = SimpleNamespace(
                chat_first_token_timeout_seconds=0.01,
                chat_stream_idle_timeout_seconds=1.0,
                chat_stream_total_timeout_seconds=1.0,
            )
            with patch("app.api.routes.chat.settings", timeout_settings):
                response = _build_streaming_response(context, SlowFirstTokenProvider(), event_stream=True)
                body = "".join([chunk async for chunk in response.body_iterator])

            self.db.refresh(context.assistant_message)
            self.assertEqual(context.assistant_message.status, "failed")
            self.assertIn('"error_code": "first_token_timeout"', body)

        asyncio.run(run_test())

    def test_streaming_response_enforces_idle_timeout_after_partial_answer(self) -> None:
        class IdleProvider:
            async def stream_chat_events(self, **_: object):
                yield SimpleNamespace(type="answer_delta", text="partial")
                await asyncio.sleep(0.05)
                yield SimpleNamespace(type="answer_delta", text="too late")

        async def run_test() -> None:
            context = self._create_stream_context()
            timeout_settings = SimpleNamespace(
                chat_first_token_timeout_seconds=1.0,
                chat_stream_idle_timeout_seconds=0.01,
                chat_stream_total_timeout_seconds=1.0,
            )
            with patch("app.api.routes.chat.settings", timeout_settings):
                response = _build_streaming_response(context, IdleProvider(), event_stream=True)
                body = "".join([chunk async for chunk in response.body_iterator])

            self.db.refresh(context.assistant_message)
            self.assertEqual(context.assistant_message.status, "failed")
            self.assertEqual(context.assistant_message.content, "partial")
            self.assertIn('"error_code": "stream_idle_timeout"', body)

        asyncio.run(run_test())

    def test_streaming_response_enforces_total_timeout_despite_regular_tokens(self) -> None:
        class EndlessSlowProvider:
            async def stream_chat_events(self, **_: object):
                while True:
                    await asyncio.sleep(0.005)
                    yield SimpleNamespace(type="answer_delta", text="x")

        async def run_test() -> None:
            context = self._create_stream_context()
            timeout_settings = SimpleNamespace(
                chat_first_token_timeout_seconds=1.0,
                chat_stream_idle_timeout_seconds=1.0,
                chat_stream_total_timeout_seconds=0.025,
            )
            with patch("app.api.routes.chat.settings", timeout_settings):
                response = _build_streaming_response(context, EndlessSlowProvider(), event_stream=True)
                body = "".join([chunk async for chunk in response.body_iterator])

            self.db.refresh(context.assistant_message)
            self.assertEqual(context.assistant_message.status, "failed")
            self.assertGreater(len(context.assistant_message.content), 0)
            self.assertIn('"error_code": "stream_total_timeout"', body)

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
                reasoning_content="旧思考",
                external_sources='[{"title":"旧来源"}]',
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
            self.assertIsNone(refreshed.reasoning_content)
            self.assertIsNone(refreshed.external_sources)

        asyncio.run(run_test())

    def test_regeneration_rejects_an_already_streaming_assistant(self) -> None:
        conversation = Conversation(
            user_id=self.user.id,
            title="并发生成测试",
            model_name="qwen-test",
        )
        assistant_message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="partial",
            status="streaming",
        )
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        assistant_message.conversation_id = conversation.id
        self.db.add(assistant_message)
        self.db.commit()
        self.db.refresh(assistant_message)

        service = MessageService(
            MessageRepository(self.db),
            AttachmentRepository(self.db),
            ConversationRepository(self.db),
        )
        with self.assertRaises(MessageGenerationConflict):
            service.reset_assistant_for_regeneration(assistant_message)

        self.db.rollback()
        self.db.refresh(assistant_message)
        self.assertEqual(assistant_message.status, "streaming")

    def test_regeneration_rejects_when_another_assistant_is_streaming(self) -> None:
        conversation = Conversation(
            user_id=self.user.id,
            title="并发重生成测试",
            model_name="qwen-test",
        )
        target_assistant = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="previous answer",
            status="done",
        )
        active_assistant = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="newer partial answer",
            status="streaming",
        )
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        target_assistant.conversation_id = conversation.id
        active_assistant.conversation_id = conversation.id
        self.db.add_all([target_assistant, active_assistant])
        self.db.commit()

        service = MessageService(
            MessageRepository(self.db),
            AttachmentRepository(self.db),
            ConversationRepository(self.db),
        )
        with self.assertRaises(MessageGenerationConflict):
            service.reset_assistant_for_regeneration(target_assistant)

        self.db.refresh(target_assistant)
        self.assertEqual(target_assistant.status, "done")
        self.assertEqual(target_assistant.content, "previous answer")

    def test_edit_rejects_when_another_assistant_is_streaming(self) -> None:
        conversation = Conversation(
            user_id=self.user.id,
            title="并发编辑测试",
            model_name="qwen-test",
        )
        user_message = Message(
            conversation_id=conversation.id,
            role="user",
            content="original question",
            status="done",
        )
        target_assistant = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="previous answer",
            status="done",
        )
        active_assistant = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="newer partial answer",
            status="streaming",
        )
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        for message in (user_message, target_assistant, active_assistant):
            message.conversation_id = conversation.id
        self.db.add_all([user_message, target_assistant, active_assistant])
        self.db.commit()

        service = MessageService(
            MessageRepository(self.db),
            AttachmentRepository(self.db),
            ConversationRepository(self.db),
        )
        with self.assertRaises(MessageGenerationConflict):
            service.edit_and_reset_for_regeneration(
                user_message=user_message,
                assistant_message=target_assistant,
                content="edited question",
                uploads=None,
                user_id=self.user.id,
            )

        self.db.refresh(user_message)
        self.db.refresh(target_assistant)
        self.assertEqual(user_message.content, "original question")
        self.assertEqual(target_assistant.status, "done")

    def test_new_turn_rejects_an_active_generation_in_the_same_conversation(self) -> None:
        conversation = Conversation(
            user_id=self.user.id,
            title="同会话并发测试",
            model_name="qwen-test",
        )
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        self.db.add(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content="partial",
                status="streaming",
            )
        )
        self.db.commit()

        async def run_test() -> None:
            service = ChatExecutionService(db=self.db, current_user=self.user)
            with self.assertRaises(HTTPException) as raised:
                await service.prepare_chat_execution(
                    ChatStreamRequest(
                        conversation_id=conversation.id,
                        content="第二个问题",
                        model_name="qwen-test",
                    )
                )
            self.assertEqual(raised.exception.status_code, 409)

        from fastapi import HTTPException

        asyncio.run(run_test())
        self.db.rollback()
        self.assertEqual(
            self.db.scalar(
                select(Message.id).where(
                    Message.conversation_id == conversation.id,
                    Message.role == "user",
                )
            ),
            None,
        )

    def test_old_generation_cannot_overwrite_new_generation(self) -> None:
        context = self._create_stream_context()
        old_generation_id = context.assistant_message.generation_id
        context.assistant_message.status = "done"
        context.message_service.save_message(context.assistant_message)
        context.message_service.reset_assistant_for_regeneration(context.assistant_message)
        self.db.refresh(context.assistant_message)
        self.assertNotEqual(context.assistant_message.generation_id, old_generation_id)

        stale_message = SimpleNamespace(
            id=context.assistant_message.id,
            conversation_id=context.assistant_message.conversation_id,
            generation_id=old_generation_id,
        )
        stale_context = SimpleNamespace(
            assistant_message=stale_message,
            message_service=context.message_service,
            external_sources=[],
        )
        persisted = _persist_stream_result(
            stale_context,
            status_value="done",
            content_parts=["旧回答"],
            reasoning_parts=[],
        )

        self.assertFalse(persisted)
        self.db.refresh(context.assistant_message)
        self.assertEqual(context.assistant_message.status, "streaming")
        self.assertEqual(context.assistant_message.content, "")

    def test_stale_streaming_message_is_fenced_after_process_failure(self) -> None:
        context = self._create_stream_context()
        old_generation_id = context.assistant_message.generation_id
        context.assistant_message.updated_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        self.db.commit()

        messages = MessageService(MessageRepository(self.db)).list_messages(context.conversation.id)

        self.assertEqual(len(messages), 2)
        self.db.refresh(context.assistant_message)
        self.assertEqual(context.assistant_message.status, "failed")
        self.assertNotEqual(context.assistant_message.generation_id, old_generation_id)

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
                reasoning_content="旧思考",
                external_sources='[{"title":"旧来源"}]',
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
            self.assertIsNone(refreshed_assistant.reasoning_content)
            self.assertIsNone(refreshed_assistant.external_sources)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()

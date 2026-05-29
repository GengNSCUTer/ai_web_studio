from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.models import *  # noqa: F403 - import all models so metadata contains every table.
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.tool_trace import ToolCallRun, ToolRouteRun
from app.models.user import User
from app.schemas.message import ChatStreamRequest
from app.schemas.upload import UploadItemReference
from app.services.chat_execution_service import (
    ChatExecutionService,
    ExistingTurnExecutionInput,
)
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

    async def build_context(self, *, query: str, enabled: bool, max_chars: int) -> ExternalContextResult:
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

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

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
            self.assertEqual(context.provider_type, "openai-compatible")
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


if __name__ == "__main__":
    unittest.main()

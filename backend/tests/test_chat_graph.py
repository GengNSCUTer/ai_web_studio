from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from app.services.agent_graph.chat_graph import run_chat_graph
from app.services.agent_graph.chat_graph_state import ChatGraphContext


class FakeAssembly:
    async def build_execution_context(self, **kwargs):
        return SimpleNamespace(
            conversation=kwargs["conversation"],
            user_message=kwargs["user_message"],
            assistant_message=kwargs["assistant_message"],
            context_details={},
            context_stats={"total_tokens_estimate": 42},
            external_sources=[{"citation_label": "[KB1]"}],
            tool_events=[{"type": "tool_result", "status": "success"}],
        )


class FakeBootstrapper:
    def bootstrap_new_turn(self, *, payload, default_settings):
        conversation = SimpleNamespace(id="conversation-1", project_id="project-1")
        user = SimpleNamespace(id="user-message-1", attachments=[])
        assistant = SimpleNamespace(id="assistant-1", generation_id="generation-1")
        return SimpleNamespace(
            conversation=conversation,
            user_message=user,
            assistant_message=assistant,
            history_rows=[SimpleNamespace(id="old-1")],
        )

    def apply_turn_overrides(self, **kwargs):
        return None


class FakeService:
    def __init__(self):
        self.current_user = SimpleNamespace(id="user-1")
        self.setting_service = SimpleNamespace(
            get_or_create_user_settings=lambda user_id: SimpleNamespace(default_model="fixture")
        )
        self.turn_bootstrapper = FakeBootstrapper()
        self.context_assembly_service = FakeAssembly()
        self.db = SimpleNamespace(commit=lambda: None)
        self.message_repo = SimpleNamespace(freeze_context_history=lambda rows: rows)

    def _build_runtime_config(self, conversation):
        return SimpleNamespace()

    def _resolve_skill_execution_context(self, **kwargs):
        return None

    @staticmethod
    def validate_attachment_context_inputs(attachments):
        return None


class ChatGraphTests(unittest.TestCase):
    def test_new_turn_uses_all_prepare_nodes_and_keeps_state_primitive(self):
        service = FakeService()
        payload = SimpleNamespace(
            skill_key=None,
            thinking_enabled=False,
            thinking_budget=None,
            web_search_enabled=False,
            knowledge_base_id=None,
            knowledge_base_ids=None,
            tool_run_mode="quick_chat",
        )
        result = asyncio.run(run_chat_graph(ChatGraphContext(service=service, turn_kind="new", payload=payload)))
        self.assertEqual(
            result.context_details["chat_graph_phases"],
            ["bootstrap", "runtime", "assemble", "finalize"],
        )
        self.assertEqual(result.context_stats["total_tokens_estimate"], 42)
        self.assertEqual(result.assistant_message.id, "assistant-1")

    def test_existing_turn_does_not_bootstrap_new_messages(self):
        service = FakeService()
        execution_input = SimpleNamespace(
            conversation=SimpleNamespace(id="conversation-2", project_id="project-1"),
            user_message=SimpleNamespace(id="user-message-2", attachments=[]),
            assistant_message=SimpleNamespace(id="assistant-2", generation_id="generation-2"),
            history_rows=[SimpleNamespace(id="old-2")],
            model_name=None,
            system_prompt=None,
            skill_key=None,
            thinking_enabled=False,
            thinking_budget=None,
            web_search_enabled=False,
            knowledge_base_id=None,
            knowledge_base_ids=None,
            tool_run_mode="quick_chat",
        )
        result = asyncio.run(run_chat_graph(ChatGraphContext(
            service=service,
            turn_kind="existing",
            execution_input=execution_input,
        )))
        self.assertEqual(result.assistant_message.id, "assistant-2")
        self.assertEqual(result.conversation.id, "conversation-2")


if __name__ == "__main__":
    unittest.main()

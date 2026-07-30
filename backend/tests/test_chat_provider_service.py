from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.services.chat_provider_service import ChatProviderService


class ChatProviderServiceTest(unittest.TestCase):
    def test_list_models_closes_openai_client(self) -> None:
        client = Mock()
        client.models = SimpleNamespace(
            list=AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(id="model-a")]))
        )
        client.close = AsyncMock()

        with patch("app.services.chat_provider_service.AsyncOpenAI", return_value=client):
            models = asyncio.run(
                ChatProviderService().list_models(
                    provider_type="openai-compatible",
                    base_url="https://example.test/v1",
                    api_key="test-key",
                )
            )

        self.assertEqual(models, ["model-a"])
        client.close.assert_awaited_once()

    def test_vllm_uses_openai_compatible_models_endpoint_without_requiring_api_key(self) -> None:
        client = Mock()
        client.models = SimpleNamespace(
            list=AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(id="Qwen/Qwen3-8B")]))
        )
        client.close = AsyncMock()

        with patch("app.services.chat_provider_service.AsyncOpenAI", return_value=client) as client_factory:
            models = asyncio.run(
                ChatProviderService().list_models(
                    provider_type="vllm",
                    base_url="http://127.0.0.1:8000/v1",
                    api_key=None,
                )
            )

        self.assertEqual(models, ["Qwen/Qwen3-8B"])
        client_factory.assert_called_once_with(
            api_key="sk-placeholder",
            base_url="http://127.0.0.1:8000/v1",
        )
        client.close.assert_awaited_once()

    def test_unknown_provider_is_rejected_instead_of_looking_like_an_empty_catalog(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported provider_type"):
            asyncio.run(
                ChatProviderService().list_models(
                    provider_type="typo",
                    base_url="https://example.test/v1",
                    api_key=None,
                )
            )

    def test_complete_chat_closes_openai_client(self) -> None:
        client = Mock()
        client.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))]
                    )
                )
            )
        )
        client.close = AsyncMock()

        with patch("app.services.chat_provider_service.AsyncOpenAI", return_value=client):
            answer = asyncio.run(
                ChatProviderService().complete_chat(
                    provider_type="openai-compatible",
                    base_url="https://example.test/v1",
                    api_key="test-key",
                    model_name="model-a",
                    messages=[{"role": "user", "content": "question"}],
                )
            )

        self.assertEqual(answer, "answer")
        client.close.assert_awaited_once()

    def test_stream_chat_closes_openai_client_when_consumer_stops(self) -> None:
        async def fake_stream():
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="partial", reasoning_content=None)
                    )
                ]
            )

        client = Mock()
        client.chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=fake_stream()))
        )
        client.close = AsyncMock()

        async def consume_one_event() -> str:
            generator = ChatProviderService().stream_chat_events(
                provider_type="openai-compatible",
                base_url="https://example.test/v1",
                api_key="test-key",
                model_name="model-a",
                messages=[{"role": "user", "content": "question"}],
                temperature=0.2,
                top_p=0.9,
                max_tokens=128,
            )
            event = await anext(generator)
            await generator.aclose()
            return event.text

        with patch("app.services.chat_provider_service.AsyncOpenAI", return_value=client):
            text = asyncio.run(consume_one_event())

        self.assertEqual(text, "partial")
        client.close.assert_awaited_once()

    def test_official_openai_enables_prompt_cache_key_and_stream_usage(self) -> None:
        async def fake_stream():
            yield SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=1200,
                    completion_tokens=20,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=1024),
                ),
            )

        create = AsyncMock(return_value=fake_stream())
        client = Mock(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        client.close = AsyncMock()

        async def consume():
            return [
                event
                async for event in ChatProviderService().stream_chat_events(
                    provider_type="openai-compatible",
                    base_url="https://api.openai.com/v1",
                    api_key="test-key",
                    model_name="gpt-test",
                    messages=[{"role": "user", "content": "question"}],
                    temperature=0.2,
                    top_p=0.9,
                    max_tokens=128,
                    prompt_cache_key="conversation-cache-key",
                )
            ]

        with patch("app.services.chat_provider_service.AsyncOpenAI", return_value=client):
            events = asyncio.run(consume())

        kwargs = create.await_args.kwargs
        self.assertEqual(kwargs["prompt_cache_key"], "conversation-cache-key")
        self.assertEqual(kwargs["stream_options"], {"include_usage": True})
        self.assertEqual(events[-1].data["cached_input_tokens"], 1024)

    def test_vllm_uses_cache_salt_and_stream_usage_without_openai_cache_key(self) -> None:
        async def fake_stream():
            yield SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=800,
                    completion_tokens=12,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=640),
                ),
            )

        create = AsyncMock(return_value=fake_stream())
        client = Mock(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        client.close = AsyncMock()

        async def consume():
            return [
                event
                async for event in ChatProviderService().stream_chat_events(
                    provider_type="vllm",
                    base_url="http://127.0.0.1:8000/v1",
                    api_key=None,
                    model_name="local-model",
                    messages=[{"role": "user", "content": "question"}],
                    temperature=0.2,
                    top_p=0.9,
                    max_tokens=128,
                    prompt_cache_key="conversation-cache-key",
                )
            ]

        with patch("app.services.chat_provider_service.AsyncOpenAI", return_value=client):
            events = asyncio.run(consume())
        kwargs = create.await_args.kwargs
        self.assertNotIn("prompt_cache_key", kwargs)
        self.assertEqual(kwargs["stream_options"], {"include_usage": True})
        self.assertEqual(len(kwargs["extra_body"]["cache_salt"]), 43)
        self.assertNotEqual(kwargs["extra_body"]["cache_salt"], "conversation-cache-key")
        self.assertEqual(events[-1].data["provider"], "vllm")
        self.assertEqual(events[-1].data["cached_input_tokens"], 640)
        self.assertTrue(events[-1].data["prompt_cache_request_key_sent"])

    def test_siliconflow_reads_cache_hit_usage_without_sending_unsupported_key(self) -> None:
        async def fake_stream():
            yield SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=500,
                    completion_tokens=18,
                    prompt_tokens_details=None,
                    prompt_cache_hit_tokens=320,
                ),
            )

        create = AsyncMock(return_value=fake_stream())
        client = Mock(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        client.close = AsyncMock()

        async def consume():
            return [
                event
                async for event in ChatProviderService().stream_chat_events(
                    provider_type="openai-compatible",
                    base_url="https://api.siliconflow.cn/v1",
                    api_key="test-key",
                    model_name="Qwen/Qwen3.5-35B-A3B",
                    messages=[{"role": "user", "content": "question"}],
                    temperature=0.2,
                    top_p=0.9,
                    max_tokens=128,
                    prompt_cache_key="must-not-be-forwarded",
                )
            ]

        with patch("app.services.chat_provider_service.AsyncOpenAI", return_value=client):
            events = asyncio.run(consume())
        kwargs = create.await_args.kwargs
        self.assertNotIn("prompt_cache_key", kwargs)
        self.assertNotIn("stream_options", kwargs)
        self.assertNotIn("cache_salt", kwargs["extra_body"])
        self.assertEqual(events[-1].data["provider"], "siliconflow")
        self.assertEqual(events[-1].data["cached_input_tokens"], 320)
        self.assertTrue(events[-1].data["prompt_cache_usage_available"])

    def test_generic_openai_compatible_does_not_receive_provider_cache_fields(self) -> None:
        async def fake_stream():
            if False:
                yield None

        create = AsyncMock(return_value=fake_stream())
        client = Mock(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        client.close = AsyncMock()

        async def consume():
            return [
                event
                async for event in ChatProviderService().stream_chat_events(
                    provider_type="openai-compatible",
                    base_url="https://example.test/v1",
                    api_key="test-key",
                    model_name="local-model",
                    messages=[{"role": "user", "content": "question"}],
                    temperature=0.2,
                    top_p=0.9,
                    max_tokens=128,
                    prompt_cache_key="must-not-be-forwarded",
                )
            ]

        with patch("app.services.chat_provider_service.AsyncOpenAI", return_value=client):
            asyncio.run(consume())
        kwargs = create.await_args.kwargs
        self.assertNotIn("prompt_cache_key", kwargs)
        self.assertNotIn("stream_options", kwargs)
        self.assertNotIn("cache_salt", kwargs["extra_body"])

    def test_anthropic_cache_control_marks_last_stable_message_only(self) -> None:
        payload = ChatProviderService()._build_anthropic_payload(
            model_name="claude-test",
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": "old question"},
                {"role": "assistant", "content": "old answer"},
                {"role": "user", "content": "current question"},
            ],
            temperature=0.2,
            top_p=0.9,
            max_tokens=1000,
            stream=True,
            thinking_enabled=False,
            thinking_budget=None,
            prompt_cache_breakpoint=3,
        )

        self.assertEqual(
            payload["messages"][1]["content"][-1]["cache_control"],
            {"type": "ephemeral"},
        )
        self.assertNotIn("cache_control", payload["messages"][-1]["content"][-1])


if __name__ == "__main__":
    unittest.main()

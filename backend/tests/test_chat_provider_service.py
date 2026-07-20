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


if __name__ == "__main__":
    unittest.main()

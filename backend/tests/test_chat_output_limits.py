"""只使用HTTP模拟验证Ollama输出限额与结束原因，不连接或加载本地模型。"""

from contextlib import asynccontextmanager
import json
import unittest
from unittest.mock import patch

import httpx

from app.services.chat_provider_service import ChatProviderService
from app.services.ollama_service import OllamaService


class ChatOutputLimitTest(unittest.IsolatedAsyncioTestCase):
    async def test_ollama_passes_num_predict_and_reports_length(self):
        requests = []
        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, content='{"message":{"content":"部分"},"done":false}\n'
                '{"message":{},"done":true,"done_reason":"length"}\n')
        @asynccontextmanager
        async def client(_):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as session:
                yield session
        with patch.object(OllamaService, "_http_client", client):
            events = [event async for event in ChatProviderService().stream_chat_events(
                provider_type="ollama", base_url="http://mock.invalid", api_key=None,
                model_name="fake-not-loaded", messages=[], temperature=.2, top_p=1, max_tokens=42)]
        self.assertEqual(requests[0]["options"]["num_predict"], 42)
        self.assertEqual([event.type for event in events], ["answer_delta", "provider_finish"])
        self.assertEqual(events[-1].data, {"finish_reason": "length"})

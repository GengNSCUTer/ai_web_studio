"""验证评测客户端真实使用Run资源和SSE，传输成功不替代业务完成。"""

import json
from pathlib import Path
import sys
import unittest

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from chat_sse_client import chat_stream, chat_response, iter_sse_events, sse_events
from app.services.chat_sse import encode_chat_sse


class ChatSseClientTest(unittest.TestCase):
    def test_create_then_subscribe_uses_json_and_only_get_sse(self):
        calls = []
        def respond(request):
            calls.append((request.method, request.url.path))
            if request.method == "POST":
                self.assertEqual(json.loads(request.content), {"content": "测试"})
                return httpx.Response(202, json={"id": "run"})
            self.assertEqual(request.headers["accept"], "text/event-stream")
            return httpx.Response(200, text=encode_chat_sse({"type": "answer_delta", "text": "中文"}, event_id="run:1")
                + encode_chat_sse({"type": "done"}, event_id="run:2"), headers={"content-type": "text/event-stream"})
        with httpx.Client(base_url="http://test", transport=httpx.MockTransport(respond)) as client:
            with chat_stream(client, json={"content": "测试"}) as response:
                events = list(iter_sse_events(response.iter_lines()))
        self.assertEqual(calls, [("POST", "/api/chat/runs"), ("GET", "/api/chat/runs/run/events")])
        self.assertEqual([e["type"] for e in events], ["answer_delta", "done"])
        self.assertEqual(events[0]["text"], "中文")

    def test_creation_error_does_not_attempt_subscription(self):
        calls = []
        def respond(request):
            calls.append(request.method)
            return httpx.Response(403, json={"detail": "not allowed"})
        with httpx.Client(base_url="http://test", transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                chat_response(client, json={"content": "x"})
        self.assertEqual(calls, ["POST"])

    def test_wrong_format_and_old_paths_are_rejected(self):
        def respond(request):
            return httpx.Response(202, json={"id": "r"}) if request.method == "POST" else httpx.Response(200, text='{"type":"done"}\n')
        with httpx.Client(base_url="http://test", transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(ValueError):
                chat_response(client, json={"content": "x"})
            with self.assertRaises(ValueError):
                chat_response(client, "/api/chat/events-stream", json={"content": "x"})

    def test_heartbeat_multiline_and_incomplete_frame(self):
        self.assertEqual(sse_events(': heartbeat\n\ndata: {"type":\ndata: "done"}\n\n'), [{"type": "done"}])
        self.assertEqual(sse_events('data: {"type":"done"}\n'), [])

    def test_content_newlines_are_json_data_not_frame_injection(self):
        payload = {"type": "answer_delta", "text": "正文\n\nid: fake\ndata: fake"}
        encoded = encode_chat_sse(payload, event_id="r:1")
        self.assertEqual(sse_events(encoded), [payload])
        self.assertEqual(encoded.count("\ndata:"), 1)

from __future__ import annotations

import unittest

from app.services.tools.mcp_client import McpHttpClient


class McpHttpClientTest(unittest.TestCase):
    def test_parse_json_response(self) -> None:
        parsed = McpHttpClient._parse_response_text('{"jsonrpc":"2.0","id":1,"result":{"ok":true}}')

        self.assertEqual(parsed["result"]["ok"], True)

    def test_parse_sse_response(self) -> None:
        parsed = McpHttpClient._parse_response_text(
            'event: message\n'
            'data: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"tavily_search"}]}}\n\n'
        )

        self.assertEqual(parsed["result"]["tools"][0]["name"], "tavily_search")


if __name__ == "__main__":
    unittest.main()

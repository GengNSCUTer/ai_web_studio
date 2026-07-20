from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from app.models.tool_config import McpTool
from app.services.tools.mcp_security import (
    McpEndpointPolicyError,
    apply_remote_tool_security_policy,
    enforce_mcp_endpoint_target_policy,
    validate_mcp_endpoint_url,
)


class McpSecurityPolicyTest(unittest.TestCase):
    @staticmethod
    def _tool(**overrides) -> McpTool:
        values = {
            "server_id": "server-1",
            "raw_name": "weather",
            "tool_key": "mcp.demo.weather",
            "display_name": "Weather",
            "input_schema_json": '{"type":"object"}',
            "output_schema_json": "{}",
            "annotations_json": '{"readOnlyHint":true}',
            "risk_level": "low",
            "read_only": True,
            "risk_reviewed": False,
            "is_enabled": True,
        }
        values.update(overrides)
        return McpTool(**values)

    def test_remote_read_only_hint_does_not_auto_approve_unreviewed_tool(self) -> None:
        tool = self._tool()

        apply_remote_tool_security_policy(
            tool=tool,
            input_schema_json='{"type":"object"}',
            output_schema_json="{}",
            annotations_json='{"readOnlyHint":true}',
        )

        self.assertFalse(tool.risk_reviewed)
        self.assertFalse(tool.read_only)
        self.assertEqual(tool.risk_level, "high")
        self.assertFalse(tool.is_enabled)

    def test_reviewed_tool_keeps_local_policy_when_remote_metadata_is_unchanged(self) -> None:
        tool = self._tool(
            risk_reviewed=True,
            last_seen_at=datetime.now(timezone.utc),
        )

        apply_remote_tool_security_policy(
            tool=tool,
            input_schema_json='{"type":"object"}',
            output_schema_json="{}",
            annotations_json='{"readOnlyHint":true}',
        )

        self.assertTrue(tool.risk_reviewed)
        self.assertTrue(tool.read_only)
        self.assertEqual(tool.risk_level, "low")
        self.assertTrue(tool.is_enabled)

    def test_schema_change_invalidates_previous_review(self) -> None:
        tool = self._tool(
            risk_reviewed=True,
            last_seen_at=datetime.now(timezone.utc),
        )

        apply_remote_tool_security_policy(
            tool=tool,
            input_schema_json='{"type":"object","required":["city"]}',
            output_schema_json="{}",
            annotations_json='{"readOnlyHint":true}',
        )

        self.assertFalse(tool.risk_reviewed)
        self.assertFalse(tool.read_only)
        self.assertEqual(tool.risk_level, "high")
        self.assertFalse(tool.is_enabled)

    def test_endpoint_url_rejects_non_http_scheme_and_embedded_password(self) -> None:
        with self.assertRaises(McpEndpointPolicyError):
            validate_mcp_endpoint_url("file:///etc/passwd")
        with self.assertRaises(McpEndpointPolicyError):
            validate_mcp_endpoint_url("https://user:password@example.com/mcp")
        with self.assertRaises(McpEndpointPolicyError):
            validate_mcp_endpoint_url("https://example.com:99999/mcp")

    def test_endpoint_target_rejects_private_dns_result(self) -> None:
        async def run_test() -> None:
            records = [(2, 1, 6, "", ("169.254.169.254", 443))]
            with patch("app.services.tools.mcp_security.socket.getaddrinfo", return_value=records):
                with self.assertRaises(McpEndpointPolicyError):
                    await enforce_mcp_endpoint_target_policy(
                        "https://metadata.example/mcp",
                        allow_private=False,
                    )

        import asyncio

        asyncio.run(run_test())

    def test_endpoint_target_allows_globally_routable_dns_result(self) -> None:
        async def run_test() -> None:
            records = [(2, 1, 6, "", ("8.8.8.8", 443))]
            with patch("app.services.tools.mcp_security.socket.getaddrinfo", return_value=records):
                await enforce_mcp_endpoint_target_policy(
                    "https://public.example/mcp",
                    allow_private=False,
                )

        import asyncio

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()

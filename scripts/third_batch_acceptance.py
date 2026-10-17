"""运行第三批固定业务验收，不调用收费 Provider 或本地模型。

三条验收分别覆盖：带来源的研究回答链、Diff/Approval 文件修改链、
Durable Worker 完成后回写原会话链。测试内部使用本地 HTTP MCP 和内存数据库，
用来稳定验证业务边界；真实在线模型烟测仍单独运行，避免把外部服务波动混入回归。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


CASES = (
    # 研究回答：工具结果带来源并进入上下文，独立分支部分失败仍可继续。
    "tests.test_tool_integration.ToolIntegrationTest.test_external_context_runs_complete_no_auth_mcp_success_chain",
    "tests.test_tool_integration.ToolIntegrationTest.test_external_context_keeps_independent_success_when_one_branch_is_invalid",
    # 文件修改：先生成 Diff，再经过确认挑战和 revision CAS 才能写入。
    "tests.test_agent_runtime_service.AgentRuntimeServiceTest.test_approval_token_and_revision_cas_make_apply_idempotent",
    # 后台审阅：Worker 结束后只生成一条原会话结果消息，失败也形成明确终态。
    "tests.test_durable_tool_runtime.DurableToolRuntimeTest.test_terminal_success_projects_one_assistant_message_to_conversation",
    "tests.test_durable_tool_runtime.DurableToolRuntimeTest.test_terminal_failure_projects_failed_message_with_error",
)


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(loader.loadTestsFromName(case) for case in CASES)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(
        "第三批固定业务验收："
        f"总数={result.testsRun}，失败={len(result.failures)}，错误={len(result.errors)}，"
        f"跳过={len(result.skipped)}"
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

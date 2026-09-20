from __future__ import annotations

import unittest

from app.services.tools.run_policy import ToolRunBudget, resolve_tool_run_policy


class ToolRunPolicyTest(unittest.TestCase):
    def test_quick_chat_preserves_five_round_compatibility_baseline(self) -> None:
        policy = resolve_tool_run_policy()

        self.assertEqual(policy.mode, "quick_chat")
        self.assertEqual(policy.max_planning_rounds, 5)
        self.assertEqual(policy.max_replans, 4)
        self.assertEqual(policy.max_total_tool_calls, 10)
        self.assertEqual(policy.max_parallel_calls, 2)

    def test_guided_research_is_explicit_and_has_larger_finite_budget(self) -> None:
        quick = resolve_tool_run_policy("quick_chat")
        research = resolve_tool_run_policy("guided_research")

        self.assertEqual(research.max_planning_rounds, 8)
        self.assertGreater(research.max_total_tool_calls, quick.max_total_tool_calls)
        self.assertGreater(research.max_wall_clock_seconds, quick.max_wall_clock_seconds)
        self.assertTrue(research.synchronous)

    def test_unknown_mode_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported tool run mode"):
            resolve_tool_run_policy("unbounded_agent")

    def test_budget_counts_attempts_without_allowing_overdraw(self) -> None:
        policy = resolve_tool_run_policy("edit_proposal")
        budget = ToolRunBudget(policy=policy)

        budget.begin_round()
        budget.record_attempted_tool_calls(100)

        self.assertEqual(budget.tool_calls_used, policy.max_total_tool_calls)
        self.assertEqual(budget.remaining_tool_calls, 0)
        self.assertEqual(budget.terminal_limit_reason(), "tool_call_budget_exhausted")

    def test_replan_budget_is_bounded_by_round_and_replan_limits(self) -> None:
        policy = resolve_tool_run_policy("quick_chat")
        budget = ToolRunBudget(policy=policy)

        for _ in range(4):
            budget.begin_round()
            self.assertTrue(budget.can_replan())
            budget.consume_replan()

        budget.begin_round()
        self.assertFalse(budget.can_replan())
        self.assertEqual(budget.replan_limit_reason(), "tool_round_budget_exhausted")


if __name__ == "__main__":
    unittest.main()

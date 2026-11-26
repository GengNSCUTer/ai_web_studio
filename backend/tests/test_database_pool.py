import unittest

from app.core.database_pool import resolve_database_pool_budget


class DatabasePoolTest(unittest.TestCase):
    def test_roles_have_distinct_connection_budgets(self):
        api = resolve_database_pool_budget("api", {})
        chat = resolve_database_pool_budget("chat-worker", {})
        memory = resolve_database_pool_budget("memory-worker", {})
        self.assertEqual((api.size, api.overflow), (5, 3))
        self.assertEqual((api.graph_size, chat.graph_size, memory.graph_size), (2, 4, 1))
        self.assertEqual((memory.size, memory.overflow), (2, 1))

    def test_role_override_takes_precedence_and_async_overflow_is_disabled(self):
        budget = resolve_database_pool_budget("chat-worker", {
            "POSTGRES_POOL_SIZE": "4", "POSTGRES_CHAT_WORKER_POOL_SIZE": "6",
            "POSTGRES_MAX_OVERFLOW": "0", "POSTGRES_ASYNC_POOL_SIZE": "3",
            "POSTGRES_GRAPH_POOL_SIZE": "5", "POSTGRES_CHAT_WORKER_GRAPH_POOL_SIZE": "3",
            "POSTGRES_APPROVAL_POOL_SIZE": "2", "POSTGRES_POOL_TIMEOUT_SECONDS": "7",
        })
        self.assertEqual((budget.size, budget.overflow, budget.graph_size, budget.approval_size), (6, 0, 3, 2))
        options = budget.sqlalchemy_options(asynchronous=True)
        self.assertEqual((options["pool_size"], options["max_overflow"], options["pool_timeout"]), (3, 0, 7))

    def test_unbounded_or_invalid_limits_fail_fast(self):
        for key, value in (("POOL_SIZE", "0"), ("MAX_OVERFLOW", "-1"),
                           ("POOL_TIMEOUT_SECONDS", "0"), ("GRAPH_POOL_SIZE", "invalid")):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, f"POSTGRES_{key}"):
                resolve_database_pool_budget("api", {f"POSTGRES_{key}": value})

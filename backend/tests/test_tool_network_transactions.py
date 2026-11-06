"""真实SQLAlchemy事务边界：等待Planner/适配器时不占连接，不偷提交业务写入。"""

import asyncio
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.core.database import Base
from app.models.message import Message
from app.models.project import Project
from app.services.external_context_service import ExternalContextService
from app.services.tools.db_boundary import release_read_transaction
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import ExternalSource, PlannedToolCall
from backend.tests.test_tool_deadline import DeadlineExecutor, make_call, make_plan
from backend.tests.test_sync_tool_graph import SequencePlanner


class ToolNetworkTransactionsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(self.engine, expire_on_commit=True)()
        self.db.add(Project(id="project", user_id="user", name="原始项目"))
        self.db.add(Message(id="history", conversation_id="conversation", role="user", content="上次提到保留17天"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    async def test_each_planner_round_releases_policy_reads_and_freezes_expiring_history(self):
        db, test = self.db, self
        first = make_plan([make_call("a")], continue_planning=True)
        second = make_plan([make_call("b")])
        class Planner(SequencePlanner):
            async def plan(self, **kwargs):
                test.assertFalse(db.in_transaction())
                test.assertEqual(kwargs["recent_messages"][0]["content"], "上次提到保留17天")
                test.assertFalse(db.in_transaction())
                return await super().plan(**kwargs)
        class Executor(DeadlineExecutor):
            async def execute(self, call):
                db.execute(text("SELECT 1"))
                return await super().execute(call)
        planner = Planner([first, second])
        executor = Executor(slow=set())
        service = ExternalContextService(db=db, planner=planner, executor=executor)
        history = self.db.get(Message, "history")
        await service.build_context(query="汇总测试查询", enabled=True, max_chars=4000, recent_messages=[history])
        self.assertEqual(planner.calls, 2)

    async def test_real_executor_releases_permission_lookup_before_adapter_await(self):
        db, test = self.db, self
        class Adapter:
            async def run(self, **kwargs):
                test.assertFalse(db.in_transaction())
                await asyncio.sleep(0)
                test.assertFalse(db.in_transaction())
                return [ExternalSource(source_type="workspace_file_read", provider="workspace", title="文档",
                    display_text="这是已授权的测试内容", metadata={"file_id": "file", "raw": {"content": "测试内容"}})], {}
        executor = ToolExecutor(db=db, user_id="user", project_id="project", adapter_runner=Adapter())
        result, _ = await executor.execute(PlannedToolCall(call_id="read", tool_key="workspace.files.read",
            provider="workspace", category="workspace_file", display_name="读取", confidence=1, reason="测试",
            arguments={"file_id": "file"}))
        self.assertNotEqual(result.status, "skipped")
        self.assertFalse(db.in_transaction())

    def test_pending_changes_are_not_committed_by_read_release(self):
        project = self.db.get(Project, "project")
        project.name = "尚未保存的新名称"
        with self.assertRaisesRegex(RuntimeError, "未保存"):
            release_read_transaction(self.db)
        self.assertIn(project, self.db.dirty)
        self.db.rollback()
        self.assertEqual(self.db.get(Project, "project").name, "原始项目")


if __name__ == "__main__":
    unittest.main()

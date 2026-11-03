"""真实 PostgreSQL 并发确认/领取验证，使用独立数据库，不触碰账号业务数据。"""

from concurrent.futures import ThreadPoolExecutor
import asyncio
import os
import unittest
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select, func, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.migrations import upgrade_database
from app.models.user import User
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.agent_runtime import AgentRun, AgentOutboxEvent, AgentApproval
from app.schemas.document_task import DocumentTaskCreate
from app.services.document_task_service import DocumentTaskService, KIND
from app.services.durable_tool_runtime import DurableToolRunService, DurableToolWorker
from app.services.agent_runtime_service import AgentRuntimeService


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要真实 PostgreSQL 条件环境")
class DocumentTasksPostgresTest(unittest.TestCase):
    def setUp(self):
        url = make_url(os.environ["TEST_POSTGRES_URL"])
        self.name = "aiws_doc_test_" + uuid4().hex[:12]
        self.admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with self.admin.connect() as db: db.execute(text(f'create database "{self.name}"'))
        self.engine = create_engine(url.set(database=self.name))
        upgrade_database(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as db:
            db.add(User(id="doc-user", email="doc-concurrency@example.test")); db.flush()
            db.add(Project(id="doc-project", user_id="doc-user", name="concurrent")); db.flush()
            db.add(ProjectFile(id="doc-file", user_id="doc-user", project_id="doc-project", file_name="fact.md",
                               kind="text", storage_key="doc-user/fact.md", parsed_text="确定性并发测试资料"))
            db.commit()

    def tearDown(self):
        self.engine.dispose()
        with self.admin.connect() as db: db.execute(text(f'drop database "{self.name}" with (force)'))
        self.admin.dispose()

    def create(self, key):
        with self.sessions() as db:
            return DocumentTaskService(db).create("doc-user", DocumentTaskCreate(
                project_id="doc-project", file_ids=["doc-file"], idempotency_key=key))["id"]

    def test_eight_concurrent_same_requests_create_one_run_and_event(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            runs = list(pool.map(lambda _: self.create("same-request"), range(8)))
        self.assertEqual(len(set(runs)), 1)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(AgentRun)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(AgentOutboxEvent)), 1)

    def test_multiple_workers_claim_different_runs_without_duplicates(self):
        runs = {self.create("first"), self.create("second")}
        def claim(index):
            with self.sessions() as db:
                claimed = DurableToolRunService(db).claim_next(owner=f"worker-{index}", runtime_kinds=(KIND,))
                return claimed.outbox_event_id if claimed else None
        with ThreadPoolExecutor(max_workers=8) as pool:
            claimed = [item for item in pool.map(claim, range(8)) if item]
        self.assertEqual(len(claimed), 2)
        self.assertEqual(len(set(claimed)), 2)
        with self.sessions() as db:
            self.assertEqual(set(db.scalars(select(AgentOutboxEvent.run_id).where(AgentOutboxEvent.id.in_(claimed)))), runs)

    def test_concurrent_optional_save_and_confirm_enqueue_once(self):
        run_id = self.create("optional-save")
        async def generate():
            worker = DurableToolWorker(session_factory=self.sessions)
            for _ in range(3):
                self.assertTrue(await worker.run_once())
        with patch("app.services.document_task_worker.ChatProviderService.complete_chat", new_callable=AsyncMock,
                   return_value="确定性草稿，不调用在线模型"):
            asyncio.run(generate())
        def prepare(_):
            with self.sessions() as db:
                return DocumentTaskService(db).request_workspace_save("doc-user", run_id)["approvals"][0]["id"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            approvals = list(pool.map(prepare, range(8)))
        self.assertEqual(len(set(approvals)), 1)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(AgentApproval)), 1)
            token = AgentRuntimeService(db).issue_approval_challenge(approval_id=approvals[0], user_id="doc-user")
        def confirm(_):
            with self.sessions() as db:
                return DocumentTaskService(db).approve("doc-user", approvals[0], token)["id"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(set(pool.map(confirm, range(8))), {run_id})
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(AgentOutboxEvent).where(AgentOutboxEvent.status == "pending")), 1)

"""验证文件工具的非阻塞执行、短事务、稳定分页和来源版本边界。"""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.models import FileRevision, Project, ProjectFile, User
from app.services.external_context_service import ExternalContextService
from app.services.tools.catalog import ToolCatalog
from app.services.tools.executor import ToolExecutor
from app.services.tools.providers.workspace_files import WorkspaceFileToolProvider
from app.services.tools.providers.workspace_file_paging import WorkspaceFileCursor
from app.services.tools.schemas import ToolExecutionFeedbackError
from backend.tests.test_workspace_file_tools import build_call


class WorkspaceFileExecutionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # 使用磁盘临时库，让独立线程获得独立连接；不把内存 SQLite 的连接共享当成并发保障。
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{self.directory.name}/files.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine, tables=[ProjectFile.__table__, FileRevision.__table__])
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)
        self.db = self.sessions()
        self.time = datetime(2026, 10, 9, 8, tzinfo=timezone.utc)
        self.db.add_all([self.file("old-target", "archive.md", "Durable retention period: 17 days.", self.time),
                         self.file("foreign-user", "archive.md", "foreign keyword", self.time, user="other"),
                         self.file("foreign-project", "archive.md", "foreign keyword", self.time, project="other")])
        self.db.commit()
        self.provider = WorkspaceFileToolProvider(db=self.db, user_id="owner", project_id="current")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.directory.cleanup()

    @staticmethod
    def file(identifier, name, content, created_at, *, user="owner", project="current"):
        return ProjectFile(id=identifier, file_name=name, parsed_text=content, user_id=user,
                           project_id=project, storage_key="opaque-test-key", kind="text", created_at=created_at)

    def add_recent_files(self, count=125):
        self.db.add_all([self.file(f"recent-{index:03}", f"note-{index}.md", "Ordinary meeting notes.",
                                   self.time + timedelta(seconds=index + 1)) for index in range(count)])
        self.db.commit()

    async def call(self, key, arguments):
        return await self.provider.run(call=build_call(key, arguments))

    async def test_slow_database_does_not_block_event_loop(self):
        started, release = threading.Event(), threading.Event()

        def pause_query(_connection, _cursor, statement, _params, _context, _many):
            if statement.lstrip().startswith("SELECT"):
                started.set()
                release.wait(2)

        event.listen(self.engine, "before_cursor_execute", pause_query)
        task = asyncio.create_task(self.call("workspace.files.read", {"file_id": "old-target"}))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            # 真正的数据库查询仍在等待时，事件循环上的其它协程应继续执行。
            ticks = 0
            for _ in range(3):
                await asyncio.sleep(0.01)
                ticks += 1
            self.assertEqual(ticks, 3)
            self.assertFalse(task.done())
        finally:
            release.set()
            await task
            event.remove(self.engine, "before_cursor_execute", pause_query)
        self.assertEqual(self.engine.pool.checkedout(), 0)

    async def test_search_scoring_releases_database_connection(self):
        original_score = WorkspaceFileToolProvider._score
        connection_counts = []

        def inspect_score(terms, value):
            connection_counts.append(self.engine.pool.checkedout())
            return original_score(terms, value)

        with patch.object(WorkspaceFileToolProvider, "_score", side_effect=inspect_score):
            await self.call("workspace.files.search", {"query": "unmatched"})
        self.assertEqual(connection_counts, [0])

    async def test_tool_owns_session_and_does_not_commit_callers_pending_changes(self):
        self.db.add(self.file("pending", "pending.md", "not committed", self.time))
        sources, _ = await self.call("workspace.files.read", {"file_id": "old-target"})
        self.assertIn("17 days", sources[0].display_text)
        with self.sessions() as check:
            self.assertIsNone(check.get(ProjectFile, "pending"))
            self.assertIsNotNone(check.get(FileRevision, sources[0].metadata["revision_id"]))
        self.assertTrue(self.db.new)
        self.db.rollback()

    async def test_cancellation_rolls_back_and_closes_worker_session_after_query_returns(self):
        started, release, closed = threading.Event(), threading.Event(), threading.Event()
        original_close = Session.close

        class TrackedSession(Session):
            def close(session):
                original_close(session)
                closed.set()

        def pause_query(_connection, _cursor, statement, _params, _context, _many):
            if statement.lstrip().startswith("SELECT"):
                started.set()
                release.wait(2)

        provider = WorkspaceFileToolProvider(db=self.db, user_id="owner", project_id="current",
            session_factory=sessionmaker(bind=self.engine, class_=TrackedSession, autoflush=False))
        event.listen(self.engine, "before_cursor_execute", pause_query)
        task = asyncio.create_task(provider.run(call=build_call("workspace.files.read", {"file_id": "old-target"})))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            release.set()
            self.assertTrue(await asyncio.to_thread(closed.wait, 2))
        finally:
            release.set()
            event.remove(self.engine, "before_cursor_execute", pause_query)
        self.assertEqual(self.engine.pool.checkedout(), 0)
        with self.sessions() as check:
            self.assertEqual(list(check.scalars(select(FileRevision)).all()), [])

    async def test_old_file_is_reached_by_continuation_and_scope_is_disclosed(self):
        self.add_recent_files()
        sources, page = await self.call("workspace.files.search", {"query": "durable"})
        self.assertEqual(page["scanned_files"], 120)
        self.assertEqual(page["matched_files"], 0)
        self.assertTrue(page["has_more"])
        self.assertFalse(page["coverage_complete"])
        self.assertIn("当前已检查范围", sources[0].display_text)
        self.assertIn("未检查", sources[0].display_text)
        self.assertLessEqual(len(page["next_cursor"]), WorkspaceFileCursor.MAX_LENGTH)
        second, last_page = await self.call("workspace.files.search", {"query": "durable", "cursor": page["next_cursor"]})
        self.assertEqual(second[0].metadata["file_id"], "old-target")
        self.assertEqual(last_page["scanned_files"], 6)
        self.assertFalse(last_page["has_more"])

    async def test_exact_name_and_file_id_find_old_file_without_scanning_recent_files(self):
        self.add_recent_files()
        for filters in ({"file_name": "archive.md"}, {"file_id": "old-target"}):
            sources, page = await self.call("workspace.files.search", {"query": "durable", **filters})
            self.assertEqual(page["scanned_files"], 1)
            self.assertEqual(sources[0].metadata["file_id"], "old-target")
            self.assertFalse(page["has_more"])
        listed, page = await self.call("workspace.files.list", {"file_name": "archive.md"})
        self.assertEqual([record["file_id"] for record in listed[0].metadata["raw"]["files"]], ["old-target"])

    async def test_identical_empty_pages_keep_distinct_continuation_observations(self):
        self.add_recent_files(245)
        first, first_page = await self.call("workspace.files.search", {"query": "durable"})
        second, second_page = await self.call("workspace.files.search", {"query": "durable", "cursor": first_page["next_cursor"]})
        self.assertEqual(first[0].display_text, second[0].display_text)
        combined = []
        ExternalContextService._merge_sources(combined, first)
        newly_added, duplicate_count = ExternalContextService._merge_sources(combined, second)
        self.assertEqual(duplicate_count, 0)
        self.assertEqual(len(newly_added), 1)
        self.assertNotEqual(first_page["next_cursor"], second_page["next_cursor"])
        third, _ = await self.call("workspace.files.search", {"query": "durable", "cursor": second_page["next_cursor"]})
        self.assertEqual(third[0].metadata["file_id"], "old-target")

    async def test_cursor_rejects_tampering_and_changed_scope_before_query(self):
        self.add_recent_files()
        _, page = await self.call("workspace.files.search", {"query": "durable"})
        cursor = page["next_cursor"]
        for arguments in ({"query": "different", "cursor": cursor},
                          {"query": "durable", "file_name": "archive.md", "cursor": cursor},
                          {"query": "durable", "cursor": cursor[:-1] + ("A" if cursor[-1] != "A" else "B")},
                          {"query": "durable", "cursor": "invalid"}):
            with self.assertRaisesRegex(ToolExecutionFeedbackError, "分页位置无效"):
                await self.call("workspace.files.search", arguments)
        for user, project in (("other", "current"), ("owner", "other")):
            provider = WorkspaceFileToolProvider(db=self.db, user_id=user, project_id=project)
            with self.assertRaisesRegex(ToolExecutionFeedbackError, "分页位置无效"):
                await provider.run(call=build_call("workspace.files.search", {"query": "durable", "cursor": cursor}))

    async def test_equal_timestamps_and_new_files_do_not_shift_continuation(self):
        self.db.add_all([self.file(f"tie-{index:03}", f"tie-{index}.md", "text", self.time) for index in range(35)])
        self.db.commit()
        first, page = await self.call("workspace.files.list", {})
        first_ids = {item["file_id"] for item in first[0].metadata["raw"]["files"]}
        self.db.add(self.file("newer", "new.md", "text", self.time + timedelta(days=1)))
        self.db.commit()
        second, last = await self.call("workspace.files.list", {"cursor": page["next_cursor"]})
        second_ids = {item["file_id"] for item in second[0].metadata["raw"]["files"]}
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(len(first_ids | second_ids), 36)
        self.assertNotIn("newer", second_ids)
        self.assertFalse(last["has_more"])

    async def test_sensitive_only_page_does_not_hide_remaining_safe_files(self):
        self.db.add_all([self.file(f"env-{index:03}", f".env.{index}", "hidden", self.time + timedelta(seconds=index + 1))
                         for index in range(120)])
        self.db.commit()
        sources, page = await self.call("workspace.files.search", {"query": "durable"})
        self.assertTrue(page["has_more"])
        self.assertEqual(page["scanned_files"], 0)
        self.assertNotIn(".env", str(sources))
        second, _ = await self.call("workspace.files.search", {"query": "durable", "cursor": page["next_cursor"]})
        self.assertEqual(second[0].metadata["file_id"], "old-target")

    async def test_returned_match_limit_is_separate_from_scan_coverage(self):
        self.db.add_all([self.file(f"match-{index}", f"matching-{index}.md", "Durable", self.time) for index in range(6)])
        self.db.commit()
        sources, page = await self.call("workspace.files.search", {"query": "durable"})
        self.assertEqual(len(sources), 5)
        self.assertEqual(page["matched_files_total"], 7)
        self.assertTrue(page["results_truncated"])
        self.assertTrue(page["coverage_complete"])
        self.assertIn("仅返回", sources[0].display_text)

    async def test_paging_reaches_planner_and_final_prompt_through_real_executor(self):
        self.add_recent_files()

        class Allowed:
            def is_tool_enabled_for_workspace(self, **kwargs):
                return True

        executor = ToolExecutor(db=self.db, user_id="owner", project_id="current", credential_resolver=Allowed())
        result, _ = await executor.execute(build_call("workspace.files.search", {"query": "durable"}))
        self.assertEqual(result.quality_status, "valid")
        self.assertEqual(result.result_semantics, "empty_answer")
        observations = ExternalContextService._build_observations(round_index=1, sources=result.sources, registry=ToolCatalog())
        facts = observations[0]["metadata"]
        cursor = result.sources[0].metadata["next_cursor"]
        self.assertEqual(facts["next_cursor"], cursor)
        self.assertEqual(facts["has_more"].lower(), "true")
        self.assertNotIn("raw", facts)
        final_text = ExternalContextService().assembler.format_sources_for_prompt(result.sources, max_chars=2000)
        self.assertIn("未检查", final_text)
        second, _ = await executor.execute(build_call("workspace.files.search", {"query": "durable", "cursor": cursor}))
        self.assertEqual(second.quality_status, "valid")
        self.assertEqual(second.sources[0].metadata["file_id"], "old-target")

    async def test_sql_wildcards_in_exact_file_name_are_literal(self):
        self.db.add(self.file("literal", "100%_report.md", "durable", self.time))
        self.db.commit()
        sources, page = await self.call("workspace.files.list", {"file_name": "100%_report.md"})
        self.assertEqual(page["files_count"], 1)
        self.assertEqual(sources[0].metadata["raw"]["files"][0]["file_id"], "literal")
        _, missing = await self.call("workspace.files.list", {"file_name": "%report%"})
        self.assertEqual(missing["files_count"], 0)


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需配置 PostgreSQL 条件集成环境")
class WorkspaceFilePostgresTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["TEST_POSTGRES_URL"])
        cls.name = f"aiws_file_tools_test_{uuid4().hex}"
        cls.admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with cls.admin.connect() as connection:
            connection.execute(text(f'create database "{cls.name}"'))
        cls.engine = create_engine(url.set(database=cls.name), pool_pre_ping=True)
        with cls.engine.begin() as connection:
            connection.execute(text("create extension if not exists vector"))
        Base.metadata.create_all(cls.engine)
        cls.sessions = sessionmaker(bind=cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        with cls.admin.connect() as connection:
            connection.execute(text(f'drop database "{cls.name}" with (force)'))
        cls.admin.dispose()

    def setUp(self):
        self.db = self.sessions()
        suffix = uuid4().hex
        user = User(email=f"file-{suffix}@example.com", username=f"file-{suffix}")
        self.db.add(user)
        self.db.flush()
        project = Project(user_id=user.id, name="isolated file test", default_model="test-model")
        self.db.add(project)
        self.db.flush()
        file = ProjectFile(user_id=user.id, project_id=project.id, kind="text", file_name="note.md",
                           storage_key="test", parsed_text="Durable retained evidence.")
        self.db.add(file)
        self.db.commit()
        self.file_id = file.id
        self.provider = WorkspaceFileToolProvider(db=self.db, user_id=user.id, project_id=project.id)

    def tearDown(self):
        self.db.close()

    async def test_concurrent_baseline_reads_use_one_persistent_revision(self):
        results = await asyncio.gather(*[self.provider.run(call=build_call("workspace.files.read", {"file_id": self.file_id}))
                                         for _ in range(4)])
        revision_ids = {sources[0].metadata["revision_id"] for sources, _ in results}
        self.assertEqual(len(revision_ids), 1)
        revisions = self.db.scalars(select(FileRevision).where(FileRevision.project_file_id == self.file_id)).all()
        self.assertEqual(len(revisions), 1)
        self.db.rollback()
        self.assertEqual(self.engine.pool.checkedout(), 0)

    async def test_two_concurrent_searches_share_no_session_and_keep_provenance(self):
        searched, read = await asyncio.gather(
            self.provider.run(call=build_call("workspace.files.search", {"query": "durable"})),
            self.provider.run(call=build_call("workspace.files.read", {"file_id": self.file_id})))
        self.assertEqual(searched[0][0].metadata["revision_id"], read[0][0].metadata["revision_id"])
        self.assertEqual(self.engine.pool.checkedout(), 0)


if __name__ == "__main__":
    unittest.main()

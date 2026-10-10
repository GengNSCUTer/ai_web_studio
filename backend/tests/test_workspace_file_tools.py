from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.project_file import ProjectFile
from app.services.tools.catalog import ToolCatalog
from app.services.tools.bindings import ToolResultBindingResolver
from app.services.tools.executor import ToolExecutor
from app.services.tools.providers.workspace_files import WorkspaceFileToolProvider
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolResultBinding
from app.services.tools.schemas import ToolExecutionFeedbackError
from app.services.external_context_service import ExternalContextService
from app.services.agent_runtime_service import AgentRuntimeService


def build_call(tool_key: str, arguments: dict) -> PlannedToolCall:
    return PlannedToolCall(
        call_id=f"call-{tool_key.rsplit('.', 1)[-1]}",
        tool_key=tool_key,
        provider="workspace",
        category="workspace_file",
        display_name=tool_key,
        confidence=1.0,
        reason="test workspace file isolation",
        arguments=arguments,
    )


class WorkspaceFileToolProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.engine = engine
        self.SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()
        self.db.add_all(
            [
                ProjectFile(
                    id="file-current",
                    project_id="project-current",
                    user_id="user-1",
                    kind="file",
                    file_name="agent-design.md",
                    mime_type="text/markdown",
                    file_size=120,
                    storage_key="user-1/private-agent-design.md",
                    parsed_text="Architecture\nDurable checkpoint and tool approval design.\nFinal line.",
                ),
                ProjectFile(
                    id="file-other-project",
                    project_id="project-other",
                    user_id="user-1",
                    kind="file",
                    file_name="other-project.md",
                    mime_type="text/markdown",
                    file_size=100,
                    storage_key="user-1/other-project.md",
                    parsed_text="This must not be visible from another project.",
                ),
                ProjectFile(
                    id="file-other-user",
                    project_id="project-current",
                    user_id="user-2",
                    kind="file",
                    file_name="secret.md",
                    mime_type="text/markdown",
                    file_size=100,
                    storage_key="user-2/secret.md",
                    parsed_text="This must never be visible to user-1.",
                ),
            ]
        )
        self.db.commit()
        self.provider = WorkspaceFileToolProvider(
            db=self.db,
            user_id="user-1",
            project_id="project-current",
        )

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_list_is_scoped_and_never_exposes_storage_key(self) -> None:
        sources, metadata = asyncio.run(self.provider.run(call=build_call("workspace.files.list", {})))

        self.assertEqual(metadata["files_count"], 1)
        self.assertEqual(len(sources), 1)
        self.assertIn("file-current", sources[0].display_text)
        self.assertNotIn("file-other-project", sources[0].display_text)
        self.assertNotIn("file-other-user", sources[0].display_text)
        self.assertNotIn("private-agent-design", str(sources[0].metadata))

    def test_search_and_read_use_opaque_file_id_and_bounded_lines(self) -> None:
        sources, metadata = asyncio.run(
            self.provider.run(call=build_call("workspace.files.search", {"query": "durable checkpoint"}))
        )

        self.assertEqual(metadata["matched_files"], 1)
        self.assertEqual(sources[0].metadata["file_id"], "file-current")
        self.assertIn("checkpoint", sources[0].display_text)

        read_sources, read_metadata = asyncio.run(
            self.provider.run(
                call=build_call(
                    "workspace.files.read",
                    {"file_id": "file-current", "start_line": 2, "max_lines": 1},
                )
            )
        )
        self.assertEqual(read_metadata["line_start"], 2)
        self.assertEqual(read_metadata["line_end"], 2)
        self.assertEqual(read_sources[0].display_text, "2: Durable checkpoint and tool approval design.")
        self.assertNotIn("storage_key", str(read_sources[0].metadata))

    def test_file_source_binds_current_revision_and_rejects_stale_follow_up(self) -> None:
        read_sources, _ = asyncio.run(
            self.provider.run(call=build_call("workspace.files.read", {"file_id": "file-current"}))
        )
        provenance = read_sources[0].metadata
        revision_id = provenance["revision_id"]
        self.assertEqual(provenance["file_id"], "file-current")
        self.assertEqual(provenance["revision_number"], 1)
        self.assertEqual(provenance["access_scope"], "current_user_current_project")
        self.assertEqual(read_sources[0].metadata["raw"]["revision_id"], revision_id)

        # Provider 不接收模型声称的工具身份；正式路径由执行器权威写入。
        # 此处显式模拟该可信覆盖，验证后续的受限事实投影而非放宽 Provider。
        bound_source = replace(
            read_sources[0],
            metadata={
                **read_sources[0].metadata,
                "tool_key": "workspace.files.read",
                "call_id": "read-current",
            },
        )
        observations = ExternalContextService._build_observations(
            round_index=1,
            sources=[bound_source],
            registry=ToolCatalog(),
        )
        self.assertEqual(observations[0]["metadata"]["revision_id"], revision_id)
        self.assertNotIn("content_hash", observations[0]["metadata"])
        self.assertNotIn("access_scope", observations[0]["metadata"])

        definition = ToolCatalog().get_or_none("workspace.files.propose_edit")
        preview_call = build_call(
            "workspace.files.propose_edit",
            {
                "file_id": "file-current",
                "old_string": "Durable checkpoint",
                "new_string": "Durable Agent checkpoint",
            },
        )
        preview_call.depends_on = ["read-current"]
        preview_call.result_bindings = [
            ToolResultBinding(
                source_call_id="read-current",
                source_path="/sources/0/metadata/raw/revision_id",
                target_argument="expected_revision_id",
            )
        ]
        bound_call, _ = ToolResultBindingResolver().resolve(
            call=preview_call,
            sources_by_call_id={"read-current": [bound_source]},
            definition=definition,
        )
        self.assertEqual(bound_call.arguments["expected_revision_id"], revision_id)

        current = self.db.get(ProjectFile, "file-current")
        current.parsed_text = "Architecture\nNew content after a concurrent edit.\nFinal line."
        self.db.commit()
        with self.assertRaisesRegex(ToolExecutionFeedbackError, "文件版本已变化"):
            asyncio.run(
                self.provider.run(
                    call=build_call(
                        "workspace.files.read",
                        {"file_id": "file-current", "expected_revision_id": revision_id},
                    )
                )
            )
        with self.assertRaisesRegex(ToolExecutionFeedbackError, "文件版本已变化"):
            asyncio.run(
                self.provider.run(
                    call=build_call(
                        "workspace.files.propose_edit",
                        {
                            "file_id": "file-current",
                            "old_string": "New content",
                            "new_string": "Revised content",
                            "expected_revision_id": revision_id,
                        },
                    )
                )
            )

    def test_empty_file_results_are_explicit_safe_answers(self) -> None:
        current = self.db.get(ProjectFile, "file-current")
        self.db.delete(current)
        self.db.commit()

        list_sources, list_metadata = asyncio.run(
            self.provider.run(call=build_call("workspace.files.list", {}))
        )
        search_sources, search_metadata = asyncio.run(
            self.provider.run(call=build_call("workspace.files.search", {"query": "不存在的内容"}))
        )
        self.db.add(
            ProjectFile(
                id="file-empty",
                project_id="project-current",
                user_id="user-1",
                kind="file",
                file_name="empty.md",
                mime_type="text/markdown",
                file_size=0,
                storage_key="user-1/empty.md",
                parsed_text="   ",
            )
        )
        self.db.commit()
        read_sources, read_metadata = asyncio.run(
            self.provider.run(call=build_call("workspace.files.read", {"file_id": "file-empty"}))
        )

        for sources, metadata in (
            (list_sources, list_metadata),
            (search_sources, search_metadata),
            (read_sources, read_metadata),
        ):
            self.assertEqual(metadata["result_semantics"], "empty_answer")
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0].metadata["result_semantics"], "empty_answer")
            self.assertNotIn("storage_key", str(sources[0].metadata))

        self.assertIn("没有可供 Agent 访问的文件", list_sources[0].display_text)
        self.assertIn("未找到与本次查询匹配的文件", search_sources[0].display_text)
        self.assertIn("没有可读取的解析文本", read_sources[0].display_text)

    def test_sensitive_files_are_hidden_and_secret_values_are_redacted(self) -> None:
        self.db.add_all(
            [
                ProjectFile(
                    id="sensitive-file",
                    project_id="project-current",
                    user_id="user-1",
                    kind="file",
                    file_name=".env",
                    mime_type="text/plain",
                    storage_key="user-1/.env",
                    parsed_text="API_KEY=sk-test-12345678901234567890\npassword=hunter2",
                ),
                ProjectFile(
                    id="safe-file",
                    project_id="project-current",
                    user_id="user-1",
                    kind="file",
                    file_name="config-notes.md",
                    mime_type="text/markdown",
                    storage_key="user-1/config-notes.md",
                    parsed_text="API_KEY=sk-test-12345678901234567890",
                ),
            ]
        )
        self.db.commit()

        listed_sources, _ = asyncio.run(self.provider.run(call=build_call("workspace.files.list", {})))
        listed_text = listed_sources[0].display_text if listed_sources else ""
        self.assertNotIn(".env", listed_text)
        self.assertIn("config-notes.md", listed_text)

        read_sources, _ = asyncio.run(
            self.provider.run(call=build_call("workspace.files.read", {"file_id": "safe-file"}))
        )
        self.assertNotIn("sk-test-12345678901234567890", read_sources[0].display_text)
        self.assertIn("API_KEY=***", read_sources[0].display_text)

        with self.assertRaisesRegex(ToolExecutionFeedbackError, "敏感文件"):
            asyncio.run(self.provider.run(call=build_call("workspace.files.read", {"file_id": "sensitive-file"})))

    def test_reading_other_project_or_user_file_fails_closed(self) -> None:
        for file_id in ("file-other-project", "file-other-user"):
            with self.assertRaisesRegex(RuntimeError, "未找到"):
                asyncio.run(
                    self.provider.run(call=build_call("workspace.files.read", {"file_id": file_id}))
                )

    def test_edit_preview_requires_unique_match_and_never_mutates_file(self) -> None:
        sources, metadata = asyncio.run(
            self.provider.run(
                call=build_call(
                    "workspace.files.propose_edit",
                    {
                        "file_id": "file-current",
                        "old_string": "Durable checkpoint",
                        "new_string": "Durable Agent checkpoint",
                    },
                )
            )
        )

        self.assertFalse(metadata["applied"])
        self.assertIn("-Durable checkpoint", sources[0].display_text)
        self.assertIn("+Durable Agent checkpoint", sources[0].display_text)
        self.assertIn("未修改源文件", sources[0].display_text)
        stored = self.db.get(ProjectFile, "file-current")
        self.assertEqual(
            stored.parsed_text,
            "Architecture\nDurable checkpoint and tool approval design.\nFinal line.",
        )

    def test_edit_preview_is_a_valid_approval_draft_not_an_applied_revision(self) -> None:
        class AllowWorkspaceTool:
            def is_tool_enabled_for_workspace(self, **_kwargs) -> bool:
                return True

        result, _ = asyncio.run(
            ToolExecutor(
                credential_resolver=AllowWorkspaceTool(),
                catalog=ToolCatalog(),
                db=self.db,
                user_id="user-1",
                project_id="project-current",
            ).execute(
                build_call(
                    "workspace.files.propose_edit",
                    {
                        "file_id": "file-current",
                        "old_string": "Durable checkpoint",
                        "new_string": "Durable Agent checkpoint",
                    },
                )
            )
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.result_semantics, "approval_draft")
        self.assertEqual(result.quality_status, "valid")
        self.assertEqual(result.quality_metadata["semantic_profile"], "approval_draft")
        self.assertFalse(result.sources[0].metadata["raw"]["applied"])
        stored = self.db.get(ProjectFile, "file-current")
        self.assertIn("Durable checkpoint", stored.parsed_text)

    def test_edit_preview_returns_safe_feedback_for_missing_or_ambiguous_match(self) -> None:
        with self.assertRaisesRegex(ToolExecutionFeedbackError, "重新读取"):
            asyncio.run(
                self.provider.run(
                    call=build_call(
                        "workspace.files.propose_edit",
                        {"file_id": "file-current", "old_string": "stale text", "new_string": "new"},
                    )
                )
            )

        current = self.db.get(ProjectFile, "file-current")
        current.parsed_text = "same\nsame\n"
        self.db.commit()
        with self.assertRaisesRegex(ToolExecutionFeedbackError, "出现 2 次"):
            asyncio.run(
                self.provider.run(
                    call=build_call(
                        "workspace.files.propose_edit",
                        {"file_id": "file-current", "old_string": "same", "new_string": "new"},
                    )
                )
            )

    def test_file_tools_require_project_workspace_context(self) -> None:
        provider = WorkspaceFileToolProvider(db=self.db, user_id="user-1", project_id=None)

        with self.assertRaisesRegex(RuntimeError, "需要关联项目"):
            asyncio.run(provider.run(call=build_call("workspace.files.list", {})))

    def test_executor_dispatches_workspace_adapter_without_credentials(self) -> None:
        class AllowWorkspaceTool:
            def is_tool_enabled_for_workspace(self, **_kwargs) -> bool:
                return True

        executor = ToolExecutor(
            credential_resolver=AllowWorkspaceTool(),
            catalog=ToolCatalog(),
            db=self.db,
            user_id="user-1",
            project_id="project-current",
        )
        result, events = asyncio.run(
            executor.execute(
                build_call(
                    "workspace.files.read",
                    {"file_id": "file-current", "start_line": 1, "max_lines": 1},
                )
            )
        )

        self.assertEqual(result.status, "success")
        self.assertIn("Architecture", result.sources[0].display_text)
        self.assertEqual(result.quality_status, "valid")
        self.assertEqual(result.quality_metadata["semantic_profile"], "file_read")
        passed_policy = [event for event in events if event.type == "tool_policy_check"][-1]
        self.assertEqual(passed_policy.payload["credential_source"], "not_required")
        checking_policy = [event for event in events if event.type == "tool_policy_check"][0]
        self.assertEqual(checking_policy.payload["adapter_type"], "workspace_file")

    def test_user_story_review_edit_approve_and_re_read_file(self) -> None:
        """按用户实际操作验证文件工具的完整闭环，而不是只测单个 Provider。"""

        class AllowWorkspaceTool:
            def is_tool_enabled_for_workspace(self, **_kwargs) -> bool:
                return True

        executor = ToolExecutor(
            credential_resolver=AllowWorkspaceTool(),
            catalog=ToolCatalog(),
            db=self.db,
            user_id="user-1",
            project_id="project-current",
        )

        listed, _ = asyncio.run(executor.execute(build_call("workspace.files.list", {})))
        self.assertEqual(listed.status, "success")
        self.assertIn("file-current", listed.sources[0].display_text)

        searched, _ = asyncio.run(
            executor.execute(
                build_call("workspace.files.search", {"query": "tool approval"})
            )
        )
        self.assertEqual(searched.status, "success")
        self.assertEqual(searched.sources[0].metadata["file_id"], "file-current")

        read_call = build_call("workspace.files.read", {"file_id": "file-current"})
        read_result, _ = asyncio.run(executor.execute(read_call))
        self.assertEqual(read_result.status, "success")
        base_revision_id = read_result.sources[0].metadata["revision_id"]
        self.assertIn("Durable checkpoint", read_result.sources[0].display_text)

        # 用户明确要求修改时，首次调用只产生可审阅 Diff，不直接改变文件。
        apply_call = build_call(
            "workspace.files.apply_edit",
            {
                "file_id": "file-current",
                "old_string": "Durable checkpoint",
                "new_string": "Durable Agent checkpoint",
                "expected_revision_id": base_revision_id,
            },
        )
        approval_result, approval_events = asyncio.run(executor.execute(apply_call))
        self.assertEqual(approval_result.status, "confirmation_required")
        self.assertEqual(approval_result.result_semantics, "approval_draft")
        self.assertIn("尚未写入", approval_result.sources[0].display_text)
        self.assertTrue(
            any(event.type == "tool_confirmation_required" for event in approval_events)
        )
        self.assertIn(
            "Durable checkpoint",
            self.db.get(ProjectFile, "file-current").parsed_text,
        )

        # 模拟界面确认：challenge 只短暂返回给用户，之后由 Runtime 做参数哈希和 CAS。
        approval_id = approval_result.sources[0].metadata["approval_id"]
        runtime = AgentRuntimeService(self.db)
        token = runtime.issue_approval_challenge(approval_id=approval_id, user_id="user-1")
        applied = runtime.apply_approved_file_edit(
            approval_id=approval_id,
            user_id="user-1",
            approval_token=token,
        )
        self.assertEqual(applied.status, "applied")
        self.assertEqual(applied.revision_number, 2)

        reread, _ = asyncio.run(
            executor.execute(
                build_call(
                    "workspace.files.read",
                    {
                        "file_id": "file-current",
                        "expected_revision_id": applied.revision_id,
                    },
                )
            )
        )
        self.assertEqual(reread.status, "success")
        self.assertIn("Durable Agent checkpoint", reread.sources[0].display_text)
        self.assertEqual(reread.sources[0].metadata["revision_id"], applied.revision_id)

    def test_next_planning_round_receives_only_opaque_file_observation(self) -> None:
        source = ExternalSource(
            source_type="workspace_file_search",
            provider="workspace",
            title="agent-design.md",
            display_text="Durable checkpoint",
            metadata={
                "tool_key": "workspace.files.search",
                "file_id": "file-current",
                "mime_type": "text/markdown",
                "line_start": 2,
                "line_end": 2,
                "storage_key": "user-1/private-agent-design.md",
                "raw": {"storage_key": "must-not-reach-planner"},
            },
        )

        observations = ExternalContextService._build_observations(
            round_index=1,
            sources=[source],
            registry=ToolCatalog(),
        )

        self.assertEqual(observations[0]["metadata"]["file_id"], "file-current")
        self.assertNotIn("storage_key", observations[0]["metadata"])
        self.assertNotIn("raw", observations[0]["metadata"])


if __name__ == "__main__":
    unittest.main()

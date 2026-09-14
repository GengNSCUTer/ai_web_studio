from __future__ import annotations

import asyncio
import json
import unittest

import app.models  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.project import Project
from app.models.user import User
from app.models.agent_runtime import AgentArtifact, AgentRun, AgentStep
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import PlannedToolCall


class _AllowWorkspaceTool:
    def is_tool_enabled_for_workspace(self, **_kwargs) -> bool:
        return True


class AgentArtifactToolProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()
        user = User(username="artifact-user", email="artifact@example.test")
        self.db.add(user)
        self.db.flush()
        project = Project(user_id=user.id, name="artifact-project")
        self.db.add(project)
        self.db.commit()
        self.user_id = user.id
        self.project_id = project.id

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_empty_artifact_list_is_a_safe_business_answer(self) -> None:
        result, events = asyncio.run(
            ToolExecutor(
                credential_resolver=_AllowWorkspaceTool(),
                db=self.db,
                user_id=self.user_id,
                project_id=self.project_id,
            ).execute(
                PlannedToolCall(
                    call_id="empty-artifacts",
                    tool_key="agent.artifacts.list",
                    provider="agent_runtime",
                    category="agent_artifact",
                    display_name="Agent 运行产物列表",
                    confidence=1.0,
                    reason="当前项目没有历史运行产物",
                )
            )
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.result_semantics, "empty_answer")
        self.assertEqual(result.quality_status, "valid")
        self.assertEqual(len(result.sources), 1)
        self.assertIn("没有可读取的历史 Agent 产物", result.sources[0].display_text)
        self.assertEqual(result.sources[0].metadata["result_semantics"], "empty_answer")
        quality_event = [event for event in events if event.type == "tool_result_quality"][0]
        self.assertEqual(quality_event.payload["metadata"]["result_semantics"], "empty_answer")

    def test_artifact_list_and_read_require_durable_identity(self) -> None:
        run = AgentRun(
            user_id=self.user_id,
            project_id=self.project_id,
            runtime_kind="durable_tool_workflow",
            status="succeeded",
            input_json="{}",
            planner_state_json="{}",
            idempotency_key="artifact-quality-run",
        )
        self.db.add(run)
        self.db.flush()
        step = AgentStep(
            run_id=run.id,
            sequence=1,
            call_id="artifact-quality-call",
            tool_key="web.tavily.search",
            arguments_json="{}",
            arguments_hash="a" * 64,
            status="succeeded",
        )
        self.db.add(step)
        self.db.flush()
        artifact = AgentArtifact(
            run_id=run.id,
            step_id=step.id,
            user_id=self.user_id,
            artifact_type="tool_result",
            content_hash="b" * 64,
            preview="脱敏产物摘要",
            content_json=json.dumps({"result": "可读取的持久化结果"}, ensure_ascii=False),
            char_count=20,
        )
        self.db.add(artifact)
        self.db.commit()

        executor = ToolExecutor(
            credential_resolver=_AllowWorkspaceTool(),
            db=self.db,
            user_id=self.user_id,
            project_id=self.project_id,
        )
        list_result, _ = asyncio.run(
            executor.execute(
                PlannedToolCall(
                    call_id="artifact-list",
                    tool_key="agent.artifacts.list",
                    provider="agent_runtime",
                    category="agent_artifact",
                    display_name="Agent 运行产物列表",
                    confidence=1.0,
                    reason="定位历史产物",
                )
            )
        )
        read_result, _ = asyncio.run(
            executor.execute(
                PlannedToolCall(
                    call_id="artifact-read",
                    tool_key="agent.artifacts.read",
                    provider="agent_runtime",
                    category="agent_artifact",
                    display_name="Agent 运行产物按需读取",
                    confidence=1.0,
                    reason="读取历史产物",
                    arguments={"artifact_id": artifact.id},
                )
            )
        )

        self.assertEqual(list_result.quality_status, "valid")
        self.assertEqual(list_result.quality_metadata["semantic_profile"], "artifact_list")
        self.assertEqual(read_result.status, "success")
        self.assertEqual(read_result.quality_status, "valid")
        self.assertEqual(read_result.quality_metadata["semantic_profile"], "artifact_read")
        self.assertEqual(read_result.sources[0].metadata["raw"]["artifact_id"], artifact.id)


if __name__ == "__main__":
    unittest.main()

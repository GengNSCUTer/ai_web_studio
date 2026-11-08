"""历史成果发现和复用不能绕过类型、租户、时效、原来源授权。"""

import asyncio
from datetime import timedelta
import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.models.agent_runtime import AgentRun, AgentStep
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.services.agent_artifact_store import AgentArtifactStore, ArtifactStoreError
from app.services.runtime_lease import utcnow
from app.services.tools.providers.agent_artifacts import AgentArtifactToolProvider
from app.services.tools.schemas import PlannedToolCall, ToolExecutionFeedbackError
from backend.tests import test_agent_artifact_tools as unit


class AgentArtifactStoreTest(unittest.TestCase):
    setUp = unit.AgentArtifactToolProviderTest.setUp
    tearDown = unit.AgentArtifactToolProviderTest.tearDown

    def artifact(self, *, kind="tool_result", preview="Redis可靠投递调研报告", payload=None, project_id=None):
        run = AgentRun(user_id=self.user_id, project_id=project_id or self.project_id,
            runtime_kind="durable_tool_workflow", status="succeeded", input_json="{}", planner_state_json="{}",
            idempotency_key="store-test:"+str(uuid4()))
        self.db.add(run)
        self.db.flush()
        step = AgentStep(run_id=run.id, sequence=1, call_id="read", tool_key="workspace.files.list",
            arguments_json="{}", arguments_hash="a"*64, status="succeeded")
        self.db.add(step)
        self.db.flush()
        artifact = AgentArtifactStore(self.db).create(run=run, step=step, artifact_type=kind, preview=preview,
            content_json=json.dumps(payload or {"result": preview}, ensure_ascii=False))
        self.db.commit()
        return run, step, artifact

    def provider(self, project_id=None):
        return AgentArtifactToolProvider(db=self.db, user_id=self.user_id, project_id=project_id or self.project_id)

    def invoke(self, key="list", **arguments):
        return asyncio.run(self.provider().run(call=PlannedToolCall("test", "agent.artifacts."+key,
            "agent_runtime", "agent_artifact", "test", 1, "test", arguments=arguments)))

    def test_query_finds_relevant_old_artifact_without_step_id(self):
        _, _, target = self.artifact()
        self.artifact(preview="高德天气查询记录")
        sources, metadata = self.invoke(query="Redis投递报告", semantic=False)
        ids = [a["artifact_id"] for a in sources[0].metadata["raw"]["artifacts"]]
        self.assertEqual(ids, [target.id])
        self.assertEqual(metadata["retrieval"], "bounded_local_query_and_recency")

    def test_internal_snapshots_cannot_be_discovered_or_read(self):
        for kind in ["chat_tool_result", "chat_generation_partial"]:
            with self.subTest(kind=kind):
                _, _, artifact = self.artifact(kind=kind)
                sources, _ = self.invoke()
                self.assertEqual(sources[0].metadata["raw"]["artifacts"], [])
                with self.assertRaises(ToolExecutionFeedbackError):
                    self.invoke("read", artifact_id=artifact.id)

    def test_other_project_cannot_be_read_even_if_owned_by_same_user(self):
        other = Project(user_id=self.user_id, name="另一个工作区")
        self.db.add(other)
        self.db.commit()
        _, _, artifact = self.artifact(project_id=other.id)
        with self.assertRaises(ToolExecutionFeedbackError):
            self.invoke("read", artifact_id=artifact.id)

    def test_expired_or_revoked_result_is_not_reused(self):
        run, _, artifact = self.artifact()
        artifact.created_at = utcnow() - timedelta(days=8)
        self.db.commit()
        self.assertFalse(AgentArtifactStore(self.db).reusable(run, artifact))
        self.assertTrue(AgentArtifactStore(self.db).reusable(run, artifact, max_age_hours=720))
        artifact.prompt_state = "revoked"
        self.db.commit()
        with self.assertRaises(ToolExecutionFeedbackError):
            self.invoke("read", artifact_id=artifact.id, max_age_hours=720)

    def test_tampering_is_rejected_in_list_and_read(self):
        _, _, artifact = self.artifact()
        artifact.content_json = '{"result":"changed"}'
        self.db.commit()
        self.assertEqual(self.invoke()[0][0].metadata["raw"]["artifacts"], [])
        with self.assertRaises(ToolExecutionFeedbackError):
            self.invoke("read", artifact_id=artifact.id)

    def test_deleted_or_modified_source_file_invalidates_reuse(self):
        import hashlib
        file = ProjectFile(user_id=self.user_id, project_id=self.project_id, file_name="note.md", kind="text",
            storage_key="test", parsed_text="版本一")
        self.db.add(file)
        self.db.commit()
        run, _, artifact = self.artifact(payload={"sources": [{"metadata": {"file_id": file.id,
            "content_hash": hashlib.sha256("版本一".encode()).hexdigest()}}]})
        self.assertTrue(AgentArtifactStore(self.db).reusable(run, artifact))
        file.parsed_text = "版本二"
        self.db.commit()
        self.assertFalse(AgentArtifactStore(self.db).reusable(run, artifact))
        self.db.delete(file)
        self.db.commit()
        with self.assertRaises(ToolExecutionFeedbackError):
            self.invoke("read", artifact_id=artifact.id)

    def test_invalid_json_is_not_persisted(self):
        run, step, _ = self.artifact()
        with self.assertRaises(ArtifactStoreError):
            AgentArtifactStore(self.db).create(run=run, step=step, artifact_type="tool_result", content_json='{"x":NaN}')

    def test_semantic_rank_can_find_different_wording_with_no_keyword_hit(self):
        _, _, artifact = self.artifact(preview="耐久消息的可靠发送与失败处理")
        with patch("app.services.artifact_semantic_ranker.ArtifactSemanticRanker.rank",
            new=AsyncMock(return_value=({artifact.id:.9}, "online_semantic_and_local_terms"))):
            sources, metadata = self.invoke(query="Outbox Redis投递", semantic=True)
        self.assertEqual(sources[0].metadata["raw"]["artifacts"][0]["artifact_id"], artifact.id)
        self.assertEqual(metadata["retrieval"], "online_semantic_and_local_terms")

    def test_business_read_strips_audit_events_and_marks_snapshot(self):
        _, _, artifact = self.artifact(payload={"result": "有效业务结果", "events": [{"type":"private_trace"}]})
        sources, _ = self.invoke("read", artifact_id=artifact.id)
        self.assertNotIn("private_trace", sources[0].display_text)
        self.assertIn("有效业务结果", sources[0].display_text)
        self.assertTrue(sources[0].metadata["historical_snapshot"])

    def test_upstream_revocation_propagates_to_derived_artifact(self):
        _, _, upstream = self.artifact()
        run, _, derived = self.artifact(payload={"sources":[{"metadata":{"artifact_id":upstream.id}}]})
        self.assertTrue(AgentArtifactStore(self.db).reusable(run, derived))
        upstream.prompt_state = "revoked"
        self.db.commit()
        self.assertFalse(AgentArtifactStore(self.db).reusable(run, derived))

    def test_semantic_flag_cannot_silently_send_previews_outside_user_intent(self):
        from app.services.tools.executor import ToolExecutor
        from app.services.tools.catalog import ToolCatalog
        self.artifact()
        executor = ToolExecutor(db=self.db, user_id=self.user_id, project_id=self.project_id,
            catalog=ToolCatalog(db=self.db, user_id=self.user_id, project_id=self.project_id),
            original_task="只在本地查找以前的报告，不要联网")
        with patch("app.services.artifact_semantic_ranker.ArtifactSemanticRanker.rank", new_callable=AsyncMock) as rank:
            result, _ = asyncio.run(executor.execute(PlannedToolCall("test", "agent.artifacts.list", "agent_runtime",
                "agent_artifact", "test", 1, "test", arguments={"query":"报告", "semantic":True})))
        self.assertEqual(result.status, "skipped")
        rank.assert_not_called()

    def test_cross_user_project_and_missing_knowledge_source_are_rejected(self):
        from app.models.user import User
        other = User(email="other-artifact@test.invalid")
        self.db.add(other)
        self.db.flush()
        project = Project(user_id=other.id, name="别人的项目")
        self.db.add(project)
        self.db.commit()
        run, _, artifact = self.artifact(project_id=project.id)
        self.assertFalse(AgentArtifactStore(self.db).reusable(run, artifact))
        run, _, artifact = self.artifact(payload={"sources":[{"metadata":{"chunk_id":"missing-chunk"}}]})
        self.assertFalse(AgentArtifactStore(self.db).reusable(run, artifact))

    def test_planner_observation_contains_real_artifact_id_for_followup_read(self):
        from app.services.tools.observation_projection import PlannerObservationProjection
        from app.services.tools.catalog import ToolCatalog
        _, _, artifact = self.artifact()
        sources, _ = self.invoke()
        sources[0].metadata["tool_key"] = "agent.artifacts.list"
        catalog = ToolCatalog(db=self.db, user_id=self.user_id, project_id=self.project_id)
        observations = PlannerObservationProjection.project_sources(round_index=1, sources=sources,
            definition_resolver=catalog.get_or_none)
        self.assertEqual(observations[0]["metadata"]["artifact_id"], artifact.id)

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.core.database import Base
from app.models.agent_runtime import AgentOutboxEvent, AgentRun, AgentStep
from app.models.project import Project
from app.models.tool_config import UserSkillInstallation
from app.models.user import User
from app.services.durable_handoff_service import DurableHandoffError, DurableHandoffService
from app.services.skill_catalog import SkillCatalog


class DurableHandoffServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.SessionLocal() as db:
            self.user = User(username="handoff-user", email="handoff@example.test")
            db.add(self.user)
            db.flush()
            self.project = Project(user_id=self.user.id, name="handoff-project")
            db.add(self.project)
            db.commit()
            self.user_id = self.user.id
            self.project_id = self.project.id

    def tearDown(self) -> None:
        self.engine.dispose()

    def _install_review_skill(self, db) -> None:
        SkillCatalog().install_or_update(
            db=db,
            user_id=self.user_id,
            skill_key="workspace.document-review",
            is_enabled=True,
        )

    def _calls(self) -> list[dict[str, object]]:
        return [
            {
                "call_id": "list-files",
                "tool_key": "workspace.files.list",
                "arguments": {},
                "depends_on": [],
                "result_bindings": [],
            }
        ]

    def test_preview_only_signs_bounded_plan_without_persisting_run(self) -> None:
        with self.SessionLocal() as db:
            self._install_review_skill(db)
            preview = DurableHandoffService(db).preview(
                user_id=self.user_id,
                project_id=self.project_id,
                conversation_id=None,
                assistant_message_id=None,
                calls=self._calls(),
                skill_key="workspace.document-review",
                max_attempts=3,
            )
            self.assertTrue(preview.handoff_token)
            self.assertEqual(preview.tool_calls[0]["tool_key"], "workspace.files.list")
            self.assertEqual(db.scalar(select(AgentRun)), None)
            self.assertEqual(db.scalar(select(AgentOutboxEvent)), None)

    def test_confirm_creates_one_idempotent_durable_run(self) -> None:
        with self.SessionLocal() as db:
            self._install_review_skill(db)
            service = DurableHandoffService(db)
            preview = service.preview(
                user_id=self.user_id,
                project_id=self.project_id,
                conversation_id=None,
                assistant_message_id=None,
                calls=self._calls(),
                skill_key="workspace.document-review",
                max_attempts=3,
            )
            first = service.confirm(user_id=self.user_id, handoff_token=preview.handoff_token)
            second = service.confirm(user_id=self.user_id, handoff_token=preview.handoff_token)
            self.assertEqual(first.id, second.id)
            self.assertEqual(first.runtime_kind, "durable_tool_workflow")
            self.assertEqual(db.query(AgentStep).filter_by(run_id=first.id).count(), 1)
            self.assertEqual(db.query(AgentOutboxEvent).filter_by(run_id=first.id).count(), 1)

    def test_preview_rejects_write_tool_and_non_durable_skill(self) -> None:
        with self.SessionLocal() as db:
            self._install_review_skill(db)
            with self.assertRaisesRegex(DurableHandoffError, "低风险只读"):
                DurableHandoffService(db).preview(
                    user_id=self.user_id,
                    project_id=self.project_id,
                    conversation_id=None,
                    assistant_message_id=None,
                    calls=[{
                        "call_id": "edit",
                        "tool_key": "workspace.files.apply_edit",
                        "arguments": {"file_id": "x", "old_string": "a", "new_string": "b"},
                    }],
                    skill_key="workspace.document-review",
                    max_attempts=3,
                )

            with self.assertRaisesRegex(DurableHandoffError, "不允许进入"):
                SkillCatalog().install_or_update(
                    db=db,
                    user_id=self.user_id,
                    skill_key="workspace.document-edit",
                    is_enabled=True,
                )
                DurableHandoffService(db).preview(
                    user_id=self.user_id,
                    project_id=self.project_id,
                    conversation_id=None,
                    assistant_message_id=None,
                    calls=self._calls(),
                    skill_key="workspace.document-edit",
                    max_attempts=3,
                )

    def test_tampered_or_expired_token_is_rejected(self) -> None:
        with self.SessionLocal() as db:
            self._install_review_skill(db)
            service = DurableHandoffService(db)
            preview = service.preview(
                user_id=self.user_id,
                project_id=self.project_id,
                conversation_id=None,
                assistant_message_id=None,
                calls=self._calls(),
                skill_key="workspace.document-review",
                max_attempts=3,
            )
            payload, signature = preview.handoff_token.split(".", 1)
            tampered = payload[:-1] + ("A" if payload[-1] != "A" else "B") + "." + signature
            with self.assertRaisesRegex(DurableHandoffError, "无效"):
                service.confirm(user_id=self.user_id, handoff_token=tampered)

            decoded = json.loads(service._decode(payload).decode("utf-8"))
            decoded["expires_at"] = int((datetime.now(timezone.utc) - timedelta(seconds=1)).timestamp())
            expired = service._sign(decoded)
            with self.assertRaisesRegex(DurableHandoffError, "过期"):
                service.confirm(user_id=self.user_id, handoff_token=expired)


if __name__ == "__main__":
    unittest.main()

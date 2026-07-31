from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import User
from app.models.tool_config import SkillInstallationRevision, UserSkillInstallation
from app.models.tool_config import WorkspaceToolSetting
from app.services.skill_catalog import SkillCatalog, SkillCatalogError
from app.services.skill_recommendation_service import SkillGoldSetEvaluator, SkillRecommendationService


class SkillCatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.SessionLocal() as db:
            user = User(username="skill-user", email="skill@example.test")
            db.add(user)
            db.commit()
            self.user_id = user.id

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_missing_credential_is_reported_and_blocks_enable(self) -> None:
        with self.SessionLocal() as db:
            catalog = SkillCatalog()
            by_key = {
                item["skill_key"]: item
                for item in catalog.list_for_user(db=db, user_id=self.user_id)
            }
            self.assertEqual(
                by_key["research.web-brief"]["missing_tool_keys"],
                ["web.tavily.search"],
            )
            self.assertEqual(
                by_key["workspace.document-review"]["missing_tool_keys"],
                [],
            )

            with self.assertRaisesRegex(SkillCatalogError, "缺少可执行能力"):
                catalog.install_or_update(
                    db=db,
                    user_id=self.user_id,
                    skill_key="research.web-brief",
                    is_enabled=True,
                )

            installation = catalog.install_or_update(
                db=db,
                user_id=self.user_id,
                skill_key="workspace.document-review",
                is_enabled=True,
            )
            self.assertTrue(installation.is_enabled)

            disabled = catalog.install_or_update(
                db=db,
                user_id=self.user_id,
                skill_key="workspace.document-review",
                is_enabled=False,
            )
            self.assertFalse(disabled.is_enabled)
            self.assertEqual(
                db.query(UserSkillInstallation)
                .filter_by(user_id=self.user_id, skill_key="workspace.document-review")
                .count(),
                1,
            )

    def test_execution_requires_install_enable_project_and_workspace_tools(self) -> None:
        with self.SessionLocal() as db:
            catalog = SkillCatalog()
            catalog.install_or_update(
                db=db,
                user_id=self.user_id,
                skill_key="workspace.document-review",
                is_enabled=True,
            )
            with self.assertRaisesRegex(SkillCatalogError, "明确的工作区"):
                catalog.resolve_for_execution(
                    db=db,
                    user_id=self.user_id,
                    project_id=None,
                    skill_key="workspace.document-review",
                )

            context = catalog.resolve_for_execution(
                db=db,
                user_id=self.user_id,
                project_id="project-1",
                skill_key="workspace.document-review",
            )
            self.assertEqual(context.skill_key, "workspace.document-review")
            self.assertEqual(
                set(context.allowed_tool_keys),
                {"workspace.files.list", "workspace.files.search", "workspace.files.read"},
            )

            db.add(
                WorkspaceToolSetting(
                    project_id="project-1",
                    tool_key="workspace.files.read",
                    is_enabled=False,
                )
            )
            db.commit()
            with self.assertRaisesRegex(SkillCatalogError, "workspace.files.read"):
                catalog.resolve_for_execution(
                    db=db,
                    user_id=self.user_id,
                    project_id="project-1",
                    skill_key="workspace.document-review",
                )

    def test_manifest_rejects_unreviewed_executable_field(self) -> None:
        record = {
            "skill_key": "unsafe.skill",
            "version": "1.0.0",
            "display_name": "Unsafe",
            "description": "Unsafe",
            "instructions": ["do work"],
            "output_contract": ["return result"],
            "required_tool_keys": ["workspace.files.list"],
            "optional_tool_keys": [],
            "script": "import os",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skills.json"
            path.write_text(json.dumps([record]), encoding="utf-8")
            with self.assertRaisesRegex(SkillCatalogError, "未审核字段"):
                SkillCatalog(manifest_path=path)

    def test_installation_locks_snapshot_until_explicit_upgrade_or_rollback(self) -> None:
        base = {
            "skill_key": "review.snapshot",
            "version": "1.0.0",
            "display_name": "快照审阅",
            "description": "只读审阅",
            "instructions": ["只读取资料。"],
            "output_contract": ["给出结论。"],
            "required_tool_keys": ["workspace.files.list"],
            "optional_tool_keys": [],
            "requires_project": False,
            "requires_tool_execution": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skills.json"
            path.write_text(json.dumps([base]), encoding="utf-8")
            with self.SessionLocal() as db:
                v1 = SkillCatalog(manifest_path=path)
                v1.install_or_update(db=db, user_id=self.user_id, skill_key="review.snapshot", is_enabled=True)
                self.assertEqual(db.query(SkillInstallationRevision).count(), 1)

                upgraded = {**base, "version": "1.1.0", "instructions": ["只读取经过审核的资料。"]}
                path.write_text(json.dumps([upgraded]), encoding="utf-8")
                v2 = SkillCatalog(manifest_path=path)

                # A catalog publication cannot silently expand or alter an
                # existing user's active workflow.
                locked = v2.resolve_for_execution(
                    db=db, user_id=self.user_id, project_id=None, skill_key="review.snapshot"
                )
                self.assertEqual(locked.version, "1.0.0")
                self.assertIn("只读取资料", locked.planner_instructions[0])

                v2.install_or_update(db=db, user_id=self.user_id, skill_key="review.snapshot", is_enabled=True)
                self.assertEqual(
                    v2.resolve_for_execution(
                        db=db, user_id=self.user_id, project_id=None, skill_key="review.snapshot"
                    ).version,
                    "1.1.0",
                )
                v2.rollback(db=db, user_id=self.user_id, skill_key="review.snapshot")
                self.assertEqual(
                    v2.resolve_for_execution(
                        db=db, user_id=self.user_id, project_id=None, skill_key="review.snapshot"
                    ).version,
                    "1.0.0",
                )

    def test_recommendation_is_read_only_and_gold_set_scores_submitted_plan(self) -> None:
        with self.SessionLocal() as db:
            catalog = SkillCatalog()
            catalog.install_or_update(
                db=db,
                user_id=self.user_id,
                skill_key="workspace.document-review",
                is_enabled=True,
            )
            before = db.query(UserSkillInstallation).count()
            recommendations = SkillRecommendationService().recommend(
                db=db,
                user_id=self.user_id,
                project_id="project-1",
                query="审阅当前工作区项目文档，但不要修改文件",
            )
            self.assertEqual(recommendations[0]["skill_key"], "workspace.document-review")
            self.assertTrue(recommendations[0]["requires_confirmation"])
            self.assertEqual(db.query(UserSkillInstallation).count(), before)

            assessment = SkillGoldSetEvaluator().assess_case(
                db=db,
                user_id=self.user_id,
                project_id="project-1",
                case_id="workspace-review",
                selected_skill_key="workspace.document-review",
                plan={
                    "execution_status": "success",
                    "calls": [
                        {"tool_key": "workspace.files.list", "arguments": {}},
                        {"tool_key": "workspace.files.search", "arguments": {}},
                        {"tool_key": "workspace.files.read", "arguments": {}},
                    ],
                },
            )
            self.assertEqual(assessment["tool_recall"], 1.0)
            self.assertEqual(assessment["task_success"], 1)


if __name__ == "__main__":
    unittest.main()

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

    def test_optional_public_credentials_keep_skills_ready_but_explicit_disable_blocks(self) -> None:
        """匿名可安装；保存 Key 后仍隔离到用户，关闭 Provider 后不可匿名绕过。"""
        from app.api.routes.tools import get_tool_settings
        from app.models.tool_config import UserToolCredential
        from app.services.secret_service import SecretService
        from app.services.tools.credentials import ToolCredentialResolver
        with self.SessionLocal() as db:
            catalog = SkillCatalog()
            catalog.install_or_update(db=db, user_id=self.user_id, skill_key="knowledge.technical-docs", is_enabled=True)
            user = db.get(User, self.user_id)
            response = get_tool_settings(db=db, current_user=user)
            credentials = {item.provider_key: item for item in response.credentials}
            self.assertTrue(credentials["context7"].is_enabled)
            self.assertFalse(credentials["context7"].has_api_key)
            self.assertEqual(credentials["context7"].source, "anonymous")
            credential = UserToolCredential(user_id=self.user_id, provider_key="context7",
                                            credential_name="可选文档凭据", is_enabled=False,
                                            api_key=SecretService().encrypt("fixture-context-key"))
            db.add(credential)
            db.commit()
            self.assertEqual(catalog._availability(db=db, user_id=self.user_id, project_id=None)
                             ["knowledge.technical-docs"]["missing_required_tool_keys"],
                             ["docs.context7.resolve", "docs.context7.query"])
            credential.is_enabled = True
            db.commit()
            resolved = ToolCredentialResolver(db).resolve(user_id=self.user_id, provider_key="context7")
            self.assertEqual(resolved.api_key, "fixture-context-key")
            self.assertEqual(catalog._availability(db=db, user_id=self.user_id, project_id=None)
                             ["knowledge.technical-docs"]["missing_required_tool_keys"], [])
            response = get_tool_settings(db=db, current_user=user)
            self.assertNotIn("fixture-context-key", str(response))
            self.assertIsNone(ToolCredentialResolver(db).resolve(user_id="another-user", provider_key="context7").api_key)

    def test_public_provider_connection_test_accepts_anonymous_and_rejects_explicit_disable(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch
        from fastapi import HTTPException
        from app.api.routes.tools import test_tool_credential
        from app.models.tool_config import UserToolCredential
        from app.services.tools.schemas import ExternalSource
        with self.SessionLocal() as db:
            user = db.get(User, self.user_id)
            source = ExternalSource(source_type="web", provider="exa", title="Official documentation",
                                    display_text="Official public documentation explaining database sessions.",
                                    url="https://docs.sqlalchemy.org/en/20/orm/session_basics.html")
            with patch("app.services.tools.adapters.ToolAdapterRunner.run", new=AsyncMock(return_value=([source], {}))) as run:
                result = asyncio.run(test_tool_credential(provider_key="exa", db=db, current_user=user))
                self.assertTrue(result.ok)
                self.assertIn("anonymous", result.message)
                self.assertIsNone(run.call_args.kwargs["api_key"])
                db.add(UserToolCredential(user_id=self.user_id, provider_key="exa",
                                          credential_name="关闭公共查询", is_enabled=False))
                db.commit()
                run.reset_mock()
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(test_tool_credential(provider_key="exa", db=db, current_user=user))
                self.assertEqual(raised.exception.status_code, 400)
                run.assert_not_called()

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

    def test_manifest_accepts_reviewed_completion_strategy(self) -> None:
        """完成合同只能引用平台策略，且已审核策略必须可被完整清单加载。"""

        record = {
            "skill_key": "workspace.replace-title",
            "version": "1.0.0",
            "display_name": "标题替换提案",
            "description": "先读取，再生成需确认的修改提案。",
            "instructions": ["只处理当前工作区。"],
            "output_contract": ["未确认前不得声称已经写入。"],
            "required_tool_keys": ["workspace.files.read", "workspace.files.apply_edit"],
            "optional_tool_keys": [],
            "requires_project": True,
            "requires_tool_execution": True,
            "completion_contract": {
                "intent_patterns": ["替换"],
                "prerequisite_tool_keys": ["workspace.files.read"],
                "required_tool_keys": ["workspace.files.read", "workspace.files.apply_edit"],
                "completion_strategy": "workspace_file_read_then_apply_exact_replacement",
                "require_edit_for_exact_replacement": True,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skills.json"
            path.write_text(json.dumps([record]), encoding="utf-8")
            catalog = SkillCatalog(manifest_path=path)

        contract = catalog._definitions["workspace.replace-title"]["completion_contract"]
        self.assertEqual(contract["completion_strategy"], "workspace_file_read_then_apply_exact_replacement")
        self.assertTrue(contract["require_edit_for_exact_replacement"])

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

            batch = SkillGoldSetEvaluator().assess_batch(
                db=db,
                user_id=self.user_id,
                project_id="project-1",
                observations=[
                    {
                        "case_id": "workspace-review",
                        "selected_skill_key": "workspace.document-review",
                        "plan": {
                            "execution_status": "success",
                            "latency_ms": 22,
                            "calls": [
                                {"tool_key": "workspace.files.list", "arguments": {}},
                                {"tool_key": "workspace.files.search", "arguments": {}},
                                {"tool_key": "workspace.files.read", "arguments": {}},
                            ],
                        },
                    }
                ],
            )
            self.assertEqual(batch["summary"]["case_count"], 1)
            self.assertEqual(batch["summary"]["metrics"]["task_success_rate"], 1.0)
            self.assertEqual(batch["summary"]["metrics"]["latency_ms_p50"], 22.0)


if __name__ == "__main__":
    unittest.main()

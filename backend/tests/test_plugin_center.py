"""覆盖插件生命周期、项目隔离和安装不扩权，而不是只检查响应格式。"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models
from app.core.database import Base
from app.models.plugin import PluginInstallation, PluginRelease, ProjectPlugin, SandboxJob
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.user import User
from app.schemas.plugins import SandboxPreviewCreate, SubmissionCreate
from app.services.plugin_service import PluginService, PluginError
from app.services.sandbox_service import SandboxService
from app.services.skill_catalog import SkillCatalog, SkillCatalogError


class PluginCenterTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(self.engine, expire_on_commit=False)()
        self.db.add_all([User(id="u", email="plugin@example.test"), User(id="other", email="other-plugin@example.test")])
        self.db.flush()
        self.db.add_all([Project(id="p", user_id="u", name="own"), Project(id="q", user_id="u", name="second"),
                         Project(id="x", user_id="other", name="foreign")])
        self.db.flush()
        self.db.add(ProjectFile(id="f", project_id="p", user_id="u", kind="text", file_name="note.md",
                                storage_key="u/note", parsed_text="授权资料内容"))
        self.db.commit()
        self.plugins = PluginService(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def enable(self):
        self.plugins.install("u", "knowledge.organize")
        self.plugins.enable("u", "knowledge.organize", "p", True)

    def preview(self, **kwargs):
        return SandboxService(self.db).preview("u", SandboxPreviewCreate(**{
            "project_id": "p", "plugin_key": "knowledge.organize", "script_key": "document_inventory", "file_ids": ["f"], **kwargs}))

    def test_four_reviewed_packages_and_preflight_do_not_install(self):
        rows = self.plugins.list("u")
        self.assertEqual(len(rows), 4)
        self.assertFalse(any(x["is_installed"] for x in rows))
        self.assertEqual(self.plugins.preflight("u", "knowledge.organize")["plugin_key"], "knowledge.organize")
        self.assertEqual(self.db.scalars(select(PluginInstallation)).all(), [])

    def test_stale_install_preflight_cannot_install_changed_permissions(self):
        from app.api.routes.plugins import _confirmed_install
        revision = self.plugins.preflight("u", "knowledge.organize")["catalog_revision"]
        self.plugins.definitions["knowledge.organize"]["permissions"].append("changed.permission")
        with self.assertRaisesRegex(PluginError, "权限已变化"):
            _confirmed_install(self.plugins, "u", "knowledge.organize", revision)
        self.assertIsNone(self.plugins.installation("u", "knowledge.organize"))

    def test_install_does_not_activate_or_enable_project(self):
        self.plugins.install("u", "knowledge.organize")
        self.assertFalse(self.plugins.list("u", "p")[0]["is_project_enabled"])
        with self.assertRaises(SkillCatalogError):
            SkillCatalog().resolve_for_execution(db=self.db, user_id="u", project_id="p", skill_key="workspace.document-review")

    def test_project_enable_reuses_skill_but_cannot_cross_project(self):
        self.enable()
        context = SkillCatalog().resolve_for_execution(db=self.db, user_id="u", project_id="p", skill_key="workspace.document-review")
        self.assertIn("workspace.files.read", context.allowed_tool_keys)
        with self.assertRaisesRegex(SkillCatalogError, "工作区"):
            SkillCatalog().resolve_for_execution(db=self.db, user_id="u", project_id="q", skill_key="workspace.document-review")

    def test_stop_and_uninstall_prevent_old_skill_execution(self):
        self.enable()
        self.plugins.enable("u", "knowledge.organize", "p", False)
        with self.assertRaises(SkillCatalogError):
            SkillCatalog().resolve_for_execution(db=self.db, user_id="u", project_id="p", skill_key="workspace.document-review")
        self.plugins.uninstall("u", "knowledge.organize")
        with self.assertRaises(SkillCatalogError):
            SkillCatalog().resolve_for_execution(db=self.db, user_id="u", project_id="p", skill_key="workspace.document-review")

    def test_install_is_idempotent_does_not_disable_same_version(self):
        self.enable()
        self.plugins.install("u", "knowledge.organize")
        self.assertTrue(self.plugins.list("u", "p")[0]["is_project_enabled"])
        self.assertEqual(len(self.db.scalars(select(PluginRelease)).all()), 1)

    def test_upgrade_and_rollback_reset_project_authorization(self):
        self.enable()
        original = self.plugins.definitions["knowledge.organize"]
        self.plugins.definitions["knowledge.organize"] = {**original, "version": "1.1.0", "description": "新审核版本"}
        self.plugins.install("u", "knowledge.organize")
        self.assertFalse(self.plugins.list("u", "p")[0]["is_project_enabled"])
        self.plugins.rollback("u", "knowledge.organize")
        _, manifest = self.plugins.installed_release("u", "knowledge.organize")
        self.assertEqual(manifest["version"], "1.0.0")

    def test_missing_rollback_and_foreign_project_fail(self):
        self.plugins.install("u", "knowledge.organize")
        with self.assertRaisesRegex(PluginError, "回滚"):
            self.plugins.rollback("u", "knowledge.organize")
        with self.assertRaises(PluginError):
            self.plugins.enable("other", "knowledge.organize", "p", True)
        with self.assertRaises(PluginError):
            self.plugins.list("u", "x")

    def test_preview_freezes_only_selected_file_and_redacts_text(self):
        self.enable()
        preview = self.preview()
        self.assertEqual(preview["status"], "awaiting_approval")
        self.assertNotIn("授权资料内容", json.dumps(preview, ensure_ascii=False))
        row = self.db.get(SandboxJob, preview["id"])
        self.db.get(ProjectFile, "f").parsed_text = "后续修改"
        self.db.commit()
        self.assertEqual(json.loads(row.plan_json)["files"][0]["text"], "授权资料内容")

    def test_confirm_without_worker_fails_and_does_not_queue(self):
        self.enable()
        preview = self.preview()
        with self.assertRaisesRegex(PluginError, "Worker"):
            SandboxService(self.db).confirm("u", preview["id"])
        self.assertEqual(self.db.get(SandboxJob, preview["id"]).status, "awaiting_approval")

    def test_repeated_confirm_same_job_and_other_user_rejected(self):
        self.enable()
        preview = self.preview()
        with patch("app.services.sandbox_service.sandbox_availability", return_value={"available": True, "reason": None}):
            first = SandboxService(self.db).confirm("u", preview["id"])
            second = SandboxService(self.db).confirm("u", preview["id"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["status"], "queued")
        with self.assertRaises(PluginError):
            SandboxService(self.db).confirm("other", preview["id"])

    def test_uninstalled_preview_cannot_be_confirmed(self):
        self.enable()
        preview = self.preview()
        self.plugins.uninstall("u", "knowledge.organize")
        with self.assertRaises(PluginError):
            SandboxService(self.db).confirm("u", preview["id"])

    def test_unknown_script_domain_dependency_and_foreign_file_rejected(self):
        self.enable()
        for kwargs in ({"script_key": "shell"}, {"domains": ["example.com"]},
                       {"dependencies": ["requests==2.32.0"]}, {"file_ids": ["unknown"]},
                       {"project_id": "q"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.preview(**kwargs)

    def test_dependencies_require_two_approvals(self):
        self.enable()
        preview = self.preview(dependencies=["tabulate==0.9.0"])
        with patch("app.services.sandbox_service.sandbox_availability", return_value={"available": True, "reason": None}):
            result = SandboxService(self.db).confirm("u", preview["id"])
            self.assertEqual(result["status"], "resolving_queued")
            row = self.db.get(SandboxJob, preview["id"])
            row.status = "awaiting_dependency_approval"
            plan = json.loads(row.plan_json)
            plan["wheel_lock"] = [{"requirement": "tabulate==0.9.0", "sha256": "a" * 64}]
            row.plan_json = json.dumps(plan)
            self.db.commit()
            self.assertEqual(SandboxService(self.db).confirm("u", row.id)["status"], "queued")

    def test_submission_cannot_self_approve(self):
        with self.assertRaises(ValueError):
            SubmissionCreate(name="bad", kind="script", source_url="https://example.com/x", status="approved")
        with self.assertRaises(ValueError):
            SubmissionCreate(name="bad", kind="mcp", source_url="https://secret:password@example.com")

    def test_changed_script_after_preview_is_rejected(self):
        self.enable()
        preview = self.preview()
        with patch("app.services.sandbox_service.sandbox_availability", return_value={"available": True, "reason": None}), \
             patch("app.services.sandbox_service.script_digest", return_value="changed"):
            with self.assertRaisesRegex(PluginError, "脚本已变化"):
                SandboxService(self.db).confirm("u", preview["id"])

    def test_api_authentication_submission_and_foreign_access(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.deps import get_current_user, get_db
        from app.api.routes.plugins import router
        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: self.db
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/plugins").status_code, 401)
            app.dependency_overrides[get_current_user] = lambda: self.db.get(User, "u")
            response = client.get("/api/plugins")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()["plugins"]), 4)
            self.assertFalse(response.json()["sandbox"]["available"])
            self.assertEqual(client.get("/api/plugins?project_id=x").status_code, 400)
            submitted = client.post("/api/plugins/submissions", json={"name": "外部脚本", "kind": "script", "source_url": "https://example.com/script"})
            self.assertEqual(submitted.status_code, 201)
            self.assertEqual(submitted.json()["status"], "pending_review")
            self.assertEqual(client.post("/api/plugins/submissions", json={"name": "外部脚本", "kind": "script", "source_url": "https://example.com", "status": "approved"}).status_code, 422)
            self.assertEqual(client.post("/api/plugins/arbitrary/install", json={"expected_revision": "a" * 64}).status_code, 400)

    def test_expired_worker_heartbeat_never_enables_execution(self):
        from datetime import datetime, timedelta, timezone
        from app.models.plugin import SandboxWorkerState
        from app.services.sandbox_service import sandbox_availability
        self.db.add(SandboxWorkerState(owner="old", status="ready", detail="stale", heartbeat_at=datetime.now(timezone.utc)-timedelta(minutes=1)))
        self.db.commit()
        self.assertFalse(sandbox_availability(self.db)["available"])

    def test_modified_skill_version_invalidates_plugin_scope(self):
        from app.models.tool_config import UserSkillInstallation
        self.enable()
        skill = self.db.scalar(select(UserSkillInstallation).where(UserSkillInstallation.user_id == "u"))
        skill.manifest_digest = "changed"
        self.db.commit()
        with self.assertRaises(SkillCatalogError):
            SkillCatalog().resolve_for_execution(db=self.db, user_id="u", project_id="p", skill_key="workspace.document-review")

    def test_artifact_requires_success_explicit_save_and_respects_read_only_policy(self):
        from fastapi import HTTPException
        from types import SimpleNamespace
        from app.api.routes.plugins import save_artifact
        from app.models.tool_config import WorkspaceAgentPolicy
        self.enable()
        preview = self.preview()
        user = self.db.get(User, "u")
        with self.assertRaises(HTTPException):
            save_artifact(preview["id"], 0, db=self.db, user=user)
        row = self.db.get(SandboxJob, preview["id"])
        row.status = "succeeded"
        row.result_json = json.dumps({"artifacts": [{"name": "inventory.md", "content": "# 新清单\n仅新草稿"}]})
        self.db.commit()
        with tempfile.TemporaryDirectory() as root, patch("app.services.document_delivery_service.settings", SimpleNamespace(upload_dir=root)):
            saved = save_artifact(row.id, 0, db=self.db, user=user)
            self.assertNotEqual(saved.file_id, "f")
            self.assertEqual(self.db.get(ProjectFile, "f").parsed_text, "授权资料内容")
            self.assertIn("sandbox-job:", self.db.get(ProjectFile, saved.file_id).parsed_text)
            self.db.add(WorkspaceAgentPolicy(project_id="p", permission_mode="read_only"))
            self.db.commit()
            with self.assertRaises(HTTPException) as error:
                save_artifact(row.id, 0, db=self.db, user=user)
            self.assertEqual(error.exception.status_code, 400)

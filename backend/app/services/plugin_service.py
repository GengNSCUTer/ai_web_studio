"""审核插件只组合已有能力，安装、项目启用和会话激活分别检查。"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.plugin import PluginInstallation, PluginRelease, ProjectPlugin
from app.models.tool_config import SkillInstallationRevision, UserSkillInstallation
from app.repositories.project_repo import ProjectRepository
from app.services.skill_catalog import SkillCatalog, SkillCatalogError


class PluginError(ValueError):
    pass


class PluginService:
    def __init__(self, db: Session, catalog_path: Path | None = None):
        self.db = db
        self.skills = SkillCatalog()
        path = catalog_path or Path(__file__).resolve().parents[1] / "plugin_manifests/reviewed_plugins.json"
        self.definitions = {item["plugin_key"]: item for item in json.loads(path.read_text())}
        for item in self.definitions.values():
            if item.get("review_status") != "approved" or item.get("compatibility") != "plugin-v1":
                raise PluginError("插件目录包含未审核或不兼容版本。")
            if not set(item["skills"]).issubset(self.skills._definitions):
                raise PluginError("插件引用了未审核 Skill。")
            from app.services.sandbox_policy import script_digest
            item["script_snapshots"] = {key: script_digest(key) for key in item.get("scripts", [])}

    def check_project(self, user_id: str, project_id: str):
        if not ProjectRepository(self.db).get_by_user(project_id, user_id):
            raise PluginError("工作区不存在或无权访问。")

    def installation(self, user_id: str, key: str):
        return self.db.scalar(select(PluginInstallation).where(
            PluginInstallation.user_id == user_id, PluginInstallation.plugin_key == key))

    def installed_release(self, user_id: str, key: str) -> tuple[PluginInstallation, dict]:
        item = self.installation(user_id, key)
        release = self.db.get(PluginRelease, item.release_id) if item else None
        if not release or release.user_id != user_id or release.plugin_key != key:
            raise PluginError("插件尚未安装或版本记录缺失。")
        manifest = json.loads(release.manifest_json)
        if manifest.get("review_status") != "approved":
            raise PluginError("插件版本未通过审核。")
        return item, manifest

    def preflight(self, user_id: str, key: str, project_id: str | None = None, manifest: dict | None = None):
        if project_id:
            self.check_project(user_id, project_id)
        definition = manifest or self.definitions.get(key)
        if not definition:
            raise PluginError("插件不存在或未审核。")
        availability = self.skills._availability(db=self.db, user_id=user_id, project_id=project_id)
        missing = sorted({tool for skill in definition["skills"]
                          for tool in availability[skill]["missing_required_tool_keys"]})
        for skill_key, snapshot in definition.get("skill_snapshots", {}).items():
            row = self.db.scalar(select(UserSkillInstallation).where(
                UserSkillInstallation.user_id == user_id, UserSkillInstallation.skill_key == skill_key))
            if not row or row.manifest_digest != snapshot["digest"]:
                missing.append(f"skill_version:{skill_key}")
        from app.services.sandbox_policy import script_digest
        for script_key, digest in definition.get("script_snapshots", {}).items():
            if script_digest(script_key) != digest:
                missing.append(f"script_version:{script_key}")
        # 版本号无法防止同版本权限被替换；安装确认必须对应刚预览的完整审核内容。
        revision = hashlib.sha256(json.dumps(self.definitions[key], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return {"plugin_key": key, "version": definition["version"], "catalog_revision": revision, "permissions": definition["permissions"],
                "dependencies": definition["dependencies"], "domains": definition["domains"],
                "missing_tool_keys": missing, "can_install": True, "can_enable": not missing,
                "notice": "安装不启用项目，不激活会话，不批准文件修改或脚本联网。"}

    def list(self, user_id: str, project_id: str | None = None):
        if project_id:
            self.check_project(user_id, project_id)
        items = []
        for key, definition in self.definitions.items():
            installation = self.installation(user_id, key)
            active = self.db.scalar(select(ProjectPlugin).where(
                ProjectPlugin.user_id == user_id, ProjectPlugin.project_id == project_id,
                ProjectPlugin.plugin_key == key)) if project_id else None
            release = self.db.get(PluginRelease, installation.release_id) if installation else None
            installed = json.loads(release.manifest_json) if release else definition
            skill_defs = [self.skills._definitions[skill] for skill in installed["skills"]]
            items.append({**definition, "is_installed": bool(installation),
                          "installed_release_id": release.id if release else None,
                          "installed_version": release.version if release else None,
                          "is_project_enabled": bool(active and active.is_enabled),
                          "update_available": bool(release and {k: v for k, v in json.loads(release.manifest_json).items() if k != "skill_snapshots"} != definition),
                          "tool_keys": sorted({k for s in skill_defs for k in s["required_tool_keys"] + s["optional_tool_keys"]}),
                          "preflight": self.preflight(user_id, key, project_id, installed)})
        return items

    def install(self, user_id: str, key: str):
        definition = self.definitions.get(key)
        if not definition:
            raise PluginError("插件不存在或未审核。")
        definition = {**definition, "skill_snapshots": {s: {"version": self.skills._definitions[s]["version"],
                       "digest": self.skills._definitions[s]["manifest_digest"]} for s in definition["skills"]}}
        current = self.installation(user_id, key)
        if current and json.loads(self.db.get(PluginRelease, current.release_id).manifest_json) == definition:
            return
        # 在同一短事务保存插件和 Skill 审核版本；不能调用内部自行 commit 的安装方法。
        for skill_key in definition["skills"]:
            skill = self.skills._definitions[skill_key]
            row = self.db.scalar(select(UserSkillInstallation).where(
                UserSkillInstallation.user_id == user_id, UserSkillInstallation.skill_key == skill_key))
            if row is None:
                self.db.add(UserSkillInstallation(user_id=user_id, skill_key=skill_key,
                    manifest_version=skill["version"], manifest_digest=skill["manifest_digest"], is_enabled=False))
            if row is None or row.manifest_digest != skill["manifest_digest"]:
                self.db.add(SkillInstallationRevision(user_id=user_id, skill_key=skill_key,
                    manifest_version=skill["version"], manifest_digest=skill["manifest_digest"],
                    manifest_json=self.skills._json(skill), source_kind=skill["source_kind"],
                    security_review_status=skill["security_review_status"], action="install"))
                if row is not None:
                    row.manifest_version, row.manifest_digest = skill["version"], skill["manifest_digest"]
        release = PluginRelease(user_id=user_id, plugin_key=key, version=definition["version"],
                                manifest_json=json.dumps(definition, ensure_ascii=False))
        self.db.add(release)
        self.db.flush()
        item = self.installation(user_id, key)
        if item:
            item.release_id = release.id
        else:
            self.db.add(PluginInstallation(user_id=user_id, plugin_key=key, release_id=release.id))
        # 升级不静默继承旧版本项目授权；用户需重新查看权限并启用。
        for row in self.db.scalars(select(ProjectPlugin).where(
                ProjectPlugin.user_id == user_id, ProjectPlugin.plugin_key == key)):
            row.is_enabled = False
        self.db.commit()

    def enable(self, user_id: str, key: str, project_id: str, enabled: bool):
        self.check_project(user_id, project_id)
        _, manifest = self.installed_release(user_id, key)
        check = self.preflight(user_id, key, project_id, manifest)
        if enabled and not check["can_enable"]:
            raise PluginError("缺少工具或凭据：" + ", ".join(check["missing_tool_keys"]))
        row = self.db.scalar(select(ProjectPlugin).where(
            ProjectPlugin.project_id == project_id, ProjectPlugin.plugin_key == key))
        if row is None:
            row = ProjectPlugin(project_id=project_id, user_id=user_id, plugin_key=key)
            self.db.add(row)
        row.is_enabled = enabled
        if enabled:
            for skill in self.db.scalars(select(UserSkillInstallation).where(
                    UserSkillInstallation.user_id == user_id, UserSkillInstallation.skill_key.in_(manifest["skills"]))):
                skill.is_enabled = True
        self.db.commit()

    def require_enabled(self, user_id: str, key: str, project_id: str):
        self.check_project(user_id, project_id)
        item, manifest = self.installed_release(user_id, key)
        enabled = self.db.scalar(select(ProjectPlugin).where(
            ProjectPlugin.user_id == user_id, ProjectPlugin.project_id == project_id,
            ProjectPlugin.plugin_key == key, ProjectPlugin.is_enabled.is_(True)))
        if not enabled:
            raise PluginError("插件未在当前工作区启用。")
        if not self.preflight(user_id, key, project_id, manifest)["can_enable"]:
            raise PluginError("插件依赖或已锁定 Skill 版本发生变化，请先重新审核。")
        return item, manifest

    def rollback(self, user_id: str, key: str):
        item, _ = self.installed_release(user_id, key)
        releases = list(self.db.scalars(select(PluginRelease).where(
            PluginRelease.user_id == user_id, PluginRelease.plugin_key == key)
            .order_by(PluginRelease.created_at.desc(), PluginRelease.id.desc())))
        previous = next((r for r in releases if r.id != item.release_id and
                         r.manifest_json != self.db.get(PluginRelease, item.release_id).manifest_json), None)
        if not previous:
            raise PluginError("暂无可回滚的不同审核版本。")
        manifest = json.loads(previous.manifest_json)
        if manifest.get("review_status") != "approved":
            raise PluginError("历史版本未通过审核。")
        for skill_key, snapshot in manifest.get("skill_snapshots", {}).items():
            previous_skill = self.skills._load_installed_snapshot(db=self.db, user_id=user_id, skill_key=skill_key,
                    manifest_version=snapshot["version"], manifest_digest=snapshot["digest"])
            row = self.db.scalar(select(UserSkillInstallation).where(
                UserSkillInstallation.user_id == user_id, UserSkillInstallation.skill_key == skill_key))
            if row:
                row.manifest_version, row.manifest_digest = previous_skill["version"], previous_skill["manifest_digest"]
        item.release_id = previous.id
        for row in self.db.scalars(select(ProjectPlugin).where(
                ProjectPlugin.user_id == user_id, ProjectPlugin.plugin_key == key)):
            row.is_enabled = False
        self.db.commit()

    def uninstall(self, user_id: str, key: str):
        item, _ = self.installed_release(user_id, key)
        for row in self.db.scalars(select(ProjectPlugin).where(
                ProjectPlugin.user_id == user_id, ProjectPlugin.plugin_key == key)):
            self.db.delete(row)
        self.db.delete(item)
        self.db.commit()


def require_plugin_skill_scope(db: Session, user_id: str, project_id: str | None, skill_key: str):
    """仅约束通过插件安装的技能；历史直接安装不被强制迁移。"""
    releases = list(db.scalars(select(PluginRelease).where(PluginRelease.user_id == user_id)))
    managed_keys = {r.plugin_key for r in releases if skill_key in json.loads(r.manifest_json).get("skills", [])}
    if not managed_keys:
        return
    for installation in db.scalars(select(PluginInstallation).where(
            PluginInstallation.user_id == user_id, PluginInstallation.plugin_key.in_(managed_keys))):
        release = db.get(PluginRelease, installation.release_id)
        if release and skill_key in json.loads(release.manifest_json).get("skills", []):
            snapshot = json.loads(release.manifest_json).get("skill_snapshots", {}).get(skill_key)
            skill_row = db.scalar(select(UserSkillInstallation).where(
                UserSkillInstallation.user_id == user_id, UserSkillInstallation.skill_key == skill_key))
            if not snapshot or not skill_row or skill_row.manifest_digest != snapshot["digest"]:
                continue
            enabled = db.scalar(select(ProjectPlugin).where(
                ProjectPlugin.project_id == project_id, ProjectPlugin.user_id == user_id,
                ProjectPlugin.plugin_key == installation.plugin_key, ProjectPlugin.is_enabled.is_(True)))
            if enabled:
                return
    raise SkillCatalogError("所属插件未在当前工作区启用；安装不等于会话授权。")

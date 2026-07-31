from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tool_config import UserSkillInstallation
from app.services.tools.catalog import ToolCatalog
from app.services.tools.credentials import ToolCredentialResolver


class SkillCatalogError(ValueError):
    pass


class SkillCatalog:
    """Loads reviewed, declarative Skill manifests.

    A manifest may reference already-audited Tool/MCP capabilities. It contains no
    executable Python, shell, remote URL, or credential, so installation cannot
    expand the runtime's permission boundary.
    """

    def __init__(self, manifest_path: Path | None = None) -> None:
        self.manifest_path = manifest_path or Path(__file__).resolve().parents[1] / "skill_manifests" / "default_skills.json"
        self._definitions = self._load()

    def list_for_user(self, *, db: Session, user_id: str) -> list[dict[str, Any]]:
        installations = {
            item.skill_key: item
            for item in db.scalars(select(UserSkillInstallation).where(UserSkillInstallation.user_id == user_id)).all()
        }
        missing_by_skill = self._missing_tools(db=db, user_id=user_id)
        result: list[dict[str, Any]] = []
        for skill in self._definitions.values():
            installation = installations.get(skill["skill_key"])
            required_tools = list(skill["required_tool_keys"])
            result.append(
                {
                    **skill,
                    "is_installed": installation is not None,
                    "is_enabled": bool(installation.is_enabled) if installation else False,
                    "installed_version": installation.manifest_version if installation else None,
                    "missing_tool_keys": missing_by_skill[skill["skill_key"]],
                }
            )
        return result

    def install_or_update(self, *, db: Session, user_id: str, skill_key: str, is_enabled: bool) -> UserSkillInstallation:
        skill = self._definitions.get(skill_key)
        if not skill:
            raise SkillCatalogError("Skill 不存在或未经过发布审核。")
        missing = self._missing_tools(db=db, user_id=user_id)[skill_key]
        if is_enabled and missing:
            raise SkillCatalogError(f"Skill 缺少可执行能力：{', '.join(missing)}")
        installation = db.scalars(
            select(UserSkillInstallation)
            .where(UserSkillInstallation.user_id == user_id, UserSkillInstallation.skill_key == skill_key)
            .limit(1)
        ).first()
        if not installation:
            installation = UserSkillInstallation(
                user_id=user_id,
                skill_key=skill_key,
                manifest_version=skill["version"],
                is_enabled=is_enabled,
            )
            db.add(installation)
        else:
            installation.manifest_version = skill["version"]
            installation.is_enabled = is_enabled
        try:
            db.commit()
        except IntegrityError as exc:
            # PUT is a state-setting operation. Two browser tabs may race on the
            # first installation; converge on the unique user/skill row.
            db.rollback()
            installation = db.scalars(
                select(UserSkillInstallation)
                .where(
                    UserSkillInstallation.user_id == user_id,
                    UserSkillInstallation.skill_key == skill_key,
                )
                .limit(1)
            ).first()
            if not installation:
                raise SkillCatalogError("Skill 安装状态保存失败。") from exc
            installation.manifest_version = skill["version"]
            installation.is_enabled = is_enabled
            db.commit()
        db.refresh(installation)
        return installation

    def _missing_tools(self, *, db: Session, user_id: str) -> dict[str, list[str]]:
        definitions = {
            item.tool_key: item
            for item in ToolCatalog(db=db, user_id=user_id).list_definitions()
        }
        resolver = ToolCredentialResolver(db)
        missing_by_skill: dict[str, list[str]] = {}
        for skill in self._definitions.values():
            missing: list[str] = []
            for tool_key in skill["required_tool_keys"]:
                definition = definitions.get(tool_key)
                if not definition:
                    missing.append(tool_key)
                    continue
                if definition.credential_required:
                    credential = resolver.resolve(
                        user_id=user_id,
                        provider_key=definition.credential_provider,
                    )
                    if not credential.is_enabled or not credential.api_key:
                        missing.append(tool_key)
            missing_by_skill[skill["skill_key"]] = missing
        return missing_by_skill

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            records = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillCatalogError("Skill manifest 无法读取。") from exc
        if not isinstance(records, list):
            raise SkillCatalogError("Skill manifest 必须是数组。")
        definitions: dict[str, dict[str, Any]] = {}
        for raw in records:
            if not isinstance(raw, dict):
                raise SkillCatalogError("Skill manifest 包含非法记录。")
            key = str(raw.get("skill_key") or "").strip()
            version = str(raw.get("version") or "").strip()
            name = str(raw.get("display_name") or "").strip()
            description = str(raw.get("description") or "").strip()
            required = raw.get("required_tool_keys") or []
            if not key or not version or not name or not description or not isinstance(required, list):
                raise SkillCatalogError("Skill manifest 缺少必要字段。")
            if key in definitions or any(not isinstance(item, str) or not item for item in required):
                raise SkillCatalogError("Skill manifest 的 key 或 required_tool_keys 非法。")
            definitions[key] = {
                "skill_key": key,
                "version": version,
                "display_name": name,
                "description": description,
                "required_tool_keys": required,
                "risk_declaration": str(raw.get("risk_declaration") or "uses_existing_capabilities_only"),
            }
        return definitions

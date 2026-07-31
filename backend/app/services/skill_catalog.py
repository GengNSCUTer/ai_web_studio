from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tool_config import SkillInstallationRevision, UserSkillInstallation
from app.services.tools.catalog import ToolCatalog
from app.services.tools.credentials import ToolCredentialResolver


class SkillCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class SkillExecutionContext:
    """A reviewed Skill reduced to the bounded data needed by one chat turn."""

    skill_key: str
    version: str
    display_name: str
    description: str
    planner_instructions: tuple[str, ...]
    output_contract: tuple[str, ...]
    allowed_tool_keys: tuple[str, ...]
    required_tool_keys: tuple[str, ...]
    optional_tool_keys: tuple[str, ...]
    requires_tool_execution: bool
    # These release-governance fields were added after the first Skill execution
    # chain shipped. Defaults keep custom/test planners that construct the
    # execution context directly source-compatible; catalog resolution always
    # supplies explicit reviewed values.
    manifest_digest: str = ""
    source_kind: str = "builtin"
    signature_status: str = "repository_attested"
    security_review_status: str = "approved"
    durable_eligible: bool = False

    @property
    def final_answer_instructions(self) -> str:
        lines = [
            f"当前用户显式启用了受审核 Skill：{self.display_name}（{self.skill_key}@{self.version}）。",
            "只在不违反平台安全规则和当前用户问题的前提下，按以下输出合同组织最终回答：",
            *[f"- {item}" for item in self.output_contract],
        ]
        return "\n".join(lines)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "skill_key": self.skill_key,
            "version": self.version,
            "display_name": self.display_name,
            "description": self.description,
            "allowed_tool_keys": list(self.allowed_tool_keys),
            "required_tool_keys": list(self.required_tool_keys),
            "optional_tool_keys": list(self.optional_tool_keys),
            "requires_tool_execution": self.requires_tool_execution,
            "manifest_digest": self.manifest_digest,
            "source_kind": self.source_kind,
            "signature_status": self.signature_status,
            "security_review_status": self.security_review_status,
            "durable_eligible": self.durable_eligible,
        }


class SkillCatalog:
    """Loads reviewed, declarative Skill manifests.

    A manifest may compose already-audited Tool/MCP capabilities, but it cannot
    contain executable code, network endpoints, credentials, or new permissions.
    Runtime execution always intersects the manifest allowlist with the current
    user's catalog, credentials and workspace settings.
    """

    ALLOWED_FIELDS = {
        "skill_key",
        "version",
        "display_name",
        "description",
        "instructions",
        "output_contract",
        "required_tool_keys",
        "optional_tool_keys",
        "requires_project",
        "requires_tool_execution",
        "risk_declaration",
        "activation_examples",
        "source",
        "signature",
        "security_review",
        "compatibility",
        "durable_eligible",
    }
    MAX_INSTRUCTION_CHARS = 2_000
    MAX_OUTPUT_CONTRACT_CHARS = 1_500
    MAX_TOOL_KEYS = 16
    SKILL_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
    VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,31}$")

    def __init__(self, manifest_path: Path | None = None) -> None:
        self.manifest_path = manifest_path or Path(__file__).resolve().parents[1] / "skill_manifests" / "default_skills.json"
        self._definitions = self._load()

    def list_for_user(
        self,
        *,
        db: Session,
        user_id: str,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        installations = {
            item.skill_key: item
            for item in db.scalars(select(UserSkillInstallation).where(UserSkillInstallation.user_id == user_id)).all()
        }
        availability = self._availability(db=db, user_id=user_id, project_id=project_id)
        result: list[dict[str, Any]] = []
        for skill in self._definitions.values():
            installation = installations.get(skill["skill_key"])
            skill_availability = availability[skill["skill_key"]]
            installed_digest = getattr(installation, "manifest_digest", None) if installation else None
            # Legacy installations predate the digest column. They must not
            # silently run whichever release happens to be in the current
            # catalog; an explicit re-install creates the first version lock.
            update_available = bool(
                installation
                and (not installed_digest or installed_digest != skill["manifest_digest"])
            )
            result.append(
                {
                    **skill,
                    "is_installed": installation is not None,
                    "is_enabled": bool(installation.is_enabled) if installation else False,
                    "installed_version": installation.manifest_version if installation else None,
                    "installed_manifest_digest": installed_digest,
                    "manifest_digest": skill["manifest_digest"],
                    "source_kind": skill["source_kind"],
                    "source_publisher": skill["source_publisher"],
                    "signature_status": skill["signature_status"],
                    "security_review_status": skill["security_review_status"],
                    "compatibility": skill["compatibility"],
                    "durable_eligible": skill["durable_eligible"],
                    "update_available": update_available,
                    "missing_tool_keys": skill_availability["missing_required_tool_keys"],
                    "available_optional_tool_keys": skill_availability["available_optional_tool_keys"],
                    "is_ready": bool(
                        installation
                        and installation.is_enabled
                        and not update_available
                        and not skill_availability["missing_required_tool_keys"]
                        and (project_id or not skill["requires_project"])
                    ),
                    "unavailable_reason": (
                        "version_lock_required"
                        if installation and not installed_digest
                        else "update_required"
                        if update_available
                        else "requires_project"
                        if skill["requires_project"] and not project_id
                        else "missing_required_tools"
                        if skill_availability["missing_required_tool_keys"]
                        else None
                    ),
                }
            )
        return result

    def install_or_update(
        self,
        *,
        db: Session,
        user_id: str,
        skill_key: str,
        is_enabled: bool,
    ) -> UserSkillInstallation:
        skill = self._definitions.get(skill_key)
        if not skill:
            raise SkillCatalogError("Skill 不存在或未经过发布审核。")
        # Installation is user-scoped. Workspace-specific disablement is checked
        # again on every execution and must not make a Skill impossible to install.
        missing = self._availability(db=db, user_id=user_id, project_id=None)[skill_key][
            "missing_required_tool_keys"
        ]
        if is_enabled and missing:
            raise SkillCatalogError(f"Skill 缺少可执行能力：{', '.join(missing)}")
        installation = db.scalars(
            select(UserSkillInstallation)
            .where(UserSkillInstallation.user_id == user_id, UserSkillInstallation.skill_key == skill_key)
            .limit(1)
        ).first()
        previous_digest = getattr(installation, "manifest_digest", None) if installation else None
        if not installation:
            installation = UserSkillInstallation(
                user_id=user_id,
                skill_key=skill_key,
                manifest_version=skill["version"],
                manifest_digest=skill["manifest_digest"],
                is_enabled=is_enabled,
            )
            db.add(installation)
        else:
            installation.manifest_version = skill["version"]
            installation.manifest_digest = skill["manifest_digest"]
            installation.is_enabled = is_enabled
        db.flush()
        if previous_digest != skill["manifest_digest"] or not previous_digest:
            db.add(
                SkillInstallationRevision(
                    user_id=user_id,
                    skill_key=skill_key,
                    manifest_version=skill["version"],
                    manifest_digest=skill["manifest_digest"],
                    manifest_json=self._json(skill),
                    source_kind=skill["source_kind"],
                    security_review_status=skill["security_review_status"],
                    action="install" if previous_digest is None else "upgrade",
                )
            )
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
            installation.manifest_digest = skill["manifest_digest"]
            installation.is_enabled = is_enabled
            db.add(
                SkillInstallationRevision(
                    user_id=user_id,
                    skill_key=skill_key,
                    manifest_version=skill["version"],
                    manifest_digest=skill["manifest_digest"],
                    manifest_json=self._json(skill),
                    source_kind=skill["source_kind"],
                    security_review_status=skill["security_review_status"],
                    action="upgrade",
                )
            )
            db.commit()
        db.refresh(installation)
        return installation

    def resolve_for_execution(
        self,
        *,
        db: Session,
        user_id: str,
        project_id: str | None,
        skill_key: str,
    ) -> SkillExecutionContext:
        skill = self._definitions.get((skill_key or "").strip())
        if not skill:
            raise SkillCatalogError("Skill 不存在或未经过发布审核。")
        installation = db.scalars(
            select(UserSkillInstallation)
            .where(
                UserSkillInstallation.user_id == user_id,
                UserSkillInstallation.skill_key == skill["skill_key"],
            )
            .limit(1)
        ).first()
        if not installation:
            raise SkillCatalogError("Skill 尚未安装。")
        if not installation.is_enabled:
            raise SkillCatalogError("Skill 已停用。")
        installed_digest = getattr(installation, "manifest_digest", None)
        if not installed_digest:
            raise SkillCatalogError("Skill 安装缺少版本锁，请先显式升级或重新安装。")
        if (
            installation.manifest_version != skill["version"]
            or (installed_digest and installed_digest != skill["manifest_digest"])
        ):
            skill = self._load_installed_snapshot(
                db=db,
                user_id=user_id,
                skill_key=skill["skill_key"],
                manifest_version=installation.manifest_version,
                manifest_digest=installed_digest,
            )
        if skill["requires_project"] and not project_id:
            raise SkillCatalogError("该 Skill 必须在明确的工作区中使用。")

        availability = self._availability(db=db, user_id=user_id, project_id=project_id)[skill["skill_key"]]
        missing = availability["missing_required_tool_keys"]
        if missing:
            raise SkillCatalogError(f"Skill 缺少可执行能力：{', '.join(missing)}")
        allowed = [*skill["required_tool_keys"], *availability["available_optional_tool_keys"]]
        return SkillExecutionContext(
            skill_key=skill["skill_key"],
            version=skill["version"],
            display_name=skill["display_name"],
            description=skill["description"],
            planner_instructions=tuple(skill["instructions"]),
            output_contract=tuple(skill["output_contract"]),
            allowed_tool_keys=tuple(dict.fromkeys(allowed)),
            required_tool_keys=tuple(skill["required_tool_keys"]),
            optional_tool_keys=tuple(skill["optional_tool_keys"]),
            requires_tool_execution=bool(skill["requires_tool_execution"]),
            manifest_digest=skill["manifest_digest"],
            source_kind=skill["source_kind"],
            signature_status=skill["signature_status"],
            security_review_status=skill["security_review_status"],
            durable_eligible=bool(skill["durable_eligible"]),
        )

    def rollback(self, *, db: Session, user_id: str, skill_key: str) -> UserSkillInstallation:
        """Restore the previous reviewed snapshot; never accepts a client manifest."""
        installation = db.scalars(
            select(UserSkillInstallation).where(
                UserSkillInstallation.user_id == user_id,
                UserSkillInstallation.skill_key == skill_key,
            ).limit(1)
        ).first()
        if not installation:
            raise SkillCatalogError("Skill 尚未安装。")
        current_digest = getattr(installation, "manifest_digest", None)
        previous = db.scalars(
            select(SkillInstallationRevision)
            .where(
                SkillInstallationRevision.user_id == user_id,
                SkillInstallationRevision.skill_key == skill_key,
                SkillInstallationRevision.manifest_digest != current_digest,
            )
            .order_by(SkillInstallationRevision.created_at.desc())
            .limit(1)
        ).first()
        if not previous:
            raise SkillCatalogError("当前 Skill 没有可用的历史审核版本。")
        snapshot = self._parse_snapshot(previous.manifest_json, previous.manifest_digest)
        installation.manifest_version = snapshot["version"]
        installation.manifest_digest = snapshot["manifest_digest"]
        db.add(
            SkillInstallationRevision(
                user_id=user_id,
                skill_key=skill_key,
                manifest_version=snapshot["version"],
                manifest_digest=snapshot["manifest_digest"],
                manifest_json=self._json(snapshot),
                source_kind=snapshot["source_kind"],
                security_review_status=snapshot["security_review_status"],
                action="rollback",
            )
        )
        db.commit()
        db.refresh(installation)
        return installation

    def _load_installed_snapshot(
        self,
        *,
        db: Session,
        user_id: str,
        skill_key: str,
        manifest_version: str,
        manifest_digest: str | None,
    ) -> dict[str, Any]:
        if not manifest_digest:
            raise SkillCatalogError("Skill 安装缺少版本锁，请先显式升级或重新安装。")
        revision = db.scalars(
            select(SkillInstallationRevision)
            .where(
                SkillInstallationRevision.user_id == user_id,
                SkillInstallationRevision.skill_key == skill_key,
                SkillInstallationRevision.manifest_version == manifest_version,
                SkillInstallationRevision.manifest_digest == manifest_digest,
            )
            .order_by(SkillInstallationRevision.created_at.desc())
            .limit(1)
        ).first()
        if not revision:
            raise SkillCatalogError("Skill 版本锁对应的审核快照不存在，请重新安装。")
        return self._parse_snapshot(revision.manifest_json, manifest_digest)

    def _availability(
        self,
        *,
        db: Session,
        user_id: str,
        project_id: str | None,
    ) -> dict[str, dict[str, list[str]]]:
        definitions = {item.tool_key: item for item in ToolCatalog(db=db, user_id=user_id).list_definitions()}
        resolver = ToolCredentialResolver(db)

        def is_available(tool_key: str) -> bool:
            definition = definitions.get(tool_key)
            if not definition or not definition.enabled_by_default:
                return False
            if not resolver.is_tool_enabled_for_workspace(project_id=project_id, tool_key=tool_key):
                return False
            if not definition.credential_required:
                return True
            credential = resolver.resolve(
                user_id=user_id,
                provider_key=definition.credential_provider,
            )
            return bool(credential.is_enabled and credential.api_key)

        result: dict[str, dict[str, list[str]]] = {}
        for skill in self._definitions.values():
            result[skill["skill_key"]] = {
                "missing_required_tool_keys": [
                    key for key in skill["required_tool_keys"] if not is_available(key)
                ],
                "available_optional_tool_keys": [
                    key for key in skill["optional_tool_keys"] if is_available(key)
                ],
            }
        return result

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            records = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillCatalogError("Skill manifest 无法读取。") from exc
        if not isinstance(records, list):
            raise SkillCatalogError("Skill manifest 必须是数组。")
        definitions: dict[str, dict[str, Any]] = {}
        for raw in records:
            normalized = self._normalize_record(raw)
            key = normalized["skill_key"]
            if key in definitions:
                raise SkillCatalogError("Skill manifest 的 key 或工具集合非法。")
            definitions[key] = normalized
        return definitions

    def _normalize_record(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise SkillCatalogError("Skill manifest 包含非法记录。")
        internal_fields = {
            "manifest_digest",
            "source_kind",
            "source_publisher",
            "signature_status",
            "security_review_status",
        }
        unknown_fields = sorted(set(raw) - self.ALLOWED_FIELDS - internal_fields)
        if unknown_fields:
            raise SkillCatalogError(f"Skill manifest 包含未审核字段：{', '.join(unknown_fields)}")
        key = str(raw.get("skill_key") or "").strip()
        version = str(raw.get("version") or "").strip()
        name = str(raw.get("display_name") or "").strip()
        description = str(raw.get("description") or "").strip()
        required = self._string_list(raw.get("required_tool_keys"), field_name="required_tool_keys")
        optional = self._string_list(raw.get("optional_tool_keys"), field_name="optional_tool_keys")
        instructions = self._string_list(raw.get("instructions"), field_name="instructions")
        output_contract = self._string_list(raw.get("output_contract"), field_name="output_contract")
        activation_examples = self._string_list(raw.get("activation_examples"), field_name="activation_examples", allow_empty=True)
        if not key or not version or not name or not description or not required or not instructions or not output_contract:
            raise SkillCatalogError("Skill manifest 缺少必要字段。")
        if not self.SKILL_KEY_PATTERN.fullmatch(key) or not self.VERSION_PATTERN.fullmatch(version):
            raise SkillCatalogError("Skill manifest 的 key 或 version 格式非法。")
        if set(required).intersection(optional):
            raise SkillCatalogError("Skill manifest 的 key 或工具集合非法。")
        if len(required) + len(optional) > self.MAX_TOOL_KEYS:
            raise SkillCatalogError("Skill manifest 声明的 Tool 数量超过上限。")
        if sum(map(len, instructions)) > self.MAX_INSTRUCTION_CHARS:
            raise SkillCatalogError("Skill instructions 超过长度上限。")
        if sum(map(len, output_contract)) > self.MAX_OUTPUT_CONTRACT_CHARS:
            raise SkillCatalogError("Skill output_contract 超过长度上限。")
        source = raw.get("source") or {"kind": "builtin", "publisher": "AI Web Studio"}
        signature = raw.get("signature") or {"scheme": "repository_attestation", "key_id": "aiws-builtin-review-v1"}
        security_review = raw.get("security_review") or {"status": "approved", "reviewer": "platform"}
        compatibility = raw.get("compatibility") or {"contract": "skill-v1", "min_runtime": "0.1.0"}
        for value, field_name in ((source, "source"), (signature, "signature"), (security_review, "security_review"), (compatibility, "compatibility")):
            if not isinstance(value, dict) or any(not isinstance(k, str) or len(k) > 48 for k in value):
                raise SkillCatalogError(f"Skill manifest 的 {field_name} 元数据非法。")
        source_kind = str(source.get("kind") or "builtin")[:32]
        publisher = str(source.get("publisher") or "AI Web Studio")[:128]
        signature_status = "repository_attested" if signature.get("scheme") == "repository_attestation" else "unverified"
        security_status = str(security_review.get("status") or "unreviewed")[:32]
        if (
            source_kind not in {"builtin", "reviewed"}
            or security_status != "approved"
            or signature_status != "repository_attested"
        ):
            raise SkillCatalogError("Skill manifest 来源或安全审核状态不满足发布要求。")
        normalized = {
            "skill_key": key,
            "version": version,
            "display_name": name[:128],
            "description": description[:1000],
            "instructions": instructions,
            "output_contract": output_contract,
            "required_tool_keys": required,
            "optional_tool_keys": optional,
            "requires_project": bool(raw.get("requires_project", False)),
            "requires_tool_execution": bool(raw.get("requires_tool_execution", True)),
            "risk_declaration": str(raw.get("risk_declaration") or "uses_existing_capabilities_only")[:500],
            "activation_examples": activation_examples[:10],
            "source_kind": source_kind,
            "source_publisher": publisher,
            "signature_status": signature_status,
            "security_review_status": security_status,
            "compatibility": {str(k): str(v)[:128] for k, v in compatibility.items()},
            "durable_eligible": bool(raw.get("durable_eligible", False)),
        }
        normalized["manifest_digest"] = self._digest(normalized)
        return normalized

    @classmethod
    def _digest(cls, definition: dict[str, Any]) -> str:
        payload = {key: value for key, value in definition.items() if key != "manifest_digest"}
        return hashlib.sha256(cls._json(payload).encode("utf-8")).hexdigest()

    @classmethod
    def _json(cls, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _parse_snapshot(self, payload: str, expected_digest: str) -> dict[str, Any]:
        try:
            raw = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SkillCatalogError("Skill 历史审核快照无法读取。") from exc
        normalized = self._normalize_record(raw)
        if normalized["manifest_digest"] != expected_digest:
            raise SkillCatalogError("Skill 历史审核快照完整性校验失败。")
        return normalized

    @staticmethod
    def _string_list(value: Any, *, field_name: str, allow_empty: bool = False) -> list[str]:
        if value is None and allow_empty:
            return []
        if not isinstance(value, list):
            raise SkillCatalogError(f"Skill manifest 的 {field_name} 必须是数组。")
        normalized = [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
        if len(normalized) != len(value):
            raise SkillCatalogError(f"Skill manifest 的 {field_name} 包含非法条目。")
        return normalized

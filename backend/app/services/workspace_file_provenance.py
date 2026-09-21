from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_runtime import FileRevision
from app.models.project_file import ProjectFile


def content_hash(value: str) -> str:
    """生成项目文件内容的稳定摘要，不将正文写入来源元数据。"""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ensure_current_revision(*, db: Session, project_file: ProjectFile) -> FileRevision:
    """返回当前文件内容对应的可审计版本，必要时只补建基线快照。

    ProjectFile 是当前可读内容，FileRevision 是用于审批 CAS、来源绑定和恢复的
    不可变快照。这里不修改文件正文；仅当正文与最新快照不一致时创建新基线。
    调用方仍负责自己的事务提交或回滚。
    """

    current = project_file.parsed_text or ""
    current_hash = content_hash(current)
    latest = db.scalars(
        select(FileRevision)
        .where(FileRevision.project_file_id == project_file.id)
        .order_by(FileRevision.revision_number.desc())
        .limit(1)
    ).first()
    if latest and latest.content_hash == current_hash:
        return latest

    revision = FileRevision(
        project_file_id=project_file.id,
        revision_number=(latest.revision_number + 1) if latest else 1,
        content_hash=current_hash,
        parsed_text=current,
        created_by="baseline_sync" if latest else "baseline",
    )
    db.add(revision)
    db.flush()
    return revision


def current_file_provenance(*, db: Session, project_file: ProjectFile) -> dict[str, str | int]:
    """返回可供 Source/Result Binding 使用的最小版本身份，不暴露用户或项目 ID。"""

    revision = ensure_current_revision(db=db, project_file=project_file)
    return {
        "file_id": project_file.id,
        "revision_id": revision.id,
        "revision_number": revision.revision_number,
        "content_hash": revision.content_hash,
        # 这不是 ACL 授权凭据；实际授权始终由 Provider 的 scoped SQL 重新检查。
        "access_scope": "current_user_current_project",
    }

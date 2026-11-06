"""导入脚本只投递正式Job，等待同一套独立Worker，不在脚本中同步重建活动索引。"""

import json
import time

from sqlalchemy import select

from app.models.knowledge import KnowledgeJob


def wait_for_job(db, *, job_id, user_id, timeout=900):
    """每次读取后释放事务；失败和超时不回退宿主同步索引。"""
    deadline = time.monotonic() + timeout
    while True:
        row = db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id,
            KnowledgeJob.user_id == user_id).execution_options(populate_existing=True))
        if row is None:
            db.rollback()
            raise RuntimeError("导入任务不存在或无权访问")
        status, result = row.status, row.result_json
        db.commit()
        if status == "succeeded":
            return json.loads(result or "{}")
        if status in {"failed", "dead_letter", "cancelled"}:
            raise RuntimeError("导入后台任务失败，请检查Job记录")
        if time.monotonic() >= deadline:
            raise TimeoutError("等待导入任务超时；任务仍保留，请检查独立Worker与Job状态")
        time.sleep(1)


def enqueue_and_wait(service, db, *, knowledge_base_id, document_id, user_id):
    """依次等待解析和索引Job；真正阶段转换由后台LangGraph负责。"""
    parsed = service.enqueue_parse_document(knowledge_base_id, document_id, user_id)
    if parsed is None:
        raise RuntimeError("导入文档不存在")
    wait_for_job(db, job_id=parsed.job.id, user_id=user_id)
    indexed = service.enqueue_index_document(knowledge_base_id, document_id, user_id)
    if indexed is None:
        raise RuntimeError("导入文档不存在")
    return wait_for_job(db, job_id=indexed.job.id, user_id=user_id)

"""摘要版本、修复任务及连续输入批次；提交仍由业务事务负责。"""

from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import delete, select

from app.models.message import Message
from app.models.conversation_summary import ConversationSummaryCheckpoint as Checkpoint, ConversationSummaryJob as Job
from app.repositories.message_repo import MessageRepository


class SummaryRebuildRequired(RuntimeError):
    def __init__(self, code="context_summary_rebuilding"):
        self.code = code
        super().__init__(code)


def summary_source_budget(budget):
    # 保留已有摘要、指令和输出空间；来源预算独立于最终问答的历史窗口。
    return max(256, min(8192, budget.model_context_window - budget.max_summary_tokens - 2048,
                        budget.max_total_tokens // 2))


def select_summary_batch(rows, *, tokenizer, max_tokens, partial_id=None, offset=0):
    """只读连续前缀；长消息分片，消费完全部正文才返回其完整边界。"""
    if tokenizer is None:
        from app.services.tokenizer_service import TokenizerEstimator
        tokenizer = TokenizerEstimator()
    selected, completed = [], []
    remaining = max_tokens
    next_partial, next_offset = None, 0
    for row in rows[:MessageRepository.SUMMARY_BATCH_LIMIT]:
        start = offset if row.id == partial_id else 0
        text = row.content or ""
        rest = text[start:]
        cost = tokenizer.estimate_text_tokens(rest) + 16
        if cost <= remaining:
            selected.append(SimpleNamespace(id=row.id, role=row.role, content=rest))
            completed.append(row.id)
            remaining -= cost
            continue
        if selected:
            break
        # 按字符二分，不切UTF-8字节，避免中文边界损坏。
        low, high = 0, len(rest)
        while low < high:
            mid = (low + high + 1) // 2
            if tokenizer.estimate_text_tokens(rest[:mid]) + 16 <= remaining:
                low = mid
            else:
                high = mid - 1
        if low == 0:
            raise ValueError("summary_input_budget_too_small")
        selected.append(SimpleNamespace(id=row.id, role=row.role, content=rest[:low]))
        next_partial, next_offset = row.id, start + low
        break
    return SimpleNamespace(rows=selected, completed=completed, partial_id=next_partial, offset=next_offset,
                           tokens=sum(tokenizer.estimate_text_tokens(row.content)+16 for row in selected))


class ConversationSummaryService:
    KEEP_CHECKPOINTS = 32

    def __init__(self, db):
        self.db = db
        self.repo = MessageRepository(db)

    def save_checkpoint(self, conversation, *, mode="model"):
        if not conversation.context_summary or not conversation.context_summary_boundary_message_id:
            return
        boundary = self.db.get(Message, conversation.context_summary_boundary_message_id)
        if not boundary or boundary.conversation_id != conversation.id:
            raise ValueError("summary_boundary_missing")
        existing = self.db.scalar(select(Checkpoint).where(Checkpoint.conversation_id == conversation.id,
            Checkpoint.boundary_message_id == boundary.id))
        if existing is None:
            existing = Checkpoint(conversation_id=conversation.id, boundary_message_id=boundary.id)
            self.db.add(existing)
        existing.boundary_created_at, existing.boundary_sequence = boundary.created_at, boundary.sequence or 0
        existing.summary_text, existing.mode = conversation.context_summary, mode
        existing.source_revision = conversation.context_history_revision or 0
        self.db.flush()
        stale = list(self.db.scalars(select(Checkpoint.id).join(Message, Message.id == Checkpoint.boundary_message_id)
            .where(Checkpoint.conversation_id == conversation.id)
            .order_by(*self.repo.order_columns(descending=True)).offset(self.KEEP_CHECKPOINTS)))
        if stale:
            self.db.execute(delete(Checkpoint).where(Checkpoint.id.in_(stale)))

    def invalidate(self, conversation, message_ids):
        """持会话锁调用；原文尚未改动，以原顺序定位失效前缀。"""
        changed = list(self.db.scalars(select(Message).where(Message.conversation_id == conversation.id,
            Message.id.in_(message_ids))))
        if not changed:
            return
        first = min(self.repo._order_key(row) for row in changed)
        boundary = self.db.get(Message, conversation.context_summary_boundary_message_id) if conversation.context_summary_boundary_message_id else None
        target = self.target(conversation)
        # 近期改动不影响摘要或修复目标，避免用户重试本轮回答时重启整份修复。
        jobs = list(self.db.scalars(select(Job).where(Job.conversation_id == conversation.id,
            Job.status.in_(["queued", "running"])) ))
        job_targets = [self.db.get(Message, job.target_message_id) for job in jobs]
        anchors = [row for row in [boundary, target, *job_targets] if row is not None]
        if anchors and first > max(self.repo._order_key(row) for row in anchors) and not (
                conversation.context_summary and boundary is None):
            return
        conversation.context_history_revision = (conversation.context_history_revision or 0) + 1
        for job in jobs:
            job.status, job.partial_summary = "cancelled", None
            job.lease_version += 1
            job.lease_owner, job.lease_expires_at = None, None
        affected = bool(conversation.context_summary and (not boundary or self.repo._order_key(boundary) >= first))
        cp_key = self.repo._sql_order_key()
        prefix = select(Message.id).where(Message.conversation_id == conversation.id, cp_key < first)
        self.db.execute(delete(Checkpoint).where(Checkpoint.conversation_id == conversation.id,
            Checkpoint.boundary_message_id.not_in(prefix)))
        if affected:
            safe = self.db.scalar(select(Checkpoint).join(Message, (Message.id == Checkpoint.boundary_message_id)
                & (Message.conversation_id == Checkpoint.conversation_id)).where(Checkpoint.conversation_id == conversation.id,
                cp_key < first).order_by(*self.repo.order_columns(descending=True)).limit(1))
            conversation.context_summary = safe.summary_text if safe else None
            conversation.context_summary_boundary_message_id = safe.boundary_message_id if safe else None
            conversation.context_summary_updated_at = datetime.now(timezone.utc) if safe else None
        elif conversation.context_summary_boundary_message_id and not boundary:
            conversation.context_summary = conversation.context_summary_boundary_message_id = None
            conversation.context_summary_updated_at = None
        # 排除即将删除/重置的消息，避免将其冻结为任务目标；后续Chat可补齐新增尾部。
        self.ensure_job(conversation, exclude_ids=message_ids)

    def target(self, conversation, *, through_id=None, exclude_ids=(), recent_history_limit=None):
        stmt = select(Message).where(Message.conversation_id == conversation.id,
            Message.role.in_(["user", "assistant"]), Message.status != "streaming")
        if exclude_ids:
            stmt = stmt.where(Message.id.not_in(exclude_ids))
        if through_id:
            through = self.db.get(Message, through_id)
            if not through or through.conversation_id != conversation.id:
                return None
            stmt = stmt.where(self.repo._sql_order_key() <= self.repo._order_key(through))
        # 任务目标是当前注入窗口之外的最后一条消息，固定到本轮历史上界。
        retained = recent_history_limit if recent_history_limit is not None else self.repo.RECENT_CONTEXT_LIMIT
        return self.db.scalar(stmt.order_by(*self.repo.order_columns(descending=True)).offset(retained).limit(1))

    def ensure_job(self, conversation, *, through_id=None, exclude_ids=(), retry_failed=False, recent_history_limit=None):
        self.db.flush()
        target = self.target(conversation, through_id=through_id, exclude_ids=exclude_ids,
                             recent_history_limit=recent_history_limit)
        if target is None:
            return None
        boundary = self.db.get(Message, conversation.context_summary_boundary_message_id) if conversation.context_summary_boundary_message_id else None
        if boundary and self.repo._order_key(boundary) >= self.repo._order_key(target):
            return None
        revision = conversation.context_history_revision or 0
        previous = self.db.scalar(select(Job).where(Job.conversation_id == conversation.id,
            Job.source_revision == revision, Job.status.in_(["queued", "running", "failed"]))
            .order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
        if previous:
            if previous.status == "failed" and retry_failed:
                previous.status, previous.attempts, previous.error_code = "queued", 0, None
                previous.next_attempt_at = None
            return previous
        job = Job(conversation_id=conversation.id, source_revision=revision,
                  target_message_id=target.id, cursor_message_id=conversation.context_summary_boundary_message_id)
        self.db.add(job)
        self.db.flush()
        return job

    def status(self, conversation):
        job = self.db.scalar(select(Job).where(Job.conversation_id == conversation.id)
            .order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
        return {"revision": conversation.context_history_revision or 0,
                "boundary_message_id": conversation.context_summary_boundary_message_id,
                "job": None if job is None else {"id": job.id, "status": job.status, "batches": job.batches,
                    "attempts": job.attempts, "cursor_message_id": job.cursor_message_id,
                    "target_message_id": job.target_message_id, "error_code": job.error_code,
                    "input_tokens": job.input_tokens, "output_tokens": job.output_tokens}}

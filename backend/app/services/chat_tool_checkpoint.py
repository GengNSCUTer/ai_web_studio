"""复用Durable原生PG池；Chat工具图使用父ChatRun的租约作为保存权限。"""

from dataclasses import dataclass

from app.services.chat_tool_runtime import VERSION
from app.services.durable_checkpoint_service import (
    CheckpointIdentity, CheckpointLeaseLost, LeaseFencedPostgresSaver,
)


@dataclass(frozen=True)
class ChatToolCheckpointIdentity(CheckpointIdentity):
    chat_run_id: str = ""

    @property
    def thread_id(self):
        return f"{VERSION}:u:{self.user_id}:p:{self.project_id or '-'}:r:{self.run_id}:s:{self.step_id}"


class ChatToolPostgresSaver(LeaseFencedPostgresSaver):
    async def _verify_lease(self, conn):
        item = self.identity
        row = await (await conn.execute("""
            SELECT c.lease_version,c.lease_owner,c.status,c.lease_expires_at > now() AS live,
                   c.deadline_at > now() AS in_budget,c.generation_id=m.generation_id AS same_generation
            FROM public.chat_runs c
            JOIN public.messages m ON m.id=c.assistant_message_id
            JOIN public.conversations v ON v.id=c.conversation_id AND v.user_id=c.user_id
            JOIN public.agent_runs r ON r.assistant_message_id=c.assistant_message_id
            JOIN public.agent_steps s ON s.run_id=r.id
            WHERE c.id=%s AND c.user_id=%s AND r.id=%s AND r.user_id=c.user_id
              AND r.runtime_kind='chat_tool_workflow' AND s.id=%s
              AND r.chat_run_id=c.id
              AND r.project_id IS NOT DISTINCT FROM %s
              AND v.project_id IS NOT DISTINCT FROM r.project_id
              AND r.input_json::jsonb->>'chat_run_id'=c.id
              AND r.input_json::jsonb->>'generation_id'=c.generation_id
            FOR UPDATE OF c
        """, (item.chat_run_id, item.user_id, item.run_id, item.step_id, item.project_id))).fetchone()
        if not row or not (row["status"] == "running" and row["live"] and row["in_budget"]
                and row["same_generation"] and row["lease_owner"] == item.owner
                and row["lease_version"] == item.lease_version):
            raise CheckpointLeaseLost("Chat工具图租约、范围或generation已失效")

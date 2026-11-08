"""Chat工具业务进度：复用Agent表，所有提交受父Chat租约和generation约束。"""

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta
import json
import time

from sqlalchemy import func, select

from app.models.agent_runtime import AgentApproval, AgentArtifact, AgentCheckpoint, AgentRun, AgentStep
from app.models.conversation import Conversation
from app.models.project import Project
from app.models.message import Message
from app.services.chat_run_service import ChatLeaseLost, ChatRunService
from app.services.runtime_lease import aware, utcnow
from app.services.tools import runtime_codec as codec
from app.services.tools.schemas import tool_execution_snapshot, redact_sensitive_arguments, ToolTraceEvent
from app.services.tool_invocation_runtime import (
    InvocationOutcome, InvocationRuntimeError, ToolInvocationRuntime, current_invocation_runtime,
)
from app.services.chat_tool_snapshot import ChatToolSnapshotCodec


current_tool_persistence = current_invocation_runtime
RUNTIME = "chat_tool_workflow"
VERSION = "langgraph_chat_tools_v1"
LOOP_FIELDS = (
    "round_index", "routed_query", "observations", "events", "sources", "notices", "last_plan",
    "planning_elapsed_ms", "execution_elapsed_ms", "selected_tool", "error_message", "terminal_reason",
    "next_action", "workflow_aggregate_status", "workflow_aggregate", "raw_sources_total",
    "duplicate_sources_total", "projection_reports",
)


class ChatToolPersistence:
    def __init__(self, sessions, claim):
        self.sessions, self.claim = sessions, claim
        self.agent_id = self.coordinator_id = None
        self.round_index = 0
        self.policy = self.skill = None
        self.wall_deadline = None
        # 同一Run的短写入排队；网络调用仍并行。SQLite测试也不依赖无效的FOR UPDATE。
        self.write_lock = asyncio.Lock()
        self.storage_failed = False

    @staticmethod
    def key(chat_id):
        return f"chat-tools:{chat_id}"

    def _lock(self, db):
        chat = ChatRunService(db)._locked_claim(self.claim)
        if chat is None or (chat.deadline_at and aware(chat.deadline_at) <= utcnow()):
            raise ChatLeaseLost()
        conversation = db.get(Conversation, chat.conversation_id, populate_existing=True)
        if not conversation or conversation.user_id != chat.user_id:
            raise ChatLeaseLost()
        if conversation.project_id:
            project = db.get(Project, conversation.project_id, populate_existing=True)
            if not project or project.user_id != chat.user_id:
                raise ChatLeaseLost()
        agent = db.scalar(select(AgentRun).where(AgentRun.idempotency_key == self.key(chat.id))
            .with_for_update().execution_options(populate_existing=True))
        if not agent or agent.chat_run_id != chat.id or agent.user_id != chat.user_id or agent.project_id != conversation.project_id:
            raise ChatLeaseLost()
        state = json.loads(agent.input_json)
        if state.get("runtime_engine") != VERSION or state.get("generation_id") != chat.generation_id:
            raise ChatLeaseLost()
        source = db.get(Message, chat.user_message_id, populate_existing=True)
        request = json.loads(chat.request_json)["payload"]
        if (not source or source.conversation_id != chat.conversation_id or source.role != "user"
                or ("content" in request and source.content != request["content"])):
            raise ValueError("原始用户问题已变化，不能把旧计划用于新任务")
        self.agent_id = agent.id
        return chat, agent

    def initialize(self):
        """首次领取后建立关联；旧Run没有此记录时不自动改变原恢复语义。"""
        with self.sessions() as db:
            chat = ChatRunService(db)._locked_claim(self.claim)
            if not chat:
                raise ChatLeaseLost()
            conversation = db.get(Conversation, chat.conversation_id)
            agent, coordinator = ensure_tool_run(db, chat, conversation)
            self.coordinator_id = coordinator.id
            self.agent_id = agent.id
            db.commit()
            # 重新走范围与版本检查，旧generation不能借initialize复用同名记录。
            self._lock(db)

    def restore(self, run):
        self.policy = run.service.run_policy.to_public_dict()
        self.skill = run.skill_context.to_public_dict() if run.skill_context else None
        with self.sessions() as db:
            _, agent = self._lock(db)
            checkpoint = db.scalar(select(AgentCheckpoint).where(AgentCheckpoint.run_id == agent.id)
                .order_by(AgentCheckpoint.state_version.desc()).limit(1))
            if not checkpoint:
                return "plan"
            saved = ChatToolSnapshotCodec(db, agent).loads(checkpoint.planner_state_json)
            if saved["version"] != VERSION or saved["policy"] != self.policy or saved["skill"] != self.skill:
                raise ValueError("工具运行版本、Skill或预算已变化，不能继续旧计划")
            if saved.get("parameters_redacted") and saved["next_node"] == "execute":
                raise ValueError("调用参数包含已脱敏内容，不能用脱敏值恢复执行")
            for name in LOOP_FIELDS:
                setattr(run, name, saved["loop"][name])
            run.used_call_ids = set(saved["used_call_ids"])
            run.call_ledger._blockers_by_fingerprint = saved["blockers"]
            if saved.get("plan") is not None:
                run.plan = saved["plan"]
            if saved.get("workflow_result") is not None:
                run.workflow_result = saved["workflow_result"]
            for name in ("planning_rounds_used", "tool_calls_used", "replans_used"):
                setattr(run.budget, name, saved["budget"][name])
            run.budget.tool_calls_used = max(run.budget.tool_calls_used, self._attempts(db, agent.id))
            # 持久化绝对截止时间；进程退出和排队等待都不能给工具阶段补回预算。
            self.wall_deadline = datetime.fromisoformat(saved["wall_deadline"])
            remaining = (aware(self.wall_deadline) - utcnow()).total_seconds()
            run.budget.started_at = time.perf_counter() - run.budget.policy.max_wall_clock_seconds + remaining
            self.round_index = run.round_index
            run.service.executor.action_guard.remember_sources(run.sources)
            self._validate_sources(db, run.sources)
            self._validate_plan(db, run.service.executor, getattr(run, "plan", None), saved.get("tool_snapshots", {}))
            # 先前轮次已完成也必须按当前权限复核，不能只检查最后一轮。
            from app.services.tools.catalog import ToolCatalog
            from app.services.tools.credentials import ToolCredentialResolver
            from app.services.tools.schemas import PlannedToolCall
            catalog = ToolCatalog(db=db, user_id=run.service.executor.user_id, project_id=run.service.executor.project_id)
            resolver = ToolCredentialResolver(db)
            for step in db.scalars(select(AgentStep).where(AgentStep.run_id == agent.id, AgentStep.sequence > 0)):
                if step.attempts == 0:
                    continue
                state = json.loads(step.result_json or "{}")
                definition = catalog.get_or_none(step.tool_key)
                call = PlannedToolCall(step.call_id, step.tool_key, "", "", step.tool_key, 1, "",
                    arguments=json.loads(step.arguments_json))
                if not definition or state.get("tool_snapshot") != tool_execution_snapshot(definition) or not self._reuse_allowed(
                        db, run.service.executor, resolver, definition, call):
                    raise ValueError("已有工具结果的当前权限、版本或文件范围已变化")
                artifact = db.get(AgentArtifact, state.get("artifact_id")) if state.get("artifact_id") else None
                if artifact:
                    ToolInvocationRuntime.validate_artifact(agent, step, artifact)
                    ToolInvocationRuntime.verify_artifact_content(artifact)
                if artifact and saved["next_node"] == "execute" and remaining <= 0:
                    from app.services.tools.quality import is_usable_tool_result
                    response, _ = codec.loads(artifact.content_json)
                    if is_usable_tool_result(response):
                        self._validate_sources(db, response.sources)
                        # 工具截止时间可能在Worker离线时耗尽；已提交有效证据仍可用于部分回答。
                        run.service._merge_sources(run.sources, response.sources)
                        run.service.executor.action_guard.remember_sources(response.sources)
            return saved["next_node"]

    @staticmethod
    def _attempts(db, agent_id):
        return db.scalar(select(func.coalesce(func.sum(AgentStep.attempts), 0)).where(
            AgentStep.run_id == agent_id, AgentStep.sequence > 0)) or 0

    def attempts_used(self):
        with self.sessions() as db:
            _, agent = self._lock(db)
            return self._attempts(db, agent.id)

    def save(self, run, next_node):
        if self.storage_failed:
            # 持久化失败不能被Workflow误当成普通工具故障后继续规划副作用操作。
            raise ChatLeaseLost()
        self.wall_deadline = self.wall_deadline or utcnow() + timedelta(milliseconds=run.budget.remaining_wall_clock_ms)
        plan = getattr(run, "plan", None)
        snapshots = {call.tool_key: tool_execution_snapshot(run.service.registry.get(call.tool_key))
                     for call in plan.calls if run.service.registry.get_or_none(call.tool_key)} if plan else {}
        saved = {"version": VERSION, "policy": self.policy, "skill": self.skill, "next_node": next_node,
            "loop": {name: getattr(run, name) for name in LOOP_FIELDS}, "used_call_ids": sorted(run.used_call_ids),
            "plan": plan, "workflow_result": getattr(run, "workflow_result", None),
            "blockers": run.call_ledger._blockers_by_fingerprint,
            "budget": {name: getattr(run.budget, name) for name in ("planning_rounds_used", "tool_calls_used", "replans_used")},
            "wall_deadline": self.wall_deadline.isoformat(), "tool_snapshots": snapshots}
        # 凭据形状的调用参数不能在脱敏后被当成原始参数重放。
        saved["parameters_redacted"] = any(redact_sensitive_arguments(call.arguments) != call.arguments for call in plan.calls) if plan else False
        with self.sessions() as db:
            _, agent = self._lock(db)
            if next_node == "execute" and plan:
                self._save_plan(db, agent, run, plan)
            if next_node == "observe" and getattr(run, "workflow_result", None):
                self._record_outcomes(db, agent, run)
            encoded, snapshot_metrics = ChatToolSnapshotCodec(db, agent).dumps(saved)
            if len(encoded.encode()) > 4 * 1024 * 1024:
                raise ValueError("工具恢复记录超过大小限制")
            agent.state_version += 1
            agent.planner_state_json = json.dumps({"runtime_engine": VERSION, "next_node": next_node,
                "round": run.round_index, "chat_run_id": self.claim.run_id,
                "workflow_status": run.workflow_aggregate_status, "next_action": run.next_action,
                "terminal_reason": run.terminal_reason, "snapshot_metrics": snapshot_metrics})
            db.add(AgentCheckpoint(run_id=agent.id, state_version=agent.state_version,
                step_sequence=agent.current_step, planner_state_json=encoded,
                remaining_budget_json=json.dumps(run.budget.to_trace_payload())))
            coordinator = db.get(AgentStep, self.coordinator_id)
            coordinator.status = "running"
            if next_node == "done":
                coordinator.status, coordinator.finished_at = "succeeded", utcnow()
                coordinator.lease_owner, coordinator.lease_expires_at = None, None
                agent.status, agent.finished_at = "succeeded", utcnow()
                for step in db.scalars(select(AgentStep).where(AgentStep.run_id == agent.id,
                        AgentStep.sequence > 0, AgentStep.status == "running")):
                    step.status, step.error_code = "blocked", "recovery_outcome_unknown"
                    step.finished_at, step.lease_owner, step.lease_expires_at = utcnow(), None, None
            self.round_index = run.round_index
            db.commit()

    def _save_plan(self, db, agent, run, plan):
        """计划的所有步骤先落库；依赖阻断也能看到Step，不伪装成真实工具请求。"""
        from app.services.tools.workflow import ToolWorkflowService
        for call in plan.calls:
            call_id = f"{run.round_index}:{call.call_id}"
            if len(call_id) > 128:
                raise ValueError("调用标识超过持久化限制")
            existing = db.scalar(select(AgentStep.id).where(AgentStep.run_id == agent.id, AgentStep.call_id == call_id))
            definition = run.service.registry.get_or_none(call.tool_key)
            if existing or not definition:
                continue
            sequence = (db.scalar(select(func.max(AgentStep.sequence)).where(AgentStep.run_id == agent.id)) or 0) + 1
            if sequence > agent.max_steps:
                raise ValueError("计划步骤超过持久化上限")
            db.add(AgentStep(run_id=agent.id, sequence=sequence, call_id=call_id, tool_key=call.tool_key,
                arguments_json=json.dumps(redact_sensitive_arguments(call.arguments), ensure_ascii=False),
                arguments_hash=ToolWorkflowService.call_fingerprint_for(call).split(":", 1)[1],
                depends_on_json=json.dumps(call.depends_on), result_bindings_json=json.dumps([asdict(b) for b in call.result_bindings]),
                result_json=json.dumps({"tool_snapshot": tool_execution_snapshot(definition)}), status="planned"))
            db.flush()
            agent.current_step = sequence

    @staticmethod
    def _record_outcomes(db, agent, run):
        for outcome in run.workflow_result.step_outcomes:
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == agent.id,
                AgentStep.call_id == f"{run.round_index}:{outcome.call_id}"))
            if step is None:
                continue
            state = json.loads(step.result_json or "{}")
            state["outcome"] = outcome.to_trace_payload()
            step.result_json = json.dumps(state)
            if step.attempts == 0:
                step.status, step.error_code, step.finished_at = outcome.execution_status, outcome.error_category[:64] or None, utcnow()

    def _validate_plan(self, db, executor, plan, snapshots):
        from app.services.tools.catalog import ToolCatalog
        from app.services.tools.credentials import ToolCredentialResolver
        catalog = ToolCatalog(db=db, user_id=executor.user_id, project_id=executor.project_id)
        resolver = ToolCredentialResolver(db)
        if not plan:
            return
        for call in plan.calls:
            definition = catalog.get_or_none(call.tool_key)
            if not definition:
                raise ValueError("工具定义发生变化，不能恢复旧调用")
            ToolInvocationRuntime.verify_snapshot(definition, snapshots.get(call.tool_key))
            if not definition.enabled_by_default or not resolver.is_tool_enabled_for_workspace(
                    project_id=executor.project_id, tool_key=call.tool_key):
                raise ValueError("工具已禁用，不能复用旧结果")
            if self.skill and call.tool_key not in self.skill["allowed_tool_keys"]:
                raise ValueError("工具已不在当前Skill权限内")

    async def execute(self, executor, call, execute):
        """逐调用先保存意图、后原子提交结果；并行分支各自使用独立短Session。"""
        from app.services.tools.db_boundary import release_read_transaction
        release_read_transaction(executor.db)
        try:
            async with self.write_lock:
                decision = await asyncio.to_thread(self._begin_call, executor, call)
        except Exception:
            self.storage_failed = True
            raise ChatLeaseLost() from None
        if decision[0] == "reused":
            result, events = decision[1]
            executor.action_guard.remember_sources(result.sources)
            events.append(ToolTraceEvent(type="tool_result_reused", payload={"call_id": call.call_id,
                "tool_key": call.tool_key, "agent_run_id": self.agent_id, "original_result_snapshot": True}))
            return result, events
        if decision[0] == "blocked":
            return executor._skipped(call, decision[1])
        result = await execute(call)
        try:
            async with self.write_lock:
                await asyncio.to_thread(self._complete_call, decision[1], result)
        except Exception:
            self.storage_failed = True
            raise ChatLeaseLost() from None
        return result

    def _begin_call(self, executor, call):
        from app.services.tools.workflow import ToolWorkflowService
        key = f"{self.round_index}:{call.call_id}"
        if len(key) > 128:
            return "blocked", "调用标识过长，无法建立可恢复步骤。"
        with self.sessions() as db:
            _, agent = self._lock(db)
            # 在线Catalog和凭据使用本线程独立Session重建，不能跨线程共享Executor的Session。
            from app.services.tools.catalog import ToolCatalog
            from app.services.tools.credentials import ToolCredentialResolver
            catalog = ToolCatalog(db=db, user_id=executor.user_id, project_id=executor.project_id)
            definition = catalog.get_or_none(call.tool_key)
            initial = executor.catalog.get_or_none(call.tool_key)
            if definition is None or initial is None or tool_execution_snapshot(definition) != tool_execution_snapshot(initial):
                return "blocked", "工具定义已变化或不再可用。"
            resolver = ToolCredentialResolver(db)
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == agent.id, AgentStep.call_id == key))
            # 复用现有指纹算法；AgentStep列存64位摘要，不存展示用的sha256:前缀。
            signature = ToolWorkflowService.call_fingerprint_for(call).split(":", 1)[1]
            if step:
                state = json.loads(step.result_json or "{}")
                if (step.tool_key != call.tool_key or (step.attempts > 0 and step.arguments_hash != signature)
                        or state.get("tool_snapshot") != tool_execution_snapshot(definition)):
                    return "blocked", "工具或参数与已保存步骤不一致。"
                if not self._reuse_allowed(db, executor, resolver, definition, call):
                    return "blocked", "当前权限、文件范围或凭据不再允许复用该结果。"
                artifact = db.get(AgentArtifact, state.get("artifact_id")) if state.get("artifact_id") else None
                decision = ToolInvocationRuntime.reconcile(agent, step, definition, artifact=artifact,
                    artifact_expected=bool(state.get("artifact_id")), active_owner=self.claim.owner, active_version=self.claim.version)
                if decision.action == "reuse":
                    restored = codec.loads(artifact.content_json)
                    self._validate_sources(db, restored[0].sources)
                    return "reused", restored
                if decision.action == "block":
                    return "blocked", decision.reason
            else:
                if not definition.read_only:
                    prior = db.scalar(select(AgentStep.id).where(AgentStep.run_id == agent.id,
                        AgentStep.tool_key == call.tool_key, AgentStep.arguments_hash == signature,
                        AgentStep.status.in_(("running", "waiting_approval"))).limit(1))
                    if prior:
                        return "blocked", "相同写入仍待核实或审批，不能通过新调用ID再次执行。"
                sequence = (db.scalar(select(func.max(AgentStep.sequence)).where(AgentStep.run_id == agent.id)) or 0) + 1
                if sequence > agent.max_steps:
                    return "blocked", "持久化工具步骤已达到上限。"
                step = AgentStep(run_id=agent.id, sequence=sequence, call_id=key, tool_key=call.tool_key,
                    arguments_json=json.dumps(redact_sensitive_arguments(call.arguments), ensure_ascii=False),
                    arguments_hash=signature, status="running", attempts=0, max_attempts=3,
                    depends_on_json=json.dumps(call.depends_on), result_bindings_json=json.dumps([asdict(b) for b in call.result_bindings]),
                    result_json=json.dumps({"tool_snapshot": tool_execution_snapshot(definition)}))
                db.add(step)
                db.flush()
                agent.current_step = sequence
            if self.policy and self._attempts(db, agent.id) >= self.policy["max_total_tool_calls"]:
                return "blocked", "该任务实际工具请求已达到总预算，不再发起新的调用。"
            step.arguments_hash = signature
            step.arguments_json = json.dumps(redact_sensitive_arguments(call.arguments), ensure_ascii=False)
            try:
                ToolInvocationRuntime.start_attempt(agent, step, owner=self.claim.owner,
                    lease_version=self.claim.version, lease_expires_at=utcnow() + timedelta(seconds=45))
            except InvocationRuntimeError as exc:
                return "blocked", str(exc)
            step_id = step.id
            db.commit()
            return "execute", step_id

    _reuse_allowed = staticmethod(ToolInvocationRuntime.reuse_allowed)

    @staticmethod
    def _validate_sources(db, sources):
        for source in sources:
            approval_id = source.metadata.get("approval_id")
            if approval_id and not source.metadata.get("applied"):
                approval = db.get(AgentApproval, approval_id, populate_existing=True)
                if not approval or approval.status != "pending" or aware(approval.expires_at) <= utcnow():
                    raise ValueError("审批状态已变化，请查看当前审批或文件版本，不复用旧等待说明")

    def _complete_call(self, step_id, result):
        with self.sessions() as db:
            chat, agent = self._lock(db)
            step = db.get(AgentStep, step_id, populate_existing=True)
            if (not step or step.run_id != agent.id or step.lease_version != self.claim.version
                    or step.lease_owner != self.claim.owner or step.status != "running"):
                raise ChatLeaseLost()
            payload = codec.dumps(result)
            artifact = ToolInvocationRuntime.create_artifact(db, run=agent, step=step,
                artifact_type="chat_tool_result", content_json=payload,
                preview=f"{step.tool_key}：{result[0].status}", max_bytes=2 * 1024 * 1024)
            state = json.loads(step.result_json)
            state["artifact_id"] = artifact.id
            step.result_json = json.dumps(state)
            response = result[0]
            step.status = InvocationOutcome.from_result(response).step_status
            state.update(quality_status=response.quality_status, result_status=response.status)
            step.result_json = json.dumps(state)
            step.finished_at, step.lease_owner, step.lease_expires_at = utcnow(), None, None
            db.commit()


def ensure_tool_run(db, chat, conversation):
    """由领取事务建立恢复事实，避免领取成功到Worker初始化之间的崩溃空窗。"""
    agent = db.scalar(select(AgentRun).where(AgentRun.idempotency_key == ChatToolPersistence.key(chat.id)))
    if not agent:
        agent = AgentRun(user_id=chat.user_id, project_id=conversation.project_id, chat_run_id=chat.id,
            conversation_id=chat.conversation_id, assistant_message_id=chat.assistant_message_id,
            runtime_kind=RUNTIME, status="running", idempotency_key=ChatToolPersistence.key(chat.id),
            max_steps=128, current_step=0, input_json=json.dumps({
                "chat_run_id": chat.id, "generation_id": chat.generation_id, "runtime_engine": VERSION}))
        db.add(agent)
        db.flush()
        coordinator = AgentStep(run_id=agent.id, sequence=0, call_id="__tool_loop__",
            tool_key="runtime.chat_tool_loop", arguments_json="{}", arguments_hash="",
            status="running", max_attempts=3)
        db.add(coordinator)
        db.flush()
    else:
        coordinator = db.scalar(select(AgentStep).where(AgentStep.run_id == agent.id, AgentStep.sequence == 0))
        if not coordinator:
            raise ValueError("Chat工具运行缺少编排步骤")
    return agent, coordinator


def archive_partial_generation(db, chat):
    """与正文重置在同一事务保存旧生成片段，不伪装成可续接Token。"""
    agent = db.scalar(select(AgentRun).where(AgentRun.idempotency_key == ChatToolPersistence.key(chat.id)))
    if not agent:
        raise ValueError("缺少回答恢复记录，不能自动重新生成")
    coordinator = db.scalar(select(AgentStep).where(AgentStep.run_id == agent.id, AgentStep.sequence == 0))
    payload = codec.dumps({"attempt": chat.model_attempts, "content": chat.content,
        "reasoning_content": chat.reasoning_content, "sources": json.loads(chat.sources_json)})
    ToolInvocationRuntime.create_artifact(db, run=agent, step=coordinator,
        artifact_type="chat_generation_partial", content_json=payload,
        preview=f"第{chat.model_attempts}次生成中断：保留{len(chat.content)}字符", prompt_state="archived")


def close_tool_run(db, chat, status):
    """父任务收口时关闭未完成的工具记录，已完成工具产物不随生成失败作废。"""
    agent = db.scalar(select(AgentRun).where(AgentRun.idempotency_key == ChatToolPersistence.key(chat.id))
        .with_for_update().execution_options(populate_existing=True))
    if not agent or agent.status == "succeeded":
        return
    agent.status = "succeeded" if status == "succeeded" else "cancelled" if status == "cancelled" else "failed"
    agent.finished_at = utcnow()
    for step in db.scalars(select(AgentStep).where(AgentStep.run_id == agent.id,
            AgentStep.status.in_(("planned", "running", "pending")))):
        step.status = "succeeded" if step.sequence == 0 and status == "succeeded" else "cancelled" if status == "cancelled" else "failed"
        step.lease_owner, step.lease_expires_at, step.finished_at = None, None, utcnow()

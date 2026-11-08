from __future__ import annotations

import asyncio

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.attachment_repo import AttachmentRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.message_repo import MessageGenerationConflict
from app.repositories.project_repo import ProjectRepository
from app.repositories.setting_repo import UserSettingRepository
from app.repositories.tool_trace_repo import ToolTraceRepository
from app.schemas.message import ChatStreamRequest
from app.services.chat_context_assembly_service import ChatContextAssemblyService
from app.services.chat_execution_models import (
    ChatExecutionContext,
    ChatRuntimeConfig,
    ExistingTurnExecutionInput,
)
from app.services.chat_provider_service import resolve_provider_base_url
from app.services.chat_turn_bootstrapper import ChatTurnBootstrapper, clean_optional_str
from app.services.context_governance_service import ContextBudgetPlanner, ContextGovernanceService
from app.services.message_service import MessageService
from app.services.setting_service import SettingService
from app.services.skill_catalog import SkillCatalog, SkillCatalogError, SkillExecutionContext
from app.services.tokenizer_service import TokenizerEstimator


class ChatExecutionService:
    """Chat准备图门面，不直接调用最终回答模型。

    正式HTTP入口先在ChatRunService原子创建消息与任务；独立Worker以existing输入
    组装记忆、工具、RAG、摘要和预算。new图入口仍供独立服务测试及显式内部调用，
    不再由Web请求持有生成生命周期。
    """

    def __init__(self, *, db: Session, current_user: User, manage_prepare_failure: bool = True) -> None:
        self.db = db
        self.manage_prepare_failure = manage_prepare_failure
        self.current_user = current_user
        self.conversation_repo = ConversationRepository(db)
        self.project_repo = ProjectRepository(db)
        self.message_repo = MessageRepository(db)
        self.message_service = MessageService(
            self.message_repo,
            AttachmentRepository(db),
            self.conversation_repo,
        )
        self.setting_service = SettingService(UserSettingRepository(db))
        self.tool_trace_repo = ToolTraceRepository(db)
        self.turn_bootstrapper = ChatTurnBootstrapper(
            conversation_repo=self.conversation_repo,
            project_repo=self.project_repo,
            message_repo=self.message_repo,
            message_service=self.message_service,
            user_id=self.current_user.id,
        )
        self.context_assembly_service = ChatContextAssemblyService(
            db=self.db,
            user_id=self.current_user.id,
            conversation_repo=self.conversation_repo,
            message_service=self.message_service,
            tool_trace_repo=self.tool_trace_repo,
        )

    @staticmethod
    def validate_attachment_context_inputs(attachments: list[object]) -> None:
        ChatTurnBootstrapper.validate_attachment_context_inputs(attachments)

    async def prepare_chat_execution(self, payload: ChatStreamRequest) -> ChatExecutionContext:
        # 新问题入口由 Chat 图负责：消息骨架、运行时配置、上下文组装和流式交接分成独立节点。
        # 图只保存阶段诊断；数据库对象和认证信息留在请求级 Runtime Context。
        from app.services.agent_graph.chat_graph import run_chat_graph
        from app.services.agent_graph.chat_graph_state import ChatGraphContext

        graph_context = ChatGraphContext(service=self, turn_kind="new", payload=payload)
        try:
            return await run_chat_graph(graph_context)
        except MessageGenerationConflict as exc:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except asyncio.CancelledError:
            if graph_context.assistant_message is not None:
                self._mark_prepare_cancelled(
                    graph_context.assistant_message,
                    generation_id=graph_context.generation_id,
                )
            raise
        except Exception:
            # 流式响应尚未创建，图准备阶段失败时必须先收口 assistant 占位消息。
            if graph_context.assistant_message is not None:
                self._mark_prepare_failed(
                    graph_context.assistant_message,
                    generation_id=graph_context.generation_id,
                )
            raise

    async def prepare_existing_turn_execution(
        self,
        execution_input: ExistingTurnExecutionInput,
    ) -> ChatExecutionContext:
        # 重生成/编辑重答复用同一张图，但不创建新的 user/assistant 消息。
        from app.services.agent_graph.chat_graph import run_chat_graph
        from app.services.agent_graph.chat_graph_state import ChatGraphContext

        graph_context = ChatGraphContext(
            service=self,
            turn_kind="existing",
            execution_input=execution_input,
            generation_id=getattr(execution_input.assistant_message, "generation_id", None),
        )
        try:
            return await run_chat_graph(graph_context)
        except asyncio.CancelledError:
            self._mark_prepare_cancelled(
                execution_input.assistant_message,
                generation_id=graph_context.generation_id,
            )
            raise
        except Exception:
            self._mark_prepare_failed(
                execution_input.assistant_message,
                generation_id=graph_context.generation_id,
            )
            raise

    def _mark_prepare_cancelled(self, assistant_message: object, *, generation_id: str | None) -> None:
        """准备阶段被上游任务取消时，按生成版本收口，避免遗留 streaming 占位。"""

        if not self.manage_prepare_failure:
            self.db.rollback()
            return
        try:
            self.db.rollback()
            self.message_service.save_generation_result(
                message=assistant_message,
                generation_id=generation_id,
                content="",
                reasoning_content=None,
                external_sources=None,
                status="cancelled",
            )
        except Exception:
            # 请求 Session 会随取消退出而关闭；收口失败也不能覆盖原始取消异常。
            pass

    def _mark_prepare_failed(self, assistant_message: object, *, generation_id: str | None = None) -> None:
        """收口模型调用前失败的 assistant 占位消息。

        保存失败时不覆盖原始异常，避免错误收口掩盖真正的 Prepare 故障。
        """
        if not self.manage_prepare_failure:
            self.db.rollback()
            return
        try:
            # Context Assembly 内部可能刚经历过 flush/commit 异常，此时 Session 处于
            # PendingRollbackError 状态；必须先 rollback，才能保存失败状态。
            self.db.rollback()
            self.message_service.mark_generation_prepare_failed(
                assistant_message,
                generation_id=generation_id,
            )
        except Exception:
            self.db.rollback()

    def _build_runtime_config(self, conversation: object) -> ChatRuntimeConfig:
        # 运行时配置是“用户设置 + 会话覆盖”的合并结果。
        # 会话上的 model_name 优先于用户默认模型；provider/base_url/api_key 来自用户设置。
        settings = self.setting_service.get_or_create_user_settings(self.current_user.id)
        provider_api_key = self.setting_service.resolve_provider_api_key(self.current_user.id)
        resolved_model = clean_optional_str(conversation.model_name) or clean_optional_str(settings.default_model)
        provider_type = clean_optional_str(getattr(settings, "provider_type", "ollama")) or "ollama"
        base_url = resolve_provider_base_url(
            provider_type=provider_type,
            configured_ollama_base_url=clean_optional_str(settings.ollama_base_url),
            configured_api_base_url=clean_optional_str(getattr(settings, "api_base_url", None)),
        )
        budget = ContextBudgetPlanner.build(
            model_context_window=max(8192, int(getattr(settings, "model_context_window", 128000) or 128000)),
            context_mode=clean_optional_str(getattr(settings, "context_mode", "balanced")) or "balanced",
            requested_output_tokens=getattr(settings, "max_tokens", None),
        )
        tokenizer = TokenizerEstimator(model_name=resolved_model)
        governance_service = ContextGovernanceService(budget=budget, tokenizer=tokenizer)
        return ChatRuntimeConfig(
            settings=settings,
            provider_api_key=provider_api_key,
            resolved_model=resolved_model,
            provider_type=provider_type,
            base_url=base_url,
            budget=budget,
            tokenizer=tokenizer,
            governance_service=governance_service,
        )

    def _resolve_skill_execution_context(
        self,
        *,
        skill_key: str | None,
        conversation: object,
    ) -> SkillExecutionContext | None:
        normalized = clean_optional_str(skill_key)
        if not normalized:
            return None
        try:
            return SkillCatalog().resolve_for_execution(
                db=self.db,
                user_id=self.current_user.id,
                project_id=getattr(conversation, "project_id", None),
                skill_key=normalized,
            )
        except SkillCatalogError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

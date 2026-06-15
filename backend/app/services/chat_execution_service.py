from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.attachment_repo import AttachmentRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.repositories.message_repo import MessageRepository
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
from app.services.memory_service import MemoryService
from app.services.message_service import MessageService
from app.services.setting_service import SettingService
from app.services.tokenizer_service import TokenizerEstimator


class ChatExecutionService:
    """Facade for chat turn bootstrapping and context assembly."""

    def __init__(self, *, db: Session, current_user: User) -> None:
        self.db = db
        self.current_user = current_user
        self.conversation_repo = ConversationRepository(db)
        self.message_repo = MessageRepository(db)
        self.message_service = MessageService(self.message_repo, AttachmentRepository(db))
        self.setting_service = SettingService(UserSettingRepository(db))
        self.memory_service = MemoryService(UserMemoryRepository(db))
        self.tool_trace_repo = ToolTraceRepository(db)
        self.turn_bootstrapper = ChatTurnBootstrapper(
            conversation_repo=self.conversation_repo,
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
            memory_service=self.memory_service,
        )

    @staticmethod
    def validate_attachment_context_inputs(attachments: list[object]) -> None:
        ChatTurnBootstrapper.validate_attachment_context_inputs(attachments)

    async def prepare_chat_execution(self, payload: ChatStreamRequest) -> ChatExecutionContext:
        default_settings = self.setting_service.get_or_create_user_settings(self.current_user.id)
        turn = self.turn_bootstrapper.bootstrap_new_turn(payload=payload, default_settings=default_settings)
        runtime = self._build_runtime_config(turn.conversation)
        return await self.context_assembly_service.build_execution_context(
            conversation=turn.conversation,
            history_rows=turn.history_rows,
            user_message=turn.user_message,
            assistant_message=turn.assistant_message,
            runtime=runtime,
            thinking_enabled=payload.thinking_enabled,
            thinking_budget=payload.thinking_budget,
            web_search_enabled=payload.web_search_enabled,
            knowledge_base_id=payload.knowledge_base_id,
            knowledge_base_ids=payload.knowledge_base_ids,
        )

    async def prepare_existing_turn_execution(
        self,
        execution_input: ExistingTurnExecutionInput,
    ) -> ChatExecutionContext:
        self.turn_bootstrapper.apply_turn_overrides(
            conversation=execution_input.conversation,
            model_name=execution_input.model_name,
            system_prompt=execution_input.system_prompt,
        )
        self.validate_attachment_context_inputs(list(getattr(execution_input.user_message, "attachments", []) or []))
        runtime = self._build_runtime_config(execution_input.conversation)
        return await self.context_assembly_service.build_execution_context(
            conversation=execution_input.conversation,
            history_rows=execution_input.history_rows,
            user_message=execution_input.user_message,
            assistant_message=execution_input.assistant_message,
            runtime=runtime,
            thinking_enabled=execution_input.thinking_enabled,
            thinking_budget=execution_input.thinking_budget,
            web_search_enabled=execution_input.web_search_enabled,
            knowledge_base_id=execution_input.knowledge_base_id,
            knowledge_base_ids=execution_input.knowledge_base_ids,
        )

    def _build_runtime_config(self, conversation: object) -> ChatRuntimeConfig:
        settings = self.setting_service.get_or_create_user_settings(self.current_user.id)
        provider_api_key = self.setting_service.resolve_provider_api_key(self.current_user.id)
        resolved_model = clean_optional_str(conversation.model_name) or clean_optional_str(settings.default_model)
        provider_type = clean_optional_str(getattr(settings, "provider_type", "ollama")) or "ollama"
        base_url = resolve_provider_base_url(
            provider_type=provider_type,
            configured_base_url=clean_optional_str(settings.ollama_base_url),
        )
        budget = ContextBudgetPlanner.build(
            model_context_window=max(8192, int(getattr(settings, "model_context_window", 128000) or 128000)),
            context_mode=clean_optional_str(getattr(settings, "context_mode", "balanced")) or "balanced",
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

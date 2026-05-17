from app.models.conversation import Conversation
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.conversation import (
    ConversationCreate,
    ConversationListItem,
    ConversationResponse,
    ConversationUpdate,
)


class ConversationService:
    def __init__(self, repo: ConversationRepository):
        self.repo = repo

    def list_conversations(self, user_id: str) -> list[ConversationListItem]:
        return [ConversationListItem.model_validate(item) for item in self.repo.list_by_user(user_id)]

    def get_conversation(self, conversation_id: str, user_id: str) -> ConversationResponse | None:
        conversation = self.repo.get_by_user(conversation_id, user_id)
        if not conversation:
            return None
        return ConversationResponse.model_validate(conversation)

    def create_conversation(self, payload: ConversationCreate, user_id: str) -> ConversationResponse:
        conversation = Conversation(
            user_id=user_id,
            project_id=payload.project_id,
            title=payload.title,
            model_name=payload.model_name,
            system_prompt=payload.system_prompt,
            context_summary=None,
            context_summary_boundary_message_id=None,
            context_summary_updated_at=None,
        )
        created = self.repo.create(conversation)
        return ConversationResponse.model_validate(created)

    def update_conversation(
        self, conversation_id: str, payload: ConversationUpdate, user_id: str
    ) -> ConversationResponse | None:
        conversation = self.repo.get_by_user(conversation_id, user_id)
        if not conversation:
            return None

        if payload.title is not None:
            conversation.title = payload.title
        if payload.system_prompt is not None:
            conversation.system_prompt = payload.system_prompt
        if payload.is_archived is not None:
            conversation.is_archived = payload.is_archived
        if payload.is_pinned is not None:
            conversation.is_pinned = payload.is_pinned
        if "project_id" in payload.model_fields_set:
            conversation.project_id = payload.project_id

        updated = self.repo.save(conversation)
        return ConversationResponse.model_validate(updated)

    def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        conversation = self.repo.get_by_user(conversation_id, user_id)
        if not conversation:
            return False
        self.repo.delete(conversation)
        return True

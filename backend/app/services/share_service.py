import secrets
from datetime import datetime, timedelta, timezone

from app.models.conversation_share import ConversationShare
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.share_repo import ConversationShareRepository
from app.schemas.share import (
    ConversationShareCreate,
    ConversationShareResponse,
    ConversationShareUpdate,
    PublicConversationShareResponse,
)
from app.services.message_service import MessageService


class ConversationShareService:
    def __init__(
        self,
        repo: ConversationShareRepository,
        conversation_repo: ConversationRepository,
        message_repo: MessageRepository,
    ):
        self.repo = repo
        self.conversation_repo = conversation_repo
        self.message_repo = message_repo

    @staticmethod
    def _expires_at(expires_in_days: int | None) -> datetime | None:
        if not expires_in_days:
            return None
        return datetime.now(timezone.utc) + timedelta(days=expires_in_days)

    def get_share(self, conversation_id: str, user_id: str) -> ConversationShareResponse | None:
        if not self.conversation_repo.get_by_user(conversation_id, user_id):
            return None
        share = self.repo.get_by_conversation(conversation_id, user_id)
        return ConversationShareResponse.model_validate(share) if share else None

    def create_or_enable_share(
        self,
        conversation_id: str,
        user_id: str,
        payload: ConversationShareCreate,
    ) -> ConversationShareResponse | None:
        if not self.conversation_repo.get_by_user(conversation_id, user_id):
            return None
        share = self.repo.get_by_conversation(conversation_id, user_id)
        if share:
            share.is_enabled = True
            share.revoked_at = None
            share.expires_at = self._expires_at(payload.expires_in_days)
        else:
            share = ConversationShare(
                conversation_id=conversation_id,
                user_id=user_id,
                token=secrets.token_urlsafe(24),
                is_enabled=True,
                expires_at=self._expires_at(payload.expires_in_days),
            )
        return ConversationShareResponse.model_validate(self.repo.save(share))

    def update_share(
        self,
        conversation_id: str,
        user_id: str,
        payload: ConversationShareUpdate,
    ) -> ConversationShareResponse | None:
        if not self.conversation_repo.get_by_user(conversation_id, user_id):
            return None
        share = self.repo.get_by_conversation(conversation_id, user_id)
        if not share:
            return None
        if payload.is_enabled is not None:
            share.is_enabled = payload.is_enabled
            share.revoked_at = None if payload.is_enabled else datetime.now(timezone.utc)
        if payload.clear_expires_at:
            share.expires_at = None
        elif payload.expires_in_days:
            share.expires_at = self._expires_at(payload.expires_in_days)
        return ConversationShareResponse.model_validate(self.repo.save(share))

    def revoke_share(self, conversation_id: str, user_id: str) -> bool:
        if not self.conversation_repo.get_by_user(conversation_id, user_id):
            return False
        share = self.repo.get_by_conversation(conversation_id, user_id)
        if not share:
            return False
        self.repo.revoke(share)
        return True

    def get_public_share(self, token: str) -> PublicConversationShareResponse | None:
        share = self.repo.get_by_token(token)
        if not share or not share.is_enabled or share.revoked_at:
            return None
        if share.expires_at:
            expires_at = share.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                return None
        conversation = self.conversation_repo.get_by_user(share.conversation_id, share.user_id)
        if not conversation:
            return None
        messages = MessageService(self.message_repo).list_messages(conversation.id)
        return PublicConversationShareResponse(
            token=share.token,
            title=conversation.title,
            model_name=conversation.model_name,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            messages=messages,
        )

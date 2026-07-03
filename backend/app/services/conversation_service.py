from app.models.conversation import Conversation
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.knowledge_repo import KnowledgeRetrievalLogRepository
from app.repositories.project_repo import ProjectRepository
from app.schemas.conversation import (
    ConversationCreate,
    ConversationListItem,
    ConversationResponse,
    ConversationUpdate,
)


class ConversationService:
    """会话业务层。

    Service 负责表达“用户可以对自己的会话做什么”：列出、读取、创建、更新、删除。
    project 归属校验属于会话业务不变量，创建/移动会话时统一在 service 内收口。
    """

    def __init__(self, repo: ConversationRepository, project_repo: ProjectRepository | None = None):
        self.repo = repo
        self.project_repo = project_repo

    def _project_belongs_to_user(self, project_id: str | None, user_id: str) -> bool:
        if project_id is None:
            return True
        if not self.project_repo:
            return False
        return self.project_repo.get_by_user(project_id, user_id) is not None

    def list_conversations(
        self,
        user_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ConversationListItem]:
        # 这里不返回 Message，列表页只需要会话元数据，避免 N+1 加载整段历史。
        return [
            ConversationListItem.model_validate(item)
            for item in self.repo.list_by_user(user_id, limit=limit, offset=offset)
        ]

    def get_conversation(self, conversation_id: str, user_id: str) -> ConversationResponse | None:
        # 所有读取都通过 repo.get_by_user 收口，避免跨用户访问。
        conversation = self.repo.get_by_user(conversation_id, user_id)
        if not conversation:
            return None
        return ConversationResponse.model_validate(conversation)

    def create_conversation(self, payload: ConversationCreate, user_id: str) -> ConversationResponse | None:
        if not self._project_belongs_to_user(payload.project_id, user_id):
            return None

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

        if "project_id" in payload.model_fields_set and not self._project_belongs_to_user(payload.project_id, user_id):
            return None

        # 只更新用户可编辑字段；上下文摘要、prefix hash 等内部状态不从 PATCH 入口写入。
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
        try:
            # RAG 检索日志需要保留用于观测，但不能继续指向即将删除的会话/消息。
            # 断链和删除必须在同一个事务里提交，避免只完成其中一半。
            KnowledgeRetrievalLogRepository(self.repo.db).detach_conversation_links(
                conversation_id=conversation.id,
                user_id=user_id,
                commit=False,
            )
            self.repo.delete(conversation, commit=False)
            self.repo.db.commit()
        except Exception:
            self.repo.db.rollback()
            raise
        return True

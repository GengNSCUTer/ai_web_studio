from app.models.attachment import Attachment
from app.models.agent_runtime import (
    AgentApproval,
    AgentArtifact,
    AgentCheckpoint,
    AgentOutboxEvent,
    AgentWorkerHeartbeat,
    AgentRun,
    AgentStep,
    FileRevision,
    PatchDraft,
)
from app.models.conversation import Conversation
from app.models.conversation_share import ConversationShare
from app.models.knowledge import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEvalCase,
    KnowledgeEvalResult,
    KnowledgeEvalRun,
    KnowledgeEvalSet,
    KnowledgeIndexGeneration,
    KnowledgeJob,
    KnowledgeRetrievalLog,
    OutboxEvent,
)
from app.models.message import Message
from app.models.chat_run import ChatRun, ChatRunEvent
from app.models.observability import ChatRuntimeMetric
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.prompt_template import PromptTemplate
from app.models.tool_config import (
    McpServer,
    McpTool,
    SkillInstallationRevision,
    UserToolCredential,
    UserSkillInstallation,
    WorkspaceAgentPolicy,
    WorkspaceToolSetting,
)
from app.models.tool_trace import ToolCallRun, ToolRouteRun
from app.models.tool_search import ToolSearchEntry
from app.models.user import User
from app.models.resource_lease import ResourceLease
from app.models.user_memory import MemoryExtractionJob, UserMemory
from app.models.memory_trigger_decision import MemoryTriggerDecision
from app.models.user_setting import UserSetting
from app.models.plugin import PluginInstallation, PluginRelease, ProjectPlugin, PluginSubmission, SandboxJob, SandboxWorkerState
from sqlalchemy import event
from app.core.database import Base
from app.core.memory_search_schema import after_create as _install_memory_search

event.listen(Base.metadata, "after_create", _install_memory_search)

__all__ = [
    "Attachment",
    "AgentRun",
    "AgentStep",
    "AgentCheckpoint",
    "AgentApproval",
    "AgentArtifact",
    "AgentOutboxEvent",
    "AgentWorkerHeartbeat",
    "PatchDraft",
    "FileRevision",
    "Conversation",
    "ConversationShare",
    "KnowledgeBase",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "KnowledgeEvalCase",
    "KnowledgeEvalResult",
    "KnowledgeEvalRun",
    "KnowledgeEvalSet",
    "KnowledgeIndexGeneration",
    "KnowledgeJob",
    "KnowledgeRetrievalLog",
    "OutboxEvent",
    "Message",
    "ChatRun",
    "ChatRunEvent",
    "ChatRuntimeMetric",
    "McpServer",
    "McpTool",
    "SkillInstallationRevision",
    "Project",
    "ProjectFile",
    "PromptTemplate",
    "ToolCallRun",
    "ToolRouteRun",
    "ToolSearchEntry",
    "UserToolCredential",
    "UserSkillInstallation",
    "User",
    "UserMemory",
    "MemoryTriggerDecision",
    "MemoryExtractionJob",
    "UserSetting",
    "WorkspaceToolSetting",
    "WorkspaceAgentPolicy",
]

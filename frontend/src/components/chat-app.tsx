"use client";

import { useEffect, useRef, useState, useTransition } from "react";

import { AppConfirmDialog } from "@/components/app-confirm-dialog";
import { ChatThread } from "@/components/chat-thread";
import { ChatSidebar } from "@/components/chat-sidebar";
import { ConversationExportModal } from "@/components/conversation-export-modal";
import { ConversationShareModal } from "@/components/conversation-share-modal";
import { WorkspaceModal } from "@/components/workspace-modal";
import {
  normalizeThemeMode,
  normalizeUserSettings,
  type UILanguage,
} from "@/lib/settings";
import type {
  Conversation,
  ConversationShare,
  ContextGovernanceInfo,
  Message,
  ProviderInfo,
  Project,
  ProjectFile,
  ProjectStats,
  User,
  UserSettings,
} from "@/lib/types";

type ChatAppProps = {
  initialUser: User | null;
  initialConversations: Conversation[];
  initialMessages: Message[];
  initialProviderInfo: ProviderInfo | null;
  initialSettings: UserSettings | null;
  initialProjects: Project[];
};

type WorkspaceModalMode = "create" | "edit" | "move" | null;
type AppDialogState =
  | { type: "rename-conversation"; conversationId: string; title: string }
  | { type: "delete-conversation"; conversationId: string; title: string }
  | { type: "delete-project"; projectId: string; title: string }
  | null;

const APP_TEXT = {
  "zh-CN": {
    appTag: "AI Web Studio",
    appTitle: "智能问答台",
    newChat: "新对话",
    unnamedUser: "未命名用户",
    settings: "设置",
    logout: "退出",
    currentProvider: "当前 Provider",
    providerLoading: "读取中...",
    providerBaseUrlLoading: "正在读取模型服务地址",
    historyChats: "历史会话",
    workspace: "工作区",
    allWorkspaces: "全部工作区",
    unassignedWorkspace: "未分配会话",
    newWorkspace: "新建工作区",
    renameWorkspace: "重命名工作区",
    configureWorkspace: "工作区设置",
    workspaceSettings: "工作区设置",
    workspaceManage: "设置",
    workspaceName: "工作区名称",
    workspaceDefaultModel: "默认模型",
    workspaceSystemPrompt: "System Prompt",
    workspaceNoDefaultModel: "不设置默认模型",
    workspaceTarget: "目标工作区",
    saveWorkspace: "保存工作区",
    moveWorkspace: "移动会话",
    deleteWorkspace: "删除工作区",
    workspaceNamePrompt: "请输入工作区名称：",
    workspaceModelPrompt: "请输入工作区默认模型（留空则不设置）：",
    workspaceSystemPromptPrompt: "请输入工作区 System Prompt（留空则不设置）：",
    workspaceDeleteConfirm: "确认删除当前工作区吗？会话不会被删除，只会变为未分配。",
    workspaceSaveFailed: "工作区保存失败：",
    workspaceDeleteFailed: "工作区删除失败：",
    workspaceFiles: "项目文件库",
    workspaceStats: "项目统计",
    workspaceAddFiles: "添加文件到工作区",
    workspaceFileEmpty: "当前工作区还没有文件。",
    workspaceFileAddFailed: "工作区文件添加失败：",
    workspaceFileDeleteFailed: "工作区文件删除失败：",
    workspaceConversationCount: "会话数",
    workspaceMessageCount: "消息数",
    workspaceFileCount: "文件数",
    workspaceTemplateCount: "模板数",
    workspaceTotalFileSize: "文件总量",
    activeChats: "进行中",
    archivedChats: "已归档",
    historyCountSuffix: "条",
    conversationSearchPlaceholder: "搜索会话标题或模型",
    noConversationMatches: "没有匹配的会话。",
    showArchived: "显示归档",
    hideArchived: "收起归档",
    noConversations: "还没有历史会话，发送第一条消息后会自动创建。",
    menuLabel: "展开会话菜单",
    rename: "重命名",
    pin: "置顶",
    unpin: "取消置顶",
    archive: "归档",
    unarchive: "取消归档",
    moveToWorkspace: "移动到工作区",
    removeFromWorkspace: "移出工作区",
    shareConversation: "分享会话",
    shareTitle: "私有分享链接",
    shareDescription: "生成只读链接后，知道链接的人可以查看该会话内容。可随时关闭或撤销。",
    shareCreate: "生成分享链接",
    shareRevoke: "撤销分享",
    shareEnable: "启用分享",
    shareDisable: "关闭分享",
    shareCopy: "复制链接",
    shareCopied: "已复制",
    shareExpiresDays: "有效期（天，留空永久）",
    shareNoLink: "当前会话还没有分享链接。",
    shareFailed: "分享操作失败：",
    openShare: "打开分享页",
    delete: "删除",
    currentConversation: "当前会话",
    newConversation: "新的对话",
    renamePrompt: "请输入新的会话名称：",
    renameFailed: "重命名失败：",
    deleteConversationConfirm: "确认删除当前会话吗？这一步不可撤销。",
    deleteConversationFailed: "删除会话失败：",
    loadMessagesFailed: "消息加载失败：",
    saveSettingsFailed: "保存设置失败：",
    testProviderFailed: "测试失败：",
    settingsTag: "用户设置",
    settingsTitle: "模型与会话设置",
    settingsTabProvider: "模型服务",
    settingsTabGeneration: "生成参数",
    settingsTabContext: "上下文",
    settingsTabMemory: "长期记忆",
    settingsTabSystem: "系统提示",
    settingsTabTools: "工具与集成",
    settingsTabAppearance: "外观",
    settingsTabTemplates: "Prompt 模板",
    settingsTabPrivacy: "隐私与导出",
    settingsSectionModels: "模型服务",
    settingsSectionCapabilities: "模型能力",
    settingsSectionContext: "上下文策略",
    settingsSectionPrivacy: "隐私与导出",
    settingsSectionWorkspace: "工作区默认设置",
    close: "关闭",
    confirm: "确认",
    dialogCancel: "取消",
    renameConversationTitle: "重命名会话",
    renameConversationDescription: "修改后会同步更新左侧历史会话标题。",
    deleteConversationTitle: "删除会话",
    deleteConversationDescription: "这一步不可撤销，会同时删除该会话下的历史消息。",
    deleteWorkspaceTitle: "删除工作区",
    deleteWorkspaceDescription: "会话不会被删除，只会变为未分配。",
    provider: "Provider",
    defaultModel: "默认模型",
    baseUrl: "Base URL",
    apiKey: "API Key",
    uiLanguage: "界面语言",
    chinese: "中文",
    english: "English",
    themeMode: "主题模式",
    themeSystem: "跟随系统",
    themeLight: "浅色",
    themeDark: "深色",
    promptTemplates: "Prompt 模板",
    promptTemplateName: "模板名称",
    promptTemplateDescription: "模板说明",
    promptTemplateContent: "模板内容",
    promptTemplateModel: "默认模型（可选）",
    promptTemplateCategory: "分类",
    promptTemplateVariables: "变量（逗号分隔）",
    promptTemplateSearch: "搜索模板名称、说明、分类",
    promptTemplateInsert: "快捷插入",
    promptTemplateVariableValues: "变量值",
    promptTemplateProject: "模板可见范围",
    promptTemplateScopeHint: "只决定模板在哪个范围下分类展示，不会自动写入工作区提示词。真正生效请在模板卡片里选择应用目标。",
    promptTemplateApplyTarget: "应用目标",
    promptTemplateApplyTo: "应用到",
    promptTemplateApplied: "Prompt 模板已应用。",
    globalTemplate: "全局模板",
    promptTemplateDefault: "设为默认模板",
    promptTemplateEmpty: "还没有 Prompt 模板。",
    promptTemplateCreate: "新增模板",
    promptTemplateUpdate: "保存修改",
    promptTemplateNew: "新建模板",
    promptTemplateEdit: "编辑",
    promptTemplateApply: "应用",
    promptTemplateApplyGlobal: "全局 System Prompt",
    promptTemplateApplyWorkspace: "工作区 System Prompt",
    promptTemplateCancelEdit: "取消编辑",
    promptTemplateDeleteConfirm: "确认删除这个 Prompt 模板吗？",
    promptTemplateSaveFailed: "Prompt 模板保存失败：",
    promptTemplateDeleteFailed: "Prompt 模板删除失败：",
    promptTemplateLoadFailed: "Prompt 模板加载失败：",
    exportOptions: "导出选项",
    exportRange: "导出范围",
    exportRangeAll: "全部消息",
    exportRangeSelected: "当前加载消息",
    exportIncludeAttachmentMetadata: "包含附件元数据",
    exportIncludeAttachmentFiles: "ZIP 中包含附件文件",
    exportIncludeContext: "包含上下文摘要",
    exportAsZip: "导出为 ZIP",
    exportRun: "开始导出",
    exportFailed: "导出失败：",
    providerHint: "先测试连接，再保存设置。测试会用当前表单里的 provider 配置实时请求。",
    toolsHint: "这里配置联网搜索背后的只读工具。未配置用户凭证时，会尝试使用后端 .env fallback。",
    toolCredential: "工具凭证",
    toolEnabled: "启用工具",
    credentialSource: "凭证来源",
    apiKeyMasked: "当前 Key",
    testTool: "测试工具",
    saveToolSettings: "保存工具设置",
    toolSettingsSaved: "工具设置已保存",
    toolSettingsFailed: "工具设置失败：",
    workspaceToolOverrides: "当前工作区工具开关",
    noActiveWorkspaceForTools: "当前未选择具体工作区，只显示用户级工具凭证。",
    testing: "测试中...",
    testConnection: "测试连接",
    temperature: "Temperature",
    topP: "Top P",
    maxTokens: "Max Tokens",
    modelContextWindow: "模型上下文窗口",
    contextMode: "上下文模式",
    contextModeConservative: "保守",
    contextModeBalanced: "平衡",
    contextModeLong: "长上下文",
    longTermMemory: "长期记忆",
    memoryEnabled: "启用长期记忆",
    memoryMaxChars: "长期记忆字符预算",
    memoryHint: "长期记忆会跨会话注入上下文，请只保存稳定偏好、项目背景、重要事实和长期指令。",
    memoryType: "记忆类型",
    memoryTypeProfile: "用户偏好",
    memoryTypeProject: "项目背景",
    memoryTypeFact: "重要事实",
    memoryTypeInstruction: "长期指令",
    memoryTitle: "记忆标题",
    memoryContent: "记忆内容",
    addMemory: "新增记忆",
    suggestMemory: "从当前会话提取建议记忆",
    suggestingMemory: "提取中...",
    suggestedMemories: "建议记忆",
    saveSuggestion: "保存",
    ignoreSuggestion: "忽略",
    riskDuplicate: "可能重复",
    riskConflict: "可能冲突",
    riskSafe: "建议保存",
    memoryConfidence: "置信度",
    memorySource: "来源会话",
    openMemorySource: "打开来源",
    noMemorySuggestions: "当前会话暂未提取到适合长期保存的记忆。",
    memorySuggestFailed: "建议记忆生成失败：",
    enableMemory: "启用",
    disableMemory: "停用",
    deleteMemory: "删除",
    memoryEmpty: "还没有长期记忆。",
    memorySaveFailed: "记忆保存失败：",
    memoryDeleteFailed: "记忆删除失败：",
    systemPrompt: "System Prompt",
    resetDefaults: "恢复默认值",
    cancel: "取消",
    saving: "保存中...",
    saveSettings: "保存设置",
    unknownError: "未知错误",
  },
  "en-US": {
    appTag: "AI Web Studio",
    appTitle: "Chat Workspace",
    newChat: "New chat",
    unnamedUser: "Unnamed user",
    settings: "Settings",
    logout: "Logout",
    currentProvider: "Current provider",
    providerLoading: "Loading...",
    providerBaseUrlLoading: "Loading model service URL",
    historyChats: "Conversations",
    workspace: "Workspace",
    allWorkspaces: "All workspaces",
    unassignedWorkspace: "Unassigned chats",
    newWorkspace: "New workspace",
    renameWorkspace: "Rename workspace",
    configureWorkspace: "Workspace settings",
    workspaceSettings: "Workspace settings",
    workspaceManage: "Settings",
    workspaceName: "Workspace name",
    workspaceDefaultModel: "Default model",
    workspaceSystemPrompt: "System Prompt",
    workspaceNoDefaultModel: "No default model",
    workspaceTarget: "Target workspace",
    saveWorkspace: "Save workspace",
    moveWorkspace: "Move conversation",
    deleteWorkspace: "Delete workspace",
    workspaceNamePrompt: "Enter workspace name:",
    workspaceModelPrompt: "Enter workspace default model (leave empty to unset):",
    workspaceSystemPromptPrompt: "Enter workspace System Prompt (leave empty to unset):",
    workspaceDeleteConfirm: "Delete this workspace? Conversations will remain and become unassigned.",
    workspaceSaveFailed: "Save workspace failed: ",
    workspaceDeleteFailed: "Delete workspace failed: ",
    workspaceFiles: "Project files",
    workspaceStats: "Project stats",
    workspaceAddFiles: "Add files to workspace",
    workspaceFileEmpty: "No files in this workspace yet.",
    workspaceFileAddFailed: "Add workspace file failed: ",
    workspaceFileDeleteFailed: "Delete workspace file failed: ",
    workspaceConversationCount: "Conversations",
    workspaceMessageCount: "Messages",
    workspaceFileCount: "Files",
    workspaceTemplateCount: "Templates",
    workspaceTotalFileSize: "Total file size",
    activeChats: "Active",
    archivedChats: "Archived",
    historyCountSuffix: "",
    conversationSearchPlaceholder: "Search by title or model",
    noConversationMatches: "No matching conversations.",
    showArchived: "Show archived",
    hideArchived: "Hide archived",
    noConversations: "No conversation yet. Your first message will create one automatically.",
    menuLabel: "Open conversation menu",
    rename: "Rename",
    pin: "Pin",
    unpin: "Unpin",
    archive: "Archive",
    unarchive: "Unarchive",
    moveToWorkspace: "Move to workspace",
    removeFromWorkspace: "Remove from workspace",
    shareConversation: "Share conversation",
    shareTitle: "Private share link",
    shareDescription: "Anyone with the link can view this conversation read-only. You can disable or revoke it anytime.",
    shareCreate: "Create share link",
    shareRevoke: "Revoke share",
    shareEnable: "Enable share",
    shareDisable: "Disable share",
    shareCopy: "Copy link",
    shareCopied: "Copied",
    shareExpiresDays: "Expires in days (empty for never)",
    shareNoLink: "No share link for this conversation.",
    shareFailed: "Share action failed: ",
    openShare: "Open share page",
    delete: "Delete",
    currentConversation: "Current conversation",
    newConversation: "New chat",
    renamePrompt: "Enter a new conversation title:",
    renameFailed: "Rename failed: ",
    deleteConversationConfirm: "Delete this conversation? This action cannot be undone.",
    deleteConversationFailed: "Delete conversation failed: ",
    loadMessagesFailed: "Failed to load messages: ",
    saveSettingsFailed: "Save settings failed: ",
    testProviderFailed: "Connection test failed: ",
    settingsTag: "User Settings",
    settingsTitle: "Model and Session Settings",
    settingsTabProvider: "Provider",
    settingsTabGeneration: "Generation",
    settingsTabContext: "Context",
    settingsTabMemory: "Memory",
    settingsTabSystem: "System Prompt",
    settingsTabTools: "Tools",
    settingsTabAppearance: "Appearance",
    settingsTabTemplates: "Prompt Templates",
    settingsTabPrivacy: "Privacy & Export",
    settingsSectionModels: "Model service",
    settingsSectionCapabilities: "Model capabilities",
    settingsSectionContext: "Context strategy",
    settingsSectionPrivacy: "Privacy & export",
    settingsSectionWorkspace: "Workspace defaults",
    close: "Close",
    confirm: "Confirm",
    dialogCancel: "Cancel",
    renameConversationTitle: "Rename conversation",
    renameConversationDescription: "The title in the conversation list will be updated.",
    deleteConversationTitle: "Delete conversation",
    deleteConversationDescription: "This cannot be undone and will delete messages in this conversation.",
    deleteWorkspaceTitle: "Delete workspace",
    deleteWorkspaceDescription: "Conversations will remain and become unassigned.",
    provider: "Provider",
    defaultModel: "Default model",
    baseUrl: "Base URL",
    apiKey: "API Key",
    uiLanguage: "Interface language",
    chinese: "Chinese",
    english: "English",
    themeMode: "Theme",
    themeSystem: "System",
    themeLight: "Light",
    themeDark: "Dark",
    promptTemplates: "Prompt templates",
    promptTemplateName: "Template name",
    promptTemplateDescription: "Description",
    promptTemplateContent: "Template content",
    promptTemplateModel: "Default model (optional)",
    promptTemplateCategory: "Category",
    promptTemplateVariables: "Variables (comma-separated)",
    promptTemplateSearch: "Search templates by name, description, category",
    promptTemplateInsert: "Quick insert",
    promptTemplateVariableValues: "Variable values",
    promptTemplateProject: "Template visibility",
    promptTemplateScopeHint:
      "This only controls where the template is categorized. It does not update any active system prompt until you choose an apply target on the template card.",
    promptTemplateApplyTarget: "Apply target",
    promptTemplateApplyTo: "Apply to",
    promptTemplateApplied: "Prompt template applied.",
    globalTemplate: "Global template",
    promptTemplateDefault: "Set as default",
    promptTemplateEmpty: "No prompt templates yet.",
    promptTemplateCreate: "Create template",
    promptTemplateUpdate: "Save changes",
    promptTemplateNew: "New template",
    promptTemplateEdit: "Edit",
    promptTemplateApply: "Apply",
    promptTemplateApplyGlobal: "Global System Prompt",
    promptTemplateApplyWorkspace: "Workspace System Prompt",
    promptTemplateCancelEdit: "Cancel edit",
    promptTemplateDeleteConfirm: "Delete this prompt template?",
    promptTemplateSaveFailed: "Save prompt template failed: ",
    promptTemplateDeleteFailed: "Delete prompt template failed: ",
    promptTemplateLoadFailed: "Load prompt templates failed: ",
    exportOptions: "Export options",
    exportRange: "Export range",
    exportRangeAll: "All messages",
    exportRangeSelected: "Currently loaded messages",
    exportIncludeAttachmentMetadata: "Include attachment metadata",
    exportIncludeAttachmentFiles: "Include attachment files in ZIP",
    exportIncludeContext: "Include context summary",
    exportAsZip: "Export as ZIP",
    exportRun: "Export",
    exportFailed: "Export failed: ",
    providerHint:
      "Test the connection before saving. The test will use the current provider form values.",
    toolsHint:
      "Configure read-only tools behind web search. If user credentials are missing, backend .env fallback is used.",
    toolCredential: "Tool credential",
    toolEnabled: "Enable tool",
    credentialSource: "Credential source",
    apiKeyMasked: "Current key",
    testTool: "Test tool",
    saveToolSettings: "Save tool settings",
    toolSettingsSaved: "Tool settings saved",
    toolSettingsFailed: "Tool settings failed: ",
    workspaceToolOverrides: "Current workspace tool switches",
    noActiveWorkspaceForTools: "No concrete workspace is selected. Showing user-level credentials only.",
    testing: "Testing...",
    testConnection: "Test connection",
    temperature: "Temperature",
    topP: "Top P",
    maxTokens: "Max Tokens",
    modelContextWindow: "Model context window",
    contextMode: "Context mode",
    contextModeConservative: "Conservative",
    contextModeBalanced: "Balanced",
    contextModeLong: "Long context",
    longTermMemory: "Long-term memory",
    memoryEnabled: "Enable long-term memory",
    memoryMaxChars: "Memory char budget",
    memoryHint: "Long-term memory is injected across chats. Save only stable preferences, project background, important facts, and long-lived instructions.",
    memoryType: "Memory type",
    memoryTypeProfile: "Profile",
    memoryTypeProject: "Project",
    memoryTypeFact: "Fact",
    memoryTypeInstruction: "Instruction",
    memoryTitle: "Memory title",
    memoryContent: "Memory content",
    addMemory: "Add memory",
    suggestMemory: "Suggest from current chat",
    suggestingMemory: "Suggesting...",
    suggestedMemories: "Suggested memories",
    saveSuggestion: "Save",
    ignoreSuggestion: "Ignore",
    riskDuplicate: "Possible duplicate",
    riskConflict: "Possible conflict",
    riskSafe: "Recommended",
    memoryConfidence: "Confidence",
    memorySource: "Source chat",
    openMemorySource: "Open source",
    noMemorySuggestions: "No long-term memory candidates were found in this chat.",
    memorySuggestFailed: "Suggest memory failed: ",
    enableMemory: "Enable",
    disableMemory: "Disable",
    deleteMemory: "Delete",
    memoryEmpty: "No long-term memory yet.",
    memorySaveFailed: "Save memory failed: ",
    memoryDeleteFailed: "Delete memory failed: ",
    systemPrompt: "System Prompt",
    resetDefaults: "Reset defaults",
    cancel: "Cancel",
    saving: "Saving...",
    saveSettings: "Save settings",
    unknownError: "Unknown error",
  },
} as const;

async function requestJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    ...init,
    cache: "no-store",
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

async function requestVoid(input: RequestInfo, init?: RequestInit): Promise<void> {
  const response = await fetch(input, {
    ...init,
    cache: "no-store",
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
}

export function ChatApp({
  initialUser,
  initialConversations,
  initialMessages,
  initialProviderInfo,
  initialSettings,
  initialProjects,
}: ChatAppProps) {
  const [currentUser, setCurrentUser] = useState<User | null>(initialUser);
  const [conversations, setConversations] = useState<Conversation[]>(initialConversations);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(
    initialConversations[0]?.id ?? null
  );
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [providerInfo, setProviderInfo] = useState<ProviderInfo | null>(initialProviderInfo);
  const [projects, setProjects] = useState<Project[]>(initialProjects);
  const [selectedProjectScope, setSelectedProjectScope] = useState<string>("all");
  const [workspaceModalMode, setWorkspaceModalMode] = useState<WorkspaceModalMode>(null);
  const [workspaceDraft, setWorkspaceDraft] = useState({
    id: "",
    name: "",
    default_model: "",
    system_prompt: "",
    target_project_id: "",
  });
  const [workspaceMoveConversation, setWorkspaceMoveConversation] = useState<Conversation | null>(null);
  const [appDialog, setAppDialog] = useState<AppDialogState>(null);
  const [renameConversationDraft, setRenameConversationDraft] = useState("");
  const [isDialogSubmitting, setIsDialogSubmitting] = useState(false);
  const [userSettings] = useState<UserSettings | null>(
    initialSettings ? normalizeUserSettings(initialSettings) : null
  );
  const [contextInfoByConversationId, setContextInfoByConversationId] = useState<
    Record<string, ContextGovernanceInfo>
  >({});
  const [selectedModel, setSelectedModel] = useState(
    initialSettings?.default_model ?? initialProviderInfo?.default_model ?? ""
  );
  const [conversationQuery, setConversationQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  const [isDeepThinkingEnabled, setIsDeepThinkingEnabled] = useState(false);
  const [isWebSearchEnabled, setIsWebSearchEnabled] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [shareModalConversation, setShareModalConversation] = useState<Conversation | null>(null);
  const [conversationShare, setConversationShare] = useState<ConversationShare | null>(null);
  const [shareExpiresDays, setShareExpiresDays] = useState("");
  const [isShareBusy, setIsShareBusy] = useState(false);
  const [shareCopied, setShareCopied] = useState(false);
  const [exportModalConversation, setExportModalConversation] = useState<Conversation | null>(null);
  const [exportOptions, setExportOptions] = useState({
    format: "markdown" as "markdown" | "json",
    range: "all" as "all" | "loaded",
    include_attachments: true,
    include_attachment_files: true,
    include_context: false,
    as_zip: false,
  });
  const [highlightedMessageId] = useState<string | null>(null);
  const [projectFiles, setProjectFiles] = useState<ProjectFile[]>([]);
  const [projectStats, setProjectStats] = useState<ProjectStats | null>(null);
  const [isAddingProjectFile, setIsAddingProjectFile] = useState(false);
  const [systemPrefersDark, setSystemPrefersDark] = useState(false);
  const [, startTransition] = useTransition();
  const [openConversationMenuId, setOpenConversationMenuId] = useState<string | null>(null);
  const conversationMenuRef = useRef<HTMLDivElement | null>(null);
  const uiLanguage: UILanguage = userSettings?.ui_language === "en-US" ? "en-US" : "zh-CN";
  const selectedThemeMode = normalizeThemeMode(userSettings?.theme_mode);
  const resolvedTheme = selectedThemeMode === "system" ? (systemPrefersDark ? "dark" : "light") : selectedThemeMode;
  const text = APP_TEXT[uiLanguage];

  const availableModels = providerInfo?.models ?? [];
  const activeProject =
    selectedProjectScope !== "all" && selectedProjectScope !== "unassigned"
      ? projects.find((project) => project.id === selectedProjectScope) ?? null
      : null;
  const modelOptions = availableModels.includes(selectedModel)
    ? availableModels
    : selectedModel
      ? [selectedModel, ...availableModels]
      : availableModels;
  const workspaceModelOptions = Array.from(
    new Set(
      [
        workspaceDraft.default_model,
        selectedModel,
        userSettings?.default_model ?? "",
        ...(providerInfo?.models ?? []),
      ].filter((model) => model.trim().length > 0)
    )
  );
  const shareUrl =
    conversationShare && typeof window !== "undefined"
      ? `${window.location.origin}/share/${conversationShare.token}`
      : "";

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (!conversationMenuRef.current) {
        return;
      }

      if (!conversationMenuRef.current.contains(event.target as Node)) {
        setOpenConversationMenuId(null);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const updatePreference = () => setSystemPrefersDark(mediaQuery.matches);

    updatePreference();
    mediaQuery.addEventListener("change", updatePreference);
    return () => mediaQuery.removeEventListener("change", updatePreference);
  }, []);

  useEffect(() => {
    if (!currentUser) {
      return;
    }
    void loadProjects().catch(() => undefined);
  }, [currentUser]);

  async function loadSession() {
    const me = await requestJson<User>("/api/session/me");
    setCurrentUser(me);
  }

  async function loadConversations() {
    const data = await requestJson<Conversation[]>("/api/backend/conversations");
    setConversations(data);
    return data;
  }

  async function loadProjects() {
    const data = await requestJson<Project[]>("/api/backend/projects");
    setProjects(data);
    return data;
  }

  async function loadProjectDetails(projectId: string) {
    const [files, stats] = await Promise.all([
      requestJson<ProjectFile[]>(`/api/backend/projects/${projectId}/files`),
      requestJson<ProjectStats>(`/api/backend/projects/${projectId}/stats`),
    ]);
    setProjectFiles(files);
    setProjectStats(stats);
    return { files, stats };
  }

  async function refreshProviderInfo() {
    const data = await requestJson<ProviderInfo>("/api/backend/models");
    setProviderInfo(data);
  }

  async function loadMessages(conversationId: string) {
    setIsLoadingMessages(true);
    try {
      const data = await requestJson<Message[]>(
        `/api/backend/conversations/${conversationId}/messages`
      );
      setMessages(data);
      return data;
    } finally {
      setIsLoadingMessages(false);
    }
  }

  async function handleConversationMessagesChanged(conversationId: string | null) {
    if (!conversationId) {
      setMessages([]);
      return;
    }

    await loadMessages(conversationId);
    await loadConversations();
  }

  function handleNewConversation() {
    setSelectedConversationId(null);
    setMessages([]);
    setErrorMessage(null);
    setOpenConversationMenuId(null);
  }

  function handleSelectProjectScope(projectScope: string) {
    setSelectedProjectScope(projectScope);
    const project = projects.find((item) => item.id === projectScope);
    if (project?.default_model) {
      setSelectedModel(project.default_model);
    }
    if (project) {
      void loadProjectDetails(project.id).catch(() => undefined);
    } else {
      setProjectFiles([]);
      setProjectStats(null);
    }
  }

  function openCreateProjectModal() {
    setWorkspaceMoveConversation(null);
    setWorkspaceDraft({
      id: "",
      name: "",
      default_model: selectedModel || userSettings?.default_model || "",
      system_prompt: userSettings?.system_prompt ?? "",
      target_project_id: projects[0]?.id ?? "",
    });
    setWorkspaceModalMode("create");
  }

  function openEditProjectModal() {
    if (!activeProject) {
      return;
    }
    void loadProjectDetails(activeProject.id).catch(() => undefined);
    setWorkspaceMoveConversation(null);
    setWorkspaceDraft({
      id: activeProject.id,
      name: activeProject.name,
      default_model: activeProject.default_model ?? "",
      system_prompt: activeProject.system_prompt ?? "",
      target_project_id: activeProject.id,
    });
    setWorkspaceModalMode("edit");
  }

  function openMoveConversationModal(conversation: Conversation) {
    setWorkspaceMoveConversation(conversation);
    setWorkspaceDraft((current) => ({
      ...current,
      target_project_id:
        conversation.project_id ?? activeProject?.id ?? projects[0]?.id ?? "",
    }));
    setWorkspaceModalMode("move");
  }

  function closeWorkspaceModal() {
    setWorkspaceModalMode(null);
    setWorkspaceMoveConversation(null);
  }

  function closeAppDialog() {
    if (isDialogSubmitting) {
      return;
    }
    setAppDialog(null);
    setRenameConversationDraft("");
  }

  function openRenameConversationDialog(conversationId = selectedConversationId) {
    if (!conversationId) {
      return;
    }
    const title = conversations.find((item) => item.id === conversationId)?.title ?? "";
    setRenameConversationDraft(title);
    setAppDialog({ type: "rename-conversation", conversationId, title });
  }

  function openDeleteConversationDialog(conversationId = selectedConversationId) {
    if (!conversationId) {
      return;
    }
    const title = conversations.find((item) => item.id === conversationId)?.title ?? text.currentConversation;
    setAppDialog({ type: "delete-conversation", conversationId, title });
  }

  function openDeleteProjectDialog() {
    if (!activeProject) {
      return;
    }
    setAppDialog({ type: "delete-project", projectId: activeProject.id, title: activeProject.name });
  }

  async function handleSaveProjectFromModal() {
    const name = workspaceDraft.name.trim();
    if (!name) {
      return;
    }
    try {
      const payload = {
        name,
        default_model: workspaceDraft.default_model.trim() || null,
        system_prompt: workspaceDraft.system_prompt.trim() || null,
      };
      const project = await requestJson<Project>(
        workspaceModalMode === "edit"
          ? `/api/backend/projects/${workspaceDraft.id}`
          : "/api/backend/projects",
        {
          method: workspaceModalMode === "edit" ? "PATCH" : "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        }
      );
      await loadProjects();
      setSelectedProjectScope(project.id);
      if (project.default_model) {
        setSelectedModel(project.default_model);
      }
      closeWorkspaceModal();
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.workspaceSaveFailed}${message}`);
    }
  }

  async function handleMoveConversationFromModal() {
    if (!workspaceMoveConversation || !workspaceDraft.target_project_id) {
      return;
    }
    await handleMoveConversationToProject(workspaceMoveConversation, workspaceDraft.target_project_id);
    closeWorkspaceModal();
  }

  async function handleConfigureProject() {
    openEditProjectModal();
  }

  async function deleteProject(projectId: string) {
    try {
      await requestVoid(`/api/backend/projects/${projectId}`, {
        method: "DELETE",
      });
      setSelectedProjectScope("all");
      await Promise.all([loadProjects(), loadConversations()]);
      closeWorkspaceModal();
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.workspaceDeleteFailed}${message}`);
    }
  }

  async function renameConversation(conversationId: string, nextTitle: string) {
    const title = nextTitle.trim();
    if (!title) {
      return;
    }

    try {
      await requestJson<Conversation>(`/api/backend/conversations/${conversationId}`, {
        method: "PATCH",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          title,
        }),
      });
      if (conversationId !== selectedConversationId) {
        setSelectedConversationId(conversationId);
      }
      await loadConversations();
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.renameFailed}${message}`);
    }
  }

  async function handleAddProjectFiles(files: FileList | null) {
    if (!activeProject || !files || files.length === 0) {
      return;
    }
    setIsAddingProjectFile(true);
    try {
      const formData = new FormData();
      Array.from(files).forEach((file) => formData.append("files", file));
      const uploads = await requestJson<Array<{
        id: string;
        file_name: string;
        mime_type: string | null;
        file_size: number;
        kind: string;
        storage_key: string;
        parsed_text: string | null;
      }>>("/api/backend/uploads", {
        method: "POST",
        body: formData,
      });
      for (const upload of uploads) {
        await requestJson<ProjectFile>(`/api/backend/projects/${activeProject.id}/files`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(upload),
        });
      }
      await loadProjectDetails(activeProject.id);
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.workspaceFileAddFailed}${message}`);
    } finally {
      setIsAddingProjectFile(false);
    }
  }

  async function handleDeleteProjectFile(fileId: string) {
    if (!activeProject) {
      return;
    }
    try {
      await requestVoid(`/api/backend/projects/${activeProject.id}/files/${fileId}`, {
        method: "DELETE",
      });
      await loadProjectDetails(activeProject.id);
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.workspaceFileDeleteFailed}${message}`);
    }
  }

  async function handleMoveConversationToProject(conversation: Conversation, projectId: string | null) {
    try {
      await requestJson<Conversation>(`/api/backend/conversations/${conversation.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
        }),
      });
      await loadConversations();
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.workspaceSaveFailed}${message}`);
    }
  }

  async function handleSelectConversation(conversationId: string) {
    setSelectedConversationId(conversationId);
    setMessages([]);
    setErrorMessage(null);
    setOpenConversationMenuId(null);

    try {
      await loadMessages(conversationId);
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.loadMessagesFailed}${message}`);
    }
  }

  function refreshAfterChat(conversationId: string, shouldSelectConversation: boolean) {
    startTransition(() => {
      if (shouldSelectConversation) {
        setSelectedConversationId(conversationId);
      }
      void loadConversations().catch(() => undefined);
      void loadMessages(conversationId).catch(() => undefined);
      void refreshProviderInfo().catch(() => undefined);
      void loadSession().catch(() => undefined);
    });
  }

  async function handleLogout() {
    await requestVoid("/api/session/logout", {
      method: "POST",
    });
    window.location.reload();
  }

  async function deleteConversation(conversationId: string) {
    if (!conversationId) {
      return;
    }

    try {
      await requestVoid(`/api/backend/conversations/${conversationId}`, {
        method: "DELETE",
      });
      const nextConversations = await loadConversations();
      if (selectedConversationId === conversationId && nextConversations[0]?.id) {
        setSelectedConversationId(nextConversations[0].id);
        await loadMessages(nextConversations[0].id);
      } else if (selectedConversationId === conversationId) {
        setSelectedConversationId(null);
        setMessages([]);
      }
      setContextInfoByConversationId((current) => {
        if (!(conversationId in current)) {
          return current;
        }
        const next = { ...current };
        delete next[conversationId];
        return next;
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.deleteConversationFailed}${message}`);
    }
  }

  async function handleConfirmAppDialog() {
    if (!appDialog || isDialogSubmitting) {
      return;
    }

    if (appDialog.type === "rename-conversation" && !renameConversationDraft.trim()) {
      return;
    }

    setIsDialogSubmitting(true);
    try {
      if (appDialog.type === "rename-conversation") {
        await renameConversation(appDialog.conversationId, renameConversationDraft);
      } else if (appDialog.type === "delete-conversation") {
        await deleteConversation(appDialog.conversationId);
      } else if (appDialog.type === "delete-project") {
        await deleteProject(appDialog.projectId);
      }
      setAppDialog(null);
      setRenameConversationDraft("");
    } finally {
      setIsDialogSubmitting(false);
    }
  }

  async function handleTogglePinned(conversation: Conversation) {
    try {
      await requestJson<Conversation>(`/api/backend/conversations/${conversation.id}`, {
        method: "PATCH",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          is_pinned: !conversation.is_pinned,
        }),
      });
      await loadConversations();
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.saveSettingsFailed}${message}`);
    }
  }

  async function handleToggleArchived(conversation: Conversation) {
    try {
      await requestJson<Conversation>(`/api/backend/conversations/${conversation.id}`, {
        method: "PATCH",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          is_archived: !conversation.is_archived,
        }),
      });
      if (conversation.id === selectedConversationId && !conversation.is_archived) {
        setShowArchived(true);
      }
      await loadConversations();
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.saveSettingsFailed}${message}`);
    }
  }

  async function handleExportConversationWithOptions() {
    if (!exportModalConversation) {
      return;
    }
    try {
      const params = new URLSearchParams({
        format: exportOptions.format,
        include_attachments: String(exportOptions.include_attachments),
        include_attachment_files: String(exportOptions.include_attachment_files),
        include_context: String(exportOptions.include_context),
        as_zip: String(exportOptions.as_zip),
      });
      if (exportOptions.range === "loaded" && messages.length > 0) {
        params.set("message_ids", messages.map((message) => message.id).join(","));
      }
      const response = await fetch(
        `/api/backend/conversations/${exportModalConversation.id}/export?${params.toString()}`,
        { cache: "no-store" }
      );
      if (!response.ok) {
        const body = await response.text();
        throw new Error(body || `Request failed: ${response.status}`);
      }

      const blob = await response.blob();
      const extension = exportOptions.as_zip ? "zip" : exportOptions.format === "json" ? "json" : "md";
      const safeTitle =
        exportModalConversation.title.replace(/[^\w\u4e00-\u9fff.-]+/g, "_").slice(0, 80) || "conversation";
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${safeTitle}.${extension}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setExportModalConversation(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.exportFailed}${message}`);
    }
  }

  async function openShareModal(conversation: Conversation) {
    setShareModalConversation(conversation);
    setConversationShare(null);
    setShareExpiresDays("");
    setShareCopied(false);
    try {
      const data = await requestJson<ConversationShare | null>(
        `/api/backend/conversations/${conversation.id}/share`
      );
      setConversationShare(data);
    } catch {
      setConversationShare(null);
    }
  }

  async function handleCreateOrEnableShare() {
    if (!shareModalConversation) {
      return;
    }
    setIsShareBusy(true);
    setErrorMessage(null);
    try {
      const payload = {
        expires_in_days: shareExpiresDays.trim() ? Number(shareExpiresDays) : null,
      };
      const data = await requestJson<ConversationShare>(
        `/api/backend/conversations/${shareModalConversation.id}/share`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        }
      );
      setConversationShare(data);
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.shareFailed}${message}`);
    } finally {
      setIsShareBusy(false);
    }
  }

  async function handleToggleShare(enabled: boolean) {
    if (!shareModalConversation || !conversationShare) {
      return;
    }
    setIsShareBusy(true);
    try {
      const data = await requestJson<ConversationShare>(
        `/api/backend/conversations/${shareModalConversation.id}/share`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ is_enabled: enabled }),
        }
      );
      setConversationShare(data);
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.shareFailed}${message}`);
    } finally {
      setIsShareBusy(false);
    }
  }

  async function handleRevokeShare() {
    if (!shareModalConversation) {
      return;
    }
    setIsShareBusy(true);
    try {
      await requestVoid(`/api/backend/conversations/${shareModalConversation.id}/share`, {
        method: "DELETE",
      });
      setConversationShare(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.shareFailed}${message}`);
    } finally {
      setIsShareBusy(false);
    }
  }

  async function handleCopyShareUrl() {
    if (!shareUrl) {
      return;
    }
    await navigator.clipboard.writeText(shareUrl);
    setShareCopied(true);
    window.setTimeout(() => setShareCopied(false), 1600);
  }

  const activeConversationTitle = selectedConversationId
    ? conversations.find((item) => item.id === selectedConversationId)?.title ?? text.currentConversation
    : text.newConversation;
  const selectedConversation = selectedConversationId
    ? conversations.find((item) => item.id === selectedConversationId) ?? null
    : null;
  const normalizedConversationQuery = conversationQuery.trim().toLowerCase();
  const filteredConversations = conversations.filter((conversation) => {
    if (selectedProjectScope === "unassigned" && conversation.project_id) {
      return false;
    }
    if (
      selectedProjectScope !== "all" &&
      selectedProjectScope !== "unassigned" &&
      conversation.project_id !== selectedProjectScope
    ) {
      return false;
    }
    if (!normalizedConversationQuery) {
      return true;
    }
    const haystack = `${conversation.title} ${conversation.model_name}`.toLowerCase();
    return haystack.includes(normalizedConversationQuery);
  });
  const activeConversations = filteredConversations.filter((conversation) => !conversation.is_archived);
  const archivedConversations = filteredConversations.filter((conversation) => conversation.is_archived);
  const shouldShowArchivedSection =
    showArchived || Boolean(normalizedConversationQuery) || Boolean(selectedConversation?.is_archived);
  const contextInfo = selectedConversationId
    ? contextInfoByConversationId[selectedConversationId] ?? null
    : null;
  const threadKey = `${selectedConversationId ?? "draft"}:${
    messages[messages.length - 1]?.id ?? "empty"
  }:${messages.length}`;

  function handleContextInfoChange(
    info: ContextGovernanceInfo | null,
    conversationId = selectedConversationId
  ) {
    if (!conversationId) {
      return;
    }

    setContextInfoByConversationId((current) => {
      if (!info) {
        if (!(conversationId in current)) {
          return current;
        }
        const next = { ...current };
        delete next[conversationId];
        return next;
      }

      return {
        ...current,
        [conversationId]: info,
      };
    });
  }

  function appDialogTitle() {
    if (!appDialog) {
      return "";
    }
    if (appDialog.type === "rename-conversation") {
      return text.renameConversationTitle;
    }
    if (appDialog.type === "delete-conversation") {
      return text.deleteConversationTitle;
    }
    if (appDialog.type === "delete-project") {
      return text.deleteWorkspaceTitle;
    }
    return "";
  }

  function appDialogDescription() {
    if (!appDialog) {
      return "";
    }
    if (appDialog.type === "rename-conversation") {
      return text.renameConversationDescription;
    }
    if (appDialog.type === "delete-conversation") {
      return text.deleteConversationDescription;
    }
    if (appDialog.type === "delete-project") {
      return text.deleteWorkspaceDescription;
    }
    return "";
  }

  return (
    <main
      data-theme={resolvedTheme}
      className="app-shell h-screen overflow-hidden bg-[var(--app-bg)] px-2 py-2 text-[var(--ink-strong)] sm:px-3 lg:px-4"
    >
      <div className="app-frame mx-auto flex h-[calc(100vh-1rem)] flex-col gap-2.5 lg:flex-row">
        <ChatSidebar
          text={text}
          currentUser={currentUser}
          providerInfo={providerInfo}
          conversations={conversations}
          filteredConversations={filteredConversations}
          activeConversations={activeConversations}
          archivedConversations={archivedConversations}
          projects={projects}
          activeProject={activeProject}
          selectedProjectScope={selectedProjectScope}
          selectedConversationId={selectedConversationId}
          conversationQuery={conversationQuery}
          showArchived={showArchived}
          shouldShowArchivedSection={shouldShowArchivedSection}
          openConversationMenuId={openConversationMenuId}
          uiLanguage={uiLanguage}
          conversationMenuRef={conversationMenuRef}
          onNewConversation={handleNewConversation}
          onOpenSettings={() => {
            window.location.href = "/settings";
          }}
          onLogout={handleLogout}
          onProjectScopeChange={handleSelectProjectScope}
          onConfigureProject={handleConfigureProject}
          onCreateProject={openCreateProjectModal}
          onConversationQueryChange={setConversationQuery}
          onToggleArchived={() => setShowArchived((current) => !current)}
          onSelectConversation={handleSelectConversation}
          onToggleConversationMenu={(conversationId) =>
            setOpenConversationMenuId((current) => (current === conversationId ? null : conversationId))
          }
          onRenameConversation={(conversationId) => {
            setOpenConversationMenuId(null);
            openRenameConversationDialog(conversationId);
          }}
          onTogglePinned={(conversation) => {
            setOpenConversationMenuId(null);
            return handleTogglePinned(conversation);
          }}
          onToggleConversationArchived={(conversation) => {
            setOpenConversationMenuId(null);
            return handleToggleArchived(conversation);
          }}
          onMoveConversation={(conversation) => {
            setOpenConversationMenuId(null);
            openMoveConversationModal(conversation);
          }}
          onRemoveConversationFromWorkspace={(conversation, projectId) => {
            setOpenConversationMenuId(null);
            return handleMoveConversationToProject(conversation, projectId);
          }}
          onShareConversation={(conversation) => {
            setOpenConversationMenuId(null);
            return openShareModal(conversation);
          }}
          onExportConversation={(conversation) => {
            setOpenConversationMenuId(null);
            setExportModalConversation(conversation);
          }}
          onDeleteConversation={(conversationId) => {
            setOpenConversationMenuId(null);
            openDeleteConversationDialog(conversationId);
          }}
        />

        <section className="chat-surface relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-[24px] border backdrop-blur">
          <header className="chat-header z-10 flex shrink-0 flex-col gap-1.5 border-b px-3 py-2.5 backdrop-blur sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-[1.45rem] font-semibold leading-tight">{activeConversationTitle}</h2>
            </div>

            <div className="flex flex-col gap-1.5 sm:items-end">
              <select
                value={selectedModel}
                onChange={(event) => setSelectedModel(event.target.value)}
                className="min-w-[220px] rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-1.5 text-sm outline-none transition focus:border-[var(--accent-strong)]"
              >
                {modelOptions.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </div>
          </header>

          {errorMessage ? (
            <div className="mx-4 mt-3 rounded-2xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-4 py-3 text-sm text-[var(--danger-text)]">
              {errorMessage}
            </div>
          ) : null}

          <ChatThread
            key={threadKey}
            initialConversationId={selectedConversationId}
            initialMessages={messages}
            isLoadingMessages={isLoadingMessages}
            selectedModel={selectedModel}
            systemPrompt={activeProject?.system_prompt ?? userSettings?.system_prompt ?? null}
            projectId={activeProject?.id ?? null}
            contextInfo={contextInfo}
            highlightedMessageId={highlightedMessageId}
            uiLanguage={uiLanguage}
            isDeepThinkingEnabled={isDeepThinkingEnabled}
            isWebSearchEnabled={isWebSearchEnabled}
            onDeepThinkingEnabledChange={setIsDeepThinkingEnabled}
            onWebSearchEnabledChange={setIsWebSearchEnabled}
            onContextInfoChange={handleContextInfoChange}
            onChatSettled={refreshAfterChat}
            onConversationMessagesChanged={handleConversationMessagesChanged}
          />

          <WorkspaceModal
            mode={workspaceModalMode}
            text={text}
            projects={projects}
            workspaceDraft={workspaceDraft}
            workspaceMoveConversation={workspaceMoveConversation}
            activeProject={activeProject}
            workspaceModelOptions={workspaceModelOptions}
            projectFiles={projectFiles}
            projectStats={projectStats}
            isAddingProjectFile={isAddingProjectFile}
            onClose={closeWorkspaceModal}
            onDraftChange={setWorkspaceDraft}
            onDeleteProject={openDeleteProjectDialog}
            onSubmit={() =>
              workspaceModalMode === "move"
                ? handleMoveConversationFromModal()
                : handleSaveProjectFromModal()
            }
            onAddProjectFiles={handleAddProjectFiles}
            onDeleteProjectFile={handleDeleteProjectFile}
          />

          <ConversationShareModal
            conversation={shareModalConversation}
            conversationShare={conversationShare}
            shareExpiresDays={shareExpiresDays}
            shareUrl={shareUrl}
            shareCopied={shareCopied}
            isShareBusy={isShareBusy}
            text={text}
            onClose={() => setShareModalConversation(null)}
            onShareExpiresDaysChange={setShareExpiresDays}
            onCreateOrEnableShare={handleCreateOrEnableShare}
            onCopyShareUrl={handleCopyShareUrl}
            onToggleShare={handleToggleShare}
            onRevokeShare={handleRevokeShare}
          />

          <ConversationExportModal
            conversation={exportModalConversation}
            exportOptions={exportOptions}
            text={text}
            onClose={() => setExportModalConversation(null)}
            onOptionsChange={setExportOptions}
            onExport={handleExportConversationWithOptions}
          />

          <AppConfirmDialog
            dialog={appDialog}
            text={text}
            title={appDialogTitle()}
            description={appDialogDescription()}
            renameConversationDraft={renameConversationDraft}
            isDialogSubmitting={isDialogSubmitting}
            onClose={closeAppDialog}
            onRenameConversationDraftChange={setRenameConversationDraft}
            onSubmit={handleConfirmAppDialog}
          />

        </section>
      </div>
    </main>
  );
}

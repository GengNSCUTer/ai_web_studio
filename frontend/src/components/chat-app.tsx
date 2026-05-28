"use client";

import { FormEvent, useEffect, useRef, useState, useTransition } from "react";

import { ChatThread } from "@/components/chat-thread";
import type {
  Conversation,
  ConversationShare,
  ContextGovernanceInfo,
  MemorySuggestion,
  Message,
  PromptTemplate,
  ProviderConnectionTestResult,
  ProviderInfo,
  Project,
  ProjectFile,
  ProjectStats,
  ToolConnectionTestResult,
  ToolSettings,
  User,
  UserMemory,
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

type UILanguage = "zh-CN" | "en-US";
type SettingsTab =
  | "provider"
  | "generation"
  | "context"
  | "memory"
  | "system"
  | "tools"
  | "appearance"
  | "templates"
  | "privacy";
type ThemeMode = "system" | "light" | "dark";
type WorkspaceModalMode = "create" | "edit" | "move" | null;
type AppDialogState =
  | { type: "rename-conversation"; conversationId: string; title: string }
  | { type: "delete-conversation"; conversationId: string; title: string }
  | { type: "delete-project"; projectId: string; title: string }
  | { type: "delete-template"; templateId: string; title: string }
  | null;

const PROVIDER_PRESETS = {
  ollama: {
    baseUrl: "http://127.0.0.1:11435",
    model: "qwen3.5:27b-q8_0",
  },
  "openai-compatible": {
    baseUrl: "https://api.siliconflow.cn/v1",
    model: "Qwen/Qwen3.5-35B-A3B",
  },
} as const;

const APP_TEXT = {
  "zh-CN": {
    appTag: "AI Web Studio",
    appTitle: "智能问答台",
    newChat: "新对话",
    unnamedUser: "未命名用户",
    settings: "设置",
    advancedSettings: "高级",
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
    deletePromptTemplateTitle: "删除 Prompt 模板",
    deletePromptTemplateDescription: "删除后无法从模板库恢复。",
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
    advancedSettings: "Advanced",
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
    deletePromptTemplateTitle: "Delete prompt template",
    deletePromptTemplateDescription: "This template cannot be restored after deletion.",
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

function buildProviderPreset(providerType: string) {
  return (
    PROVIDER_PRESETS[providerType as keyof typeof PROVIDER_PRESETS] ?? PROVIDER_PRESETS.ollama
  );
}

function normalizeUserSettings(settings: UserSettings): UserSettings {
  return {
    ...settings,
    memory_enabled: settings.memory_enabled ?? true,
    memory_max_chars: settings.memory_max_chars || 4000,
    theme_mode: settings.theme_mode || "system",
  };
}

function normalizeThemeMode(value: string | null | undefined): ThemeMode {
  return value === "light" || value === "dark" || value === "system" ? value : "system";
}

function formatTime(value: string | null, uiLanguage: UILanguage) {
  if (!value) {
    return "--";
  }

  return new Intl.DateTimeFormat(uiLanguage, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

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
  const [userSettings, setUserSettings] = useState<UserSettings | null>(
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
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const [isTestingProvider, setIsTestingProvider] = useState(false);
  const [isSuggestingMemory, setIsSuggestingMemory] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isDeepThinkingEnabled, setIsDeepThinkingEnabled] = useState(false);
  const [isWebSearchEnabled, setIsWebSearchEnabled] = useState(false);
  const [activeSettingsTab, setActiveSettingsTab] = useState<SettingsTab>("provider");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [settingsMessage, setSettingsMessage] = useState<string | null>(null);
  const [settingsModels, setSettingsModels] = useState<string[]>(initialProviderInfo?.models ?? []);
  const [memories, setMemories] = useState<UserMemory[]>([]);
  const [promptTemplates, setPromptTemplates] = useState<PromptTemplate[]>([]);
  const [editingPromptTemplateId, setEditingPromptTemplateId] = useState<string | null>(null);
  const [promptTemplateDraft, setPromptTemplateDraft] = useState({
    project_id: "",
    name: "",
    description: "",
    content: "",
    default_model: "",
    category: "",
    variables: "",
    is_default: false,
  });
  const [promptTemplateApplyTargets, setPromptTemplateApplyTargets] = useState<Record<string, string>>({});
  const [promptTemplateQuery, setPromptTemplateQuery] = useState("");
  const [promptTemplateVariableValues, setPromptTemplateVariableValues] = useState<Record<string, Record<string, string>>>({});
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
  const [highlightedMessageId, setHighlightedMessageId] = useState<string | null>(null);
  const [projectFiles, setProjectFiles] = useState<ProjectFile[]>([]);
  const [projectStats, setProjectStats] = useState<ProjectStats | null>(null);
  const [isAddingProjectFile, setIsAddingProjectFile] = useState(false);
  const [toolSettings, setToolSettings] = useState<ToolSettings | null>(null);
  const [toolCredentialDrafts, setToolCredentialDrafts] = useState<Record<string, string>>({});
  const [toolEnabledDrafts, setToolEnabledDrafts] = useState<Record<string, boolean>>({});
  const [workspaceToolEnabledDrafts, setWorkspaceToolEnabledDrafts] = useState<Record<string, boolean>>({});
  const [isSavingToolSettings, setIsSavingToolSettings] = useState(false);
  const [testingToolProvider, setTestingToolProvider] = useState<string | null>(null);
  const [systemPrefersDark, setSystemPrefersDark] = useState(false);
  const [memoryDraft, setMemoryDraft] = useState({
    memory_type: "fact",
    title: "",
    content: "",
  });
  const [memorySuggestions, setMemorySuggestions] = useState<MemorySuggestion[]>([]);
  const [isPending, startTransition] = useTransition();
  const [openConversationMenuId, setOpenConversationMenuId] = useState<string | null>(null);
  const conversationMenuRef = useRef<HTMLDivElement | null>(null);
  const uiLanguage: UILanguage = userSettings?.ui_language === "en-US" ? "en-US" : "zh-CN";
  const selectedThemeMode = normalizeThemeMode(userSettings?.theme_mode);
  const resolvedTheme = selectedThemeMode === "system" ? (systemPrefersDark ? "dark" : "light") : selectedThemeMode;
  const text = APP_TEXT[uiLanguage];
  const settingsTabs: Array<{ id: SettingsTab; label: string }> = [
    { id: "provider", label: text.settingsTabProvider },
    { id: "generation", label: text.settingsTabGeneration },
    { id: "context", label: text.settingsTabContext },
    { id: "memory", label: text.settingsTabMemory },
    { id: "system", label: text.settingsTabSystem },
    { id: "tools", label: text.settingsTabTools },
    { id: "templates", label: text.settingsTabTemplates },
    { id: "privacy", label: text.settingsTabPrivacy },
    { id: "appearance", label: text.settingsTabAppearance },
  ];

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
  const settingsModelOptions = settingsModels.includes(userSettings?.default_model ?? "")
    ? settingsModels
    : userSettings?.default_model
      ? [userSettings.default_model, ...settingsModels]
      : settingsModels;
  const workspaceModelOptions = Array.from(
    new Set(
      [
        workspaceDraft.default_model,
        selectedModel,
        userSettings?.default_model ?? "",
        ...(providerInfo?.models ?? []),
        ...settingsModels,
      ].filter((model) => model.trim().length > 0)
    )
  );
  const promptTemplateTargetOptions = [
    { id: "global", label: text.promptTemplateApplyGlobal },
    ...projects.map((project) => ({
      id: project.id,
      label: `${text.promptTemplateApplyWorkspace} · ${project.name}`,
    })),
  ];
  const filteredPromptTemplates = promptTemplates.filter((template) => {
    const query = promptTemplateQuery.trim().toLowerCase();
    if (!query) {
      return true;
    }
    return [template.name, template.description ?? "", template.category ?? "", template.content]
      .join(" ")
      .toLowerCase()
      .includes(query);
  });
  const shareUrl =
    conversationShare && typeof window !== "undefined"
      ? `${window.location.origin}/share/${conversationShare.token}`
      : "";
  const toolProviders = Array.from(new Set((toolSettings?.tools ?? []).map((tool) => tool.provider)));
  const credentialByProvider = Object.fromEntries(
    (toolSettings?.credentials ?? []).map((credential) => [credential.provider_key, credential])
  );

  function getPromptTemplateApplyTarget(template: PromptTemplate) {
    return promptTemplateApplyTargets[template.id] ?? activeProject?.id ?? "global";
  }

  function promptTemplateVariables(template: PromptTemplate) {
    return (template.variables ?? "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function renderPromptTemplateContent(template: PromptTemplate) {
    const values = promptTemplateVariableValues[template.id] ?? {};
    return promptTemplateVariables(template).reduce((content, variable) => {
      const value = values[variable] ?? "";
      return content
        .replaceAll(`{{${variable}}}`, value)
        .replaceAll(`{${variable}}`, value);
    }, template.content);
  }

  function buildSettingsPayload(settings: UserSettings) {
    return {
      provider_type: settings.provider_type,
      default_model: settings.default_model,
      ollama_base_url: settings.ollama_base_url,
      api_key: settings.api_key,
      temperature: settings.temperature,
      top_p: settings.top_p,
      max_tokens: settings.max_tokens,
      system_prompt: settings.system_prompt,
      model_context_window: settings.model_context_window,
      context_mode: settings.context_mode,
      memory_enabled: settings.memory_enabled,
      memory_max_chars: settings.memory_max_chars,
      ui_language: settings.ui_language,
      theme_mode: settings.theme_mode,
    };
  }

  function applyToolSettingsDrafts(data: ToolSettings) {
    setToolSettings(data);
    setToolCredentialDrafts(
      Object.fromEntries(data.credentials.map((credential) => [credential.provider_key, ""]))
    );
    setToolEnabledDrafts(
      Object.fromEntries(data.credentials.map((credential) => [credential.provider_key, credential.is_enabled]))
    );
    const workspaceMap = new Map(data.workspace_settings.map((item) => [item.tool_key, item.is_enabled]));
    setWorkspaceToolEnabledDrafts(
      Object.fromEntries(data.tools.map((tool) => [tool.tool_key, workspaceMap.get(tool.tool_key) ?? true]))
    );
  }

  async function loadToolSettings(projectId?: string | null) {
    const suffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
    const data = await requestJson<ToolSettings>(`/api/backend/tools/settings${suffix}`);
    applyToolSettingsDrafts(data);
    return data;
  }

  function applyProviderPreset(providerType: string) {
    const preset = buildProviderPreset(providerType);

    setSettingsMessage(null);
    setSettingsModels([]);
    setUserSettings((current) =>
      current
        ? {
            ...current,
            provider_type: providerType,
            default_model: preset.model,
            ollama_base_url: preset.baseUrl,
            model_context_window: providerType === "ollama" ? 100000 : 128000,
          }
        : current
    );
  }

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
    void loadMemories().catch(() => undefined);
    void loadPromptTemplates().catch(() => undefined);
    void loadProjects().catch(() => undefined);
    void loadToolSettings(activeProject?.id).catch(() => undefined);
  }, [currentUser]);

  useEffect(() => {
    if (!currentUser || !isSettingsOpen || activeSettingsTab !== "tools") {
      return;
    }
    void loadToolSettings(activeProject?.id).catch((error) => {
      const message = error instanceof Error ? error.message : text.unknownError;
      setSettingsMessage(`${text.toolSettingsFailed}${message}`);
    });
  }, [activeProject?.id, activeSettingsTab, currentUser, isSettingsOpen]);

  function resetSettingsToDefaults() {
    if (!userSettings) {
      return;
    }

    const preset = buildProviderPreset(userSettings.provider_type);
    setSettingsMessage(null);
    setUserSettings((current) =>
      current
        ? {
            ...current,
            default_model: preset.model,
            ollama_base_url: preset.baseUrl,
            api_key: current.provider_type === "openai-compatible" ? current.api_key : null,
            temperature: 0.7,
            top_p: 0.9,
            max_tokens: null,
            system_prompt: null,
            model_context_window: current.provider_type === "ollama" ? 100000 : 128000,
            context_mode: "balanced",
            memory_enabled: current.memory_enabled ?? true,
            memory_max_chars: current.memory_max_chars || 4000,
            ui_language: current.ui_language || "zh-CN",
            theme_mode: current.theme_mode || "system",
          }
        : current
    );
    setSettingsModels([]);
  }

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

  async function loadSettings() {
    const data = await requestJson<UserSettings>("/api/backend/settings");
    const normalized = normalizeUserSettings(data);
    setUserSettings(normalized);
    return normalized;
  }

  async function loadMemories() {
    const data = await requestJson<UserMemory[]>("/api/backend/memories");
    setMemories(data);
    return data;
  }

  async function loadPromptTemplates() {
    const data = await requestJson<PromptTemplate[]>("/api/backend/prompt-templates");
    setPromptTemplates(data);
    return data;
  }

  function resetPromptTemplateDraft() {
    setEditingPromptTemplateId(null);
    setPromptTemplateDraft({
      project_id: "",
      name: "",
      description: "",
      content: "",
      default_model: "",
      category: "",
      variables: "",
      is_default: false,
    });
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

  function openDeletePromptTemplateDialog(template: PromptTemplate) {
    setAppDialog({ type: "delete-template", templateId: template.id, title: template.name });
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

  async function handleOpenMemorySource(conversationId: string, sourceMessageIds?: string | null) {
    setIsSettingsOpen(false);
    await handleSelectConversation(conversationId);
    const firstMessageId = sourceMessageIds?.split(",").map((item) => item.trim()).filter(Boolean)[0];
    if (firstMessageId) {
      setHighlightedMessageId(firstMessageId);
      window.setTimeout(() => setHighlightedMessageId(null), 5000);
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
      void loadSettings().catch(() => undefined);
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

  async function deletePromptTemplate(templateId: string) {
    try {
      await requestVoid(`/api/backend/prompt-templates/${templateId}`, {
        method: "DELETE",
      });
      if (editingPromptTemplateId === templateId) {
        resetPromptTemplateDraft();
      }
      await loadPromptTemplates();
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.promptTemplateDeleteFailed}${message}`);
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
      } else if (appDialog.type === "delete-template") {
        await deletePromptTemplate(appDialog.templateId);
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

  async function handleSavePromptTemplate() {
    const name = promptTemplateDraft.name.trim();
    const content = promptTemplateDraft.content.trim();
    if (!name || !content) {
      return;
    }

    const payload = {
      name,
      project_id: promptTemplateDraft.project_id || null,
      description: promptTemplateDraft.description.trim() || null,
      content,
      default_model: promptTemplateDraft.default_model.trim() || null,
      category: promptTemplateDraft.category.trim() || null,
      variables: promptTemplateDraft.variables.trim() || null,
      is_default: promptTemplateDraft.is_default,
    };

    try {
      if (editingPromptTemplateId) {
        await requestJson<PromptTemplate>(`/api/backend/prompt-templates/${editingPromptTemplateId}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        await requestJson<PromptTemplate>("/api/backend/prompt-templates", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      resetPromptTemplateDraft();
      await loadPromptTemplates();
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.promptTemplateSaveFailed}${message}`);
    }
  }

  function handleEditPromptTemplate(template: PromptTemplate) {
    setEditingPromptTemplateId(template.id);
    setPromptTemplateDraft({
      name: template.name,
      project_id: template.project_id ?? "",
      description: template.description ?? "",
      content: template.content,
      default_model: template.default_model ?? "",
      category: template.category ?? "",
      variables: template.variables ?? "",
      is_default: template.is_default,
    });
  }

  async function handleApplyPromptTemplate(template: PromptTemplate, targetId: string) {
    try {
      if (targetId === "global") {
        if (!userSettings) {
          return;
        }

        const renderedContent = renderPromptTemplateContent(template);
        const nextSettings = {
          ...userSettings,
          system_prompt: renderedContent,
          default_model: template.default_model || userSettings.default_model,
        };
        const savedRaw = await requestJson<UserSettings>("/api/backend/settings", {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(buildSettingsPayload(nextSettings)),
        });
        const saved = normalizeUserSettings(savedRaw);
        setUserSettings(saved);
        if (template.default_model) {
          setSelectedModel(template.default_model);
        }
        setSettingsMessage(text.promptTemplateApplied);
        return;
      }

      const targetProject = projects.find((project) => project.id === targetId);
      if (!targetProject) {
        return;
      }

      const project = await requestJson<Project>(`/api/backend/projects/${targetProject.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          system_prompt: renderPromptTemplateContent(template),
          default_model: template.default_model || targetProject.default_model,
        }),
      });
      const nextProjects = await loadProjects();
      if (selectedProjectScope === project.id && project.default_model) {
        setSelectedModel(project.default_model);
      }
      if (workspaceModalMode === "edit" && workspaceDraft.id === project.id) {
        const refreshedProject = nextProjects.find((item) => item.id === project.id) ?? project;
        setWorkspaceDraft({
          id: refreshedProject.id,
          name: refreshedProject.name,
          default_model: refreshedProject.default_model ?? "",
          system_prompt: refreshedProject.system_prompt ?? "",
          target_project_id: refreshedProject.id,
        });
      }
      setSettingsMessage(text.promptTemplateApplied);
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.workspaceSaveFailed}${message}`);
    }
  }

  function handleQuickInsertPromptTemplate(template: PromptTemplate) {
    const renderedContent = renderPromptTemplateContent(template);
    setUserSettings((current) =>
      current
        ? {
            ...current,
            system_prompt: [current.system_prompt, renderedContent].filter(Boolean).join("\n\n"),
          }
        : current
    );
    setActiveSettingsTab("system");
  }

  async function handleSaveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!userSettings) {
      return;
    }

    setIsSavingSettings(true);
    setErrorMessage(null);
    setSettingsMessage(null);

    try {
      const savedRaw = await requestJson<UserSettings>("/api/backend/settings", {
        method: "PATCH",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify(buildSettingsPayload(userSettings)),
      });
      const saved = normalizeUserSettings(savedRaw);
      setUserSettings(saved);
      setSelectedModel(saved.default_model);
      setProviderInfo((current) => ({
        provider: saved.provider_type,
        base_url: saved.ollama_base_url,
        default_model: saved.default_model,
        models:
          current?.provider === saved.provider_type && current.models.length > 0
            ? current.models
            : [saved.default_model],
      }));
      setSettingsModels((current) =>
        current.length > 0
          ? current
          : [saved.default_model]
      );
      void refreshProviderInfo().catch(() => undefined);
      setIsSettingsOpen(false);
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.saveSettingsFailed}${message}`);
    } finally {
      setIsSavingSettings(false);
    }
  }

  async function handleTestProviderConnection() {
    if (!userSettings) {
      return;
    }

    setIsTestingProvider(true);
    setErrorMessage(null);
    setSettingsMessage(null);

    try {
      const result = await requestJson<ProviderConnectionTestResult>(
        "/api/backend/settings/test-provider",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
          },
          body: JSON.stringify({
            provider_type: userSettings.provider_type,
            ollama_base_url: userSettings.ollama_base_url,
            api_key:
              userSettings.provider_type === "openai-compatible" ? userSettings.api_key : null,
          }),
        }
      );

      const nextDefaultModel =
        result.default_model ??
        (result.models.includes(userSettings.default_model)
          ? userSettings.default_model
          : (result.models[0] ?? userSettings.default_model));

      setProviderInfo({
        provider: result.provider,
        base_url: result.base_url,
        default_model: nextDefaultModel,
        models: result.models,
      });
      setSettingsModels(result.models);
      setUserSettings((current) =>
        current
          ? {
              ...current,
              default_model: nextDefaultModel,
            }
          : current
      );
      setSelectedModel(nextDefaultModel);
      setSettingsMessage(result.message);
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setSettingsMessage(`${text.testProviderFailed}${message}`);
    } finally {
      setIsTestingProvider(false);
    }
  }

  async function handleSaveToolSettings() {
    if (!toolSettings) {
      return;
    }

    setIsSavingToolSettings(true);
    setErrorMessage(null);
    setSettingsMessage(null);

    try {
      for (const providerKey of toolProviders) {
        await requestJson(`/api/backend/tools/credentials/${providerKey}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            is_enabled: toolEnabledDrafts[providerKey] ?? true,
            api_key: toolCredentialDrafts[providerKey]?.trim()
              ? toolCredentialDrafts[providerKey].trim()
              : undefined,
          }),
        });
      }

      if (activeProject) {
        for (const tool of toolSettings.tools) {
          await requestJson(`/api/backend/tools/workspaces/${activeProject.id}/${encodeURIComponent(tool.tool_key)}`, {
            method: "PATCH",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              is_enabled: workspaceToolEnabledDrafts[tool.tool_key] ?? true,
            }),
          });
        }
      }

      await loadToolSettings(activeProject?.id);
      setSettingsMessage(text.toolSettingsSaved);
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.toolSettingsFailed}${message}`);
    } finally {
      setIsSavingToolSettings(false);
    }
  }

  async function handleTestToolProvider(providerKey: string) {
    setTestingToolProvider(providerKey);
    setErrorMessage(null);
    setSettingsMessage(null);
    try {
      if (toolCredentialDrafts[providerKey]?.trim()) {
        await requestJson(`/api/backend/tools/credentials/${providerKey}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            is_enabled: toolEnabledDrafts[providerKey] ?? true,
            api_key: toolCredentialDrafts[providerKey].trim(),
          }),
        });
      }
      const result = await requestJson<ToolConnectionTestResult>(
        `/api/backend/tools/credentials/${providerKey}/test`,
        { method: "POST" }
      );
      setSettingsMessage(result.message);
      await loadToolSettings(activeProject?.id);
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.toolSettingsFailed}${message}`);
    } finally {
      setTestingToolProvider(null);
    }
  }

  async function handleAddMemory() {
    const title = memoryDraft.title.trim();
    const content = memoryDraft.content.trim();
    if (!title || !content) {
      return;
    }

    try {
      await requestJson<UserMemory>("/api/backend/memories", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          memory_type: memoryDraft.memory_type,
          title,
          content,
          is_enabled: true,
        }),
      });
      setMemoryDraft({ memory_type: "fact", title: "", content: "" });
      await loadMemories();
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.memorySaveFailed}${message}`);
    }
  }

  async function handleSuggestMemories() {
    if (!selectedConversationId || isSuggestingMemory) {
      return;
    }

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 90_000);
    setIsSuggestingMemory(true);
    setMemorySuggestions([]);
    setSettingsMessage(null);
    setErrorMessage(null);
    try {
      const result = await requestJson<{ suggestions: MemorySuggestion[] }>(
        "/api/backend/memories/suggest",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
          },
          body: JSON.stringify({
            conversation_id: selectedConversationId,
            max_candidates: 5,
          }),
          signal: controller.signal,
        }
      );
      setMemorySuggestions(result.suggestions);
      if (result.suggestions.length === 0) {
        setSettingsMessage(text.noMemorySuggestions);
      }
    } catch (error) {
      const message =
        error instanceof DOMException && error.name === "AbortError"
          ? "请求超时，请稍后重试或切换更快的模型"
          : error instanceof Error
            ? error.message
            : text.unknownError;
      setErrorMessage(`${text.memorySuggestFailed}${message}`);
    } finally {
      window.clearTimeout(timeoutId);
      setIsSuggestingMemory(false);
    }
  }

  async function handleSaveMemorySuggestion(suggestion: MemorySuggestion, index: number) {
    try {
      await requestJson<UserMemory>("/api/backend/memories", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          memory_type: suggestion.memory_type,
          title: suggestion.title,
          content: suggestion.content,
          is_enabled: true,
          source_conversation_id: suggestion.source_conversation_id,
          source_message_ids: suggestion.source_message_ids,
          confidence: suggestion.confidence,
        }),
      });
      setMemorySuggestions((current) => current.filter((_, itemIndex) => itemIndex !== index));
      await loadMemories();
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.memorySaveFailed}${message}`);
    }
  }

  function handleIgnoreMemorySuggestion(index: number) {
    setMemorySuggestions((current) => current.filter((_, itemIndex) => itemIndex !== index));
  }

  async function handleToggleMemory(memory: UserMemory) {
    try {
      await requestJson<UserMemory>(`/api/backend/memories/${memory.id}`, {
        method: "PATCH",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          is_enabled: !memory.is_enabled,
        }),
      });
      await loadMemories();
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.memorySaveFailed}${message}`);
    }
  }

  async function handleDeleteMemory(memoryId: string) {
    try {
      await requestVoid(`/api/backend/memories/${memoryId}`, {
        method: "DELETE",
      });
      await loadMemories();
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.memoryDeleteFailed}${message}`);
    }
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
    return text.deletePromptTemplateTitle;
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
    return text.deletePromptTemplateDescription;
  }

  function renderConversationItem(conversation: Conversation) {
    const isActive = conversation.id === selectedConversationId;
    const isMenuOpen = openConversationMenuId === conversation.id;

    return (
      <div key={conversation.id} className="relative">
        <button
          type="button"
          onClick={() => void handleSelectConversation(conversation.id)}
          className={`conversation-card w-full rounded-2xl border px-4 py-3 pr-12 text-left transition ${
            isActive ? "is-active" : ""
          }`}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="line-clamp-2 text-sm font-medium">{conversation.title}</div>
            {conversation.is_pinned ? (
              <span className="conversation-pin shrink-0 rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-[0.18em]">
                PIN
              </span>
            ) : null}
          </div>
          <div className="conversation-meta mt-2 flex items-center justify-between text-[11px]">
            <span>{conversation.model_name}</span>
            <span>{formatTime(conversation.updated_at, uiLanguage)}</span>
          </div>
        </button>

        <div className="absolute right-2 top-2">
              <button
                type="button"
                onClick={() =>
              setOpenConversationMenuId((current) =>
                current === conversation.id ? null : conversation.id
              )
            }
            className="sidebar-icon-button inline-flex h-8 w-8 items-center justify-center rounded-full border backdrop-blur transition"
            aria-label={text.menuLabel}
            title={text.menuLabel}
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4 fill-none stroke-current stroke-2">
              <circle cx="12" cy="5" r="1.5" />
              <circle cx="12" cy="12" r="1.5" />
              <circle cx="12" cy="19" r="1.5" />
            </svg>
          </button>

          {isMenuOpen ? (
            <div className="sidebar-menu absolute right-0 z-20 mt-2 w-40 overflow-hidden rounded-2xl border py-1">
              <button
                type="button"
                onClick={() => {
                  setOpenConversationMenuId(null);
                  openRenameConversationDialog(conversation.id);
                }}
                className="sidebar-menu-item block w-full px-3 py-2 text-left text-sm transition"
              >
                {text.rename}
              </button>
              <button
                type="button"
                onClick={() => {
                  setOpenConversationMenuId(null);
                  void handleTogglePinned(conversation);
                }}
                className="sidebar-menu-item block w-full px-3 py-2 text-left text-sm transition"
              >
                {conversation.is_pinned ? text.unpin : text.pin}
              </button>
              <button
                type="button"
                onClick={() => {
                  setOpenConversationMenuId(null);
                  void handleToggleArchived(conversation);
                }}
                className="sidebar-menu-item block w-full px-3 py-2 text-left text-sm transition"
              >
                {conversation.is_archived ? text.unarchive : text.archive}
              </button>
              {projects.length > 0 ? (
                <button
                  type="button"
                  onClick={() => {
                    setOpenConversationMenuId(null);
                    openMoveConversationModal(conversation);
                  }}
                  className="sidebar-menu-item block w-full px-3 py-2 text-left text-sm transition"
                >
                  {text.moveToWorkspace}
                </button>
              ) : null}
              {conversation.project_id ? (
                <button
                  type="button"
                  onClick={() => {
                    setOpenConversationMenuId(null);
                    void handleMoveConversationToProject(conversation, null);
                  }}
                  className="sidebar-menu-item block w-full px-3 py-2 text-left text-sm transition"
                >
                  {text.removeFromWorkspace}
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => {
                  setOpenConversationMenuId(null);
                  void openShareModal(conversation);
                }}
                className="sidebar-menu-item block w-full px-3 py-2 text-left text-sm transition"
              >
                {text.shareConversation}
              </button>
              <button
                type="button"
                onClick={() => {
                  setOpenConversationMenuId(null);
                  setExportModalConversation(conversation);
                }}
                className="sidebar-menu-item block w-full px-3 py-2 text-left text-sm transition"
              >
                {text.exportOptions}
              </button>
              <button
                type="button"
                onClick={() => {
                  setOpenConversationMenuId(null);
                  openDeleteConversationDialog(conversation.id);
                }}
                className="sidebar-menu-item is-danger block w-full px-3 py-2 text-left text-sm transition"
              >
                {text.delete}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <main
      data-theme={resolvedTheme}
      className="app-shell h-screen overflow-hidden bg-[var(--app-bg)] px-2 py-2 text-[var(--ink-strong)] sm:px-3 lg:px-4"
    >
      <div className="app-frame mx-auto flex h-[calc(100vh-1rem)] flex-col gap-2.5 lg:flex-row">
        <aside className="app-sidebar flex w-full min-h-0 max-h-[42vh] flex-col overflow-hidden rounded-[22px] border p-3 lg:h-full lg:max-h-none lg:w-[276px] lg:shrink-0">
          <div className="mb-3 flex items-center justify-between">
            <div>
              <p className="text-xs uppercase tracking-[0.35em] text-white/55">
                {text.appTag}
              </p>
              <h1 className="mt-2 text-2xl font-semibold">{text.appTitle}</h1>
            </div>
            <button
              type="button"
              onClick={handleNewConversation}
              className="primary-action rounded-full border px-4 py-2 text-sm transition hover:brightness-105"
            >
              {text.newChat}
            </button>
          </div>

          <div className="sidebar-user-card rounded-2xl border p-3 text-sm">
            <p className="font-medium">{currentUser?.username ?? text.unnamedUser}</p>
            <p className="mt-1 break-all text-xs text-white/45">
              {currentUser?.email ?? "--"}
            </p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                onClick={() => {
                  window.location.href = "/settings";
                }}
                className="rounded-full border border-white/12 bg-white/8 px-3 py-1.5 text-xs transition hover:bg-white/14"
              >
                {text.settings}
              </button>
              <button
                type="button"
                onClick={() => setIsSettingsOpen(true)}
                className="rounded-full border border-white/12 bg-white/8 px-3 py-1.5 text-xs transition hover:bg-white/14"
              >
                {text.advancedSettings}
              </button>
              <button
                type="button"
                onClick={() => void handleLogout()}
                className="rounded-full border border-white/12 bg-white/8 px-3 py-1.5 text-xs transition hover:bg-white/14"
              >
                {text.logout}
              </button>
            </div>
          </div>

          <div className="sidebar-provider-card mt-3 rounded-2xl border p-3 text-sm">
            <p>{text.currentProvider}：{providerInfo?.provider ?? text.providerLoading}</p>
            <p className="mt-1 break-all text-xs text-white/45">
              {providerInfo?.base_url ?? text.providerBaseUrlLoading}
            </p>
          </div>

          <div ref={conversationMenuRef} className="mt-3 flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="mb-3 flex shrink-0 items-center justify-between">
              <p className="text-sm font-medium text-white/75">{text.historyChats}</p>
              <p className="text-xs text-white/45">
                {conversations.length}
                {text.historyCountSuffix ? ` ${text.historyCountSuffix}` : ""}
              </p>
            </div>

            <div className="sidebar-section-card mb-3 shrink-0 rounded-2xl border p-2">
              <div className="mb-2 px-1 text-xs uppercase tracking-[0.18em] text-white/45">
                {text.workspace}
              </div>
              <div className="flex items-center gap-1.5">
                <select
                  value={selectedProjectScope}
                  onChange={(event) => handleSelectProjectScope(event.target.value)}
                  className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/8 px-3 py-2 text-xs text-white outline-none"
                >
                  <option value="all">{text.allWorkspaces}</option>
                  <option value="unassigned">{text.unassignedWorkspace}</option>
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
                {activeProject ? (
                  <button
                    type="button"
                    onClick={() => void handleConfigureProject()}
                    className="shrink-0 rounded-xl border border-white/12 bg-white/8 px-2.5 py-2 text-[11px] text-white/70 transition hover:bg-white/14"
                  >
                    {text.workspaceManage}
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={openCreateProjectModal}
                  className="shrink-0 rounded-xl border border-white/12 bg-white/8 px-2.5 py-2 text-xs text-white/75 transition hover:bg-white/14"
                  aria-label={text.newWorkspace}
                >
                  +
                </button>
              </div>
            </div>

            <input
              value={conversationQuery}
              onChange={(event) => setConversationQuery(event.target.value)}
              placeholder={text.conversationSearchPlaceholder}
              className="mb-3 w-full shrink-0 rounded-2xl border border-white/10 bg-white/8 px-4 py-2.5 text-sm text-white placeholder:text-white/35 outline-none transition focus:border-[#f0c419]/45"
            />

            <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto overscroll-contain pr-1">
              {conversations.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-white/15 px-4 py-6 text-sm text-white/45">
                  {text.noConversations}
                </div>
              ) : null}

              {conversations.length > 0 && filteredConversations.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-white/15 px-4 py-6 text-sm text-white/45">
                  {text.noConversationMatches}
                </div>
              ) : null}

              {activeConversations.length > 0 ? (
                <>
                  <div className="px-1 pt-1 text-[11px] uppercase tracking-[0.2em] text-white/38">
                    {text.activeChats}
                  </div>
                  {activeConversations.map(renderConversationItem)}
                </>
              ) : null}

              {archivedConversations.length > 0 ? (
                <>
                  <button
                    type="button"
                    onClick={() => setShowArchived((current) => !current)}
                    className="mt-1 flex items-center justify-between rounded-2xl border border-white/10 bg-white/6 px-3 py-2 text-left text-xs uppercase tracking-[0.18em] text-white/55 transition hover:bg-white/10"
                  >
                    <span>{text.archivedChats}</span>
                    <span>{showArchived ? text.hideArchived : text.showArchived}</span>
                  </button>
                  {shouldShowArchivedSection
                    ? archivedConversations.map(renderConversationItem)
                    : null}
                </>
              ) : null}
            </div>
          </div>
        </aside>

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

          {workspaceModalMode ? (
            <div className="absolute inset-0 z-30 flex items-center justify-center rounded-[32px] bg-[var(--overlay-bg)] p-4 backdrop-blur-sm">
              <div className="flex max-h-[calc(100vh-4rem)] w-full max-w-2xl flex-col overflow-hidden rounded-[28px] border border-[var(--panel-border)] bg-[var(--modal-bg)] shadow-[var(--panel-shadow)]">
                <div className="flex shrink-0 items-center justify-between border-b border-[var(--hairline)] px-5 py-4">
                  <div>
                    <p className="text-xs uppercase tracking-[0.22em] text-[var(--ink-muted)]">
                      {text.workspace}
                    </p>
                    <h3 className="mt-1 text-2xl font-semibold text-[var(--ink-strong)]">
                      {workspaceModalMode === "move"
                        ? text.moveWorkspace
                        : workspaceModalMode === "edit"
                          ? text.workspaceSettings
                          : text.newWorkspace}
                    </h3>
                  </div>
                  <button
                    type="button"
                    onClick={closeWorkspaceModal}
                    className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)]"
                  >
                    {text.close}
                  </button>
                </div>

                <div className="min-h-0 overflow-y-auto px-5 py-5">
                  {workspaceModalMode === "move" ? (
                    <div className="space-y-4">
                      <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-4 py-3">
                        <p className="text-sm font-medium text-[var(--ink-strong)]">
                          {workspaceMoveConversation?.title ?? text.currentConversation}
                        </p>
                        <p className="mt-1 text-xs text-[var(--ink-muted)]">
                          {workspaceMoveConversation?.model_name ?? "--"}
                        </p>
                      </div>
                      <label className="block text-sm">
                        <span className="mb-2 block text-[var(--ink-soft)]">{text.workspaceTarget}</span>
                        <select
                          value={workspaceDraft.target_project_id}
                          onChange={(event) =>
                            setWorkspaceDraft((current) => ({
                              ...current,
                              target_project_id: event.target.value,
                            }))
                          }
                          className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                        >
                          {projects.map((project) => (
                            <option key={project.id} value={project.id}>
                              {project.name}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  ) : (
                    <div className="space-y-4">
                      <label className="block text-sm">
                        <span className="mb-2 block text-[var(--ink-soft)]">{text.workspaceName}</span>
                        <input
                          value={workspaceDraft.name}
                          onChange={(event) =>
                            setWorkspaceDraft((current) => ({
                              ...current,
                              name: event.target.value,
                            }))
                          }
                          className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                        />
                      </label>

                      <label className="block text-sm">
                        <span className="mb-2 block text-[var(--ink-soft)]">{text.workspaceDefaultModel}</span>
                        <select
                          value={workspaceDraft.default_model}
                          onChange={(event) =>
                            setWorkspaceDraft((current) => ({
                              ...current,
                              default_model: event.target.value,
                            }))
                          }
                          className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                        >
                          <option value="">{text.workspaceNoDefaultModel}</option>
                          {workspaceModelOptions.map((model) => (
                            <option key={model} value={model}>
                              {model}
                            </option>
                          ))}
                        </select>
                      </label>

                      <label className="block text-sm">
                        <span className="mb-2 block text-[var(--ink-soft)]">{text.workspaceSystemPrompt}</span>
                        <textarea
                          value={workspaceDraft.system_prompt}
                          onChange={(event) =>
                            setWorkspaceDraft((current) => ({
                              ...current,
                              system_prompt: event.target.value,
                            }))
                          }
                          className="min-h-[180px] w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                        />
                      </label>

                      {workspaceModalMode === "edit" && activeProject ? (
                        <div className="space-y-4 rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                          <div className="flex flex-wrap items-center justify-between gap-3">
                            <div>
                              <p className="text-sm font-semibold text-[var(--ink-strong)]">{text.workspaceStats}</p>
                              <p className="mt-1 text-xs text-[var(--ink-muted)]">{text.workspaceFiles}</p>
                            </div>
                            <label className="cursor-pointer rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]">
                              {isAddingProjectFile ? text.saving : text.workspaceAddFiles}
                              <input
                                type="file"
                                multiple
                                accept="image/*,.txt,.md,.markdown,.pdf,.docx"
                                className="hidden"
                                disabled={isAddingProjectFile}
                                onChange={(event) => {
                                  void handleAddProjectFiles(event.target.files);
                                  event.target.value = "";
                                }}
                              />
                            </label>
                          </div>
                          {projectStats ? (
                            <div className="grid gap-2 sm:grid-cols-5">
                              {[
                                [text.workspaceConversationCount, projectStats.conversation_count],
                                [text.workspaceMessageCount, projectStats.message_count],
                                [text.workspaceFileCount, projectStats.file_count],
                                [text.workspaceTemplateCount, projectStats.prompt_template_count],
                                [text.workspaceTotalFileSize, formatBytes(projectStats.total_file_size)],
                              ].map(([label, value]) => (
                                <div key={label} className="rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-3 py-2">
                                  <p className="text-[10px] text-[var(--ink-muted)]">{label}</p>
                                  <p className="mt-1 break-all text-sm font-semibold text-[var(--ink-strong)]">{value}</p>
                                </div>
                              ))}
                            </div>
                          ) : null}
                          <div className="space-y-2">
                            {projectFiles.length > 0 ? (
                              projectFiles.map((file) => (
                                <div
                                  key={file.id}
                                  className="flex items-center justify-between gap-3 rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-3 py-2"
                                >
                                  <div className="min-w-0">
                                    <p className="truncate text-sm font-medium text-[var(--ink-strong)]">{file.file_name}</p>
                                    <p className="mt-1 text-xs text-[var(--ink-muted)]">
                                      {file.kind} · {formatBytes(file.file_size ?? 0)}
                                    </p>
                                  </div>
                                  <button
                                    type="button"
                                    onClick={() => void handleDeleteProjectFile(file.id)}
                                    className="shrink-0 rounded-full border border-[rgba(174,65,45,0.22)] px-3 py-1 text-xs text-[#9f3a2b]"
                                  >
                                    {text.delete}
                                  </button>
                                </div>
                              ))
                            ) : (
                              <p className="rounded-2xl border border-dashed border-[var(--control-border)] px-3 py-3 text-xs text-[var(--ink-soft)]">
                                {text.workspaceFileEmpty}
                              </p>
                            )}
                          </div>
                        </div>
                      ) : null}
                    </div>
                  )}
                </div>

                <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-[var(--hairline)] px-5 py-4">
                  <div>
                    {workspaceModalMode === "edit" ? (
                      <button
                        type="button"
                        onClick={openDeleteProjectDialog}
                        className="rounded-full border border-[rgba(174,65,45,0.22)] px-4 py-2 text-sm text-[#9f3a2b]"
                      >
                        {text.deleteWorkspace}
                      </button>
                    ) : null}
                  </div>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={closeWorkspaceModal}
                      className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)]"
                    >
                      {text.cancel}
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        workspaceModalMode === "move"
                          ? void handleMoveConversationFromModal()
                          : void handleSaveProjectFromModal()
                      }
                      disabled={
                        workspaceModalMode === "move"
                          ? !workspaceDraft.target_project_id
                          : !workspaceDraft.name.trim()
                      }
                      className="primary-action rounded-full px-5 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-55"
                    >
                      {workspaceModalMode === "move" ? text.moveWorkspace : text.saveWorkspace}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {shareModalConversation ? (
            <div className="absolute inset-0 z-40 flex items-center justify-center rounded-[32px] bg-[var(--overlay-bg)] p-4 backdrop-blur-sm">
              <div className="w-full max-w-2xl overflow-hidden rounded-[28px] border border-[var(--panel-border)] bg-[var(--modal-bg)] shadow-[var(--panel-shadow)]">
                <div className="flex items-start justify-between gap-4 border-b border-[var(--hairline)] px-5 py-4">
                  <div>
                    <p className="text-xs uppercase tracking-[0.22em] text-[var(--ink-muted)]">
                      {text.shareConversation}
                    </p>
                    <h3 className="mt-1 text-2xl font-semibold text-[var(--ink-strong)]">{text.shareTitle}</h3>
                    <p className="mt-2 text-sm leading-6 text-[var(--ink-soft)]">{text.shareDescription}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShareModalConversation(null)}
                    className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)]"
                  >
                    {text.close}
                  </button>
                </div>
                <div className="space-y-4 px-5 py-5">
                  <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-4 py-3">
                    <p className="text-sm font-medium text-[var(--ink-strong)]">{shareModalConversation.title}</p>
                    <p className="mt-1 text-xs text-[var(--ink-muted)]">{shareModalConversation.model_name}</p>
                  </div>
                  <label className="block text-sm">
                    <span className="mb-2 block text-[var(--ink-soft)]">{text.shareExpiresDays}</span>
                    <input
                      type="number"
                      min="1"
                      max="365"
                      value={shareExpiresDays}
                      onChange={(event) => setShareExpiresDays(event.target.value)}
                      className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                    />
                  </label>
                  {conversationShare ? (
                    <div className="space-y-3 rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-4 py-3">
                      <p className="break-all text-sm text-[var(--ink-soft)]">{shareUrl}</p>
                      <p className="text-xs text-[var(--ink-muted)]">
                        {conversationShare.is_enabled ? text.shareEnable : text.shareDisable}
                        {conversationShare.expires_at ? ` · ${conversationShare.expires_at}` : ""}
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => void handleCopyShareUrl()}
                          className="rounded-full border border-[var(--control-border)] px-3 py-1.5 text-xs text-[var(--ink-soft)]"
                        >
                          {shareCopied ? text.shareCopied : text.shareCopy}
                        </button>
                        <a
                          href={shareUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="rounded-full border border-[var(--control-border)] px-3 py-1.5 text-xs text-[var(--ink-soft)]"
                        >
                          {text.openShare}
                        </a>
                        <button
                          type="button"
                          onClick={() => void handleToggleShare(!conversationShare.is_enabled)}
                          disabled={isShareBusy}
                          className="rounded-full border border-[var(--control-border)] px-3 py-1.5 text-xs text-[var(--ink-soft)] disabled:opacity-55"
                        >
                          {conversationShare.is_enabled ? text.shareDisable : text.shareEnable}
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleRevokeShare()}
                          disabled={isShareBusy}
                          className="rounded-full border border-[rgba(174,65,45,0.22)] px-3 py-1.5 text-xs text-[#9f3a2b] disabled:opacity-55"
                        >
                          {text.shareRevoke}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <p className="rounded-2xl border border-dashed border-[var(--control-border)] bg-[var(--soft-bg)] px-4 py-3 text-sm text-[var(--ink-soft)]">
                      {text.shareNoLink}
                    </p>
                  )}
                </div>
                <div className="flex justify-end gap-2 border-t border-[var(--hairline)] px-5 py-4">
                  <button
                    type="button"
                    onClick={() => void handleCreateOrEnableShare()}
                    disabled={isShareBusy}
                    className="primary-action rounded-full px-5 py-2 text-sm font-medium disabled:opacity-55"
                  >
                    {text.shareCreate}
                  </button>
                </div>
              </div>
            </div>
          ) : null}

          {exportModalConversation ? (
            <div className="absolute inset-0 z-40 flex items-center justify-center rounded-[32px] bg-[var(--overlay-bg)] p-4 backdrop-blur-sm">
              <div className="w-full max-w-xl overflow-hidden rounded-[28px] border border-[var(--panel-border)] bg-[var(--modal-bg)] shadow-[var(--panel-shadow)]">
                <div className="flex items-center justify-between border-b border-[var(--hairline)] px-5 py-4">
                  <div>
                    <p className="text-xs uppercase tracking-[0.22em] text-[var(--ink-muted)]">{text.exportOptions}</p>
                    <h3 className="mt-1 text-2xl font-semibold text-[var(--ink-strong)]">
                      {exportModalConversation.title}
                    </h3>
                  </div>
                  <button
                    type="button"
                    onClick={() => setExportModalConversation(null)}
                    className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)]"
                  >
                    {text.close}
                  </button>
                </div>
                <div className="space-y-4 px-5 py-5">
                  <label className="block text-sm">
                    <span className="mb-2 block text-[var(--ink-soft)]">{text.exportRange}</span>
                    <select
                      value={exportOptions.range}
                      onChange={(event) =>
                        setExportOptions((current) => ({
                          ...current,
                          range: event.target.value as "all" | "loaded",
                        }))
                      }
                      className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                    >
                      <option value="all">{text.exportRangeAll}</option>
                      <option value="loaded">{text.exportRangeSelected}</option>
                    </select>
                  </label>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <button
                      type="button"
                      onClick={() => setExportOptions((current) => ({ ...current, format: "markdown" }))}
                      className={`rounded-2xl border px-4 py-3 text-sm ${
                        exportOptions.format === "markdown"
                          ? "border-[var(--accent-strong)] bg-[var(--soft-bg)]"
                          : "border-[var(--control-border)] bg-[var(--control-bg)]"
                      }`}
                    >
                      Markdown
                    </button>
                    <button
                      type="button"
                      onClick={() => setExportOptions((current) => ({ ...current, format: "json" }))}
                      className={`rounded-2xl border px-4 py-3 text-sm ${
                        exportOptions.format === "json"
                          ? "border-[var(--accent-strong)] bg-[var(--soft-bg)]"
                          : "border-[var(--control-border)] bg-[var(--control-bg)]"
                      }`}
                    >
                      JSON
                    </button>
                  </div>
                  {[
                    ["include_attachments", text.exportIncludeAttachmentMetadata],
                    ["include_context", text.exportIncludeContext],
                    ["as_zip", text.exportAsZip],
                  ].map(([key, label]) => (
                    <label key={key} className="flex items-center gap-2 text-sm text-[var(--ink-soft)]">
                      <input
                        type="checkbox"
                        checked={Boolean(exportOptions[key as keyof typeof exportOptions])}
                        onChange={(event) =>
                          setExportOptions((current) => ({
                            ...current,
                            [key]: event.target.checked,
                          }))
                        }
                      />
                      {label}
                    </label>
                  ))}
                  <label
                    className={`flex items-center gap-2 text-sm ${
                      exportOptions.as_zip ? "text-[var(--ink-soft)]" : "text-[var(--ink-muted)] opacity-60"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={exportOptions.include_attachment_files}
                      disabled={!exportOptions.as_zip}
                      onChange={(event) =>
                        setExportOptions((current) => ({
                          ...current,
                          include_attachment_files: event.target.checked,
                        }))
                      }
                    />
                    {text.exportIncludeAttachmentFiles}
                  </label>
                </div>
                <div className="flex justify-end gap-2 border-t border-[var(--hairline)] px-5 py-4">
                  <button
                    type="button"
                    onClick={() => void handleExportConversationWithOptions()}
                    className="primary-action rounded-full px-5 py-2 text-sm font-medium"
                  >
                    {text.exportRun}
                  </button>
                </div>
              </div>
            </div>
          ) : null}

          {appDialog ? (
            <div className="absolute inset-0 z-40 flex items-center justify-center rounded-[32px] bg-[var(--overlay-bg)] p-4 backdrop-blur-sm">
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void handleConfirmAppDialog();
                }}
                className="w-full max-w-lg overflow-hidden rounded-[28px] border border-[var(--panel-border)] bg-[var(--modal-bg)] shadow-[var(--panel-shadow)]"
              >
                <div className="border-b border-[var(--hairline)] px-5 py-4">
                  <p className="text-xs uppercase tracking-[0.22em] text-[var(--ink-muted)]">
                    {appDialog.type === "rename-conversation" ? text.rename : text.delete}
                  </p>
                  <h3 className="mt-1 text-2xl font-semibold text-[var(--ink-strong)]">
                    {appDialogTitle()}
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-[var(--ink-soft)]">
                    {appDialogDescription()}
                  </p>
                </div>

                <div className="px-5 py-5">
                  {appDialog.type === "rename-conversation" ? (
                    <label className="block text-sm">
                      <span className="mb-2 block text-[var(--ink-soft)]">{text.currentConversation}</span>
                      <input
                        autoFocus
                        value={renameConversationDraft}
                        onChange={(event) => setRenameConversationDraft(event.target.value)}
                        className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 text-[var(--ink-strong)] outline-none focus:border-[var(--accent-strong)]"
                      />
                    </label>
                  ) : (
                    <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-4 py-3">
                      <p className="break-words text-sm font-medium text-[var(--ink-strong)]">
                        {appDialog.title}
                      </p>
                    </div>
                  )}
                </div>

                <div className="flex items-center justify-end gap-2 border-t border-[var(--hairline)] px-5 py-4">
                  <button
                    type="button"
                    onClick={closeAppDialog}
                    disabled={isDialogSubmitting}
                    className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] disabled:cursor-not-allowed disabled:opacity-55"
                  >
                    {text.dialogCancel}
                  </button>
                  <button
                    type="submit"
                    disabled={
                      isDialogSubmitting ||
                      (appDialog.type === "rename-conversation" && !renameConversationDraft.trim())
                    }
                    className={`rounded-full px-5 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-55 ${
                      appDialog.type === "rename-conversation"
                        ? "primary-action hover:brightness-105"
                        : "border border-[rgba(174,65,45,0.22)] bg-[var(--danger-bg)] text-[var(--danger-text)] hover:brightness-95"
                    }`}
                  >
                    {appDialog.type === "rename-conversation" ? text.confirm : text.delete}
                  </button>
                </div>
              </form>
            </div>
          ) : null}

          {isSettingsOpen && userSettings ? (
            <div className="absolute inset-0 z-20 flex items-start justify-end rounded-[32px] bg-[var(--overlay-bg)] p-4 backdrop-blur-sm">
              <form
                onSubmit={handleSaveSettings}
                className="flex max-h-[calc(100vh-4rem)] w-full max-w-4xl flex-col overflow-hidden rounded-[28px] border border-[var(--panel-border)] bg-[var(--modal-bg)] shadow-[var(--panel-shadow)]"
              >
                <div className="flex shrink-0 items-center justify-between border-b border-[var(--hairline)] px-5 py-4">
                  <div className="pr-4">
                    <p className="text-xs uppercase tracking-[0.28em] text-[var(--ink-muted)]">
                      {text.settingsTag}
                    </p>
                    <h3 className="mt-2 text-2xl font-semibold">{text.settingsTitle}</h3>
                  </div>
                  <button
                    type="button"
                    onClick={() => setIsSettingsOpen(false)}
                    className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)]"
                  >
                    {text.close}
                  </button>
                </div>

                <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden sm:grid-cols-[12rem_1fr]">
                  <nav className="settings-nav flex gap-2 overflow-x-auto border-b border-[var(--hairline)] bg-[var(--soft-bg)] p-3 sm:flex-col sm:overflow-visible sm:border-b-0 sm:border-r">
                    {settingsTabs.map((tab) => (
                      <button
                        key={tab.id}
                        type="button"
                        onClick={() => setActiveSettingsTab(tab.id)}
                        className={`settings-tab whitespace-nowrap rounded-2xl border px-4 py-2 text-left text-sm transition ${
                          activeSettingsTab === tab.id ? "is-active" : ""
                        }`}
                      >
                        {tab.label}
                      </button>
                    ))}
                  </nav>

                  <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
                    <div className="space-y-4">
                      {activeSettingsTab === "appearance" ? (
                        <>
                          <label className="block text-sm">
                            <span className="mb-2 block text-[var(--ink-soft)]">{text.themeMode}</span>
                            <select
                              value={selectedThemeMode}
                              onChange={(event) =>
                                setUserSettings((current) =>
                                  current
                                    ? {
                                        ...current,
                                        theme_mode: event.target.value,
                                      }
                                    : current
                                )
                              }
                              className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                            >
                              <option value="system">{text.themeSystem}</option>
                              <option value="light">{text.themeLight}</option>
                              <option value="dark">{text.themeDark}</option>
                            </select>
                          </label>

                          <label className="block text-sm">
                            <span className="mb-2 block text-[var(--ink-soft)]">{text.uiLanguage}</span>
                            <select
                              value={userSettings.ui_language}
                              onChange={(event) =>
                                setUserSettings((current) =>
                                  current
                                    ? {
                                        ...current,
                                        ui_language: event.target.value,
                                      }
                                    : current
                                )
                              }
                              className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                            >
                              <option value="zh-CN">{text.chinese}</option>
                              <option value="en-US">{text.english}</option>
                            </select>
                          </label>
                        </>
                      ) : null}

                      {activeSettingsTab === "provider" ? (
                        <>
                          <label className="block text-sm">
                            <span className="mb-2 block text-[var(--ink-soft)]">{text.provider}</span>
                            <select
                              value={userSettings.provider_type}
                              onChange={(event) => applyProviderPreset(event.target.value)}
                              className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                            >
                              <option value="ollama">ollama</option>
                              <option value="openai-compatible">openai-compatible</option>
                            </select>
                          </label>

                          <label className="block text-sm">
                            <span className="mb-2 block text-[var(--ink-soft)]">{text.defaultModel}</span>
                            {settingsModelOptions.length > 0 ? (
                              <select
                                value={userSettings.default_model}
                                onChange={(event) =>
                                  setUserSettings((current) =>
                                    current
                                      ? {
                                          ...current,
                                          default_model: event.target.value,
                                        }
                                      : current
                                  )
                                }
                                className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                              >
                                {Array.from(new Set(settingsModelOptions)).map((model) => (
                                  <option key={model} value={model}>
                                    {model}
                                  </option>
                                ))}
                              </select>
                            ) : (
                              <input
                                value={userSettings.default_model}
                                onChange={(event) =>
                                  setUserSettings((current) =>
                                    current
                                      ? {
                                          ...current,
                                          default_model: event.target.value,
                                        }
                                      : current
                                  )
                                }
                                className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                              />
                            )}
                          </label>

                          <label className="block text-sm">
                            <span className="mb-2 block text-[var(--ink-soft)]">{text.baseUrl}</span>
                            <input
                              value={userSettings.ollama_base_url}
                              onChange={(event) =>
                                setUserSettings((current) =>
                                  current
                                    ? {
                                        ...current,
                                        ollama_base_url: event.target.value,
                                      }
                                    : current
                                )
                              }
                              className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                            />
                          </label>

                          {userSettings.provider_type === "openai-compatible" ? (
                            <label className="block text-sm">
                              <span className="mb-2 block text-[var(--ink-soft)]">{text.apiKey}</span>
                              <input
                                type="password"
                                value={userSettings.api_key ?? ""}
                                onChange={(event) =>
                                  setUserSettings((current) =>
                                    current
                                      ? {
                                          ...current,
                                          api_key: event.target.value,
                                        }
                                      : current
                                  )
                                }
                                className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                              />
                            </label>
                          ) : null}

                          <div className="rounded-2xl border border-[rgba(22,34,27,0.08)] bg-[rgba(248,244,234,0.72)] px-4 py-3">
                            <div className="flex items-center justify-between gap-3">
                              <p className="text-sm text-[var(--ink-soft)]">{text.providerHint}</p>
                              <button
                                type="button"
                                onClick={() => void handleTestProviderConnection()}
                                disabled={isTestingProvider}
                                className="shrink-0 rounded-full border border-[rgba(22,34,27,0.12)] bg-white px-4 py-2 text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] disabled:cursor-not-allowed disabled:opacity-55"
                              >
                                {isTestingProvider ? text.testing : text.testConnection}
                              </button>
                            </div>
                            {settingsMessage ? (
                              <p className="mt-3 text-sm text-[var(--ink-soft)]">{settingsMessage}</p>
                            ) : null}
                          </div>
                        </>
                      ) : null}

                      {activeSettingsTab === "generation" ? (
                        <>
                          <label className="block text-sm">
                            <span className="mb-2 block text-[var(--ink-soft)]">{text.temperature}</span>
                            <input
                              type="number"
                              min="0"
                              max="2"
                              step="0.1"
                              value={userSettings.temperature}
                              onChange={(event) =>
                                setUserSettings((current) =>
                                  current
                                    ? {
                                        ...current,
                                        temperature: Number(event.target.value),
                                      }
                                    : current
                                )
                              }
                              className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                            />
                          </label>

                          <label className="block text-sm">
                            <span className="mb-2 block text-[var(--ink-soft)]">{text.topP}</span>
                            <input
                              type="number"
                              min="0"
                              max="1"
                              step="0.05"
                              value={userSettings.top_p}
                              onChange={(event) =>
                                setUserSettings((current) =>
                                  current
                                    ? {
                                        ...current,
                                        top_p: Number(event.target.value),
                                      }
                                    : current
                                )
                              }
                              className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                            />
                          </label>

                          <label className="block text-sm">
                            <span className="mb-2 block text-[var(--ink-soft)]">{text.maxTokens}</span>
                            <input
                              type="number"
                              min="0"
                              step="1"
                              value={userSettings.max_tokens ?? ""}
                              onChange={(event) =>
                                setUserSettings((current) =>
                                  current
                                    ? {
                                        ...current,
                                        max_tokens: event.target.value
                                          ? Number(event.target.value)
                                          : null,
                                      }
                                    : current
                                )
                              }
                              className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                            />
                          </label>
                        </>
                      ) : null}

                      {activeSettingsTab === "context" ? (
                        <>
                          <label className="block text-sm">
                            <span className="mb-2 block text-[var(--ink-soft)]">{text.modelContextWindow}</span>
                            <input
                              type="number"
                              min="8192"
                              step="1024"
                              value={userSettings.model_context_window}
                              onChange={(event) =>
                                setUserSettings((current) =>
                                  current
                                    ? {
                                        ...current,
                                        model_context_window: Number(event.target.value),
                                      }
                                    : current
                                )
                              }
                              className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                            />
                          </label>

                          <label className="block text-sm">
                            <span className="mb-2 block text-[var(--ink-soft)]">{text.contextMode}</span>
                            <select
                              value={userSettings.context_mode}
                              onChange={(event) =>
                                setUserSettings((current) =>
                                  current
                                    ? {
                                        ...current,
                                        context_mode: event.target.value,
                                      }
                                    : current
                                )
                              }
                              className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                            >
                              <option value="conservative">{text.contextModeConservative}</option>
                              <option value="balanced">{text.contextModeBalanced}</option>
                              <option value="long-context">{text.contextModeLong}</option>
                            </select>
                          </label>
                        </>
                      ) : null}

                      {activeSettingsTab === "memory" ? (
                        <div className="rounded-[24px] border border-[rgba(22,34,27,0.08)] bg-[rgba(248,244,234,0.72)] p-4">
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <p className="text-sm font-semibold text-[var(--ink-strong)]">{text.longTermMemory}</p>
                              <p className="mt-1 text-xs leading-5 text-[var(--ink-soft)]">{text.memoryHint}</p>
                            </div>
                            <label className="flex shrink-0 items-center gap-2 text-xs text-[var(--ink-soft)]">
                              <input
                                type="checkbox"
                                checked={Boolean(userSettings.memory_enabled)}
                                onChange={(event) =>
                                  setUserSettings((current) =>
                                    current
                                      ? {
                                          ...current,
                                          memory_enabled: event.target.checked,
                                        }
                                      : current
                                  )
                                }
                              />
                              {text.memoryEnabled}
                            </label>
                          </div>

                          <label className="mt-4 block text-sm">
                            <span className="mb-2 block text-[var(--ink-soft)]">{text.memoryMaxChars}</span>
                            <input
                              type="number"
                              min="500"
                              max="20000"
                              step="500"
                              value={userSettings.memory_max_chars ?? 4000}
                              onChange={(event) =>
                                setUserSettings((current) =>
                                  current
                                    ? {
                                        ...current,
                                        memory_max_chars: Number(event.target.value),
                                      }
                                    : current
                                )
                              }
                              className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                            />
                          </label>

                          <div className="mt-4 grid gap-3 sm:grid-cols-[0.9fr_1.1fr]">
                            <label className="block text-sm">
                              <span className="mb-2 block text-[var(--ink-soft)]">{text.memoryType}</span>
                              <select
                                value={memoryDraft.memory_type}
                                onChange={(event) =>
                                  setMemoryDraft((current) => ({
                                    ...current,
                                    memory_type: event.target.value,
                                  }))
                                }
                                className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                              >
                                <option value="profile">{text.memoryTypeProfile}</option>
                                <option value="project">{text.memoryTypeProject}</option>
                                <option value="fact">{text.memoryTypeFact}</option>
                                <option value="instruction">{text.memoryTypeInstruction}</option>
                              </select>
                            </label>
                            <label className="block text-sm">
                              <span className="mb-2 block text-[var(--ink-soft)]">{text.memoryTitle}</span>
                              <input
                                value={memoryDraft.title}
                                onChange={(event) =>
                                  setMemoryDraft((current) => ({
                                    ...current,
                                    title: event.target.value,
                                  }))
                                }
                                className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                              />
                            </label>
                          </div>

                          <label className="mt-3 block text-sm">
                            <span className="mb-2 block text-[var(--ink-soft)]">{text.memoryContent}</span>
                            <textarea
                              value={memoryDraft.content}
                              onChange={(event) =>
                                setMemoryDraft((current) => ({
                                  ...current,
                                  content: event.target.value,
                                }))
                              }
                              className="min-h-[92px] w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                            />
                          </label>

                          <div className="mt-3 flex flex-wrap justify-end gap-2">
                            <button
                              type="button"
                              onClick={() => void handleSuggestMemories()}
                              disabled={!selectedConversationId || isSuggestingMemory}
                              className="rounded-full border border-[rgba(22,34,27,0.12)] bg-white px-4 py-2 text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] disabled:cursor-not-allowed disabled:opacity-55"
                            >
                              {isSuggestingMemory ? text.suggestingMemory : text.suggestMemory}
                            </button>
                            <button
                              type="button"
                              onClick={() => void handleAddMemory()}
                              className="primary-action rounded-full px-4 py-2 text-sm transition hover:brightness-105"
                            >
                              {text.addMemory}
                            </button>
                          </div>

                          {memorySuggestions.length > 0 ? (
                            <div className="mt-4 rounded-2xl border border-[rgba(22,34,27,0.08)] bg-white/70 p-3">
                              <p className="text-sm font-semibold text-[var(--ink-strong)]">
                                {text.suggestedMemories}
                              </p>
                              <div className="mt-3 space-y-2">
                                {memorySuggestions.map((suggestion, index) => (
                                  <div
                                    key={`${suggestion.memory_type}-${suggestion.title}-${index}`}
                                    className="rounded-2xl border border-[rgba(22,34,27,0.08)] bg-[rgba(248,244,234,0.72)] px-3 py-3"
                                  >
                                    <div className="flex items-start justify-between gap-3">
                                      <div>
                                        <div className="mb-2 flex flex-wrap gap-2">
                                          <span
                                            className={`rounded-full px-2.5 py-1 text-[11px] ${
                                              suggestion.risk_level === "duplicate"
                                                ? "bg-[#fff0cc] text-[#8a5a00]"
                                                : suggestion.risk_level === "conflict"
                                                  ? "bg-[#ffe3dd] text-[#9f3a2b]"
                                                  : "bg-[#e7f3e7] text-[#2f6b3e]"
                                            }`}
                                          >
                                            {suggestion.risk_level === "duplicate"
                                              ? text.riskDuplicate
                                              : suggestion.risk_level === "conflict"
                                                ? text.riskConflict
                                                : text.riskSafe}
                                          </span>
                                          {suggestion.confidence ? (
                                            <span className="rounded-full bg-white/80 px-2.5 py-1 text-[11px] text-[var(--ink-muted)]">
                                              {text.memoryConfidence}: {suggestion.confidence}
                                            </span>
                                          ) : null}
                                        </div>
                                        <p className="text-sm font-medium text-[var(--ink-strong)]">
                                          {suggestion.title}
                                        </p>
                                        <p className="mt-1 text-xs text-[var(--ink-muted)]">
                                          {suggestion.memory_type}
                                        </p>
                                      </div>
                                      <div className="flex shrink-0 gap-2">
                                        <button
                                          type="button"
                                          onClick={() => void handleSaveMemorySuggestion(suggestion, index)}
                                          className="primary-action rounded-full px-3 py-1 text-xs"
                                        >
                                          {text.saveSuggestion}
                                        </button>
                                        <button
                                          type="button"
                                          onClick={() => handleIgnoreMemorySuggestion(index)}
                                          className="rounded-full border border-[rgba(22,34,27,0.12)] px-3 py-1 text-xs text-[var(--ink-soft)]"
                                        >
                                          {text.ignoreSuggestion}
                                        </button>
                                      </div>
                                    </div>
                                    <p className="mt-2 text-xs leading-5 text-[var(--ink-soft)]">
                                      {suggestion.content}
                                    </p>
                                    {suggestion.reason ? (
                                      <p className="mt-2 text-[11px] leading-5 text-[var(--ink-muted)]">
                                        {suggestion.reason}
                                      </p>
                                    ) : null}
                                    {suggestion.risk_reason ? (
                                      <p className="mt-2 rounded-xl border border-[rgba(22,34,27,0.08)] bg-white/70 px-3 py-2 text-[11px] leading-5 text-[var(--ink-soft)]">
                                        {suggestion.risk_reason}
                                      </p>
                                    ) : null}
                                  </div>
                                ))}
                              </div>
                            </div>
                          ) : null}

                          <div className="mt-4 space-y-2">
                            {memories.length > 0 ? (
                              memories.map((memory) => (
                                <div
                                  key={memory.id}
                                  className="rounded-2xl border border-[rgba(22,34,27,0.08)] bg-white/76 px-3 py-3"
                                >
                                  <div className="flex items-start justify-between gap-3">
                                    <div>
                                      <p className="text-sm font-medium text-[var(--ink-strong)]">{memory.title}</p>
                                      <p className="mt-1 text-xs text-[var(--ink-muted)]">
                                        {memory.memory_type} · {memory.is_enabled ? text.enableMemory : text.disableMemory}
                                      </p>
                                    </div>
                                    <div className="flex shrink-0 gap-2">
                                      <button
                                        type="button"
                                        onClick={() => void handleToggleMemory(memory)}
                                        className="rounded-full border border-[rgba(22,34,27,0.12)] px-3 py-1 text-xs text-[var(--ink-soft)]"
                                      >
                                        {memory.is_enabled ? text.disableMemory : text.enableMemory}
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => void handleDeleteMemory(memory.id)}
                                        className="rounded-full border border-[rgba(174,65,45,0.22)] px-3 py-1 text-xs text-[#9f3a2b]"
                                      >
                                        {text.deleteMemory}
                                      </button>
                                    </div>
                                  </div>
                                  <p className="mt-2 text-xs leading-5 text-[var(--ink-soft)]">{memory.content}</p>
                                  {memory.source_conversation_id ? (
                                    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[rgba(22,34,27,0.08)] bg-white/72 px-3 py-2">
                                      <p className="min-w-0 truncate text-[11px] text-[var(--ink-muted)]">
                                        {text.memorySource}:{" "}
                                        <span className="text-[var(--ink-soft)]">
                                          {memory.source_conversation_title || memory.source_conversation_id}
                                        </span>
                                      </p>
                                      <button
                                        type="button"
                                        onClick={() =>
                                          void handleOpenMemorySource(
                                            memory.source_conversation_id!,
                                            memory.source_message_ids
                                          )
                                        }
                                        className="shrink-0 rounded-full border border-[rgba(22,34,27,0.12)] px-3 py-1 text-[11px] text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] hover:text-[var(--ink-strong)]"
                                      >
                                        {text.openMemorySource}
                                      </button>
                                    </div>
                                  ) : null}
                                </div>
                              ))
                            ) : (
                              <p className="rounded-2xl border border-dashed border-[rgba(22,34,27,0.12)] bg-white/60 px-3 py-3 text-xs text-[var(--ink-soft)]">
                                {text.memoryEmpty}
                              </p>
                            )}
                          </div>
                        </div>
                      ) : null}

                      {activeSettingsTab === "system" ? (
                        <label className="block text-sm">
                          <span className="mb-2 block text-[var(--ink-soft)]">{text.systemPrompt}</span>
                          <textarea
                            value={userSettings.system_prompt ?? ""}
                            onChange={(event) =>
                              setUserSettings((current) =>
                                current
                                  ? {
                                      ...current,
                                      system_prompt: event.target.value,
                                    }
                                  : current
                              )
                            }
                            className="min-h-[260px] w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                          />
                        </label>
                      ) : null}

                      {activeSettingsTab === "tools" ? (
                        <div className="space-y-4">
                          <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                            <p className="text-sm font-semibold text-[var(--ink-strong)]">
                              {text.settingsTabTools}
                            </p>
                            <p className="mt-2 text-sm leading-6 text-[var(--ink-soft)]">{text.toolsHint}</p>
                          </div>

                          {toolProviders.map((providerKey) => {
                            const credential = credentialByProvider[providerKey];
                            return (
                              <div
                                key={providerKey}
                                className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4"
                              >
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                  <div>
                                    <p className="text-sm font-semibold text-[var(--ink-strong)]">
                                      {text.toolCredential} · {providerKey}
                                    </p>
                                    <p className="mt-1 text-xs text-[var(--ink-muted)]">
                                      {text.credentialSource}: {credential?.source ?? "missing"}
                                      {credential?.api_key_masked ? ` · ${text.apiKeyMasked}: ${credential.api_key_masked}` : ""}
                                    </p>
                                  </div>
                                  <label className="flex items-center gap-2 text-xs text-[var(--ink-soft)]">
                                    <input
                                      type="checkbox"
                                      checked={toolEnabledDrafts[providerKey] ?? credential?.is_enabled ?? true}
                                      onChange={(event) =>
                                        setToolEnabledDrafts((current) => ({
                                          ...current,
                                          [providerKey]: event.target.checked,
                                        }))
                                      }
                                    />
                                    {text.toolEnabled}
                                  </label>
                                </div>

                                <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto]">
                                  <input
                                    type="password"
                                    value={toolCredentialDrafts[providerKey] ?? ""}
                                    placeholder={credential?.has_api_key ? credential.api_key_masked ?? "******" : "API Key"}
                                    onChange={(event) =>
                                      setToolCredentialDrafts((current) => ({
                                        ...current,
                                        [providerKey]: event.target.value,
                                      }))
                                    }
                                    className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 text-sm outline-none focus:border-[var(--accent-strong)]"
                                  />
                                  <button
                                    type="button"
                                    onClick={() => void handleTestToolProvider(providerKey)}
                                    disabled={testingToolProvider === providerKey}
                                    className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] disabled:cursor-not-allowed disabled:opacity-55"
                                  >
                                    {testingToolProvider === providerKey ? text.testing : text.testTool}
                                  </button>
                                </div>
                              </div>
                            );
                          })}

                          <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                            <p className="text-sm font-semibold text-[var(--ink-strong)]">
                              {text.workspaceToolOverrides}
                            </p>
                            <p className="mt-1 text-xs leading-5 text-[var(--ink-muted)]">
                              {activeProject ? activeProject.name : text.noActiveWorkspaceForTools}
                            </p>
                            {activeProject ? (
                              <div className="mt-3 grid gap-2">
                                {(toolSettings?.tools ?? []).map((tool) => (
                                  <label
                                    key={tool.tool_key}
                                    className="flex items-start justify-between gap-3 rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-3 py-3 text-sm"
                                  >
                                    <span>
                                      <span className="block font-medium text-[var(--ink-strong)]">
                                        {tool.display_name}
                                      </span>
                                      <span className="mt-1 block text-xs leading-5 text-[var(--ink-soft)]">
                                        {tool.description}
                                      </span>
                                    </span>
                                    <input
                                      type="checkbox"
                                      checked={workspaceToolEnabledDrafts[tool.tool_key] ?? true}
                                      onChange={(event) =>
                                        setWorkspaceToolEnabledDrafts((current) => ({
                                          ...current,
                                          [tool.tool_key]: event.target.checked,
                                        }))
                                      }
                                    />
                                  </label>
                                ))}
                              </div>
                            ) : null}
                          </div>

                          <div className="flex justify-end">
                            <button
                              type="button"
                              onClick={() => void handleSaveToolSettings()}
                              disabled={isSavingToolSettings}
                              className="primary-action rounded-full px-5 py-2 text-sm transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-55"
                            >
                              {isSavingToolSettings ? text.saving : text.saveToolSettings}
                            </button>
                          </div>
                        </div>
                      ) : null}

                      {activeSettingsTab === "privacy" ? (
                        <div className="space-y-4">
                          <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                            <p className="text-sm font-semibold text-[var(--ink-strong)]">
                              {text.settingsSectionPrivacy}
                            </p>
                            <p className="mt-2 text-sm leading-6 text-[var(--ink-soft)]">
                              {text.shareDescription}
                            </p>
                            {selectedConversationId ? (
                              <button
                                type="button"
                                onClick={() => {
                                  const conversation = conversations.find((item) => item.id === selectedConversationId);
                                  if (conversation) {
                                    void openShareModal(conversation);
                                  }
                                }}
                                className="mt-3 rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
                              >
                                {text.shareConversation}
                              </button>
                            ) : null}
                          </div>
                          <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                            <p className="text-sm font-semibold text-[var(--ink-strong)]">
                              {text.exportOptions}
                            </p>
                            <p className="mt-2 text-sm leading-6 text-[var(--ink-soft)]">
                              {text.exportIncludeAttachmentMetadata}；{text.exportIncludeAttachmentFiles}；
                              {text.exportIncludeContext}；{text.exportAsZip}
                            </p>
                          </div>
                        </div>
                      ) : null}

                      {activeSettingsTab === "templates" ? (
                        <div className="space-y-4">
                          <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                            <div className="mb-3 flex items-center justify-between gap-3">
                              <div>
                                <p className="text-sm font-semibold text-[var(--ink-strong)]">
                                  {editingPromptTemplateId ? text.promptTemplateEdit : text.promptTemplateNew}
                                </p>
                                <p className="mt-1 text-xs text-[var(--ink-muted)]">
                                  {text.promptTemplates}
                                </p>
                              </div>
                              {editingPromptTemplateId ? (
                                <button
                                  type="button"
                                  onClick={resetPromptTemplateDraft}
                                  className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)]"
                                >
                                  {text.promptTemplateCancelEdit}
                                </button>
                              ) : null}
                            </div>

                            <div className="grid gap-3 sm:grid-cols-3">
                              <label className="block text-sm">
                                <span className="mb-2 block text-[var(--ink-soft)]">{text.promptTemplateName}</span>
                                <input
                                  value={promptTemplateDraft.name}
                                  onChange={(event) =>
                                    setPromptTemplateDraft((current) => ({
                                      ...current,
                                      name: event.target.value,
                                    }))
                                  }
                                  className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                                />
                              </label>
                              <label className="block text-sm">
                                <span className="mb-2 block text-[var(--ink-soft)]">{text.promptTemplateProject}</span>
                                <select
                                  value={promptTemplateDraft.project_id}
                                  onChange={(event) =>
                                    setPromptTemplateDraft((current) => ({
                                      ...current,
                                      project_id: event.target.value,
                                    }))
                                  }
                                  className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                                >
                                  <option value="">{text.globalTemplate}</option>
                                  {projects.map((project) => (
                                    <option key={project.id} value={project.id}>
                                      {project.name}
                                    </option>
                                  ))}
                                </select>
                                <span className="mt-2 block text-[11px] leading-5 text-[var(--ink-muted)]">
                                  {text.promptTemplateScopeHint}
                                </span>
                              </label>
                              <label className="block text-sm">
                                <span className="mb-2 block text-[var(--ink-soft)]">{text.promptTemplateModel}</span>
                                <input
                                  value={promptTemplateDraft.default_model}
                                  onChange={(event) =>
                                    setPromptTemplateDraft((current) => ({
                                      ...current,
                                      default_model: event.target.value,
                                    }))
                                  }
                                  className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                                />
                              </label>
                            </div>

                            <div className="mt-3 grid gap-3 sm:grid-cols-2">
                              <label className="block text-sm">
                                <span className="mb-2 block text-[var(--ink-soft)]">{text.promptTemplateCategory}</span>
                                <input
                                  value={promptTemplateDraft.category}
                                  onChange={(event) =>
                                    setPromptTemplateDraft((current) => ({
                                      ...current,
                                      category: event.target.value,
                                    }))
                                  }
                                  className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                                />
                              </label>
                              <label className="block text-sm">
                                <span className="mb-2 block text-[var(--ink-soft)]">{text.promptTemplateVariables}</span>
                                <input
                                  value={promptTemplateDraft.variables}
                                  onChange={(event) =>
                                    setPromptTemplateDraft((current) => ({
                                      ...current,
                                      variables: event.target.value,
                                    }))
                                  }
                                  className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                                  placeholder="topic, role, format"
                                />
                              </label>
                            </div>

                            <label className="mt-3 block text-sm">
                              <span className="mb-2 block text-[var(--ink-soft)]">
                                {text.promptTemplateDescription}
                              </span>
                              <input
                                value={promptTemplateDraft.description}
                                onChange={(event) =>
                                  setPromptTemplateDraft((current) => ({
                                    ...current,
                                    description: event.target.value,
                                  }))
                                }
                                className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                              />
                            </label>

                            <label className="mt-3 block text-sm">
                              <span className="mb-2 block text-[var(--ink-soft)]">{text.promptTemplateContent}</span>
                              <textarea
                                value={promptTemplateDraft.content}
                                onChange={(event) =>
                                  setPromptTemplateDraft((current) => ({
                                    ...current,
                                    content: event.target.value,
                                  }))
                                }
                                className="min-h-[180px] w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                              />
                            </label>

                            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                              <label className="flex items-center gap-2 text-xs text-[var(--ink-soft)]">
                                <input
                                  type="checkbox"
                                  checked={promptTemplateDraft.is_default}
                                  onChange={(event) =>
                                    setPromptTemplateDraft((current) => ({
                                      ...current,
                                      is_default: event.target.checked,
                                    }))
                                  }
                                />
                                {text.promptTemplateDefault}
                              </label>
                              <button
                                type="button"
                                onClick={() => void handleSavePromptTemplate()}
                                disabled={!promptTemplateDraft.name.trim() || !promptTemplateDraft.content.trim()}
                                className="primary-action rounded-full px-4 py-2 text-sm transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-55"
                              >
                                {editingPromptTemplateId ? text.promptTemplateUpdate : text.promptTemplateCreate}
                              </button>
                            </div>
                          </div>

                          <input
                            value={promptTemplateQuery}
                            onChange={(event) => setPromptTemplateQuery(event.target.value)}
                            placeholder={text.promptTemplateSearch}
                            className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 text-sm outline-none focus:border-[var(--accent-strong)]"
                          />

                          <div className="space-y-2">
                            {filteredPromptTemplates.length > 0 ? (
                              filteredPromptTemplates.map((template) => {
                                const applyTarget = getPromptTemplateApplyTarget(template);
                                const variables = promptTemplateVariables(template);
                                const scopeLabel = template.project_id
                                  ? projects.find((project) => project.id === template.project_id)?.name ??
                                    template.project_id
                                  : text.globalTemplate;

                                return (
                                  <div
                                    key={template.id}
                                    className="rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-4 py-3"
                                  >
                                    <div className="flex flex-wrap items-start justify-between gap-3">
                                      <div className="min-w-0">
                                        <div className="flex flex-wrap items-center gap-2">
                                          <p className="text-sm font-semibold text-[var(--ink-strong)]">
                                            {template.name}
                                          </p>
                                          {template.is_default ? (
                                            <span className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5 text-[10px] text-[var(--ink-muted)]">
                                              DEFAULT
                                            </span>
                                          ) : null}
                                        </div>
                                        {template.description ? (
                                          <p className="mt-1 text-xs text-[var(--ink-muted)]">
                                            {template.description}
                                          </p>
                                        ) : null}
                                        {template.default_model ? (
                                          <p className="mt-1 text-xs text-[var(--ink-muted)]">
                                            {template.default_model}
                                          </p>
                                        ) : null}
                                        {template.category ? (
                                          <p className="mt-1 text-xs text-[var(--ink-muted)]">
                                            {text.promptTemplateCategory}：{template.category}
                                          </p>
                                        ) : null}
                                        <p className="mt-1 text-xs text-[var(--ink-muted)]">
                                          {text.promptTemplateProject}：{scopeLabel}
                                        </p>
                                      </div>
                                      <div className="flex shrink-0 flex-wrap items-center gap-2">
                                        <label className="flex items-center gap-2 text-xs text-[var(--ink-soft)]">
                                          <span>{text.promptTemplateApplyTarget}</span>
                                          <select
                                            value={applyTarget}
                                            onChange={(event) =>
                                              setPromptTemplateApplyTargets((current) => ({
                                                ...current,
                                                [template.id]: event.target.value,
                                              }))
                                            }
                                            className="max-w-[220px] rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-1.5 text-xs outline-none focus:border-[var(--accent-strong)]"
                                          >
                                            {promptTemplateTargetOptions.map((target) => (
                                              <option key={target.id} value={target.id}>
                                                {target.label}
                                              </option>
                                            ))}
                                          </select>
                                        </label>
                                        <button
                                          type="button"
                                          onClick={() => void handleApplyPromptTemplate(template, applyTarget)}
                                          className="primary-action rounded-full px-3 py-1.5 text-xs"
                                        >
                                          {text.promptTemplateApply}
                                        </button>
                                        <button
                                          type="button"
                                          onClick={() => handleQuickInsertPromptTemplate(template)}
                                          className="rounded-full border border-[var(--control-border)] px-3 py-1.5 text-xs text-[var(--ink-soft)]"
                                        >
                                          {text.promptTemplateInsert}
                                        </button>
                                        <button
                                          type="button"
                                          onClick={() => handleEditPromptTemplate(template)}
                                          className="rounded-full border border-[var(--control-border)] px-3 py-1.5 text-xs text-[var(--ink-soft)]"
                                        >
                                          {text.promptTemplateEdit}
                                        </button>
                                        <button
                                          type="button"
                                          onClick={() => openDeletePromptTemplateDialog(template)}
                                          className="rounded-full border border-[rgba(174,65,45,0.22)] px-3 py-1.5 text-xs text-[#9f3a2b]"
                                        >
                                          {text.delete}
                                        </button>
                                      </div>
                                    </div>
                                    <p className="mt-3 line-clamp-3 whitespace-pre-wrap text-xs leading-5 text-[var(--ink-soft)]">
                                      {template.content}
                                    </p>
                                    {variables.length > 0 ? (
                                      <div className="mt-3 grid gap-2 sm:grid-cols-2">
                                        {variables.map((variable) => (
                                          <label key={variable} className="block text-xs">
                                            <span className="mb-1 block text-[var(--ink-muted)]">{variable}</span>
                                            <input
                                              value={promptTemplateVariableValues[template.id]?.[variable] ?? ""}
                                              onChange={(event) =>
                                                setPromptTemplateVariableValues((current) => ({
                                                  ...current,
                                                  [template.id]: {
                                                    ...(current[template.id] ?? {}),
                                                    [variable]: event.target.value,
                                                  },
                                                }))
                                              }
                                              className="w-full rounded-xl border border-[var(--control-border)] bg-[var(--soft-bg)] px-3 py-2 outline-none focus:border-[var(--accent-strong)]"
                                            />
                                          </label>
                                        ))}
                                      </div>
                                    ) : null}
                                  </div>
                                );
                              })
                            ) : (
                              <p className="rounded-2xl border border-dashed border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-4 text-sm text-[var(--ink-soft)]">
                                {text.promptTemplateEmpty}
                              </p>
                            )}
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </div>

                <div className="flex shrink-0 items-center justify-end gap-3 border-t border-[rgba(22,34,27,0.08)] px-5 py-4">
                  <button
                    type="button"
                    onClick={() => void resetSettingsToDefaults()}
                    className="rounded-full border border-[rgba(22,34,27,0.12)] bg-white px-4 py-2 text-sm text-[var(--ink-soft)]"
                  >
                    {text.resetDefaults}
                  </button>
                  <button
                    type="button"
                    onClick={() => setIsSettingsOpen(false)}
                    className="rounded-full border border-[rgba(22,34,27,0.12)] bg-white px-4 py-2 text-sm text-[var(--ink-soft)]"
                  >
                    {text.cancel}
                  </button>
                  <button
                    type="submit"
                    disabled={isSavingSettings || isPending}
                    className="primary-action rounded-full px-5 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-55"
                  >
                    {isSavingSettings ? text.saving : text.saveSettings}
                  </button>
                </div>
              </form>
            </div>
          ) : null}
        </section>
      </div>
    </main>
  );
}

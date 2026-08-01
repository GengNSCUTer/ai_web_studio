"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import {
  buildProviderPreset,
  buildSettingsPayload,
  normalizeThemeMode,
  normalizeUserSettings,
} from "@/lib/settings";
import { inferEmbeddingDimensions } from "@/lib/knowledge-models";
import type {
  KnowledgeCredential,
  KnowledgeConnectionTestResult,
  KnowledgeModelOptions,
  Project,
  PromptTemplate,
  McpServer,
  McpSyncResult,
  ProviderInfo,
  ToolConnectionTestResult,
  ToolSettings,
  User,
  UserMemory,
  UserSettings,
} from "@/lib/types";

type SettingsTab =
  | "provider"
  | "knowledge"
  | "generation"
  | "context"
  | "memory"
  | "system"
  | "tools"
  | "templates"
  | "appearance"
  | "privacy"
  | "workspace";

type SettingsCenterProps = {
  currentUser: User;
  initialSettings: UserSettings;
  initialProviderInfo: ProviderInfo | null;
  initialProjects: Project[];
  initialToolSettings: ToolSettings | null;
  initialMemories?: UserMemory[];
  initialPromptTemplates?: PromptTemplate[];
  initialMineruCredential?: KnowledgeCredential | null;
};

const TEXT = {
  "zh-CN": {
    title: "设置中心",
    subtitle: "统一管理模型服务、上下文策略、工具与集成、长期记忆和工作区默认设置。",
    backHome: "返回工作台",
    save: "保存设置",
    saving: "保存中...",
    loading: "加载中...",
    settingsSaved: "设置已保存",
    settingsSaveFailed: "设置保存失败：",
    testProvider: "测试连接",
    testing: "测试中...",
    providerTestFailed: "连接测试失败：",
    providerSaved: "问答模型设置已保存",
    provider: "问答模型",
    knowledge: "知识库模型",
    generation: "生成参数",
    context: "上下文",
    memory: "长期记忆",
    system: "系统提示",
    tools: "工具与集成",
    templates: "Prompt 模板",
    appearance: "外观",
    privacy: "隐私与导出",
    workspace: "工作区默认设置",
    defaultModel: "默认模型",
    providerType: "Provider 类型",
    apiBaseUrl: "API Base URL",
    ollamaBaseUrl: "Ollama Base URL",
    apiKey: "API Key",
    currentKey: "当前 Key",
    clearKey: "清空当前 Key",
    hasApiKey: "已配置凭证",
    noApiKey: "未配置凭证",
    temperature: "Temperature",
    topP: "Top P",
    maxTokens: "Max Tokens",
    systemPrompt: "System Prompt",
    modelContextWindow: "模型上下文窗口",
    contextMode: "上下文模式",
    memoryEnabled: "启用长期记忆",
    memoryMaxChars: "长期记忆最大注入字符数",
    uiLanguage: "界面语言",
    themeMode: "主题模式",
    themeSystem: "跟随系统",
    themeLight: "浅色",
    themeDark: "深色",
    toolsHint: "这里配置联网搜索和结构化外部工具的凭证与启用状态。",
    toolCredential: "工具凭证",
    toolEnabled: "启用工具",
    credentialSource: "凭证来源",
    workspaceToolOverrides: "当前工作区工具开关",
    workspacePermissionMode: "Agent 权限模式",
    permissionReadOnly: "只读",
    permissionAsk: "操作前询问",
    permissionFullWorkspace: "完全访问工作区",
    permissionReadOnlyHint: "只允许低风险读取，不执行文件修改或外部副作用。",
    permissionAskHint: "读取自动执行；文件修改生成 Diff，确认后再应用。",
    permissionFullWorkspaceHint: "仅自动应用受 ACL、版本 CAS 和审计约束的工作区文件修改。",
    permissionBoundaryHint: "完全访问工作区不包含服务器 Shell、任意路径、SQL、支付、发布或任意 HTTP 写入。",
    noWorkspaceSelected: "当前未选择具体工作区，仅显示用户级凭证。",
    saveToolSettings: "保存工具设置",
    toolSettingsSaved: "工具设置已保存",
    toolSettingsFailed: "工具设置失败：",
    mcpServers: "MCP Servers",
    addMcpServer: "添加 MCP Server",
    mcpServerKey: "Server Key",
    mcpServerName: "名称",
    mcpServerUrl: "MCP URL",
    mcpAuthType: "鉴权方式",
    mcpCredentialProvider: "凭据 Provider Key",
    syncMcpTools: "同步工具",
    noMcpServers: "还没有自定义 MCP Server。",
    mcpTools: "MCP 工具",
    noMcpTools: "同步后会在这里显示 MCP 工具。新工具默认不启用。",
    skills: "Skills",
    skillsHint: "Skill 只能编排已审核的 Tool/MCP 能力，不包含可执行代码、凭证或额外权限。",
    requiredTools: "依赖工具",
    missingTools: "缺少能力",
    enabled: "已启用",
    disabled: "未启用",
    providerConnectionSuccess: "连接成功",
    templatesCount: "模板数量",
    memoriesCount: "记忆数量",
    projectsCount: "工作区数量",
    settingsSectionTip:
      "这一版设置中心先承接现有核心能力；后续还会继续细化成更完整的产品化页面。",
    noTemplates: "当前还没有 Prompt 模板。",
    noMemories: "当前还没有长期记忆。",
    noProjects: "当前还没有工作区。",
    activeWorkspaceDefaults: "工作区默认配置",
    selectWorkspace: "选择工作区",
    testTool: "测试工具",
    close: "关闭",
    cancel: "取消",
    schema: "参数 Schema",
    editTool: "编辑工具",
    toolCategory: "工具分类",
    toolDescription: "工具描述",
    testArguments: "测试参数 JSON",
    testResult: "测试结果",
    addMcpServerTitle: "添加 MCP Server",
    mcpServerDialogHint: "填写 Streamable HTTP / SSE MCP Server 地址。需要 API Key 时，先在工具凭证里配置对应 provider。",
    mcpServerScopeWorkspace: "作用域：当前工作区",
    mcpServerScopeUser: "作用域：当前用户的所有工作区",
    mcpServerScopeHint: "选择了工作区时，Server 只会在该工作区的工具目录中出现；未选择工作区时，作为当前用户级 Server 复用。",
    knowledgeSettingsHint:
      "知识库模型服务独立于聊天模型服务：Embedding/Rerank 可使用云端 API，也可后续切到本地模型服务。新建知识库时会默认带入这里的配置。",
    parserProvider: "默认解析器",
    embeddingProvider: "Embedding Provider",
    embeddingBaseUrl: "Embedding Base URL",
    embeddingModel: "Embedding 模型",
    embeddingDimensions: "Embedding 维度",
    rerankEnabled: "启用 Rerank",
    rerankProvider: "Rerank Provider",
    rerankBaseUrl: "Rerank Base URL",
    rerankModel: "Rerank 模型",
    mineruCredentialEntry: "MinerU 凭据",
    mineruCredentialHint: "MinerU token 按用户加密保存，不会在页面回显明文。用于 PDF/复杂文档入库解析。",
    saveMineruToken: "保存 MinerU Token",
    testMineruToken: "测试 MinerU",
    refreshModelOptions: "测试连接",
    modelOptionsSource: "模型列表来源",
    embeddingApiKey: "Embedding API Key",
    rerankApiKey: "Rerank API Key",
    knowledgeApiKeyHint: "云端服务需要对应 API Key；本地 Ollama 可留空。Key 按用户加密保存，不回显明文。",
    modelOptionsFailed: "连接测试失败：",
    modelOptionsHint: "点击测试连接会验证当前 Provider/Base URL/API Key，并同步更新可用模型候选。",
  },
  "en-US": {
    title: "Settings Center",
    subtitle:
      "Manage model services, context strategy, tool integrations, long-term memory, and workspace defaults in one place.",
    backHome: "Back to Workspace",
    save: "Save",
    saving: "Saving...",
    loading: "Loading...",
    settingsSaved: "Settings saved",
    settingsSaveFailed: "Failed to save settings: ",
    testProvider: "Test Connection",
    testing: "Testing...",
    providerTestFailed: "Provider test failed: ",
    providerSaved: "Chat model settings saved",
    provider: "Chat model",
    knowledge: "Knowledge Models",
    generation: "Generation",
    context: "Context",
    memory: "Memory",
    system: "System Prompt",
    tools: "Tools",
    templates: "Prompt Templates",
    appearance: "Appearance",
    privacy: "Privacy & Export",
    workspace: "Workspace Defaults",
    defaultModel: "Default Model",
    providerType: "Provider Type",
    apiBaseUrl: "API Base URL",
    ollamaBaseUrl: "Ollama Base URL",
    apiKey: "API Key",
    currentKey: "Current key",
    clearKey: "Clear current key",
    hasApiKey: "Credential configured",
    noApiKey: "No credential",
    temperature: "Temperature",
    topP: "Top P",
    maxTokens: "Max Tokens",
    systemPrompt: "System Prompt",
    modelContextWindow: "Model context window",
    contextMode: "Context mode",
    memoryEnabled: "Enable long-term memory",
    memoryMaxChars: "Max injected chars for memory",
    uiLanguage: "UI language",
    themeMode: "Theme mode",
    themeSystem: "System",
    themeLight: "Light",
    themeDark: "Dark",
    toolsHint: "Configure credentials and enablement for web search and structured external tools.",
    toolCredential: "Tool credential",
    toolEnabled: "Enable tool",
    credentialSource: "Credential source",
    workspaceToolOverrides: "Workspace tool switches",
    workspacePermissionMode: "Agent permission mode",
    permissionReadOnly: "Read only",
    permissionAsk: "Ask before actions",
    permissionFullWorkspace: "Full workspace access",
    permissionReadOnlyHint: "Allow low-risk reads only; block file changes and external side effects.",
    permissionAskHint: "Run reads automatically; apply file diffs only after confirmation.",
    permissionFullWorkspaceHint: "Auto-apply only ACL-scoped, CAS-protected and audited workspace file edits.",
    permissionBoundaryHint: "Full workspace access never includes host shell, arbitrary paths, SQL, payment, publishing or arbitrary HTTP writes.",
    noWorkspaceSelected: "No workspace selected. Showing user-level credentials only.",
    saveToolSettings: "Save tool settings",
    toolSettingsSaved: "Tool settings saved",
    toolSettingsFailed: "Tool settings failed: ",
    mcpServers: "MCP Servers",
    addMcpServer: "Add MCP Server",
    mcpServerKey: "Server Key",
    mcpServerName: "Name",
    mcpServerUrl: "MCP URL",
    mcpAuthType: "Auth type",
    mcpCredentialProvider: "Credential provider key",
    syncMcpTools: "Sync tools",
    noMcpServers: "No custom MCP Server yet.",
    mcpTools: "MCP tools",
    noMcpTools: "MCP tools will appear here after sync. New tools are disabled by default.",
    skills: "Skills",
    skillsHint: "Skills only compose reviewed Tool/MCP capabilities. They contain no executable code, credentials, or extra permissions.",
    requiredTools: "Required tools",
    missingTools: "Missing capabilities",
    enabled: "Enabled",
    disabled: "Disabled",
    providerConnectionSuccess: "Connection succeeded",
    templatesCount: "Template count",
    memoriesCount: "Memory count",
    projectsCount: "Workspace count",
    settingsSectionTip:
      "This first settings center consolidates existing core capabilities; it will be further productized later.",
    noTemplates: "No prompt templates yet.",
    noMemories: "No long-term memories yet.",
    noProjects: "No workspaces yet.",
    activeWorkspaceDefaults: "Workspace default configuration",
    selectWorkspace: "Select workspace",
    testTool: "Test tool",
    close: "Close",
    cancel: "Cancel",
    schema: "Input schema",
    editTool: "Edit tool",
    toolCategory: "Tool category",
    toolDescription: "Tool description",
    testArguments: "Test arguments JSON",
    testResult: "Test result",
    addMcpServerTitle: "Add MCP Server",
    mcpServerDialogHint: "Fill in a Streamable HTTP / SSE MCP Server URL. If it needs an API key, configure its provider credential first.",
    mcpServerScopeWorkspace: "Scope: current workspace",
    mcpServerScopeUser: "Scope: all workspaces for this user",
    mcpServerScopeHint: "With a workspace selected, the Server is visible only in that workspace. Without one, it is reusable across this user's workspaces.",
    knowledgeSettingsHint:
      "Knowledge model services are separate from chat model services. Embedding/Rerank can use cloud APIs and can later be switched to local model services. New knowledge bases use these defaults.",
    parserProvider: "Default parser",
    embeddingProvider: "Embedding provider",
    embeddingBaseUrl: "Embedding base URL",
    embeddingModel: "Embedding model",
    embeddingDimensions: "Embedding dimensions",
    rerankEnabled: "Enable rerank",
    rerankProvider: "Rerank provider",
    rerankBaseUrl: "Rerank base URL",
    rerankModel: "Rerank model",
    mineruCredentialEntry: "MinerU credential",
    mineruCredentialHint: "MinerU token is encrypted per user and never displayed in plaintext. It is used for PDF/complex document parsing.",
    saveMineruToken: "Save MinerU token",
    testMineruToken: "Test MinerU",
    refreshModelOptions: "Test connection",
    modelOptionsSource: "Model list source",
    embeddingApiKey: "Embedding API Key",
    rerankApiKey: "Rerank API Key",
    knowledgeApiKeyHint: "Cloud services need a matching API key; local Ollama can leave it empty. Keys are encrypted per user and never displayed in plaintext.",
    modelOptionsFailed: "Connection test failed: ",
    modelOptionsHint: "Test connection validates the current provider/base URL/API key and refreshes available model candidates.",
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

function ModelPicker({
  value,
  options,
  onChange,
  placeholder,
  emptyLabel,
}: {
  value: string;
  options: string[];
  onChange: (value: string) => void;
  placeholder: string;
  emptyLabel?: string;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState(value);
  const normalizedOptions = useMemo(
    () => Array.from(new Set([value, ...options].filter(Boolean))),
    [options, value]
  );
  const filteredOptions = normalizedOptions.filter((model) =>
    model.toLowerCase().includes(query.trim().toLowerCase())
  );
  const typedValue = query.trim();
  const canUseTypedValue = Boolean(typedValue && !normalizedOptions.includes(typedValue));

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => {
          setQuery("");
          setIsOpen((current) => !current);
        }}
        className="flex w-full items-center justify-between gap-3 rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 text-left text-sm text-[var(--ink-strong)] outline-none transition hover:border-[var(--accent-strong)] focus:border-[var(--accent-strong)]"
      >
        <span className={value ? "truncate" : "truncate text-[var(--ink-muted)]"}>
          {value || emptyLabel || placeholder}
        </span>
        <span className="text-[var(--ink-muted)]">⌄</span>
      </button>
      {isOpen ? (
        <div className="absolute left-0 right-0 top-[calc(100%+8px)] z-50 rounded-[22px] border border-[var(--control-border)] bg-[var(--panel)] p-2 shadow-[var(--panel-shadow)]">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={placeholder}
            className="mb-2 w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm text-[var(--ink-strong)] outline-none focus:border-[var(--accent-strong)]"
          />
          <div className="max-h-72 overflow-y-auto pr-1">
            {emptyLabel ? (
              <button
                type="button"
                onClick={() => {
                  onChange("");
                  setIsOpen(false);
                }}
                className="block w-full rounded-2xl px-3 py-2 text-left text-sm text-[var(--ink-soft)] transition hover:bg-[var(--soft-bg)] hover:text-[var(--ink-strong)]"
              >
                {emptyLabel}
              </button>
            ) : null}
            {filteredOptions.map((model) => (
              <button
                key={model}
                type="button"
                onClick={() => {
                  onChange(model);
                  setIsOpen(false);
                }}
                className={`block w-full rounded-2xl px-3 py-2 text-left text-sm transition hover:bg-[var(--soft-bg)] hover:text-[var(--ink-strong)] ${
                  model === value ? "bg-[var(--soft-bg)] text-[var(--ink-strong)]" : "text-[var(--ink-soft)]"
                }`}
              >
                {model}
              </button>
            ))}
            {canUseTypedValue ? (
              <button
                type="button"
                onClick={() => {
                  onChange(typedValue);
                  setIsOpen(false);
                }}
                className="mt-1 block w-full rounded-2xl border border-dashed border-[var(--control-border)] px-3 py-2 text-left text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] hover:text-[var(--ink-strong)]"
              >
                使用：{typedValue}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function knowledgeProviderDefaultBaseUrl(provider: string) {
  if (provider === "ollama") {
    return "http://127.0.0.1:11435";
  }
  return "https://api.siliconflow.cn/v1";
}

export function SettingsCenter({
  currentUser,
  initialSettings,
  initialProviderInfo,
  initialProjects,
  initialToolSettings,
  initialMemories = [],
  initialPromptTemplates = [],
  initialMineruCredential = null,
}: SettingsCenterProps) {
  const [userSettings, setUserSettings] = useState<UserSettings>(normalizeUserSettings(initialSettings));
  const [providerInfo, setProviderInfo] = useState<ProviderInfo | null>(initialProviderInfo);
  const [projects, setProjects] = useState<Project[]>(initialProjects);
  const [toolSettings, setToolSettings] = useState<ToolSettings | null>(initialToolSettings);
  const [memories, setMemories] = useState<UserMemory[]>(initialMemories);
  const [promptTemplates] = useState<PromptTemplate[]>(initialPromptTemplates);
  const [selectedProjectId, setSelectedProjectId] = useState<string>(initialProjects[0]?.id ?? "");
  const [activeTab, setActiveTab] = useState<SettingsTab>("provider");
  const [settingsMessage, setSettingsMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const [reviewingMemoryId, setReviewingMemoryId] = useState<string | null>(null);
  const [isTestingProvider, setIsTestingProvider] = useState(false);
  const [toolCredentialDrafts, setToolCredentialDrafts] = useState<Record<string, string>>({});
  const [toolEnabledDrafts, setToolEnabledDrafts] = useState<Record<string, boolean>>({});
  const [workspaceToolEnabledDrafts, setWorkspaceToolEnabledDrafts] = useState<Record<string, boolean>>({});
  const [workspacePermissionMode, setWorkspacePermissionMode] = useState<
    "read_only" | "ask" | "full_workspace"
  >("ask");
  const [providerApiKeyDraft, setProviderApiKeyDraft] = useState("");
  const [clearProviderApiKey, setClearProviderApiKey] = useState(false);
  const [knowledgeEmbeddingApiKeyDraft, setKnowledgeEmbeddingApiKeyDraft] = useState("");
  const [clearKnowledgeEmbeddingApiKey, setClearKnowledgeEmbeddingApiKey] = useState(false);
  const [knowledgeRerankApiKeyDraft, setKnowledgeRerankApiKeyDraft] = useState("");
  const [clearKnowledgeRerankApiKey, setClearKnowledgeRerankApiKey] = useState(false);
  const [mineruCredential, setMineruCredential] = useState<KnowledgeCredential | null>(initialMineruCredential);
  const [mineruTokenDraft, setMineruTokenDraft] = useState("");
  const [isSavingMineru, setIsSavingMineru] = useState(false);
  const [isTestingMineru, setIsTestingMineru] = useState(false);
  const [embeddingModelOptions, setEmbeddingModelOptions] = useState<string[]>([
    userSettings.knowledge_embedding_model,
    "BAAI/bge-m3",
    "Qwen/Qwen3-Embedding-0.6B",
    "Qwen/Qwen3-Embedding-4B",
    "Qwen/Qwen3-Embedding-8B",
  ].filter(Boolean));
  const [rerankModelOptions, setRerankModelOptions] = useState<string[]>([
    userSettings.knowledge_rerank_model,
    "BAAI/bge-reranker-v2-m3",
    "Qwen/Qwen3-Reranker-0.6B",
    "Qwen/Qwen3-Reranker-8B",
  ].filter(Boolean));
  const [knowledgeModelOptionsSource, setKnowledgeModelOptionsSource] = useState<string>("remote");
  const [loadingKnowledgeModels, setLoadingKnowledgeModels] = useState<"embedding" | "rerank" | null>(null);
  const [clearToolApiKeys, setClearToolApiKeys] = useState<Record<string, boolean>>({});
  const [isSavingToolSettings, setIsSavingToolSettings] = useState(false);
  const [isSavingWorkspace, setIsSavingWorkspace] = useState(false);
  const [testingToolProvider, setTestingToolProvider] = useState<string | null>(null);
  const [mcpServerDraft, setMcpServerDraft] = useState({
    server_key: "",
    name: "",
    url: "",
    auth_type: "none",
    credential_provider: "",
  });
  const [isMcpServerDialogOpen, setIsMcpServerDialogOpen] = useState(false);
  const [expandedMcpToolId, setExpandedMcpToolId] = useState<string | null>(null);
  const [mcpToolDrafts, setMcpToolDrafts] = useState<Record<string, { description: string; category: string }>>({});
  const [mcpToolTestArgs, setMcpToolTestArgs] = useState<Record<string, string>>({});
  const [testingMcpToolId, setTestingMcpToolId] = useState<string | null>(null);
  const [mcpToolTestResult, setMcpToolTestResult] = useState<Record<string, string>>({});
  const [isCreatingMcpServer, setIsCreatingMcpServer] = useState(false);
  const [testingMcpServerId, setTestingMcpServerId] = useState<string | null>(null);
  const [syncingMcpServerId, setSyncingMcpServerId] = useState<string | null>(null);
  const [updatingSkillKey, setUpdatingSkillKey] = useState<string | null>(null);
  const [systemPrefersDark, setSystemPrefersDark] = useState(false);

  const uiLanguage = userSettings.ui_language === "en-US" ? "en-US" : "zh-CN";
  const text = TEXT[uiLanguage];
  const selectedThemeMode = normalizeThemeMode(userSettings.theme_mode);
  const resolvedTheme = selectedThemeMode === "system" ? (systemPrefersDark ? "dark" : "light") : selectedThemeMode;
  const availableModels = useMemo(() => providerInfo?.models ?? [], [providerInfo?.models]);
  const selectedProject = projects.find((project) => project.id === selectedProjectId) ?? null;
  const toolProviders = Array.from(new Set((toolSettings?.credentials ?? []).map((credential) => credential.provider_key)));
  const credentialByProvider = Object.fromEntries(
    (toolSettings?.credentials ?? []).map((credential) => [credential.provider_key, credential])
  );

  const tabs: Array<{ id: SettingsTab; label: string }> = [
    { id: "provider", label: text.provider },
    { id: "knowledge", label: text.knowledge },
    { id: "generation", label: text.generation },
    { id: "context", label: text.context },
    { id: "memory", label: text.memory },
    { id: "system", label: text.system },
    { id: "tools", label: text.tools },
    { id: "workspace", label: text.workspace },
    { id: "templates", label: text.templates },
    { id: "appearance", label: text.appearance },
    { id: "privacy", label: text.privacy },
  ];

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const updatePreference = () => setSystemPrefersDark(mediaQuery.matches);
    updatePreference();
    mediaQuery.addEventListener("change", updatePreference);
    return () => mediaQuery.removeEventListener("change", updatePreference);
  }, []);

  useEffect(() => {
    if (!toolSettings) {
      return;
    }
    /* eslint-disable react-hooks/set-state-in-effect */
    setToolCredentialDrafts(
      Object.fromEntries(toolSettings.credentials.map((credential) => [credential.provider_key, ""]))
    );
    setToolEnabledDrafts(
      Object.fromEntries(toolSettings.credentials.map((credential) => [credential.provider_key, credential.is_enabled]))
    );
    setClearToolApiKeys(
      Object.fromEntries(toolSettings.credentials.map((credential) => [credential.provider_key, false]))
    );
    const workspaceMap = new Map(toolSettings.workspace_settings.map((item) => [item.tool_key, item.is_enabled]));
    setWorkspaceToolEnabledDrafts(
      Object.fromEntries(toolSettings.tools.map((tool) => [tool.tool_key, workspaceMap.get(tool.tool_key) ?? true]))
    );
    setWorkspacePermissionMode(toolSettings.workspace_policy?.permission_mode ?? "ask");
    setMcpToolDrafts(
      Object.fromEntries(
        toolSettings.mcp_tools.map((tool) => [
          tool.id,
          {
            description: tool.description_override ?? tool.description ?? "",
            category: tool.category ?? "mcp_tool",
          },
        ])
      )
    );
    setMcpToolTestArgs((current) =>
      Object.fromEntries(toolSettings.mcp_tools.map((tool) => [tool.id, current[tool.id] ?? "{}"]))
    );
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [toolSettings]);

  useEffect(() => {
    if (!selectedProjectId) {
      return;
    }
    void reloadToolSettings(selectedProjectId).catch(() => undefined);
  }, [selectedProjectId]);

  useEffect(() => {
    if (!settingsMessage && !errorMessage) {
      return;
    }
    const timer = window.setTimeout(() => {
      setSettingsMessage(null);
      setErrorMessage(null);
    }, 5000);
    return () => window.clearTimeout(timer);
  }, [settingsMessage, errorMessage]);

  useEffect(() => {
    if (activeTab !== "knowledge") {
      return;
    }
    void handleRefreshKnowledgeModels("embedding", true);
    void handleRefreshKnowledgeModels("rerank", true);
    // Refresh when the selected knowledge model service changes. Draft API key is
    // intentionally excluded to avoid firing network calls while the user types.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    activeTab,
    userSettings.knowledge_embedding_provider,
    userSettings.knowledge_embedding_base_url,
    userSettings.knowledge_rerank_provider,
    userSettings.knowledge_rerank_base_url,
  ]);

  const selectedProjectModelOptions = useMemo(() => {
    return Array.from(
      new Set(
        [
          selectedProject?.default_model ?? "",
          userSettings.default_model,
          providerInfo?.default_model ?? "",
          ...availableModels,
        ].filter(Boolean)
      )
    );
  }, [availableModels, providerInfo?.default_model, selectedProject?.default_model, userSettings.default_model]);
  const settingsModelOptions = useMemo(() => {
    return Array.from(
      new Set(
        [
          userSettings.default_model,
          providerInfo?.default_model ?? "",
          ...availableModels,
        ].filter(Boolean)
      )
    );
  }, [availableModels, providerInfo?.default_model, userSettings.default_model]);

  async function reloadToolSettings(projectId?: string) {
    const suffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
    const data = await requestJson<ToolSettings>(`/api/backend/tools/settings${suffix}`);
    setToolSettings(data);
    return data;
  }

  function applyProviderPreset(providerType: string) {
    const defaults = buildProviderPreset(providerType);
    setProviderInfo((current) =>
      current && current.provider === providerType
        ? current
        : {
            provider: providerType,
            base_url: providerType === "ollama" ? defaults.ollamaBaseUrl : defaults.apiBaseUrl,
            default_model: defaults.model,
            models: current?.provider === providerType ? current.models : [],
          }
    );
    setUserSettings((current) => ({
      ...current,
      provider_type: providerType,
      ollama_base_url: current.ollama_base_url || defaults.ollamaBaseUrl,
      api_base_url: providerType === "ollama" ? current.api_base_url : defaults.apiBaseUrl,
      default_model: defaults.model,
      model_context_window: defaults.modelContextWindow,
    }));
    setProviderApiKeyDraft("");
    setClearProviderApiKey(false);
  }

  async function handleSaveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSavingSettings(true);
    setErrorMessage(null);
    setSettingsMessage(null);
    try {
      const saved = await requestJson<UserSettings>("/api/backend/settings", {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          ...buildSettingsPayload(userSettings),
          api_key: providerApiKeyDraft.trim() ? providerApiKeyDraft.trim() : undefined,
          clear_api_key: clearProviderApiKey,
          knowledge_embedding_api_key: knowledgeEmbeddingApiKeyDraft.trim()
            ? knowledgeEmbeddingApiKeyDraft.trim()
            : undefined,
          clear_knowledge_embedding_api_key: clearKnowledgeEmbeddingApiKey,
          knowledge_rerank_api_key: knowledgeRerankApiKeyDraft.trim()
            ? knowledgeRerankApiKeyDraft.trim()
            : undefined,
          clear_knowledge_rerank_api_key: clearKnowledgeRerankApiKey,
        }),
      });
      setUserSettings(normalizeUserSettings(saved));
      setProviderApiKeyDraft("");
      setClearProviderApiKey(false);
      setKnowledgeEmbeddingApiKeyDraft("");
      setClearKnowledgeEmbeddingApiKey(false);
      setKnowledgeRerankApiKeyDraft("");
      setClearKnowledgeRerankApiKey(false);
      setSettingsMessage(text.settingsSaved);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setErrorMessage(`${text.settingsSaveFailed}${message}`);
    } finally {
      setIsSavingSettings(false);
    }
  }

  async function reviewMemory(memory: UserMemory, action: "approve" | "reject") {
    setReviewingMemoryId(memory.id);
    setErrorMessage(null);
    try {
      let body: string | undefined;
      if (action === "approve") {
        let expiresAt: string | null = null;
        if (memory.risk_level === "volatile") {
          const input = window.prompt(
            uiLanguage === "zh-CN"
              ? "该候选包含短期信息，请输入过期时间（ISO 8601，例如 2026-08-30T00:00:00+08:00）"
              : "Enter an ISO 8601 expiry for this volatile memory"
          );
          if (!input) {
            return;
          }
          expiresAt = input;
        }
        body = JSON.stringify({ expires_at: expiresAt });
      }
      const updated = await requestJson<UserMemory>(`/api/backend/memories/${memory.id}/${action}`, {
        method: "POST",
        headers: body ? { "content-type": "application/json" } : undefined,
        body,
      });
      setMemories((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setSettingsMessage(
        uiLanguage === "zh-CN"
          ? action === "approve" ? "候选记忆已确认并启用" : "候选记忆已拒绝"
          : action === "approve" ? "Memory candidate approved" : "Memory candidate rejected"
      );
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Memory review failed");
    } finally {
      setReviewingMemoryId(null);
    }
  }

  async function handleTestProvider() {
    setIsTestingProvider(true);
    setErrorMessage(null);
    setSettingsMessage(null);
    try {
      const result = await requestJson<ProviderInfo & { message?: string }>(
        "/api/backend/settings/test-provider",
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            provider_type: userSettings.provider_type,
            ollama_base_url: userSettings.ollama_base_url,
            api_base_url: userSettings.api_base_url,
            api_key:
              userSettings.provider_type !== "ollama" && providerApiKeyDraft.trim()
                ? providerApiKeyDraft.trim()
                : null,
          }),
        }
      );
      setProviderInfo({
        provider: result.provider,
        base_url: result.base_url,
        default_model: result.default_model,
        models: result.models,
      });
      setUserSettings((current) => ({
        ...current,
        default_model: result.default_model || current.default_model,
      }));
      setSettingsMessage(result.message || text.providerConnectionSuccess);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setErrorMessage(`${text.providerTestFailed}${message}`);
    } finally {
      setIsTestingProvider(false);
    }
  }

  async function handleRefreshKnowledgeModels(modelKind: "embedding" | "rerank", silent = false) {
    setLoadingKnowledgeModels(modelKind);
    setErrorMessage(null);
    setSettingsMessage(null);
    const provider =
      modelKind === "embedding"
        ? userSettings.knowledge_embedding_provider
        : userSettings.knowledge_rerank_provider;
    const baseUrl =
      modelKind === "embedding"
        ? userSettings.knowledge_embedding_base_url
        : userSettings.knowledge_rerank_base_url;
    const apiKeyDraft =
      modelKind === "embedding" ? knowledgeEmbeddingApiKeyDraft : knowledgeRerankApiKeyDraft;
    try {
      const result = await requestJson<KnowledgeModelOptions>("/api/backend/settings/knowledge-model-options", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          provider,
          base_url: baseUrl,
          model_kind: modelKind,
          api_key: apiKeyDraft.trim() ? apiKeyDraft.trim() : undefined,
          strict: !silent,
        }),
      });
      if (modelKind === "embedding") {
        setEmbeddingModelOptions(
          Array.from(new Set([userSettings.knowledge_embedding_model, ...result.models].filter(Boolean)))
        );
      } else {
        setRerankModelOptions(
          Array.from(new Set([userSettings.knowledge_rerank_model, ...result.models].filter(Boolean)))
        );
      }
      setKnowledgeModelOptionsSource(result.source);
      if (!silent) {
        setSettingsMessage(result.message);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      if (!silent) {
        setErrorMessage(`${text.modelOptionsFailed}${message}`);
      }
    } finally {
      setLoadingKnowledgeModels(null);
    }
  }

  async function handleSaveMineruCredential() {
    setIsSavingMineru(true);
    setErrorMessage(null);
    setSettingsMessage(null);
    try {
      const updated = await requestJson<KnowledgeCredential>("/api/backend/knowledge/credentials/mineru", {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          api_key: mineruTokenDraft.trim() || undefined,
          is_enabled: true,
        }),
      });
      setMineruCredential(updated);
      setMineruTokenDraft("");
      setSettingsMessage(uiLanguage === "zh-CN" ? "MinerU token 已保存" : "MinerU token saved");
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setErrorMessage(`${text.settingsSaveFailed}${message}`);
    } finally {
      setIsSavingMineru(false);
    }
  }

  async function handleTestMineruCredential() {
    setIsTestingMineru(true);
    setErrorMessage(null);
    setSettingsMessage(null);
    try {
      const result = await requestJson<KnowledgeConnectionTestResult>(
        "/api/backend/knowledge/credentials/mineru/test",
        { method: "POST" }
      );
      setSettingsMessage(result.message);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setErrorMessage(`${text.settingsSaveFailed}${message}`);
    } finally {
      setIsTestingMineru(false);
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
            clear_api_key: clearToolApiKeys[providerKey] || undefined,
          }),
        });
      }
      if (selectedProject) {
        await requestJson(`/api/backend/tools/workspace-policies/${selectedProject.id}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ permission_mode: workspacePermissionMode }),
        });
        for (const tool of toolSettings.tools) {
          await requestJson(`/api/backend/tools/workspaces/${selectedProject.id}/${encodeURIComponent(tool.tool_key)}`, {
            method: "PATCH",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              is_enabled: workspaceToolEnabledDrafts[tool.tool_key] ?? true,
            }),
          });
        }
      }
      await reloadToolSettings(selectedProject?.id);
      setClearToolApiKeys((current) =>
        Object.fromEntries(Object.keys(current).map((providerKey) => [providerKey, false]))
      );
      setSettingsMessage(text.toolSettingsSaved);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
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
      await reloadToolSettings(selectedProject?.id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setErrorMessage(`${text.toolSettingsFailed}${message}`);
    } finally {
      setTestingToolProvider(null);
    }
  }

  async function handleToggleSkill(skillKey: string, isEnabled: boolean) {
    setUpdatingSkillKey(skillKey);
    setErrorMessage(null);
    setSettingsMessage(null);
    try {
      await requestJson(`/api/backend/tools/skills/${encodeURIComponent(skillKey)}`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ is_enabled: isEnabled }),
      });
      await reloadToolSettings(selectedProject?.id);
      setSettingsMessage(text.toolSettingsSaved);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setErrorMessage(`${text.toolSettingsFailed}${message}`);
    } finally {
      setUpdatingSkillKey(null);
    }
  }

  async function handleCreateMcpServer() {
    setIsCreatingMcpServer(true);
    setErrorMessage(null);
    setSettingsMessage(null);
    try {
      const payload = {
        server_key: mcpServerDraft.server_key.trim(),
        name: mcpServerDraft.name.trim(),
        url: mcpServerDraft.url.trim(),
        auth_type: mcpServerDraft.auth_type,
        transport_type: "streamable_http",
        credential_provider: mcpServerDraft.credential_provider.trim() || mcpServerDraft.server_key.trim(),
        project_id: selectedProject?.id ?? null,
        is_enabled: true,
      };
      if (!payload.server_key || !payload.name || !payload.url) {
        throw new Error(uiLanguage === "zh-CN" ? "请填写 Server Key、名称和 URL" : "Server key, name, and URL are required");
      }
      await requestJson<McpServer>("/api/backend/tools/mcp-servers", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      setMcpServerDraft({ server_key: "", name: "", url: "", auth_type: "none", credential_provider: "" });
      setIsMcpServerDialogOpen(false);
      await reloadToolSettings(selectedProject?.id);
      setSettingsMessage(uiLanguage === "zh-CN" ? "MCP Server 已添加" : "MCP Server added");
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setErrorMessage(`${text.toolSettingsFailed}${message}`);
    } finally {
      setIsCreatingMcpServer(false);
    }
  }

  async function handleTestMcpServer(serverId: string) {
    setTestingMcpServerId(serverId);
    setErrorMessage(null);
    setSettingsMessage(null);
    try {
      const result = await requestJson<ToolConnectionTestResult>(
        `/api/backend/tools/mcp-servers/${serverId}/test`,
        { method: "POST" }
      );
      setSettingsMessage(result.message);
      await reloadToolSettings(selectedProject?.id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setErrorMessage(`${text.toolSettingsFailed}${message}`);
    } finally {
      setTestingMcpServerId(null);
    }
  }

  async function handleSyncMcpServer(serverId: string) {
    setSyncingMcpServerId(serverId);
    setErrorMessage(null);
    setSettingsMessage(null);
    try {
      const result = await requestJson<McpSyncResult>(
        `/api/backend/tools/mcp-servers/${serverId}/sync-tools`,
        { method: "POST" }
      );
      setSettingsMessage(result.message);
      await reloadToolSettings(selectedProject?.id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setErrorMessage(`${text.toolSettingsFailed}${message}`);
    } finally {
      setSyncingMcpServerId(null);
    }
  }

  async function handleUpdateMcpTool(toolId: string, patch: Record<string, unknown>) {
    setErrorMessage(null);
    setSettingsMessage(null);
    try {
      await requestJson(`/api/backend/tools/mcp-tools/${toolId}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(patch),
      });
      await reloadToolSettings(selectedProject?.id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setErrorMessage(`${text.toolSettingsFailed}${message}`);
    }
  }

  async function handleSaveMcpToolMeta(toolId: string) {
    const draft = mcpToolDrafts[toolId];
    if (!draft) {
      return;
    }
    await handleUpdateMcpTool(toolId, {
      description_override: draft.description,
      category: draft.category,
    });
    setSettingsMessage(uiLanguage === "zh-CN" ? "MCP 工具信息已保存" : "MCP tool metadata saved");
  }

  async function handleTestMcpTool(toolId: string) {
    setTestingMcpToolId(toolId);
    setErrorMessage(null);
    setSettingsMessage(null);
    try {
      let args: Record<string, unknown> = {};
      const rawArgs = (mcpToolTestArgs[toolId] ?? "{}").trim();
      if (rawArgs) {
        const parsed = JSON.parse(rawArgs) as unknown;
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          throw new Error(uiLanguage === "zh-CN" ? "测试参数必须是 JSON 对象" : "Test arguments must be a JSON object");
        }
        args = parsed as Record<string, unknown>;
      }
      const result = await requestJson<ToolConnectionTestResult>(`/api/backend/tools/mcp-tools/${toolId}/test`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ arguments: args }),
      });
      setMcpToolTestResult((current) => ({
        ...current,
        [toolId]: JSON.stringify(result.raw ?? { message: result.message }, null, 2),
      }));
      setSettingsMessage(result.message);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setErrorMessage(`${text.toolSettingsFailed}${message}`);
    } finally {
      setTestingMcpToolId(null);
    }
  }

  async function handleSaveWorkspaceSettings() {
    if (!selectedProject) {
      return;
    }
    setIsSavingWorkspace(true);
    setErrorMessage(null);
    setSettingsMessage(null);
    try {
      const saved = await requestJson<Project>(`/api/backend/projects/${selectedProject.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          default_model: (selectedProject.default_model ?? "").trim() || null,
          system_prompt: (selectedProject.system_prompt ?? "").trim() || null,
        }),
      });
      setProjects((current) =>
        current.map((project) => (project.id === saved.id ? saved : project))
      );
      setSettingsMessage(text.settingsSaved);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setErrorMessage(`${text.settingsSaveFailed}${message}`);
    } finally {
      setIsSavingWorkspace(false);
    }
  }

  return (
    <main
      data-theme={resolvedTheme}
      className="min-h-screen bg-[var(--app-bg)] px-4 py-4 text-[var(--ink-strong)] sm:px-6"
    >
      <div className="mx-auto flex max-w-7xl flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3 rounded-[28px] border border-[var(--hairline)] bg-[var(--panel)] p-6 shadow-[var(--panel-shadow)]">
          <div>
            <p className="text-xs uppercase tracking-[0.32em] text-[var(--ink-muted)]">AI Web Studio</p>
            <h1 className="mt-3 text-3xl font-semibold">{text.title}</h1>
            <p className="mt-3 max-w-3xl text-sm leading-7 text-[var(--ink-soft)]">{text.subtitle}</p>
          </div>
          <div className="flex items-center gap-3">
            <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-4 py-3 text-sm text-[var(--ink-soft)]">
              {currentUser.email || currentUser.username || currentUser.id}
            </div>
            <Link
              href="/"
              className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
            >
              {text.backHome}
            </Link>
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
          <aside className="rounded-[28px] border border-[var(--hairline)] bg-[var(--panel)] p-3 shadow-[var(--panel-shadow)]">
            <div className="mb-3 rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] p-4 text-sm leading-6 text-[var(--ink-soft)]">
              {text.settingsSectionTip}
            </div>
            <div className="grid gap-2">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveTab(tab.id)}
                  className={`rounded-2xl border px-4 py-3 text-left text-sm transition ${
                    activeTab === tab.id
                      ? "border-[var(--accent-strong)] bg-[var(--accent-soft)] text-[var(--ink-strong)]"
                      : "border-[var(--hairline)] bg-[var(--soft-bg)] text-[var(--ink-soft)] hover:border-[var(--accent-strong)]"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </aside>

          <section className="rounded-[28px] border border-[var(--hairline)] bg-[var(--panel)] p-6 shadow-[var(--panel-shadow)]">
            {errorMessage ? (
              <div className="mb-4 rounded-2xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-4 py-3 text-sm text-[var(--danger-text)]">
                {errorMessage}
              </div>
            ) : null}
            {settingsMessage ? (
              <div className="mb-4 rounded-2xl border border-[var(--success-border)] bg-[var(--success-bg)] px-4 py-3 text-sm text-[var(--success-text)]">
                {settingsMessage}
              </div>
            ) : null}

            <form onSubmit={handleSaveSettings} className="space-y-6">
              {activeTab === "provider" ? (
                <div className="grid gap-4 xl:grid-cols-2">
                  <label className="block text-sm">
                    <span className="mb-2 block text-[var(--ink-soft)]">{text.providerType}</span>
                    <select
                      value={userSettings.provider_type}
                      onChange={(event) => applyProviderPreset(event.target.value)}
                      className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                    >
                      <option value="ollama">ollama</option>
                      <option value="openai-compatible">openai-compatible</option>
                      <option value="vllm">vllm</option>
                      <option value="anthropic">anthropic</option>
                    </select>
                  </label>
                  <label className="block text-sm">
                    <span className="mb-2 block text-[var(--ink-soft)]">{text.apiBaseUrl}</span>
                    <input
                      value={userSettings.api_base_url}
                      onChange={(event) =>
                        setUserSettings((current) => ({
                          ...current,
                          api_base_url: event.target.value,
                        }))
                      }
                      className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                    />
                  </label>
                  <label className="block text-sm">
                    <span className="mb-2 block text-[var(--ink-soft)]">{text.ollamaBaseUrl}</span>
                    <input
                      value={userSettings.ollama_base_url}
                      onChange={(event) =>
                        setUserSettings((current) => ({
                          ...current,
                          ollama_base_url: event.target.value,
                        }))
                      }
                      className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                    />
                  </label>
                  <label className="block text-sm xl:col-span-2">
                    <span className="mb-2 block text-[var(--ink-soft)]">{text.defaultModel}</span>
                    <ModelPicker
                      value={userSettings.default_model}
                      options={settingsModelOptions}
                      placeholder={uiLanguage === "zh-CN" ? "搜索或输入模型名" : "Search or enter model name"}
                      onChange={(model) =>
                        setUserSettings((current) => ({
                          ...current,
                          default_model: model,
                        }))
                      }
                    />
                  </label>
                  {userSettings.provider_type !== "ollama" ? (
                    <div className="xl:col-span-2 rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-[var(--ink-strong)]">{text.apiKey}</p>
                          <p className="mt-1 text-xs text-[var(--ink-muted)]">
                            {userSettings.has_api_key ? text.hasApiKey : text.noApiKey}
                            {userSettings.api_key_masked ? ` · ${text.currentKey}: ${userSettings.api_key_masked}` : ""}
                          </p>
                        </div>
                        <label className="flex items-center gap-2 text-xs text-[var(--ink-soft)]">
                          <input
                            type="checkbox"
                            checked={clearProviderApiKey}
                            onChange={(event) => {
                              setClearProviderApiKey(event.target.checked);
                              if (event.target.checked) {
                                setProviderApiKeyDraft("");
                              }
                            }}
                          />
                          {text.clearKey}
                        </label>
                      </div>
                      <input
                        type="password"
                        value={providerApiKeyDraft}
                        onChange={(event) => {
                          setProviderApiKeyDraft(event.target.value);
                          if (event.target.value.trim()) {
                            setClearProviderApiKey(false);
                          }
                        }}
                        placeholder={userSettings.api_key_masked ?? text.apiKey}
                        className="mt-3 w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                      />
                    </div>
                  ) : null}
                  <div className="xl:col-span-2 flex justify-end">
                    <button
                      type="button"
                      onClick={() => void handleTestProvider()}
                      disabled={isTestingProvider}
                      className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] disabled:opacity-55"
                    >
                      {isTestingProvider ? text.testing : text.testProvider}
                    </button>
                  </div>
                </div>
              ) : null}

              {activeTab === "knowledge" ? (
                <div className="space-y-4">
                  <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                    <p className="text-sm leading-7 text-[var(--ink-soft)]">{text.knowledgeSettingsHint}</p>
                  </div>

                  <div className="grid gap-4 xl:grid-cols-2">
                    <label className="block text-sm">
                      <span className="mb-2 block text-[var(--ink-soft)]">{text.parserProvider}</span>
                      <select
                        value={userSettings.knowledge_parser_provider}
                        onChange={(event) =>
                          setUserSettings((current) => ({
                            ...current,
                            knowledge_parser_provider: event.target.value,
                          }))
                        }
                        className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                      >
                        <option value="local_basic">local_basic</option>
                        <option value="mineru">mineru</option>
                      </select>
                    </label>

                    <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-4 py-3 text-sm">
                      <p className="font-semibold text-[var(--ink-strong)]">{text.mineruCredentialEntry}</p>
                      <p className="mt-1 text-xs leading-6 text-[var(--ink-soft)]">{text.mineruCredentialHint}</p>
                      <div className="mt-3 rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-3 py-2 text-xs text-[var(--ink-muted)]">
                        {mineruCredential?.has_api_key
                          ? `${text.hasApiKey}${mineruCredential.api_key_masked ? ` · ${mineruCredential.api_key_masked}` : ""}`
                          : text.noApiKey}
                      </div>
                      <input
                        type="password"
                        value={mineruTokenDraft}
                        onChange={(event) => setMineruTokenDraft(event.target.value)}
                        placeholder={mineruCredential?.api_key_masked ?? "MinerU token"}
                        className="mt-3 w-full rounded-2xl border border-[var(--control-border)] bg-[var(--soft-bg)] px-3 py-2 text-sm text-[var(--ink-strong)] outline-none focus:border-[var(--accent-strong)]"
                      />
                      <div className="mt-3 flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => void handleSaveMineruCredential()}
                          disabled={isSavingMineru || !mineruTokenDraft.trim()}
                          className="rounded-full border border-[var(--control-border)] bg-[var(--soft-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] disabled:cursor-not-allowed disabled:opacity-55"
                        >
                          {isSavingMineru ? text.saving : text.saveMineruToken}
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleTestMineruCredential()}
                          disabled={isTestingMineru}
                          className="rounded-full border border-[var(--control-border)] bg-[var(--soft-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] disabled:cursor-not-allowed disabled:opacity-55"
                        >
                          {isTestingMineru ? text.testing : text.testMineruToken}
                        </button>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                    <div className="mb-4">
                      <p className="text-sm font-semibold text-[var(--ink-strong)]">Embedding</p>
                      <p className="mt-1 text-xs leading-6 text-[var(--ink-muted)]">
                        {uiLanguage === "zh-CN"
                          ? "用于文档向量化。新建知识库时默认带入，也可以在创建弹窗中覆盖。"
                          : "Used for document vectorization. New knowledge bases use this as the default and can override it in the create dialog."}
                      </p>
                      <p className="mt-1 text-xs leading-6 text-[var(--ink-muted)]">{text.modelOptionsHint}</p>
                    </div>
                    <div className="grid gap-4 xl:grid-cols-2">
                      <label className="block text-sm">
                        <span className="mb-2 block text-[var(--ink-soft)]">{text.embeddingProvider}</span>
                        <select
                          value={userSettings.knowledge_embedding_provider}
                          onChange={(event) =>
                            setUserSettings((current) => ({
                              ...current,
                              knowledge_embedding_provider: event.target.value,
                              knowledge_embedding_base_url: knowledgeProviderDefaultBaseUrl(event.target.value),
                            }))
                          }
                          className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                        >
                          <option value="siliconflow">siliconflow</option>
                          <option value="openai-compatible">openai-compatible</option>
                          <option value="ollama">ollama</option>
                        </select>
                      </label>
                      <label className="block text-sm">
                        <span className="mb-2 block text-[var(--ink-soft)]">{text.embeddingBaseUrl}</span>
                        <input
                          value={userSettings.knowledge_embedding_base_url}
                          onChange={(event) =>
                            setUserSettings((current) => ({
                              ...current,
                              knowledge_embedding_base_url: event.target.value,
                            }))
                          }
                          className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                        />
                      </label>
                      <label className="block text-sm">
                        <span className="mb-2 block text-[var(--ink-soft)]">{text.embeddingModel}</span>
                        <ModelPicker
                          value={userSettings.knowledge_embedding_model}
                          options={embeddingModelOptions}
                          placeholder={uiLanguage === "zh-CN" ? "搜索或输入 Embedding 模型" : "Search or enter embedding model"}
                          onChange={(model) =>
                            setUserSettings((current) => ({
                              ...current,
                              knowledge_embedding_model: model,
                              knowledge_embedding_dimensions: inferEmbeddingDimensions(
                                model,
                                current.knowledge_embedding_dimensions
                              ),
                            }))
                          }
                        />
                      </label>
                      <div className="block text-sm">
                        <span className="mb-2 block text-[var(--ink-soft)]">{text.embeddingDimensions}</span>
                        <div className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3">
                          <p className="text-base font-semibold text-[var(--ink-strong)]">
                            {userSettings.knowledge_embedding_dimensions}
                          </p>
                          <p className="mt-1 text-xs leading-5 text-[var(--ink-muted)]">
                            {uiLanguage === "zh-CN"
                              ? "由当前 Embedding 模型自动确定。创建索引后如果更换模型或维度，需要重建索引。"
                              : "Derived from the selected embedding model. Rebuild indexes after changing the model or dimension."}
                          </p>
                        </div>
                      </div>
                      <div className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] p-4 text-sm xl:col-span-2">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <p className="font-semibold text-[var(--ink-strong)]">{text.embeddingApiKey}</p>
                            <p className="mt-1 text-xs leading-6 text-[var(--ink-soft)]">{text.knowledgeApiKeyHint}</p>
                            <p className="mt-1 text-xs text-[var(--ink-muted)]">
                              {userSettings.knowledge_embedding_has_api_key ? text.hasApiKey : text.noApiKey}
                              {userSettings.knowledge_embedding_api_key_masked
                                ? ` · ${text.currentKey}: ${userSettings.knowledge_embedding_api_key_masked}`
                                : ""}
                            </p>
                          </div>
                          <label className="flex items-center gap-2 text-xs text-[var(--ink-soft)]">
                            <input
                              type="checkbox"
                              checked={clearKnowledgeEmbeddingApiKey}
                              onChange={(event) => {
                                setClearKnowledgeEmbeddingApiKey(event.target.checked);
                                if (event.target.checked) {
                                  setKnowledgeEmbeddingApiKeyDraft("");
                                }
                              }}
                            />
                            {text.clearKey}
                          </label>
                        </div>
                        <input
                          type="password"
                          value={knowledgeEmbeddingApiKeyDraft}
                          onChange={(event) => {
                            setKnowledgeEmbeddingApiKeyDraft(event.target.value);
                            if (event.target.value.trim()) {
                              setClearKnowledgeEmbeddingApiKey(false);
                            }
                          }}
                          placeholder={userSettings.knowledge_embedding_api_key_masked ?? text.embeddingApiKey}
                          className="mt-3 w-full rounded-2xl border border-[var(--control-border)] bg-[var(--soft-bg)] px-4 py-3 text-sm text-[var(--ink-strong)] outline-none focus:border-[var(--accent-strong)]"
                        />
                      </div>
                    </div>
                    <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                      <p className="text-xs text-[var(--ink-muted)]">
                        {text.modelOptionsSource}: {knowledgeModelOptionsSource}
                      </p>
                      <button
                        type="button"
                        onClick={() => void handleRefreshKnowledgeModels("embedding")}
                        disabled={loadingKnowledgeModels === "embedding"}
                        className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] disabled:opacity-55"
                      >
                        {loadingKnowledgeModels === "embedding" ? text.loading : text.refreshModelOptions}
                      </button>
                    </div>
                  </div>

                  <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-[var(--ink-strong)]">Rerank</p>
                        <p className="mt-1 text-xs leading-6 text-[var(--ink-muted)]">
                          {uiLanguage === "zh-CN"
                            ? "用于对召回片段二次排序。关闭后会直接使用向量召回结果。"
                            : "Used to re-rank retrieved chunks. If disabled, vector retrieval results are used directly."}
                        </p>
                      </div>
                      <label className="flex items-center gap-2 text-xs text-[var(--ink-soft)]">
                        <input
                          type="checkbox"
                          checked={Boolean(userSettings.knowledge_rerank_enabled)}
                          onChange={(event) =>
                            setUserSettings((current) => ({
                              ...current,
                              knowledge_rerank_enabled: event.target.checked,
                            }))
                          }
                        />
                        {text.rerankEnabled}
                      </label>
                    </div>
                    <div className="grid gap-4 xl:grid-cols-2">
                      <label className="block text-sm">
                        <span className="mb-2 block text-[var(--ink-soft)]">{text.rerankProvider}</span>
                        <select
                          value={userSettings.knowledge_rerank_provider}
                          onChange={(event) =>
                            setUserSettings((current) => ({
                              ...current,
                              knowledge_rerank_provider: event.target.value,
                              knowledge_rerank_base_url: knowledgeProviderDefaultBaseUrl(event.target.value),
                            }))
                          }
                          className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                        >
                          <option value="siliconflow">siliconflow</option>
                          <option value="openai-compatible">openai-compatible</option>
                          <option value="ollama">ollama</option>
                        </select>
                      </label>
                      <label className="block text-sm">
                        <span className="mb-2 block text-[var(--ink-soft)]">{text.rerankBaseUrl}</span>
                        <input
                          value={userSettings.knowledge_rerank_base_url}
                          onChange={(event) =>
                            setUserSettings((current) => ({
                              ...current,
                              knowledge_rerank_base_url: event.target.value,
                            }))
                          }
                          className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                        />
                      </label>
                      <label className="block text-sm xl:col-span-2">
                        <span className="mb-2 block text-[var(--ink-soft)]">{text.rerankModel}</span>
                        <ModelPicker
                          value={userSettings.knowledge_rerank_model}
                          options={rerankModelOptions}
                          placeholder={uiLanguage === "zh-CN" ? "搜索或输入 Rerank 模型" : "Search or enter rerank model"}
                          onChange={(model) =>
                            setUserSettings((current) => ({
                              ...current,
                              knowledge_rerank_model: model,
                            }))
                          }
                        />
                      </label>
                      <div className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] p-4 text-sm xl:col-span-2">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <p className="font-semibold text-[var(--ink-strong)]">{text.rerankApiKey}</p>
                            <p className="mt-1 text-xs leading-6 text-[var(--ink-soft)]">{text.knowledgeApiKeyHint}</p>
                            <p className="mt-1 text-xs text-[var(--ink-muted)]">
                              {userSettings.knowledge_rerank_has_api_key ? text.hasApiKey : text.noApiKey}
                              {userSettings.knowledge_rerank_api_key_masked
                                ? ` · ${text.currentKey}: ${userSettings.knowledge_rerank_api_key_masked}`
                                : ""}
                            </p>
                          </div>
                          <label className="flex items-center gap-2 text-xs text-[var(--ink-soft)]">
                            <input
                              type="checkbox"
                              checked={clearKnowledgeRerankApiKey}
                              onChange={(event) => {
                                setClearKnowledgeRerankApiKey(event.target.checked);
                                if (event.target.checked) {
                                  setKnowledgeRerankApiKeyDraft("");
                                }
                              }}
                            />
                            {text.clearKey}
                          </label>
                        </div>
                        <input
                          type="password"
                          value={knowledgeRerankApiKeyDraft}
                          onChange={(event) => {
                            setKnowledgeRerankApiKeyDraft(event.target.value);
                            if (event.target.value.trim()) {
                              setClearKnowledgeRerankApiKey(false);
                            }
                          }}
                          placeholder={userSettings.knowledge_rerank_api_key_masked ?? text.rerankApiKey}
                          className="mt-3 w-full rounded-2xl border border-[var(--control-border)] bg-[var(--soft-bg)] px-4 py-3 text-sm text-[var(--ink-strong)] outline-none focus:border-[var(--accent-strong)]"
                        />
                      </div>
                    </div>
                    <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                      <p className="text-xs text-[var(--ink-muted)]">
                        {text.modelOptionsSource}: {knowledgeModelOptionsSource}
                      </p>
                      <button
                        type="button"
                        onClick={() => void handleRefreshKnowledgeModels("rerank")}
                        disabled={loadingKnowledgeModels === "rerank"}
                        className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] disabled:opacity-55"
                      >
                        {loadingKnowledgeModels === "rerank" ? text.loading : text.refreshModelOptions}
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}

              {activeTab === "generation" ? (
                <div className="grid gap-4 xl:grid-cols-3">
                  <label className="block text-sm">
                    <span className="mb-2 block text-[var(--ink-soft)]">{text.temperature}</span>
                    <input
                      type="number"
                      step="0.1"
                      min="0"
                      max="2"
                      value={userSettings.temperature}
                      onChange={(event) =>
                        setUserSettings((current) => ({
                          ...current,
                          temperature: Number(event.target.value),
                        }))
                      }
                      className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                    />
                  </label>
                  <label className="block text-sm">
                    <span className="mb-2 block text-[var(--ink-soft)]">{text.topP}</span>
                    <input
                      type="number"
                      step="0.05"
                      min="0"
                      max="1"
                      value={userSettings.top_p}
                      onChange={(event) =>
                        setUserSettings((current) => ({
                          ...current,
                          top_p: Number(event.target.value),
                        }))
                      }
                      className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                    />
                  </label>
                  <label className="block text-sm">
                    <span className="mb-2 block text-[var(--ink-soft)]">{text.maxTokens}</span>
                    <input
                      type="number"
                      min="1"
                      value={userSettings.max_tokens ?? ""}
                      onChange={(event) =>
                        setUserSettings((current) => ({
                          ...current,
                          max_tokens: event.target.value ? Number(event.target.value) : null,
                        }))
                      }
                      className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                    />
                  </label>
                </div>
              ) : null}

              {activeTab === "context" ? (
                <div className="grid gap-4 xl:grid-cols-2">
                  <label className="block text-sm">
                    <span className="mb-2 block text-[var(--ink-soft)]">{text.modelContextWindow}</span>
                    <input
                      type="number"
                      min="8192"
                      max="262144"
                      value={userSettings.model_context_window}
                      onChange={(event) =>
                        setUserSettings((current) => ({
                          ...current,
                          model_context_window: Number(event.target.value),
                        }))
                      }
                      className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                    />
                  </label>
                  <label className="block text-sm">
                    <span className="mb-2 block text-[var(--ink-soft)]">{text.contextMode}</span>
                    <select
                      value={userSettings.context_mode}
                      onChange={(event) =>
                        setUserSettings((current) => ({
                          ...current,
                          context_mode: event.target.value,
                        }))
                      }
                      className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                    >
                      <option value="conservative">conservative</option>
                      <option value="balanced">balanced</option>
                      <option value="long-context">long-context</option>
                    </select>
                  </label>
                </div>
              ) : null}

              {activeTab === "memory" ? (
                <div className="space-y-4">
                  <div className="grid gap-4 xl:grid-cols-2">
                    <label className="flex items-center gap-3 rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-4 py-3 text-sm">
                      <input
                        type="checkbox"
                        checked={Boolean(userSettings.memory_enabled)}
                        onChange={(event) =>
                          setUserSettings((current) => ({
                            ...current,
                            memory_enabled: event.target.checked,
                          }))
                        }
                      />
                      {text.memoryEnabled}
                    </label>
                    <label className="block text-sm">
                      <span className="mb-2 block text-[var(--ink-soft)]">{text.memoryMaxChars}</span>
                      <input
                        type="number"
                        min="500"
                        max="20000"
                        value={userSettings.memory_max_chars}
                        onChange={(event) =>
                          setUserSettings((current) => ({
                            ...current,
                            memory_max_chars: Number(event.target.value),
                          }))
                        }
                        className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                      />
                    </label>
                    <label className="flex items-center gap-3 rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-4 py-3 text-sm">
                      <input
                        type="checkbox"
                        checked={Boolean(userSettings.memory_auto_candidate_enabled)}
                        onChange={(event) =>
                          setUserSettings((current) => ({
                            ...current,
                            memory_auto_candidate_enabled: event.target.checked,
                          }))
                        }
                      />
                      {uiLanguage === "zh-CN" ? "自动生成待审核记忆候选" : "Auto-create reviewable memory candidates"}
                    </label>
                    <label className="block text-sm">
                      <span className="mb-2 block text-[var(--ink-soft)]">
                        {uiLanguage === "zh-CN" ? "每多少轮生成一次候选" : "Candidate interval (turns)"}
                      </span>
                      <input
                        type="number"
                        min="1"
                        max="50"
                        value={userSettings.memory_auto_candidate_turn_interval}
                        onChange={(event) =>
                          setUserSettings((current) => ({
                            ...current,
                            memory_auto_candidate_turn_interval: Number(event.target.value),
                          }))
                        }
                        className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                      />
                    </label>
                  </div>
                  <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                    <div className="mb-3 flex items-center justify-between">
                      <p className="text-sm font-semibold text-[var(--ink-strong)]">{text.memory}</p>
                      <span className="rounded-full bg-[var(--control-bg)] px-3 py-1 text-xs text-[var(--ink-soft)]">
                        {text.memoriesCount}: {memories.length}
                      </span>
                    </div>
                    {memories.length > 0 ? (
                      <div className="grid gap-3">
                        {memories.map((memory) => (
                          <div key={memory.id} className="rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-4 py-3">
                            <div className="flex items-center justify-between gap-3">
                              <div>
                                <p className="text-sm font-medium text-[var(--ink-strong)]">{memory.title}</p>
                                <p className="mt-1 text-xs text-[var(--ink-muted)]">{memory.memory_type}</p>
                              </div>
                              <span className="rounded-full bg-[var(--soft-bg)] px-3 py-1 text-xs text-[var(--ink-soft)]">
                                {memory.status || (memory.is_enabled ? "active" : "disabled")}
                              </span>
                            </div>
                            <p className="mt-2 text-xs leading-6 text-[var(--ink-soft)]">{memory.content}</p>
                            {memory.candidate_reason ? (
                              <p className="mt-2 text-xs text-[var(--ink-muted)]">
                                {memory.risk_level}: {memory.candidate_reason}
                              </p>
                            ) : null}
                            {memory.status === "pending" ? (
                              <div className="mt-3 flex flex-wrap gap-2">
                                <button
                                  type="button"
                                  disabled={reviewingMemoryId === memory.id}
                                  onClick={() => void reviewMemory(memory, "approve")}
                                  className="rounded-full bg-[var(--accent-strong)] px-3 py-1 text-xs text-white disabled:opacity-50"
                                >
                                  {uiLanguage === "zh-CN" ? "确认启用" : "Approve"}
                                </button>
                                <button
                                  type="button"
                                  disabled={reviewingMemoryId === memory.id}
                                  onClick={() => void reviewMemory(memory, "reject")}
                                  className="rounded-full border border-[var(--hairline)] px-3 py-1 text-xs disabled:opacity-50"
                                >
                                  {uiLanguage === "zh-CN" ? "拒绝" : "Reject"}
                                </button>
                              </div>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-[var(--ink-soft)]">{text.noMemories}</p>
                    )}
                  </div>
                </div>
              ) : null}

              {activeTab === "system" ? (
                <label className="block text-sm">
                  <span className="mb-2 block text-[var(--ink-soft)]">{text.systemPrompt}</span>
                  <textarea
                    value={userSettings.system_prompt ?? ""}
                    onChange={(event) =>
                      setUserSettings((current) => ({
                        ...current,
                        system_prompt: event.target.value,
                      }))
                    }
                    className="min-h-[280px] w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                  />
                </label>
              ) : null}

              {activeTab === "tools" ? (
                <div className="space-y-4">
                  <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                    <p className="text-sm leading-6 text-[var(--ink-soft)]">{text.toolsHint}</p>
                  </div>
                  <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-[var(--ink-strong)]">{text.skills}</p>
                        <p className="mt-1 text-xs leading-5 text-[var(--ink-soft)]">{text.skillsHint}</p>
                      </div>
                      <span className="rounded-full bg-[var(--control-bg)] px-3 py-1 text-xs text-[var(--ink-soft)]">
                        {(toolSettings?.skills ?? []).filter((skill) => skill.is_enabled).length}/
                        {(toolSettings?.skills ?? []).length}
                      </span>
                    </div>
                    <div className="grid gap-2">
                      {(toolSettings?.skills ?? []).map((skill) => {
                        const missingCapabilities = skill.missing_tool_keys.length > 0;
                        return (
                          <div
                            key={skill.skill_key}
                            className="flex flex-wrap items-start justify-between gap-3 rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-4 py-3"
                          >
                            <div className="min-w-0 flex-1">
                              <p className="text-sm font-medium text-[var(--ink-strong)]">
                                {skill.display_name} · v{skill.version}
                              </p>
                              <p className="mt-1 text-xs leading-5 text-[var(--ink-soft)]">{skill.description}</p>
                              <p className="mt-2 break-words text-[10px] leading-4 text-[var(--ink-muted)]">
                                {text.requiredTools}: {skill.required_tool_keys.join(", ") || "--"}
                              </p>
                              {missingCapabilities ? (
                                <p className="mt-1 break-words text-[10px] leading-4 text-[var(--danger-text)]">
                                  {text.missingTools}: {skill.missing_tool_keys.join(", ")}
                                </p>
                              ) : null}
                            </div>
                            <label className="flex shrink-0 items-center gap-2 text-xs text-[var(--ink-soft)]">
                              <input
                                type="checkbox"
                                checked={skill.is_enabled}
                                disabled={
                                  (missingCapabilities && !skill.is_enabled) ||
                                  updatingSkillKey === skill.skill_key
                                }
                                onChange={(event) =>
                                  void handleToggleSkill(skill.skill_key, event.target.checked)
                                }
                              />
                              {skill.is_enabled ? text.enabled : text.disabled}
                            </label>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                  <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <p className="text-sm font-semibold text-[var(--ink-strong)]">{text.mcpServers}</p>
                      <button
                        type="button"
                        onClick={() => setIsMcpServerDialogOpen(true)}
                        className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
                      >
                        {text.addMcpServer} · {(toolSettings?.mcp_servers ?? []).length}
                      </button>
                    </div>
                    <div className="mt-4 grid gap-3">
                      {(toolSettings?.mcp_servers ?? []).length === 0 ? (
                        <p className="text-sm text-[var(--ink-soft)]">{text.noMcpServers}</p>
                      ) : (
                        (toolSettings?.mcp_servers ?? []).map((server) => (
                          <div key={server.id} className="rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-4 py-3">
                            <div className="flex flex-wrap items-start justify-between gap-3">
                              <div>
                                <p className="text-sm font-medium text-[var(--ink-strong)]">
                                  {server.name} · {server.server_key}
                                </p>
                                <p className="mt-1 break-all text-xs text-[var(--ink-muted)]">{server.url}</p>
                                <p className="mt-1 text-xs text-[var(--ink-soft)]">
                                  auth={server.auth_type} · credential={server.credential_provider ?? server.server_key}
                                  {server.last_error ? ` · error=${server.last_error}` : ""}
                                </p>
                                <p className="mt-1 text-[10px] text-[var(--ink-muted)]">
                                  {server.project_id ? text.mcpServerScopeWorkspace : text.mcpServerScopeUser}
                                </p>
                              </div>
                              <div className="flex flex-wrap gap-2">
                                <button
                                  type="button"
                                  onClick={() => void handleTestMcpServer(server.id)}
                                  disabled={testingMcpServerId === server.id}
                                  className="rounded-full border border-[var(--control-border)] bg-[var(--soft-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] disabled:opacity-55"
                                >
                                  {testingMcpServerId === server.id ? text.testing : text.testTool}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => void handleSyncMcpServer(server.id)}
                                  disabled={syncingMcpServerId === server.id}
                                  className="rounded-full border border-[var(--control-border)] bg-[var(--soft-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] disabled:opacity-55"
                                >
                                  {syncingMcpServerId === server.id ? text.loading : text.syncMcpTools}
                                </button>
                              </div>
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                  <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <p className="text-sm font-semibold text-[var(--ink-strong)]">{text.mcpTools}</p>
                      <span className="rounded-full bg-[var(--control-bg)] px-3 py-1 text-xs text-[var(--ink-soft)]">
                        {(toolSettings?.mcp_tools ?? []).length}
                      </span>
                    </div>
                    {(toolSettings?.mcp_tools ?? []).length === 0 ? (
                      <p className="text-sm text-[var(--ink-soft)]">{text.noMcpTools}</p>
                    ) : (
                      <div className="grid gap-2">
                        {(toolSettings?.mcp_tools ?? []).map((tool) => (
                          <div
                            key={tool.id}
                            className="rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-3 py-3 text-sm"
                          >
                            <div className="flex items-start justify-between gap-3">
                              <button
                                type="button"
                                onClick={() => setExpandedMcpToolId((current) => (current === tool.id ? null : tool.id))}
                                className="min-w-0 text-left"
                              >
                                <span className="block font-medium text-[var(--ink-strong)]">
                                  {tool.display_name} · {tool.server_key}
                                </span>
                                <span className="mt-1 block break-all text-xs text-[var(--ink-muted)]">{tool.tool_key}</span>
                                <span className="mt-1 block text-xs leading-5 text-[var(--ink-soft)]">
                                  {tool.description_override || tool.description || tool.raw_name}
                                </span>
                                <span className="mt-1 flex flex-wrap gap-2 text-[10px] text-[var(--ink-muted)]">
                                  <span className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5">category={tool.category}</span>
                                  <span className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5">risk={tool.risk_level}</span>
                                  <span className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5">read_only={String(tool.read_only)}</span>
                                  <span className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5">
                                    remote_hint={String(tool.remote_read_only_hint)}
                                  </span>
                                  <span className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5">
                                    reviewed={String(tool.risk_reviewed)}
                                  </span>
                                </span>
                              </button>
                              <div className="flex shrink-0 flex-col items-end gap-2">
                                <label className="flex items-center gap-2 text-xs text-[var(--ink-soft)]">
                                  <input
                                    type="checkbox"
                                    checked={tool.is_enabled}
                                    disabled={!tool.risk_reviewed || !tool.read_only || tool.risk_level === "high"}
                                    onChange={(event) =>
                                      void handleUpdateMcpTool(tool.id, { is_enabled: event.target.checked })
                                    }
                                  />
                                  {tool.is_enabled ? text.enabled : text.disabled}
                                </label>
                                {!tool.risk_reviewed && tool.remote_read_only_hint === true ? (
                                  <button
                                    type="button"
                                    onClick={() =>
                                      void handleUpdateMcpTool(tool.id, {
                                        read_only: true,
                                        risk_level: "low",
                                        risk_reviewed: true,
                                      })
                                    }
                                    className="rounded-full border border-[var(--control-border)] bg-[var(--soft-bg)] px-3 py-1.5 text-[10px] text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
                                  >
                                    {uiLanguage === "zh-CN" ? "审核并确认为只读" : "Review as read-only"}
                                  </button>
                                ) : null}
                                {!tool.risk_reviewed && tool.remote_read_only_hint !== true ? (
                                  <span className="max-w-40 text-right text-[10px] leading-4 text-[var(--danger-text)]">
                                    {uiLanguage === "zh-CN"
                                      ? "远端未声明只读，当前版本保持阻断"
                                      : "Not declared read-only; blocked in this version"}
                                  </span>
                                ) : null}
                              </div>
                            </div>
                            {expandedMcpToolId === tool.id ? (
                              <div className="mt-4 grid gap-3 border-t border-[var(--hairline)] pt-4">
                                <div className="grid gap-3 md:grid-cols-[1fr_180px_auto]">
                                  <label className="grid gap-1 text-xs text-[var(--ink-soft)]">
                                    {text.toolDescription}
                                    <input
                                      value={mcpToolDrafts[tool.id]?.description ?? ""}
                                      onChange={(event) =>
                                        setMcpToolDrafts((current) => ({
                                          ...current,
                                          [tool.id]: {
                                            description: event.target.value,
                                            category: current[tool.id]?.category ?? tool.category,
                                          },
                                        }))
                                      }
                                      className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm text-[var(--ink-strong)] outline-none focus:border-[var(--accent-strong)]"
                                    />
                                  </label>
                                  <label className="grid gap-1 text-xs text-[var(--ink-soft)]">
                                    {text.toolCategory}
                                    <input
                                      value={mcpToolDrafts[tool.id]?.category ?? tool.category}
                                      onChange={(event) =>
                                        setMcpToolDrafts((current) => ({
                                          ...current,
                                          [tool.id]: {
                                            description: current[tool.id]?.description ?? tool.description_override ?? tool.description ?? "",
                                            category: event.target.value,
                                          },
                                        }))
                                      }
                                      className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm text-[var(--ink-strong)] outline-none focus:border-[var(--accent-strong)]"
                                    />
                                  </label>
                                  <button
                                    type="button"
                                    onClick={() => void handleSaveMcpToolMeta(tool.id)}
                                    className="self-end rounded-full border border-[var(--control-border)] bg-[var(--soft-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
                                  >
                                    {text.save}
                                  </button>
                                </div>
                                <details className="rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-3 py-2">
                                  <summary className="cursor-pointer text-xs font-medium text-[var(--ink-strong)]">
                                    {text.schema} · input
                                  </summary>
                                  <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-xl bg-[var(--control-bg)] p-3 text-xs leading-5 text-[var(--ink-soft)]">
                                    {JSON.stringify(tool.input_schema ?? {}, null, 2)}
                                  </pre>
                                </details>
                                {Object.keys(tool.output_schema ?? {}).length > 0 ? (
                                  <details className="rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-3 py-2">
                                    <summary className="cursor-pointer text-xs font-medium text-[var(--ink-strong)]">
                                      {text.schema} · output
                                    </summary>
                                    <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-xl bg-[var(--control-bg)] p-3 text-xs leading-5 text-[var(--ink-soft)]">
                                      {JSON.stringify(tool.output_schema, null, 2)}
                                    </pre>
                                  </details>
                                ) : null}
                                <div className="grid gap-2">
                                  <label className="grid gap-1 text-xs text-[var(--ink-soft)]">
                                    {text.testArguments}
                                    <textarea
                                      value={mcpToolTestArgs[tool.id] ?? "{}"}
                                      onChange={(event) =>
                                        setMcpToolTestArgs((current) => ({ ...current, [tool.id]: event.target.value }))
                                      }
                                      className="min-h-28 rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 font-mono text-xs text-[var(--ink-strong)] outline-none focus:border-[var(--accent-strong)]"
                                    />
                                  </label>
                                  <div className="flex justify-end">
                                    <button
                                      type="button"
                                      onClick={() => void handleTestMcpTool(tool.id)}
                                      disabled={testingMcpToolId === tool.id}
                                      className="rounded-full border border-[var(--control-border)] bg-[var(--soft-bg)] px-4 py-2 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] disabled:opacity-55"
                                    >
                                      {testingMcpToolId === tool.id ? text.testing : text.testTool}
                                    </button>
                                  </div>
                                  {mcpToolTestResult[tool.id] ? (
                                    <details className="rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-3 py-2" open>
                                      <summary className="cursor-pointer text-xs font-medium text-[var(--ink-strong)]">
                                        {text.testResult}
                                      </summary>
                                      <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-xl bg-[var(--control-bg)] p-3 text-xs leading-5 text-[var(--ink-soft)]">
                                        {mcpToolTestResult[tool.id]}
                                      </pre>
                                    </details>
                                  ) : null}
                                </div>
                              </div>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  {toolProviders.map((providerKey) => {
                    const credential = credentialByProvider[providerKey];
                    return (
                      <div key={providerKey} className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <p className="text-sm font-semibold text-[var(--ink-strong)]">
                              {text.toolCredential} · {providerKey}
                            </p>
                            <p className="mt-1 text-xs text-[var(--ink-muted)]">
                              {text.credentialSource}: {credential?.source ?? "missing"}
                              {credential?.api_key_masked ? ` · ${text.currentKey}: ${credential.api_key_masked}` : ""}
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
                            placeholder={credential?.api_key_masked ?? text.apiKey}
                            onChange={(event) =>
                              {
                                setToolCredentialDrafts((current) => ({
                                  ...current,
                                  [providerKey]: event.target.value,
                                }));
                                setClearToolApiKeys((current) => ({
                                  ...current,
                                  [providerKey]: false,
                                }));
                              }
                            }
                            className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                          />
                          <div className="flex gap-2">
                            <label className="flex items-center gap-2 rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-xs text-[var(--ink-soft)]">
                              <input
                                type="checkbox"
                                checked={clearToolApiKeys[providerKey] ?? false}
                                onChange={(event) => {
                                  setClearToolApiKeys((current) => ({
                                    ...current,
                                    [providerKey]: event.target.checked,
                                  }));
                                  if (event.target.checked) {
                                    setToolCredentialDrafts((current) => ({
                                      ...current,
                                      [providerKey]: "",
                                    }));
                                  }
                                }}
                              />
                              {text.clearKey}
                            </label>
                            <button
                              type="button"
                              onClick={() => void handleTestToolProvider(providerKey)}
                              disabled={testingToolProvider === providerKey}
                              className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] disabled:opacity-55"
                            >
                              {testingToolProvider === providerKey ? text.testing : text.testTool}
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                  <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <p className="text-sm font-semibold text-[var(--ink-strong)]">{text.workspaceToolOverrides}</p>
                      <select
                        value={selectedProjectId}
                        onChange={(event) => setSelectedProjectId(event.target.value)}
                        className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-xs outline-none focus:border-[var(--accent-strong)]"
                      >
                        {projects.map((project) => (
                          <option key={project.id} value={project.id}>
                            {project.name}
                          </option>
                        ))}
                      </select>
                    </div>
                    {selectedProject ? (
                      <fieldset className="mb-4 border-0 p-0">
                        <legend className="mb-2 text-xs font-semibold text-[var(--ink-strong)]">
                          {text.workspacePermissionMode}
                        </legend>
                        <div className="grid grid-cols-1 gap-1 rounded-lg border border-[var(--control-border)] bg-[var(--control-bg)] p-1 sm:grid-cols-3">
                          {(
                            [
                              ["read_only", text.permissionReadOnly, text.permissionReadOnlyHint],
                              ["ask", text.permissionAsk, text.permissionAskHint],
                              [
                                "full_workspace",
                                text.permissionFullWorkspace,
                                text.permissionFullWorkspaceHint,
                              ],
                            ] as const
                          ).map(([mode, label, hint]) => (
                            <button
                              key={mode}
                              type="button"
                              onClick={() => setWorkspacePermissionMode(mode)}
                              className={`min-h-16 px-3 py-2 text-left text-xs transition ${
                                workspacePermissionMode === mode
                                  ? "bg-[var(--accent-strong)] text-white"
                                  : "text-[var(--ink-soft)] hover:bg-[var(--soft-bg)]"
                              }`}
                              title={hint}
                            >
                              <span className="block font-semibold">{label}</span>
                              <span className="mt-1 block leading-4 opacity-80">{hint}</span>
                            </button>
                          ))}
                        </div>
                        <p className="mt-2 text-xs leading-5 text-[var(--ink-soft)]">
                          {text.permissionBoundaryHint}
                        </p>
                      </fieldset>
                    ) : null}
                    {selectedProject ? (
                      <div className="grid gap-2">
                        {(toolSettings?.tools ?? []).map((tool) => (
                          <label
                            key={tool.tool_key}
                            className="flex items-start justify-between gap-3 rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-3 py-3 text-sm"
                          >
                            <span>
                              <span className="block font-medium text-[var(--ink-strong)]">{tool.display_name}</span>
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
                    ) : (
                      <p className="text-sm text-[var(--ink-soft)]">{text.noWorkspaceSelected}</p>
                    )}
                  </div>
                  <div className="flex justify-end">
                    <button
                      type="button"
                      onClick={() => void handleSaveToolSettings()}
                      disabled={isSavingToolSettings}
                      className="rounded-full bg-[var(--accent-strong)] px-5 py-2 text-sm text-white transition hover:brightness-105 disabled:opacity-55"
                    >
                      {isSavingToolSettings ? text.saving : text.saveToolSettings}
                    </button>
                  </div>
                </div>
              ) : null}

              {activeTab === "workspace" ? (
                <div className="space-y-4">
                  <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <p className="text-sm font-semibold text-[var(--ink-strong)]">{text.activeWorkspaceDefaults}</p>
                      <select
                        value={selectedProjectId}
                        onChange={(event) => setSelectedProjectId(event.target.value)}
                        className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-xs outline-none focus:border-[var(--accent-strong)]"
                      >
                        {projects.map((project) => (
                          <option key={project.id} value={project.id}>
                            {project.name}
                          </option>
                        ))}
                      </select>
                    </div>
                    {selectedProject ? (
                      <div className="grid gap-4 xl:grid-cols-2">
                        <label className="block text-sm">
                          <span className="mb-2 block text-[var(--ink-soft)]">{text.defaultModel}</span>
                          <ModelPicker
                            value={selectedProject.default_model ?? ""}
                            options={selectedProjectModelOptions}
                            placeholder={uiLanguage === "zh-CN" ? "搜索或输入模型名" : "Search or enter model name"}
                            emptyLabel={uiLanguage === "zh-CN" ? "不设置默认模型" : "No default model"}
                            onChange={(model) =>
                              setProjects((current) =>
                                current.map((project) =>
                                  project.id === selectedProject.id
                                    ? { ...project, default_model: model || null }
                                    : project
                                )
                              )
                            }
                          />
                        </label>
                        <label className="block text-sm xl:col-span-2">
                          <span className="mb-2 block text-[var(--ink-soft)]">{text.systemPrompt}</span>
                          <textarea
                            value={selectedProject.system_prompt ?? ""}
                            onChange={(event) =>
                              setProjects((current) =>
                                current.map((project) =>
                                  project.id === selectedProject.id
                                    ? { ...project, system_prompt: event.target.value || null }
                                    : project
                                )
                              )
                            }
                            className="min-h-[220px] w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                          />
                        </label>
                      </div>
                    ) : (
                      <p className="text-sm text-[var(--ink-soft)]">{text.noProjects}</p>
                    )}
                    {selectedProject ? (
                      <div className="mt-4 flex justify-end">
                        <button
                          type="button"
                          onClick={() => void handleSaveWorkspaceSettings()}
                          disabled={isSavingWorkspace}
                          className="rounded-full bg-[var(--accent-strong)] px-5 py-2 text-sm text-white transition hover:brightness-105 disabled:opacity-55"
                        >
                          {isSavingWorkspace ? text.saving : text.save}
                        </button>
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}

              {activeTab === "templates" ? (
                <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                  <div className="mb-3 flex items-center justify-between">
                    <p className="text-sm font-semibold text-[var(--ink-strong)]">{text.templates}</p>
                    <span className="rounded-full bg-[var(--control-bg)] px-3 py-1 text-xs text-[var(--ink-soft)]">
                      {text.templatesCount}: {promptTemplates.length}
                    </span>
                  </div>
                  {promptTemplates.length > 0 ? (
                    <div className="grid gap-3">
                      {promptTemplates.map((template) => (
                        <div key={template.id} className="rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-4 py-3">
                          <p className="text-sm font-medium text-[var(--ink-strong)]">{template.name}</p>
                          <p className="mt-1 text-xs text-[var(--ink-muted)]">
                            {template.category || "uncategorized"}
                          </p>
                          {template.description ? (
                            <p className="mt-2 text-xs leading-6 text-[var(--ink-soft)]">{template.description}</p>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-[var(--ink-soft)]">{text.noTemplates}</p>
                  )}
                </div>
              ) : null}

              {activeTab === "appearance" ? (
                <div className="grid gap-4 xl:grid-cols-2">
                  <label className="block text-sm">
                    <span className="mb-2 block text-[var(--ink-soft)]">{text.uiLanguage}</span>
                    <select
                      value={userSettings.ui_language}
                      onChange={(event) =>
                        setUserSettings((current) => ({
                          ...current,
                          ui_language: event.target.value,
                        }))
                      }
                      className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                    >
                      <option value="zh-CN">中文</option>
                      <option value="en-US">English</option>
                    </select>
                  </label>
                  <label className="block text-sm">
                    <span className="mb-2 block text-[var(--ink-soft)]">{text.themeMode}</span>
                    <select
                      value={userSettings.theme_mode}
                      onChange={(event) =>
                        setUserSettings((current) => ({
                          ...current,
                          theme_mode: event.target.value,
                        }))
                      }
                      className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                    >
                      <option value="system">{text.themeSystem}</option>
                      <option value="light">{text.themeLight}</option>
                      <option value="dark">{text.themeDark}</option>
                    </select>
                  </label>
                </div>
              ) : null}

              {activeTab === "privacy" ? (
                <div className="grid gap-4 xl:grid-cols-3">
                  <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4 text-sm text-[var(--ink-soft)]">
                    {text.projectsCount}: {projects.length}
                  </div>
                  <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4 text-sm text-[var(--ink-soft)]">
                    {text.memoriesCount}: {memories.length}
                  </div>
                  <div className="rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4 text-sm text-[var(--ink-soft)]">
                    {text.templatesCount}: {promptTemplates.length}
                  </div>
                </div>
              ) : null}

              <div className="flex justify-end gap-3 border-t border-[var(--hairline)] pt-4">
                <button
                  type="submit"
                  disabled={isSavingSettings}
                  className="rounded-full bg-[var(--accent-strong)] px-5 py-2 text-sm text-white transition hover:brightness-105 disabled:opacity-55"
                >
                  {isSavingSettings ? text.saving : text.save}
                </button>
              </div>
            </form>
          </section>
        </div>
      </div>
      {isMcpServerDialogOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 px-4 backdrop-blur-sm">
          <div className="w-full max-w-2xl rounded-[28px] border border-[var(--hairline)] bg-[var(--panel-bg)] p-5 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-lg font-semibold text-[var(--ink-strong)]">{text.addMcpServerTitle}</h2>
                <p className="mt-1 text-sm leading-6 text-[var(--ink-soft)]">{text.mcpServerDialogHint}</p>
              </div>
              <button
                type="button"
                onClick={() => setIsMcpServerDialogOpen(false)}
                className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)]"
              >
                {text.close}
              </button>
            </div>
            <div className="mt-5 grid gap-3 sm:grid-cols-2">
              <div className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm text-[var(--ink-soft)] sm:col-span-2">
                <p className="font-medium text-[var(--ink-strong)]">
                  {selectedProject ? text.mcpServerScopeWorkspace : text.mcpServerScopeUser}
                </p>
                <p className="mt-1 text-xs leading-5 text-[var(--ink-muted)]">{text.mcpServerScopeHint}</p>
              </div>
              <input
                value={mcpServerDraft.server_key}
                onChange={(event) =>
                  setMcpServerDraft((current) => ({ ...current, server_key: event.target.value }))
                }
                placeholder={text.mcpServerKey}
                className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm outline-none focus:border-[var(--accent-strong)]"
              />
              <input
                value={mcpServerDraft.name}
                onChange={(event) =>
                  setMcpServerDraft((current) => ({ ...current, name: event.target.value }))
                }
                placeholder={text.mcpServerName}
                className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm outline-none focus:border-[var(--accent-strong)]"
              />
              <input
                value={mcpServerDraft.url}
                onChange={(event) =>
                  setMcpServerDraft((current) => ({ ...current, url: event.target.value }))
                }
                placeholder={text.mcpServerUrl}
                className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm outline-none focus:border-[var(--accent-strong)] sm:col-span-2"
              />
              <select
                value={mcpServerDraft.auth_type}
                onChange={(event) =>
                  setMcpServerDraft((current) => ({ ...current, auth_type: event.target.value }))
                }
                className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm outline-none focus:border-[var(--accent-strong)]"
              >
                <option value="none">none</option>
                <option value="api_key">api_key in URL</option>
                <option value="bearer">bearer</option>
                <option value="api_key_header">X-API-Key</option>
              </select>
              <input
                value={mcpServerDraft.credential_provider}
                onChange={(event) =>
                  setMcpServerDraft((current) => ({ ...current, credential_provider: event.target.value }))
                }
                placeholder={text.mcpCredentialProvider}
                className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm outline-none focus:border-[var(--accent-strong)]"
              />
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setIsMcpServerDialogOpen(false)}
                className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)]"
              >
                {text.cancel}
              </button>
              <button
                type="button"
                onClick={() => void handleCreateMcpServer()}
                disabled={isCreatingMcpServer}
                className="rounded-full bg-[var(--accent-strong)] px-5 py-2 text-sm text-white transition hover:brightness-105 disabled:opacity-55"
              >
                {isCreatingMcpServer ? text.saving : text.addMcpServer}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}

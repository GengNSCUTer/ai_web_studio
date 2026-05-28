"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import type {
  Project,
  PromptTemplate,
  ProviderInfo,
  ToolConnectionTestResult,
  ToolSettings,
  User,
  UserMemory,
  UserSettings,
} from "@/lib/types";

type SettingsTab =
  | "provider"
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
};

type UILanguage = "zh-CN" | "en-US";
type ThemeMode = "system" | "light" | "dark";

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
    providerSaved: "模型服务设置已保存",
    provider: "模型服务",
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
    baseUrl: "Base URL",
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
    noWorkspaceSelected: "当前未选择具体工作区，仅显示用户级凭证。",
    saveToolSettings: "保存工具设置",
    toolSettingsSaved: "工具设置已保存",
    toolSettingsFailed: "工具设置失败：",
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
    providerSaved: "Provider settings saved",
    provider: "Provider",
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
    baseUrl: "Base URL",
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
    noWorkspaceSelected: "No workspace selected. Showing user-level credentials only.",
    saveToolSettings: "Save tool settings",
    toolSettingsSaved: "Tool settings saved",
    toolSettingsFailed: "Tool settings failed: ",
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
  },
} as const;

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

function providerDefaults(providerType: string) {
  if (providerType === "ollama") {
    return {
      baseUrl: "http://127.0.0.1:11435",
      model: "qwen3.5:27b-q8_0",
      modelContextWindow: 100000,
    };
  }
  return {
    baseUrl: "https://api.siliconflow.cn/v1",
    model: "Qwen/Qwen3.5-35B-A3B",
    modelContextWindow: 128000,
  };
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

export function SettingsCenter({
  currentUser,
  initialSettings,
  initialProviderInfo,
  initialProjects,
  initialToolSettings,
  initialMemories = [],
  initialPromptTemplates = [],
}: SettingsCenterProps) {
  const [userSettings, setUserSettings] = useState<UserSettings>(normalizeUserSettings(initialSettings));
  const [providerInfo, setProviderInfo] = useState<ProviderInfo | null>(initialProviderInfo);
  const [projects, setProjects] = useState<Project[]>(initialProjects);
  const [toolSettings, setToolSettings] = useState<ToolSettings | null>(initialToolSettings);
  const [memories, setMemories] = useState<UserMemory[]>(initialMemories);
  const [promptTemplates, setPromptTemplates] = useState<PromptTemplate[]>(initialPromptTemplates);
  const [selectedProjectId, setSelectedProjectId] = useState<string>(initialProjects[0]?.id ?? "");
  const [activeTab, setActiveTab] = useState<SettingsTab>("provider");
  const [settingsMessage, setSettingsMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const [isTestingProvider, setIsTestingProvider] = useState(false);
  const [toolCredentialDrafts, setToolCredentialDrafts] = useState<Record<string, string>>({});
  const [toolEnabledDrafts, setToolEnabledDrafts] = useState<Record<string, boolean>>({});
  const [workspaceToolEnabledDrafts, setWorkspaceToolEnabledDrafts] = useState<Record<string, boolean>>({});
  const [providerApiKeyDraft, setProviderApiKeyDraft] = useState("");
  const [clearProviderApiKey, setClearProviderApiKey] = useState(false);
  const [clearToolApiKeys, setClearToolApiKeys] = useState<Record<string, boolean>>({});
  const [isSavingToolSettings, setIsSavingToolSettings] = useState(false);
  const [isSavingWorkspace, setIsSavingWorkspace] = useState(false);
  const [testingToolProvider, setTestingToolProvider] = useState<string | null>(null);
  const [systemPrefersDark, setSystemPrefersDark] = useState(false);

  const uiLanguage: UILanguage = userSettings.ui_language === "en-US" ? "en-US" : "zh-CN";
  const text = TEXT[uiLanguage];
  const selectedThemeMode = normalizeThemeMode(userSettings.theme_mode);
  const resolvedTheme = selectedThemeMode === "system" ? (systemPrefersDark ? "dark" : "light") : selectedThemeMode;
  const availableModels = providerInfo?.models ?? [];
  const selectedProject = projects.find((project) => project.id === selectedProjectId) ?? null;
  const toolProviders = Array.from(new Set((toolSettings?.tools ?? []).map((tool) => tool.provider)));
  const credentialByProvider = Object.fromEntries(
    (toolSettings?.credentials ?? []).map((credential) => [credential.provider_key, credential])
  );

  const tabs: Array<{ id: SettingsTab; label: string }> = [
    { id: "provider", label: text.provider },
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
  }, [toolSettings]);

  useEffect(() => {
    if (!selectedProjectId) {
      return;
    }
    void reloadToolSettings(selectedProjectId).catch(() => undefined);
  }, [selectedProjectId]);

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

  async function reloadToolSettings(projectId?: string) {
    const suffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
    const data = await requestJson<ToolSettings>(`/api/backend/tools/settings${suffix}`);
    setToolSettings(data);
    return data;
  }

  function applyProviderPreset(providerType: string) {
    const defaults = providerDefaults(providerType);
    setProviderInfo((current) =>
      current && current.provider === providerType
        ? current
        : {
            provider: providerType,
            base_url: defaults.baseUrl,
            default_model: defaults.model,
            models: current?.provider === providerType ? current.models : [],
          }
    );
    setUserSettings((current) => ({
      ...current,
      provider_type: providerType,
      ollama_base_url: defaults.baseUrl,
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
          provider_type: userSettings.provider_type,
          default_model: userSettings.default_model,
          ollama_base_url: userSettings.ollama_base_url,
          api_key: providerApiKeyDraft.trim() ? providerApiKeyDraft.trim() : undefined,
          clear_api_key: clearProviderApiKey,
          temperature: userSettings.temperature,
          top_p: userSettings.top_p,
          max_tokens: userSettings.max_tokens,
          system_prompt: userSettings.system_prompt,
          model_context_window: userSettings.model_context_window,
          context_mode: userSettings.context_mode,
          memory_enabled: userSettings.memory_enabled,
          memory_max_chars: userSettings.memory_max_chars,
          ui_language: userSettings.ui_language,
          theme_mode: userSettings.theme_mode,
        }),
      });
      setUserSettings(normalizeUserSettings(saved));
      setProviderApiKeyDraft("");
      setClearProviderApiKey(false);
      setSettingsMessage(text.settingsSaved);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setErrorMessage(`${text.settingsSaveFailed}${message}`);
    } finally {
      setIsSavingSettings(false);
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
            api_key:
              userSettings.provider_type === "openai-compatible" && providerApiKeyDraft.trim()
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
            <a
              href="/"
              className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
            >
              {text.backHome}
            </a>
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
                    </select>
                  </label>
                  <label className="block text-sm">
                    <span className="mb-2 block text-[var(--ink-soft)]">{text.baseUrl}</span>
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
                    <input
                      list="settings-models"
                      value={userSettings.default_model}
                      onChange={(event) =>
                        setUserSettings((current) => ({
                          ...current,
                          default_model: event.target.value,
                        }))
                      }
                      className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                    />
                    <datalist id="settings-models">
                      {availableModels.map((model) => (
                        <option key={model} value={model} />
                      ))}
                    </datalist>
                  </label>
                  {userSettings.provider_type === "openai-compatible" ? (
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
                                {memory.is_enabled ? "enabled" : "disabled"}
                              </span>
                            </div>
                            <p className="mt-2 text-xs leading-6 text-[var(--ink-soft)]">{memory.content}</p>
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
                          <input
                            list="workspace-models"
                            value={selectedProject.default_model ?? ""}
                            onChange={(event) =>
                              setProjects((current) =>
                                current.map((project) =>
                                  project.id === selectedProject.id
                                    ? { ...project, default_model: event.target.value || null }
                                    : project
                                )
                              )
                            }
                            className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                          />
                          <datalist id="workspace-models">
                            {selectedProjectModelOptions.map((model) => (
                              <option key={model} value={model} />
                            ))}
                          </datalist>
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
    </main>
  );
}

"use client";

import { FormEvent, useEffect, useRef, useState, useTransition } from "react";

import { ChatThread } from "@/components/chat-thread";
import type {
  Conversation,
  ContextGovernanceInfo,
  Message,
  ProviderConnectionTestResult,
  ProviderInfo,
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
};

type UILanguage = "zh-CN" | "en-US";
type SettingsTab = "provider" | "generation" | "context" | "memory" | "system";

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
    logout: "退出",
    currentProvider: "当前 Provider",
    providerLoading: "读取中...",
    providerBaseUrlLoading: "正在读取模型服务地址",
    historyChats: "历史会话",
    historyCountSuffix: "条",
    noConversations: "还没有历史会话，发送第一条消息后会自动创建。",
    menuLabel: "展开会话菜单",
    rename: "重命名",
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
    close: "关闭",
    provider: "Provider",
    defaultModel: "默认模型",
    baseUrl: "Base URL",
    apiKey: "API Key",
    uiLanguage: "界面语言",
    chinese: "中文",
    english: "English",
    providerHint: "先测试连接，再保存设置。测试会用当前表单里的 provider 配置实时请求。",
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
    historyCountSuffix: "",
    noConversations: "No conversation yet. Your first message will create one automatically.",
    menuLabel: "Open conversation menu",
    rename: "Rename",
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
    close: "Close",
    provider: "Provider",
    defaultModel: "Default model",
    baseUrl: "Base URL",
    apiKey: "API Key",
    uiLanguage: "Interface language",
    chinese: "Chinese",
    english: "English",
    providerHint:
      "Test the connection before saving. The test will use the current provider form values.",
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
  };
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
}: ChatAppProps) {
  const [currentUser, setCurrentUser] = useState<User | null>(initialUser);
  const [conversations, setConversations] = useState<Conversation[]>(initialConversations);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(
    initialConversations[0]?.id ?? null
  );
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [providerInfo, setProviderInfo] = useState<ProviderInfo | null>(initialProviderInfo);
  const [userSettings, setUserSettings] = useState<UserSettings | null>(
    initialSettings ? normalizeUserSettings(initialSettings) : null
  );
  const [contextInfo, setContextInfo] = useState<ContextGovernanceInfo | null>(null);
  const [selectedModel, setSelectedModel] = useState(
    initialSettings?.default_model ?? initialProviderInfo?.default_model ?? ""
  );
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const [isTestingProvider, setIsTestingProvider] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [activeSettingsTab, setActiveSettingsTab] = useState<SettingsTab>("provider");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [settingsMessage, setSettingsMessage] = useState<string | null>(null);
  const [settingsModels, setSettingsModels] = useState<string[]>(initialProviderInfo?.models ?? []);
  const [memories, setMemories] = useState<UserMemory[]>([]);
  const [memoryDraft, setMemoryDraft] = useState({
    memory_type: "fact",
    title: "",
    content: "",
  });
  const [isPending, startTransition] = useTransition();
  const [openConversationMenuId, setOpenConversationMenuId] = useState<string | null>(null);
  const conversationMenuRef = useRef<HTMLDivElement | null>(null);
  const uiLanguage: UILanguage = userSettings?.ui_language === "en-US" ? "en-US" : "zh-CN";
  const text = APP_TEXT[uiLanguage];
  const settingsTabs: Array<{ id: SettingsTab; label: string }> = [
    { id: "provider", label: text.settingsTabProvider },
    { id: "generation", label: text.settingsTabGeneration },
    { id: "context", label: text.settingsTabContext },
    { id: "memory", label: text.settingsTabMemory },
    { id: "system", label: text.settingsTabSystem },
  ];

  const availableModels = providerInfo?.models ?? [];
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
    if (!currentUser) {
      return;
    }
    void loadMemories().catch(() => undefined);
  }, [currentUser]);

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
      setContextInfo(null);
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

  function handleNewConversation() {
    setSelectedConversationId(null);
    setMessages([]);
    setContextInfo(null);
    setErrorMessage(null);
    setOpenConversationMenuId(null);
  }

  async function handleSelectConversation(conversationId: string) {
    setSelectedConversationId(conversationId);
    setMessages([]);
    setContextInfo(null);
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

  async function handleRenameConversation(conversationId = selectedConversationId) {
    if (!conversationId) {
      return;
    }

    const currentTitle =
      conversations.find((item) => item.id === conversationId)?.title ?? "";
    const nextTitle = window.prompt(text.renamePrompt, currentTitle);
    if (!nextTitle || !nextTitle.trim()) {
      return;
    }

    try {
      await requestJson<Conversation>(`/api/backend/conversations/${conversationId}`, {
        method: "PATCH",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          title: nextTitle.trim(),
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

  async function handleDeleteConversation(conversationId = selectedConversationId) {
    if (!conversationId) {
      return;
    }

    const confirmed = window.confirm(text.deleteConversationConfirm);
    if (!confirmed) {
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
    } catch (error) {
      const message = error instanceof Error ? error.message : text.unknownError;
      setErrorMessage(`${text.deleteConversationFailed}${message}`);
    }
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
        body: JSON.stringify({
          provider_type: userSettings.provider_type,
          default_model: userSettings.default_model,
          ollama_base_url: userSettings.ollama_base_url,
          api_key: userSettings.api_key,
          temperature: userSettings.temperature,
          top_p: userSettings.top_p,
          max_tokens: userSettings.max_tokens,
          system_prompt: userSettings.system_prompt,
          model_context_window: userSettings.model_context_window,
          context_mode: userSettings.context_mode,
          memory_enabled: userSettings.memory_enabled,
          memory_max_chars: userSettings.memory_max_chars,
          ui_language: userSettings.ui_language,
        }),
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
  const threadKey = `${selectedConversationId ?? "draft"}:${
    messages[messages.length - 1]?.id ?? "empty"
  }:${messages.length}`;

  return (
    <main className="h-screen overflow-hidden bg-[radial-gradient(circle_at_top_left,_rgba(64,145,108,0.22),_transparent_32%),radial-gradient(circle_at_top_right,_rgba(240,196,25,0.18),_transparent_28%),linear-gradient(180deg,_#f8f4ea_0%,_#f1ecde_100%)] px-3 py-3 text-[var(--ink-strong)] sm:px-4 lg:px-5">
      <div className="mx-auto flex h-[calc(100vh-1.5rem)] max-w-[1840px] flex-col gap-2.5 lg:flex-row">
        <aside className="flex w-full min-h-0 flex-col overflow-hidden rounded-[24px] border border-white/70 bg-[rgba(16,31,24,0.92)] p-3 text-white shadow-[0_24px_80px_rgba(16,31,24,0.28)] lg:h-full lg:w-[260px] lg:shrink-0">
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
              className="rounded-full border border-white/15 bg-white/8 px-4 py-2 text-sm text-white transition hover:bg-white/14"
            >
              {text.newChat}
            </button>
          </div>

          <div className="rounded-2xl border border-white/10 bg-white/6 p-3 text-sm text-white/72">
            <p className="font-medium">{currentUser?.username ?? text.unnamedUser}</p>
            <p className="mt-1 break-all text-xs text-white/45">
              {currentUser?.email ?? "--"}
            </p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                onClick={() => setIsSettingsOpen(true)}
                className="rounded-full border border-white/12 bg-white/8 px-3 py-1.5 text-xs transition hover:bg-white/14"
              >
                {text.settings}
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

          <div className="mt-3 rounded-2xl border border-white/10 bg-white/6 p-3 text-sm text-white/70">
            <p>{text.currentProvider}：{providerInfo?.provider ?? text.providerLoading}</p>
            <p className="mt-1 break-all text-xs text-white/45">
              {providerInfo?.base_url ?? text.providerBaseUrlLoading}
            </p>
          </div>

          <div ref={conversationMenuRef} className="mt-3 flex-1 overflow-hidden">
            <div className="mb-3 flex items-center justify-between">
              <p className="text-sm font-medium text-white/75">{text.historyChats}</p>
              <p className="text-xs text-white/45">
                {conversations.length}
                {text.historyCountSuffix ? ` ${text.historyCountSuffix}` : ""}
              </p>
            </div>

            <div className="flex max-h-[45vh] flex-col gap-2 overflow-y-auto pr-1 lg:max-h-[calc(100vh-16rem)]">
              {conversations.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-white/15 px-4 py-6 text-sm text-white/45">
                  {text.noConversations}
                </div>
              ) : null}

              {conversations.map((conversation) => {
                const isActive = conversation.id === selectedConversationId;
                const isMenuOpen = openConversationMenuId === conversation.id;

                return (
                  <div key={conversation.id} className="relative">
                    <button
                      type="button"
                      onClick={() => void handleSelectConversation(conversation.id)}
                      className={`w-full rounded-2xl border px-4 py-3 pr-12 text-left transition ${
                        isActive
                          ? "border-[#f0c419]/60 bg-[#f0c419]/16 text-white"
                          : "border-white/8 bg-white/4 text-white/80 hover:bg-white/10"
                      }`}
                    >
                      <div className="line-clamp-2 text-sm font-medium">
                        {conversation.title}
                      </div>
                      <div className="mt-2 flex items-center justify-between text-[11px] text-white/45">
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
                        className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-white/12 bg-black/18 text-white/75 backdrop-blur transition hover:bg-black/28"
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
                        <div className="absolute right-0 z-20 mt-2 w-28 overflow-hidden rounded-2xl border border-white/12 bg-[rgba(16,31,24,0.98)] py-1 shadow-[0_18px_40px_rgba(0,0,0,0.28)]">
                          <button
                            type="button"
                            onClick={() => {
                              setOpenConversationMenuId(null);
                              void handleRenameConversation(conversation.id);
                            }}
                            className="block w-full px-3 py-2 text-left text-sm text-white/82 transition hover:bg-white/8"
                          >
                            {text.rename}
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setOpenConversationMenuId(null);
                              void handleDeleteConversation(conversation.id);
                            }}
                            className="block w-full px-3 py-2 text-left text-sm text-[#ffcabd] transition hover:bg-white/8"
                          >
                            {text.delete}
                          </button>
                        </div>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </aside>

        <section className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-[28px] border border-white/80 bg-[rgba(255,250,242,0.9)] shadow-[0_28px_100px_rgba(112,96,56,0.18)] backdrop-blur">
          <header className="z-10 flex shrink-0 flex-col gap-1.5 border-b border-[rgba(22,34,27,0.08)] bg-[rgba(255,250,242,0.94)] px-3 py-2.5 backdrop-blur sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-[1.45rem] font-semibold leading-tight">{activeConversationTitle}</h2>
            </div>

            <div className="flex flex-col gap-1.5 sm:items-end">
              <select
                value={selectedModel}
                onChange={(event) => setSelectedModel(event.target.value)}
                className="min-w-[220px] rounded-full border border-[rgba(22,34,27,0.12)] bg-white px-4 py-1.5 text-sm outline-none transition focus:border-[var(--accent-strong)]"
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
            <div className="mx-4 mt-3 rounded-2xl border border-[rgba(185,66,42,0.18)] bg-[rgba(255,238,231,0.95)] px-4 py-3 text-sm text-[#8f3524]">
              {errorMessage}
            </div>
          ) : null}

          <ChatThread
            key={threadKey}
            initialConversationId={selectedConversationId}
            initialMessages={messages}
            isLoadingMessages={isLoadingMessages}
            selectedModel={selectedModel}
            systemPrompt={userSettings?.system_prompt ?? null}
            contextInfo={contextInfo}
            uiLanguage={uiLanguage}
            onContextInfoChange={setContextInfo}
            onChatSettled={refreshAfterChat}
            onConversationMessagesChanged={handleConversationMessagesChanged}
          />

          {isSettingsOpen && userSettings ? (
            <div className="absolute inset-0 z-20 flex items-start justify-end rounded-[32px] bg-[rgba(24,35,29,0.18)] p-4 backdrop-blur-sm">
              <form
                onSubmit={handleSaveSettings}
                className="flex max-h-[calc(100vh-4rem)] w-full max-w-4xl flex-col overflow-hidden rounded-[28px] border border-white/75 bg-[rgba(255,250,242,0.98)] shadow-[0_28px_90px_rgba(16,31,24,0.16)]"
              >
                <div className="flex shrink-0 items-center justify-between border-b border-[rgba(22,34,27,0.08)] px-5 py-4">
                  <div className="pr-4">
                    <p className="text-xs uppercase tracking-[0.28em] text-[var(--ink-muted)]">
                      {text.settingsTag}
                    </p>
                    <h3 className="mt-2 text-2xl font-semibold">{text.settingsTitle}</h3>
                  </div>
                  <button
                    type="button"
                    onClick={() => setIsSettingsOpen(false)}
                    className="rounded-full border border-[rgba(22,34,27,0.12)] bg-white px-3 py-1.5 text-xs text-[var(--ink-soft)]"
                  >
                    {text.close}
                  </button>
                </div>

                <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden sm:grid-cols-[12rem_1fr]">
                  <nav className="flex gap-2 overflow-x-auto border-b border-[rgba(22,34,27,0.08)] bg-[rgba(248,244,234,0.72)] p-3 sm:flex-col sm:overflow-visible sm:border-b-0 sm:border-r">
                    {settingsTabs.map((tab) => (
                      <button
                        key={tab.id}
                        type="button"
                        onClick={() => setActiveSettingsTab(tab.id)}
                        className={`whitespace-nowrap rounded-2xl px-4 py-2 text-left text-sm transition ${
                          activeSettingsTab === tab.id
                            ? "bg-[var(--ink-strong)] text-white shadow-[0_12px_30px_rgba(16,31,24,0.16)]"
                            : "bg-white/64 text-[var(--ink-soft)] hover:bg-white"
                        }`}
                      >
                        {tab.label}
                      </button>
                    ))}
                  </nav>

                  <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
                    <div className="space-y-4">
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
                              className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                            >
                              <option value="zh-CN">{text.chinese}</option>
                              <option value="en-US">{text.english}</option>
                            </select>
                          </label>

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

                          <div className="mt-3 flex justify-end">
                            <button
                              type="button"
                              onClick={() => void handleAddMemory()}
                              className="rounded-full bg-[var(--ink-strong)] px-4 py-2 text-sm text-white transition hover:opacity-90"
                            >
                              {text.addMemory}
                            </button>
                          </div>

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
                    className="rounded-full bg-[linear-gradient(135deg,_#d38d2d_0%,_#be6f24_100%)] px-5 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-55"
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

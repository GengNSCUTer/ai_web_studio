"use client";
/* eslint-disable @next/next/no-img-element */

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";

import { MessageMarkdown } from "@/components/message-markdown";
import type { ContextGovernanceInfo, Message, UploadItem } from "@/lib/types";

type UILanguage = "zh-CN" | "en-US";

type ChatThreadProps = {
  initialConversationId: string | null;
  initialMessages: Message[];
  isLoadingMessages: boolean;
  selectedModel: string;
  systemPrompt: string | null;
  contextInfo: ContextGovernanceInfo | null;
  uiLanguage: UILanguage;
  onContextInfoChange: (info: ContextGovernanceInfo | null) => void;
  onChatSettled: (conversationId: string, shouldSelectConversation: boolean) => void;
  onConversationMessagesChanged: (conversationId: string | null) => Promise<void>;
};

type ThreadMessage = {
  id: string;
  role: "user" | "assistant" | "system" | string;
  content: string;
  status: "done" | "streaming" | "failed" | "cancelled" | string;
  created_at: string;
  attachments: UploadItem[];
  isEphemeral?: boolean;
};

type ConversationCreateResponse = {
  id: string;
};

const THREAD_TEXT = {
  "zh-CN": {
    user: "你",
    assistant: "助手",
    thinking: "思考中",
    loadingHistory: "正在加载历史消息...",
    emptyTitle: "可以直接开始对话了",
    emptySubtitle: "当前已支持文本对话、图片与文档附件进入上下文，也已支持历史消息回放。",
    waitPlaceholder: "模型正在思考，请稍候...",
    firstToken: "等待首个回答 token",
    firstTokenGenerating: "首个回答 token 生成中",
    streaming: "正在流式输出",
    replyFailed: "该条回答生成失败，请稍后重试。",
    replyStopped: "该条回答已停止生成。",
    copyAnswer: "复制回答",
    copied: "已复制",
    enterDeleteMode: "进入删除模式",
    deleteMode: "已进入多选删除模式",
    selected: "已选中",
    selectAll: "全选",
    deselectAll: "取消全选",
    cancel: "取消",
    deleteSelected: "删除选中",
    inputPlaceholder: "输入你的问题",
    uploadAttachment: "上传附件",
    uploading: "正在上传...",
    stopGenerating: "停止生成",
    sending: "发送中...",
    send: "发送消息",
    authConnected: "登录体系：已接通",
    memoryConnected: "会话记忆：已接通",
    modelConnected: "模型切换：已接通",
    streamFetch: "文本流：原生 Fetch",
    previewConnected: "附件缩略图：已接通",
    contextPanelTitle: "上下文治理诊断",
    contextNoticesTitle: "上下文提示",
    contextMode: "上下文模式",
    modelContextWindow: "模型上下文窗口",
    totalCharsEstimate: "本轮估算字符数",
    truncatedHistoryMessages: "被裁剪的历史消息数",
    summaryChars: "摘要字符数",
    summaryTriggered: "本轮是否触发摘要刷新",
    summaryRefreshTriggered: "摘要刷新已触发",
    summaryRefreshModelUsed: "模型摘要已使用",
    summaryRefreshFallbackUsed: "规则摘要回退",
    summaryRefreshSourceMessages: "摘要刷新源消息数",
    summaryRefreshSourceChars: "摘要刷新源字符数",
    budgetMaxTotalChars: "预算总字符上限",
    budgetMaxAttachmentChars: "附件字符上限",
    contextButton: "上下文",
    closeContextPanel: "关闭诊断",
    yes: "是",
    no: "否",
  },
  "en-US": {
    user: "You",
    assistant: "Assistant",
    thinking: "Thinking",
    loadingHistory: "Loading message history...",
    emptyTitle: "You can start chatting now",
    emptySubtitle: "Text chat, image and document attachments, and history replay are enabled.",
    waitPlaceholder: "The model is thinking, please wait...",
    firstToken: "Waiting for the first answer token",
    firstTokenGenerating: "Generating first answer token",
    streaming: "Streaming output",
    replyFailed: "This answer failed to generate. Please try again later.",
    replyStopped: "This answer was stopped.",
    copyAnswer: "Copy answer",
    copied: "Copied",
    enterDeleteMode: "Enter delete mode",
    deleteMode: "Multi-select delete mode enabled",
    selected: "Selected",
    selectAll: "Select all",
    deselectAll: "Deselect all",
    cancel: "Cancel",
    deleteSelected: "Delete selected",
    inputPlaceholder: "Type your question",
    uploadAttachment: "Upload attachment",
    uploading: "Uploading...",
    stopGenerating: "Stop",
    sending: "Sending...",
    send: "Send",
    authConnected: "Auth: connected",
    memoryConnected: "Memory: connected",
    modelConnected: "Model switch: connected",
    streamFetch: "Streaming: native Fetch",
    previewConnected: "Attachment previews: connected",
    contextPanelTitle: "Context governance diagnostics",
    contextNoticesTitle: "Context notices",
    contextMode: "Context mode",
    modelContextWindow: "Model context window",
    totalCharsEstimate: "Estimated chars this turn",
    truncatedHistoryMessages: "Truncated history messages",
    summaryChars: "Summary chars",
    summaryTriggered: "Summary refresh triggered",
    summaryRefreshTriggered: "Summary refresh happened",
    summaryRefreshModelUsed: "Model summary used",
    summaryRefreshFallbackUsed: "Rule fallback used",
    summaryRefreshSourceMessages: "Summary refresh source messages",
    summaryRefreshSourceChars: "Summary refresh source chars",
    budgetMaxTotalChars: "Total char budget",
    budgetMaxAttachmentChars: "Attachment char budget",
    contextButton: "Context",
    closeContextPanel: "Close diagnostics",
    yes: "Yes",
    no: "No",
  },
} as const;

function parseContextStatsHeader(value: string | null): Record<string, string> {
  if (!value) {
    return {};
  }

  return value
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean)
    .reduce<Record<string, string>>((acc, item) => {
      const [key, ...rest] = item.split("=");
      if (!key || rest.length === 0) {
        return acc;
      }
      acc[key.trim()] = rest.join("=").trim();
      return acc;
    }, {});
}

function parseContextNoticesHeader(value: string | null): string[] {
  if (!value) {
    return [];
  }

  try {
    const decoded = atob(value);
    const bytes = Uint8Array.from(decoded, (char) => char.charCodeAt(0));
    const text = new TextDecoder().decode(bytes);
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) {
      return parsed.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
    }
  } catch {
    // fallback for legacy plain-text header format
  }

  return value
    .split("|")
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatFileSize(size: number) {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function buildDraftTitle(content: string, uiLanguage: UILanguage) {
  const normalized = content.trim().replace(/\s+/g, " ");
  return normalized.slice(0, 24) || (uiLanguage === "en-US" ? "New Chat" : "新对话");
}

function formatElapsedLabel(totalSeconds: number) {
  if (totalSeconds < 60) {
    return `${totalSeconds}s`;
  }

  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${seconds}s`;
}

function attachmentKindLabel(kind: string, uiLanguage: UILanguage) {
  if (uiLanguage === "en-US") {
    return kind === "image" ? "Image" : "File";
  }

  return kind === "image" ? "图片" : "文件";
}

function isImageAttachment(attachment: UploadItem) {
  if (attachment.kind === "image") {
    return true;
  }

  const mimeType = attachment.mime_type ?? "";
  return mimeType.startsWith("image/");
}

function buildAttachmentUrl(storageKey: string) {
  return `/api/backend/uploads/file?storage_key=${encodeURIComponent(storageKey)}`;
}

function formatMessageTime(value: string, uiLanguage: UILanguage) {
  return new Intl.DateTimeFormat(uiLanguage, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function toThreadMessages(messages: Message[]): ThreadMessage[] {
  return messages
    .filter((message) => message.role === "user" || message.role === "assistant" || message.role === "system")
    .map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      status: message.status,
      created_at: message.created_at,
      attachments: message.attachments ?? [],
    }));
}

function requestErrorMessage(error: unknown) {
  if (error instanceof Error) {
    const message = error.message.trim();
    try {
      const parsed = JSON.parse(message) as { detail?: string };
      if (typeof parsed.detail === "string" && parsed.detail.trim()) {
        return parsed.detail;
      }
    } catch {
      // fall through
    }
    return message || "未知错误";
  }

  return "未知错误";
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

export function ChatThread({
  initialConversationId,
  initialMessages,
  isLoadingMessages,
  selectedModel,
  systemPrompt,
  contextInfo,
  uiLanguage,
  onContextInfoChange,
  onChatSettled,
  onConversationMessagesChanged,
}: ChatThreadProps) {
  const [composer, setComposer] = useState("");
  const [uploadedItems, setUploadedItems] = useState<UploadItem[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [isManageMode, setIsManageMode] = useState(false);
  const [selectedMessageIds, setSelectedMessageIds] = useState<string[]>([]);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [isDeletingMessages, setIsDeletingMessages] = useState(false);
  const [isContextPanelOpen, setIsContextPanelOpen] = useState(false);
  const [localConversationId, setLocalConversationId] = useState<string | null>(initialConversationId);
  const [streamingStartedAt, setStreamingStartedAt] = useState<number | null>(null);
  const [streamingElapsedSeconds, setStreamingElapsedSeconds] = useState(0);
  const [threadMessages, setThreadMessages] = useState<ThreadMessage[]>(() =>
    toThreadMessages(initialMessages)
  );
  const [isGenerating, setIsGenerating] = useState(false);
  const messageEndRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const activeConversationIdRef = useRef<string | null>(initialConversationId);
  const shouldSelectAfterFinishRef = useRef(false);
  const didSyncAfterRequestRef = useRef(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const text = THREAD_TEXT[uiLanguage];
  const statEntries = Object.entries(contextInfo?.stats ?? {});
  const statMap = Object.fromEntries(statEntries);
  const formatBooleanStat = (value: string | undefined) =>
    value === "true" || value === "1" ? text.yes : value === "false" || value === "0" ? text.no : value;
  const contextStatsCards = [
    { key: "context_mode", label: text.contextMode, value: statMap.context_mode },
    { key: "model_context_window", label: text.modelContextWindow, value: statMap.model_context_window },
    { key: "total_chars_estimate", label: text.totalCharsEstimate, value: statMap.total_chars_estimate },
    { key: "truncated_history_messages", label: text.truncatedHistoryMessages, value: statMap.truncated_history_messages },
    { key: "summary_chars", label: text.summaryChars, value: statMap.summary_chars },
    {
      key: "summary_triggered",
      label: text.summaryTriggered,
      value: formatBooleanStat(statMap.summary_triggered),
    },
    {
      key: "summary_refresh_triggered",
      label: text.summaryRefreshTriggered,
      value: formatBooleanStat(statMap.summary_refresh_triggered),
    },
    {
      key: "summary_refresh_model_used",
      label: text.summaryRefreshModelUsed,
      value: formatBooleanStat(statMap.summary_refresh_model_used),
    },
    {
      key: "summary_refresh_fallback_used",
      label: text.summaryRefreshFallbackUsed,
      value: formatBooleanStat(statMap.summary_refresh_fallback_used),
    },
    {
      key: "summary_refresh_source_messages",
      label: text.summaryRefreshSourceMessages,
      value: statMap.summary_refresh_source_messages,
    },
    {
      key: "summary_refresh_source_chars",
      label: text.summaryRefreshSourceChars,
      value: statMap.summary_refresh_source_chars,
    },
    {
      key: "budget_max_total_chars",
      label: text.budgetMaxTotalChars,
      value: statMap.budget_max_total_chars,
    },
    {
      key: "budget_max_attachment_chars",
      label: text.budgetMaxAttachmentChars,
      value: statMap.budget_max_attachment_chars,
    },
  ].filter((item) => typeof item.value === "string" && item.value.trim().length > 0);

  const activeConversationId = localConversationId ?? initialConversationId;
  const activeStreamingAssistantMessage = [...threadMessages]
    .reverse()
    .find((message) => message.role === "assistant" && message.status === "streaming");
  const displayError = localError;
  const streamingStatusLabel =
    isGenerating && !activeStreamingAssistantMessage
      ? `${uiLanguage === "zh-CN" ? "模型正在思考，等待首个 token..." : "Model is thinking, waiting for the first token..."} ${formatElapsedLabel(streamingElapsedSeconds)}${
          selectedModel.includes("27b")
            ? uiLanguage === "zh-CN"
              ? "，27B 模型冷启动时通常会更久一些"
              : ", 27B models may take longer to warm up"
            : ""
        }`
      : isGenerating
        ? `${uiLanguage === "zh-CN" ? "模型正在持续生成回答..." : "Model is still generating..."} ${formatElapsedLabel(streamingElapsedSeconds)}`
        : null;

  useEffect(() => {
    activeConversationIdRef.current = activeConversationId;
  }, [activeConversationId]);

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({
      behavior: streamingStartedAt ? "auto" : "smooth",
    });
  }, [threadMessages.length, activeStreamingAssistantMessage?.content, streamingStartedAt]);

  useEffect(() => {
    if (!streamingStartedAt) {
      return;
    }

    const timer = window.setInterval(() => {
      const elapsed = Math.max(0, Math.floor((Date.now() - streamingStartedAt) / 1000));
      setStreamingElapsedSeconds(elapsed);
    }, 1000);

    return () => window.clearInterval(timer);
  }, [streamingStartedAt]);

  useEffect(() => {
    const textarea = composerRef.current;
    if (!textarea) {
      return;
    }

    const maxHeight = 240;
    textarea.style.height = "auto";
    const nextHeight = Math.min(textarea.scrollHeight, maxHeight);
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
  }, [composer]);

  function syncAfterRequest() {
    if (didSyncAfterRequestRef.current) {
      return;
    }

    didSyncAfterRequestRef.current = true;
    const conversationId = activeConversationIdRef.current;
    if (!conversationId) {
      shouldSelectAfterFinishRef.current = false;
      return;
    }

    onChatSettled(conversationId, shouldSelectAfterFinishRef.current);
    shouldSelectAfterFinishRef.current = false;
  }

  async function createConversationForDraft(content: string) {
    const created = await requestJson<ConversationCreateResponse>("/api/backend/conversations", {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify({
        title: buildDraftTitle(content, uiLanguage),
        model_name: selectedModel,
        system_prompt: systemPrompt,
      }),
    });

    setLocalConversationId(created.id);
    activeConversationIdRef.current = created.id;
    shouldSelectAfterFinishRef.current = true;
    return created.id;
  }

  async function handleUpload(files: FileList | null) {
    if (!files || files.length === 0) {
      return;
    }

    setIsUploading(true);
    setLocalError(null);

    try {
      const formData = new FormData();
      Array.from(files).forEach((file) => {
        formData.append("files", file);
      });

      const response = await fetch("/api/backend/uploads", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `Upload failed: ${response.status}`);
      }

      const data = (await response.json()) as UploadItem[];
      setUploadedItems((current) => [...current, ...data]);
    } catch (uploadError) {
      setLocalError(`上传失败：${requestErrorMessage(uploadError)}`);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }

  function removeUploadedItem(itemId: string) {
    setUploadedItems((current) => current.filter((item) => item.id !== itemId));
  }

  async function reloadConversationMessages() {
    await onConversationMessagesChanged(activeConversationIdRef.current);
  }

  async function handleBulkDeleteMessages() {
    const conversationId = activeConversationIdRef.current;
    if (!conversationId || selectedMessageIds.length === 0 || isGenerating || isDeletingMessages) {
      return;
    }

    const confirmed = window.confirm(`确认删除选中的 ${selectedMessageIds.length} 条消息吗？`);
    if (!confirmed) {
      return;
    }

    setIsDeletingMessages(true);
    setLocalError(null);

    try {
      const response = await fetch(`/api/backend/conversations/${conversationId}/messages/bulk-delete`, {
        method: "POST",
        cache: "no-store",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          message_ids: selectedMessageIds,
        }),
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `Request failed: ${response.status}`);
      }

      setSelectedMessageIds([]);
      setIsManageMode(false);
      await reloadConversationMessages();
    } catch (deleteError) {
      setLocalError(`批量删除失败：${requestErrorMessage(deleteError)}`);
    } finally {
      setIsDeletingMessages(false);
    }
  }

  function toggleSelectMessage(messageId: string) {
    setSelectedMessageIds((current) =>
      current.includes(messageId)
        ? current.filter((id) => id !== messageId)
        : [...current, messageId]
    );
  }

  function enterManageMode(messageId?: string) {
    if (!activeConversationIdRef.current || isGenerating || threadMessages.length === 0) {
      return;
    }

    setIsManageMode(true);
    setSelectedMessageIds(messageId ? [messageId] : []);
  }

  function exitManageMode() {
    setIsManageMode(false);
    setSelectedMessageIds([]);
  }

  function toggleSelectAllMessages() {
    if (selectedMessageIds.length === threadMessages.length) {
      setSelectedMessageIds([]);
      return;
    }

    setSelectedMessageIds(threadMessages.map((message) => message.id));
  }

  async function handleCopyMessage(messageId: string, content: string) {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedMessageId(messageId);
      window.setTimeout(() => {
        setCopiedMessageId((current) => (current === messageId ? null : current));
      }, 1800);
    } catch (copyError) {
      setLocalError(`复制失败：${requestErrorMessage(copyError)}`);
    }
  }

  async function handleSubmit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();

    const content = composer.trim();
    if (!content || isGenerating) {
      return;
    }

    setLocalError(null);
    didSyncAfterRequestRef.current = false;
    shouldSelectAfterFinishRef.current = false;

    let conversationId = activeConversationIdRef.current;
    if (!conversationId) {
      try {
        conversationId = await createConversationForDraft(content);
      } catch (createError) {
        setLocalError(`创建会话失败：${requestErrorMessage(createError)}`);
        return;
      }
    }

    const requestConversationId = conversationId;
    const pendingUploads = [...uploadedItems];
    const nowIso = new Date().toISOString();
    const tempUserMessageId = `temp-user-${Date.now()}`;
    const tempAssistantMessageId = `temp-assistant-${Date.now()}`;

    setStreamingStartedAt(Date.now());
    setStreamingElapsedSeconds(0);
    setComposer("");
    setUploadedItems([]);
    setIsGenerating(true);
    onContextInfoChange(null);

    setThreadMessages((current) => [
      ...current,
      {
        id: tempUserMessageId,
        role: "user",
        content,
        status: "done",
        created_at: nowIso,
        attachments: pendingUploads,
        isEphemeral: true,
      },
      {
        id: tempAssistantMessageId,
        role: "assistant",
        content: "",
        status: "streaming",
        created_at: nowIso,
        attachments: [],
        isEphemeral: true,
      },
    ]);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          conversationId: requestConversationId,
          modelName: selectedModel,
          systemPrompt,
          title: buildDraftTitle(content, uiLanguage),
          attachments: pendingUploads,
          messages: [
            {
              role: "user",
              parts: [{ type: "text", text: content }],
            },
          ],
        }),
        cache: "no-store",
        signal: controller.signal,
      });

      onContextInfoChange({
        notices: parseContextNoticesHeader(response.headers.get("x-context-notices")),
        stats: parseContextStatsHeader(response.headers.get("x-context-stats")),
      });
      if (!response.headers.get("x-context-stats") && !response.headers.get("x-context-notices")) {
        setIsContextPanelOpen(false);
      }

      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `Request failed: ${response.status}`);
      }

      if (!response.body) {
        throw new Error("响应体为空，无法开始流式读取");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let assistantText = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }

        if (!value) {
          continue;
        }

        assistantText += decoder.decode(value, { stream: true });
        setThreadMessages((current) =>
          current.map((message) =>
            message.id === tempAssistantMessageId
              ? {
                  ...message,
                  content: assistantText,
                  status: "streaming",
                }
              : message
          )
        );
      }

      assistantText += decoder.decode();
      setThreadMessages((current) =>
        current.map((message) =>
          message.id === tempAssistantMessageId
            ? {
                ...message,
                content: assistantText,
                status: "done",
              }
            : message
        )
      );

      setStreamingStartedAt(null);
      setStreamingElapsedSeconds(0);
      setLocalError(null);
      syncAfterRequest();
    } catch (sendError) {
      const isAbort = sendError instanceof DOMException && sendError.name === "AbortError";
      setStreamingStartedAt(null);
      setStreamingElapsedSeconds(0);
      setComposer(content);
      setUploadedItems(pendingUploads);
      setThreadMessages((current) =>
        current.map((message) =>
          message.id === tempAssistantMessageId
            ? {
                ...message,
                status: isAbort ? "cancelled" : "failed",
              }
            : message
        )
      );
      if (!isAbort) {
        setLocalError(requestErrorMessage(sendError));
      }
      syncAfterRequest();
    } finally {
      setIsGenerating(false);
      abortControllerRef.current = null;
    }
  }

  async function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!composer.trim() || isGenerating) {
        return;
      }
      await handleSubmit();
    }
  }

  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3 sm:px-4">
        <div className="mx-auto flex w-full max-w-[86rem] flex-col gap-4">
          {threadMessages.length === 0 && !isLoadingMessages ? (
            <div className="rounded-[24px] border border-dashed border-[rgba(22,34,27,0.12)] bg-white/70 px-5 py-6 text-center">
              <p className="text-lg font-medium">{text.emptyTitle}</p>
              <p className="mt-2 text-sm text-[var(--ink-soft)]">{text.emptySubtitle}</p>
            </div>
          ) : null}

          {isLoadingMessages ? (
            <div className="rounded-3xl border border-[rgba(22,34,27,0.1)] bg-white/75 px-5 py-3 text-sm text-[var(--ink-soft)]">
              {text.loadingHistory}
            </div>
          ) : null}

          {threadMessages.map((message) => {
            const isUser = message.role === "user";
            const isStreamingAssistant =
              message.role === "assistant" &&
              message.id === activeStreamingAssistantMessage?.id;
            const isWaitingAssistant = isStreamingAssistant && !message.content.trim();
            const isSelected = selectedMessageIds.includes(message.id);
            const showActionBar = !isGenerating && !isStreamingAssistant && message.role === "assistant";
            const messageStatus = message.status;
            const messageTime = formatMessageTime(message.created_at, uiLanguage);
            const shouldShowWaitingPlaceholder = isWaitingAssistant && isGenerating;
            const shouldShowStreamingIndicator = isStreamingAssistant && message.content.trim() && isGenerating;

            return (
              <article
                key={message.id}
                className={`group max-w-[96%] rounded-[24px] px-4 py-3.5 shadow-[0_18px_40px_rgba(64,58,42,0.08)] lg:max-w-[92%] ${
                  isUser
                    ? "ml-auto bg-[linear-gradient(135deg,_#16221b_0%,_#254636_100%)] text-white"
                    : "border border-[rgba(22,34,27,0.08)] bg-white text-[var(--ink-strong)]"
                } ${isSelected ? "ring-2 ring-[var(--accent-strong)] ring-offset-2 ring-offset-transparent" : ""}`}
              >
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3 text-xs uppercase tracking-[0.2em]">
                    {isManageMode ? (
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => toggleSelectMessage(message.id)}
                        className="h-4 w-4 accent-[var(--accent-strong)]"
                      />
                    ) : null}
                    <span className={isUser ? "text-white/65" : "text-[var(--ink-muted)]"}>
                      {isUser ? text.user : text.assistant}
                    </span>
                    <span className={isUser ? "text-white/45" : "text-[var(--ink-muted)]"}>
                      {isWaitingAssistant ? text.thinking : messageStatus}
                    </span>
                  </div>
                  <span className={isUser ? "text-white/45 text-[11px]" : "text-[var(--ink-muted)] text-[11px]"}>
                    {messageTime}
                  </span>
                </div>
                {isUser ? (
                  <div className="whitespace-pre-wrap text-sm leading-6 sm:text-[15px]">
                    {message.content || (shouldShowWaitingPlaceholder ? text.waitPlaceholder : "")}
                  </div>
                ) : (
                  <MessageMarkdown
                    content={message.content || (shouldShowWaitingPlaceholder ? text.waitPlaceholder : "")}
                    isStreaming={Boolean(shouldShowStreamingIndicator)}
                  />
                )}
                {message.attachments.length > 0 ? (
                  <div className="mt-4 flex flex-wrap gap-3">
                    {message.attachments.map((attachment) => (
                      isImageAttachment(attachment) ? (
                        <a
                          key={attachment.id}
                          href={buildAttachmentUrl(attachment.storage_key)}
                          target="_blank"
                          rel="noreferrer"
                          className={`group block overflow-hidden rounded-2xl border ${
                            isUser
                              ? "border-white/14 bg-white/8"
                              : "border-[rgba(22,34,27,0.1)] bg-[rgba(248,244,234,0.92)]"
                          }`}
                        >
                          <img
                            src={buildAttachmentUrl(attachment.storage_key)}
                            alt={attachment.file_name}
                            className="h-32 w-32 object-cover transition group-hover:scale-[1.02]"
                          />
                          <div className="flex w-32 flex-col gap-0.5 px-2.5 py-2 text-left text-[11px]">
                            <span className="truncate">{attachment.file_name}</span>
                            <span className="text-[var(--ink-muted)]">{formatFileSize(attachment.file_size ?? 0)}</span>
                          </div>
                        </a>
                      ) : (
                        <div
                          key={attachment.id}
                          className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs ${
                            isUser
                              ? "border-white/14 bg-white/10 text-white/82"
                              : "border-[rgba(22,34,27,0.1)] bg-[rgba(248,244,234,0.92)] text-[var(--ink-soft)]"
                          }`}
                        >
                          <span>{attachmentKindLabel(attachment.kind, uiLanguage)}</span>
                          <span className="max-w-[260px] truncate">{attachment.file_name}</span>
                          <span>{formatFileSize(attachment.file_size ?? 0)}</span>
                        </div>
                      )
                    ))}
                  </div>
                ) : null}
                {shouldShowWaitingPlaceholder ? (
                  <div className="mt-2.5 inline-flex items-center gap-2 rounded-full border border-[rgba(22,34,27,0.08)] bg-[rgba(248,244,234,0.8)] px-3 py-1 text-[11px] text-[var(--ink-muted)]">
                    <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent-strong)] animate-pulse" />
                    <span>{text.firstTokenGenerating}</span>
                  </div>
                ) : null}
                {shouldShowStreamingIndicator ? (
                  <div className="mt-2.5 inline-flex items-center gap-2 rounded-full border border-[rgba(22,34,27,0.08)] bg-[rgba(248,244,234,0.8)] px-3 py-1 text-[11px] text-[var(--ink-muted)]">
                    <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent-strong)] animate-pulse" />
                    <span>{text.streaming}</span>
                  </div>
                ) : null}
                {!isUser && (messageStatus === "failed" || messageStatus === "cancelled") ? (
                  <div className="mt-2.5 rounded-2xl border border-[rgba(185,66,42,0.14)] bg-[rgba(255,238,231,0.92)] px-3 py-2 text-xs text-[#8f3524]">
                    {messageStatus === "failed" ? text.replyFailed : text.replyStopped}
                  </div>
                ) : null}
                {showActionBar ? (
                  <div className="mt-2.5 flex items-center gap-2 opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100">
                    <button
                      type="button"
                      onClick={() => void handleCopyMessage(message.id, message.content)}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-[rgba(22,34,27,0.12)] bg-[rgba(248,244,234,0.92)] text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] hover:text-[var(--ink-strong)]"
                      aria-label={text.copyAnswer}
                      title={copiedMessageId === message.id ? text.copied : text.copyAnswer}
                    >
                      {copiedMessageId === message.id ? (
                        <svg viewBox="0 0 24 24" className="h-4 w-4 fill-none stroke-current stroke-2">
                          <path d="M5 13l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      ) : (
                        <svg viewBox="0 0 24 24" className="h-4 w-4 fill-none stroke-current stroke-2">
                          <rect x="9" y="9" width="10" height="10" rx="2" />
                          <path d="M15 9V7a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2" />
                        </svg>
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => enterManageMode(message.id)}
                      disabled={isDeletingMessages}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-[rgba(185,66,42,0.14)] bg-[rgba(255,238,231,0.92)] text-[#8f3524] transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50"
                      aria-label={text.enterDeleteMode}
                      title={text.enterDeleteMode}
                    >
                      <svg viewBox="0 0 24 24" className="h-4 w-4 fill-none stroke-current stroke-2">
                        <path d="M4 7h16" strokeLinecap="round" />
                        <path d="M10 11v6M14 11v6" strokeLinecap="round" />
                        <path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" />
                        <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
                      </svg>
                    </button>
                  </div>
                ) : null}
              </article>
            );
          })}

          {isGenerating && !activeStreamingAssistantMessage ? (
            <article className="max-w-[96%] rounded-[24px] border border-[rgba(22,34,27,0.08)] bg-white px-4 py-3.5 text-[var(--ink-strong)] shadow-[0_18px_40px_rgba(64,58,42,0.08)] lg:max-w-[92%]">
              <div className="mb-2 flex items-center justify-between gap-3">
                <div className="flex items-center gap-3 text-xs uppercase tracking-[0.2em]">
                  <span className="text-[var(--ink-muted)]">{text.assistant}</span>
                  <span className="text-[var(--ink-muted)]">{text.thinking}</span>
                </div>
              </div>
              <div className="text-sm leading-6 sm:text-[15px]">{text.waitPlaceholder}</div>
              <div className="mt-3 inline-flex items-center gap-2 rounded-full border border-[rgba(22,34,27,0.08)] bg-[rgba(248,244,234,0.8)] px-3 py-1 text-[11px] text-[var(--ink-muted)]">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent-strong)]" />
                <span>{text.firstToken}</span>
              </div>
            </article>
          ) : null}

          <div ref={messageEndRef} />
        </div>
      </div>

      <footer className="border-t border-[rgba(22,34,27,0.08)] px-3 py-2.5 sm:px-4">
        {displayError ? (
          <div className="mx-auto mb-2.5 w-full max-w-[86rem] rounded-2xl border border-[rgba(185,66,42,0.18)] bg-[rgba(255,238,231,0.95)] px-4 py-3 text-sm text-[#8f3524]">
            {displayError}
          </div>
        ) : null}

        {isManageMode ? (
          <div className="mx-auto mb-2.5 flex w-full max-w-[86rem] flex-wrap items-center justify-between gap-3 rounded-[22px] border border-[rgba(22,34,27,0.08)] bg-[rgba(255,250,242,0.96)] px-4 py-2.5 shadow-[0_14px_36px_rgba(32,45,35,0.08)]">
            <div className="flex flex-wrap items-center gap-3 text-sm text-[var(--ink-soft)]">
              <span>{text.deleteMode}</span>
              <span>
                {text.selected} {selectedMessageIds.length} / {threadMessages.length}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={toggleSelectAllMessages}
                className="rounded-full border border-[rgba(22,34,27,0.12)] bg-white px-3 py-1.5 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
              >
                {selectedMessageIds.length === threadMessages.length ? text.deselectAll : text.selectAll}
              </button>
              <button
                type="button"
                onClick={exitManageMode}
                className="rounded-full border border-[rgba(22,34,27,0.12)] bg-white px-3 py-1.5 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
              >
                {text.cancel}
              </button>
              <button
                type="button"
                onClick={() => void handleBulkDeleteMessages()}
                disabled={selectedMessageIds.length === 0 || isDeletingMessages}
                className="rounded-full border border-[rgba(185,66,42,0.16)] bg-[rgba(255,238,231,0.95)] px-3 py-1.5 text-xs text-[#8f3524] transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {text.deleteSelected}
              </button>
            </div>
          </div>
        ) : null}

        <form onSubmit={handleSubmit} className="mx-auto w-full max-w-[86rem]">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="image/*,.txt,.md,.markdown,.pdf,.docx"
            className="hidden"
            onChange={(event) => void handleUpload(event.target.files)}
          />

          <div className="rounded-[24px] border border-[rgba(22,34,27,0.1)] bg-white px-4 py-2.5 shadow-[0_18px_48px_rgba(32,45,35,0.08)]">
            {uploadedItems.length > 0 ? (
              <div className="mb-4 flex flex-wrap gap-3">
                {uploadedItems.map((item) => (
                  isImageAttachment(item) ? (
                    <div
                      key={item.id}
                    className="overflow-hidden rounded-2xl border border-[rgba(22,34,27,0.12)] bg-[rgba(248,244,234,0.9)]"
                    >
                      <div className="flex items-start gap-3 p-2">
                        <img
                          src={buildAttachmentUrl(item.storage_key)}
                          alt={item.file_name}
                          className="h-16 w-16 rounded-xl object-cover"
                        />
                        <div className="min-w-0 pr-2 text-xs text-[var(--ink-soft)]">
                          <div className="truncate font-medium">{item.file_name}</div>
                          <div className="mt-1 text-[var(--ink-muted)]">{formatFileSize(item.file_size)}</div>
                          <button
                            type="button"
                            onClick={() => removeUploadedItem(item.id)}
                            className="mt-2 text-[var(--ink-muted)] transition hover:text-[var(--ink-strong)]"
                          >
                            {uiLanguage === "en-US" ? "Remove" : "移除"}
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div
                      key={item.id}
                      className="inline-flex items-center gap-2 rounded-full border border-[rgba(22,34,27,0.12)] bg-[rgba(248,244,234,0.9)] px-3 py-2 text-xs text-[var(--ink-soft)]"
                    >
                      <span>{attachmentKindLabel(item.kind, uiLanguage)}</span>
                      <span>{item.file_name}</span>
                      <span>{formatFileSize(item.file_size)}</span>
                      <button
                        type="button"
                        onClick={() => removeUploadedItem(item.id)}
                        className="text-[var(--ink-muted)] transition hover:text-[var(--ink-strong)]"
                      >
                        {uiLanguage === "en-US" ? "Remove" : "移除"}
                      </button>
                    </div>
                  )
                ))}
              </div>
            ) : null}

            <textarea
              ref={composerRef}
              value={composer}
              onChange={(event) => setComposer(event.target.value)}
              onKeyDown={(event) => void handleComposerKeyDown(event)}
              placeholder={text.inputPlaceholder}
              rows={2}
              className="min-h-[40px] w-full resize-none border-none bg-transparent text-[15px] leading-6 outline-none placeholder:text-[var(--ink-muted)]"
            />

            <div className="mt-2.5 flex flex-col gap-2 border-t border-[rgba(22,34,27,0.08)] pt-2.5">
              {streamingStatusLabel ? (
                <div className="rounded-2xl border border-[rgba(22,34,27,0.08)] bg-[rgba(248,244,234,0.72)] px-4 py-2 text-sm text-[var(--ink-soft)]">
                  {streamingStatusLabel}
                </div>
              ) : null}

              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="rounded-full border border-[rgba(22,34,27,0.12)] bg-[rgba(248,244,234,0.9)] px-3 py-1.5 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
                >
                  {text.uploadAttachment}
                </button>
                {contextInfo && (contextStatsCards.length > 0 || contextInfo.notices.length > 0) ? (
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setIsContextPanelOpen((current) => !current)}
                      className="rounded-full border border-[rgba(22,34,27,0.12)] bg-[rgba(248,244,234,0.9)] px-3 py-1.5 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
                    >
                      {text.contextButton}
                    </button>

                    {isContextPanelOpen ? (
                      <div className="absolute bottom-[calc(100%+0.75rem)] left-0 z-20 w-[min(92vw,44rem)] rounded-[24px] border border-[rgba(22,34,27,0.12)] bg-[rgba(255,250,242,0.98)] p-4 shadow-[0_24px_80px_rgba(32,45,35,0.16)] backdrop-blur">
                        <div className="flex items-center justify-between gap-3">
                          <p className="text-xs uppercase tracking-[0.18em] text-[var(--ink-muted)]">
                            {text.contextPanelTitle}
                          </p>
                          <button
                            type="button"
                            onClick={() => setIsContextPanelOpen(false)}
                            className="rounded-full border border-[rgba(22,34,27,0.12)] bg-white px-3 py-1 text-[11px] text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
                          >
                            {text.closeContextPanel}
                          </button>
                        </div>

                        {contextStatsCards.length > 0 ? (
                          <div className="mt-3 grid gap-2 sm:grid-cols-2">
                            {contextStatsCards.map((item) => (
                              <div
                                key={item.key}
                                className="rounded-2xl border border-[rgba(22,34,27,0.08)] bg-white/78 px-3 py-2"
                              >
                                <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
                                  {item.label}
                                </p>
                                <p className="mt-1 break-all text-sm font-medium text-[var(--ink-strong)]">
                                  {item.value}
                                </p>
                              </div>
                            ))}
                          </div>
                        ) : null}

                        {contextInfo.notices.length > 0 ? (
                          <div className="mt-3 rounded-2xl border border-dashed border-[rgba(22,34,27,0.12)] bg-white/60 px-3 py-2">
                            <p className="text-xs uppercase tracking-[0.18em] text-[var(--ink-muted)]">
                              {text.contextNoticesTitle}
                            </p>
                            <div className="mt-2 flex flex-col gap-1 text-xs leading-5 text-[var(--ink-soft)]">
                              {contextInfo.notices.map((notice) => (
                                <span key={notice}>{notice}</span>
                              ))}
                            </div>
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {isUploading ? (
                  <span className="text-xs text-[var(--ink-muted)]">{text.uploading}</span>
                ) : null}
              </div>

              <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex flex-wrap items-center gap-3 text-[11px] uppercase tracking-[0.16em] text-[var(--ink-muted)]">
                  <span>{text.authConnected}</span>
                  <span>{text.memoryConnected}</span>
                  <span>{text.modelConnected}</span>
                  <span>{text.streamFetch}</span>
                  <span>{text.previewConnected}</span>
                </div>

                <div className="flex items-center gap-3">
                  {isGenerating ? (
                    <button
                      type="button"
                      onClick={() => abortControllerRef.current?.abort()}
                      className="inline-flex items-center justify-center rounded-full border border-[rgba(185,66,42,0.22)] bg-[rgba(255,238,231,0.95)] px-5 py-2 text-sm font-medium text-[#8f3524] transition hover:brightness-95"
                    >
                      {text.stopGenerating}
                    </button>
                  ) : null}
                  <button
                    type="submit"
                    disabled={isGenerating || !composer.trim()}
                    className="inline-flex items-center justify-center rounded-full bg-[linear-gradient(135deg,_#d38d2d_0%,_#be6f24_100%)] px-6 py-2 text-sm font-medium text-white transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-55"
                  >
                    {isGenerating ? text.sending : text.send}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </form>
      </footer>
    </>
  );
}

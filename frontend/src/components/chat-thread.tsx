"use client";
/* eslint-disable @next/next/no-img-element */

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";

import { AttachmentPreviewModal } from "@/components/attachment-preview-modal";
import { ChatComposer } from "@/components/chat-composer";
import { ExternalSourceCard } from "@/components/external-source-card";
import { MessageMarkdown } from "@/components/message-markdown";
import { ToolTracePanel } from "@/components/tool-trace-panel";
import {
  attachmentKindLabel,
  buildAttachmentUrl,
  classifyClientFile,
  cloneUploadItems,
  formatFileSize,
  isImageAttachment,
} from "@/lib/attachments";
import type {
  ContextAttachmentChunk,
  ContextGovernanceInfo,
  ExternalSource,
  Message,
  ToolPlanPayload,
  ToolTraceEvent,
  UploadItem,
} from "@/lib/types";

type UILanguage = "zh-CN" | "en-US";

type ChatThreadProps = {
  initialConversationId: string | null;
  initialMessages: Message[];
  isLoadingMessages: boolean;
  selectedModel: string;
  systemPrompt: string | null;
  projectId: string | null;
  contextInfo: ContextGovernanceInfo | null;
  highlightedMessageId: string | null;
  uiLanguage: UILanguage;
  isDeepThinkingEnabled: boolean;
  isWebSearchEnabled: boolean;
  onDeepThinkingEnabledChange: (enabled: boolean) => void;
  onWebSearchEnabledChange: (enabled: boolean) => void;
  onContextInfoChange: (
    info: ContextGovernanceInfo | null,
    conversationId?: string | null
  ) => void;
  onChatSettled: (conversationId: string, shouldSelectConversation: boolean) => void;
  onConversationMessagesChanged: (conversationId: string | null) => Promise<void>;
};

type ThreadMessage = {
  id: string;
  role: "user" | "assistant" | "system" | string;
  content: string;
  reasoningContent?: string | null;
  externalSources?: ExternalSource[];
  toolEvents?: ToolTraceEvent[];
  status: "done" | "streaming" | "failed" | "cancelled" | string;
  created_at: string;
  attachments: UploadItem[];
  isEphemeral?: boolean;
};

type ConversationCreateResponse = {
  id: string;
};

const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const MAX_FILE_BYTES = 20 * 1024 * 1024;

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
    deepThinking: "深度思考",
    webSearch: "联网搜索",
    reasoningTitle: "思考过程",
    toolTraceTitle: "工具过程",
    sourcesTitle: "外部来源",
    replyFailed: "该条回答生成失败，请稍后重试。",
    replyStopped: "该条回答已停止生成。",
    copyAnswer: "复制回答",
    copied: "已复制",
    regenerateAnswer: "重新生成",
    editLastUser: "编辑后重答",
    saveAndRetry: "保存并重答",
    removeAttachment: "移除附件",
    editingMessage: "正在编辑上一条问题",
    previewAttachment: "预览附件",
    openOriginal: "打开原文件",
    closePreview: "关闭预览",
    editPromptTitle: "修改上一条用户消息",
    enterDeleteMode: "进入删除模式",
    deleteMode: "已进入多选删除模式",
    selected: "已选中",
    selectAll: "全选",
    deselectAll: "取消全选",
    cancel: "取消",
    confirm: "确认",
    deleteSelected: "删除选中",
    deleteMessagesTitle: "删除选中消息",
    deleteMessagesDescription: "删除后无法恢复，确认删除这些消息吗？",
    inputPlaceholder: "输入你的问题",
    uploadAttachment: "上传附件",
    uploadUnsupportedType:
      "当前仅支持图片（png/jpg/jpeg/webp/gif）以及 txt、md、pdf、docx 文档上传。",
    uploadImageTooLarge: "图片过大，单文件不能超过 10MB。",
    uploadFileTooLarge: "文档过大，单文件不能超过 20MB。",
    uploading: "正在上传...",
    stopGenerating: "停止生成",
    sending: "发送中...",
    send: "发送消息",
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
    memoryEnabled: "长期记忆已启用",
    memoryInjected: "长期记忆已注入",
    memoryCount: "长期记忆条数",
    memoryChars: "长期记忆字符数",
    budgetMaxTotalChars: "预算总字符上限",
    budgetMaxAttachmentChars: "附件字符上限",
    promptTemplateVersion: "Prompt 模板版本",
    providerTemplate: "Provider 模板",
    modelFamily: "模型族",
    promptLayers: "Prompt 上下文层级",
    promptSystemLayers: "系统前缀层数",
    promptHistoryMessages: "Prompt 历史消息数",
    promptAttachmentContextInjected: "附件片段已注入",
    promptImageMessages: "注入图片消息数",
    attachmentFilesSeen: "本轮附件文件数",
    attachmentChunksTotal: "附件总片段数",
    attachmentChunksSelected: "选中附件片段数",
    attachmentContextChars: "附件片段字符数",
    attachmentContextTokens: "附件片段 Token 数",
    attachmentTruncatedChunks: "未注入附件片段数",
    attachmentTruncatedChars: "未注入附件字符数",
    totalTokensEstimate: "本轮估算 Token 数",
    budgetMaxTotalTokens: "预算总 Token 上限",
    budgetMaxAttachmentTokens: "附件 Token 上限",
    tokenizerEncoding: "Tokenizer 编码",
    summaryTokens: "摘要 Token 数",
    summaryRefreshSourceTokens: "摘要源 Token 数",
    summaryCompressionRatio: "摘要压缩比",
    promptPrefixHash: "稳定前缀 Hash",
    promptPrefixTokens: "稳定前缀 Token 数",
    promptTotalTokens: "Prompt 总 Token 数",
    promptRecentHistoryTokens: "最近历史 Token 数",
    promptPrefixReusedLastTurn: "稳定前缀复用",
    attachmentPreviewTitle: "已选附件片段",
    attachmentPreviewExpand: "展开",
    attachmentPreviewCollapse: "收起",
    attachmentPreviewMeta: "片段",
    contextButton: "上下文",
    closeContextPanel: "关闭诊断",
    contextOverviewTitle: "概览",
    contextAdvancedTitle: "高级诊断",
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
    deepThinking: "Deep thinking",
    webSearch: "Web search",
    reasoningTitle: "Reasoning",
    toolTraceTitle: "Tool trace",
    sourcesTitle: "Sources",
    replyFailed: "This answer failed to generate. Please try again later.",
    replyStopped: "This answer was stopped.",
    copyAnswer: "Copy answer",
    copied: "Copied",
    regenerateAnswer: "Regenerate",
    editLastUser: "Edit and retry",
    saveAndRetry: "Save and retry",
    removeAttachment: "Remove attachment",
    editingMessage: "Editing the last user message",
    previewAttachment: "Preview attachment",
    openOriginal: "Open original",
    closePreview: "Close preview",
    editPromptTitle: "Edit the last user message",
    enterDeleteMode: "Enter delete mode",
    deleteMode: "Multi-select delete mode enabled",
    selected: "Selected",
    selectAll: "Select all",
    deselectAll: "Deselect all",
    cancel: "Cancel",
    confirm: "Confirm",
    deleteSelected: "Delete selected",
    deleteMessagesTitle: "Delete selected messages",
    deleteMessagesDescription: "Deleted messages cannot be restored. Continue?",
    inputPlaceholder: "Type your question",
    uploadAttachment: "Upload attachment",
    uploadUnsupportedType:
      "Only images (png/jpg/jpeg/webp/gif) and txt, md, pdf, docx documents are supported.",
    uploadImageTooLarge: "Image is too large. Each image must be 10MB or less.",
    uploadFileTooLarge: "Document is too large. Each file must be 20MB or less.",
    uploading: "Uploading...",
    stopGenerating: "Stop",
    sending: "Sending...",
    send: "Send",
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
    memoryEnabled: "Memory enabled",
    memoryInjected: "Memory injected",
    memoryCount: "Memory count",
    memoryChars: "Memory chars",
    budgetMaxTotalChars: "Total char budget",
    budgetMaxAttachmentChars: "Attachment char budget",
    promptTemplateVersion: "Prompt template version",
    providerTemplate: "Provider template",
    modelFamily: "Model family",
    promptLayers: "Prompt context layers",
    promptSystemLayers: "System prefix layers",
    promptHistoryMessages: "Prompt history messages",
    promptAttachmentContextInjected: "Attachment context injected",
    promptImageMessages: "Image messages",
    attachmentFilesSeen: "Attachment files this turn",
    attachmentChunksTotal: "Attachment chunks total",
    attachmentChunksSelected: "Attachment chunks selected",
    attachmentContextChars: "Attachment context chars",
    attachmentContextTokens: "Attachment context tokens",
    attachmentTruncatedChunks: "Attachment chunks skipped",
    attachmentTruncatedChars: "Attachment chars skipped",
    totalTokensEstimate: "Estimated tokens this turn",
    budgetMaxTotalTokens: "Total token budget",
    budgetMaxAttachmentTokens: "Attachment token budget",
    tokenizerEncoding: "Tokenizer encoding",
    summaryTokens: "Summary tokens",
    summaryRefreshSourceTokens: "Summary source tokens",
    summaryCompressionRatio: "Summary compression ratio",
    promptPrefixHash: "Stable prefix hash",
    promptPrefixTokens: "Stable prefix tokens",
    promptTotalTokens: "Prompt total tokens",
    promptRecentHistoryTokens: "Recent history tokens",
    promptPrefixReusedLastTurn: "Stable prefix reused",
    attachmentPreviewTitle: "Selected attachment chunks",
    attachmentPreviewExpand: "Expand",
    attachmentPreviewCollapse: "Collapse",
    attachmentPreviewMeta: "Chunk",
    contextButton: "Context",
    closeContextPanel: "Close diagnostics",
    contextOverviewTitle: "Overview",
    contextAdvancedTitle: "Advanced diagnostics",
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

function parseContextDetailsHeader(
  value: string | null
): { attachment_chunks?: ContextAttachmentChunk[] } | null {
  if (!value) {
    return null;
  }

  try {
    const decoded = atob(value);
    const bytes = Uint8Array.from(decoded, (char) => char.charCodeAt(0));
    const text = new TextDecoder().decode(bytes);
    const parsed = JSON.parse(text) as { attachment_chunks?: ContextAttachmentChunk[] };
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
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
      reasoningContent: message.reasoning_content ?? null,
      externalSources: parseExternalSources(message.external_sources),
      toolEvents: message.tool_events ?? [],
      status: message.status,
      created_at: message.created_at,
      attachments: message.attachments ?? [],
    }));
}

function parseExternalSources(value: string | null | undefined): ExternalSource[] {
  if (!value) {
    return [];
  }
  try {
    const parsed = JSON.parse(value) as unknown;
    if (Array.isArray(parsed)) {
      return parsed.filter((item): item is ExternalSource => Boolean(item) && typeof item === "object");
    }
  } catch {
    return [];
  }
  return [];
}

function isToolTraceEvent(event: ChatStreamEvent): event is ToolTraceEvent {
  return (
    event.type === "tool_plan" ||
    event.type === "tool_call_start" ||
    event.type === "tool_call_end" ||
    event.type === "tool_call_error" ||
    event.type === "tool_call_fallback"
  );
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

type ChatStreamEvent =
  | { type: "answer_delta"; text: string }
  | { type: "reasoning_delta"; text: string }
  | { type: "tool_sources"; sources: ExternalSource[] }
  | ToolTraceEvent
  | { type: "done"; assistant_message_id?: string };

function parseStreamEvents(buffer: string) {
  const lines = buffer.split("\n");
  const rest = lines.pop() ?? "";
  const events: ChatStreamEvent[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }
    try {
      const parsed = JSON.parse(trimmed) as ChatStreamEvent;
      if (parsed && typeof parsed === "object" && "type" in parsed) {
        events.push(parsed);
      }
    } catch {
      // Ignore malformed partial chunks; the server always emits newline-delimited JSON.
    }
  }
  return { events, rest };
}

export function ChatThread({
  initialConversationId,
  initialMessages,
  isLoadingMessages,
  selectedModel,
  systemPrompt,
  projectId,
  contextInfo,
  highlightedMessageId,
  uiLanguage,
  isDeepThinkingEnabled,
  isWebSearchEnabled,
  onDeepThinkingEnabledChange,
  onWebSearchEnabledChange,
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
  const [expandedChunkKeys, setExpandedChunkKeys] = useState<string[]>([]);
  const [previewItem, setPreviewItem] = useState<UploadItem | null>(null);
  const [isDeleteMessagesDialogOpen, setIsDeleteMessagesDialogOpen] = useState(false);
  const [localConversationId, setLocalConversationId] = useState<string | null>(initialConversationId);
  const [streamingStartedAt, setStreamingStartedAt] = useState<number | null>(null);
  const [streamingElapsedSeconds, setStreamingElapsedSeconds] = useState(0);
  const [editingUserMessageId, setEditingUserMessageId] = useState<string | null>(null);
  const [editingContent, setEditingContent] = useState("");
  const [editingAttachments, setEditingAttachments] = useState<UploadItem[]>([]);
  const [isEditingUploading, setIsEditingUploading] = useState(false);
  const [isSubmittingEdit, setIsSubmittingEdit] = useState(false);
  const [threadMessages, setThreadMessages] = useState<ThreadMessage[]>(() =>
    toThreadMessages(initialMessages)
  );
  const [isGenerating, setIsGenerating] = useState(false);
  const messageEndRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const editFileInputRef = useRef<HTMLInputElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const editTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const activeConversationIdRef = useRef<string | null>(initialConversationId);
  const shouldSelectAfterFinishRef = useRef(false);
  const didSyncAfterRequestRef = useRef(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const text = THREAD_TEXT[uiLanguage];
  const statEntries = Object.entries(contextInfo?.stats ?? {});
  const statMap = Object.fromEntries(statEntries);
  const attachmentChunkDetails = contextInfo?.details?.attachment_chunks ?? [];
  const formatBooleanStat = (value: string | undefined) =>
    value === "true" || value === "1" ? text.yes : value === "false" || value === "0" ? text.no : value;
  const overviewStatCards = [
    { key: "context_mode", label: text.contextMode, value: statMap.context_mode },
    { key: "model_context_window", label: text.modelContextWindow, value: statMap.model_context_window },
    { key: "total_chars_estimate", label: text.totalCharsEstimate, value: statMap.total_chars_estimate },
    { key: "total_tokens_estimate", label: text.totalTokensEstimate, value: statMap.total_tokens_estimate },
    { key: "truncated_history_messages", label: text.truncatedHistoryMessages, value: statMap.truncated_history_messages },
    { key: "summary_chars", label: text.summaryChars, value: statMap.summary_chars },
    { key: "summary_tokens", label: text.summaryTokens, value: statMap.summary_tokens },
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
      key: "memory_enabled",
      label: text.memoryEnabled,
      value: formatBooleanStat(statMap.memory_enabled),
    },
    {
      key: "memory_injected",
      label: text.memoryInjected,
      value: formatBooleanStat(statMap.memory_injected),
    },
    {
      key: "memory_count",
      label: text.memoryCount,
      value: statMap.memory_count,
    },
    {
      key: "memory_chars",
      label: text.memoryChars,
      value: statMap.memory_chars,
    },
    {
      key: "budget_max_total_chars",
      label: text.budgetMaxTotalChars,
      value: statMap.budget_max_total_chars,
    },
    {
      key: "budget_max_total_tokens",
      label: text.budgetMaxTotalTokens,
      value: statMap.budget_max_total_tokens,
    },
    {
      key: "budget_max_attachment_chars",
      label: text.budgetMaxAttachmentChars,
      value: statMap.budget_max_attachment_chars,
    },
    {
      key: "budget_max_attachment_tokens",
      label: text.budgetMaxAttachmentTokens,
      value: statMap.budget_max_attachment_tokens,
    },
    {
      key: "attachment_chunks_selected",
      label: text.attachmentChunksSelected,
      value: statMap.attachment_chunks_selected,
    },
    {
      key: "attachment_context_chars",
      label: text.attachmentContextChars,
      value: statMap.attachment_context_chars,
    },
    {
      key: "attachment_context_tokens",
      label: text.attachmentContextTokens,
      value: statMap.attachment_context_tokens,
    },
  ].filter((item) => typeof item.value === "string" && item.value.trim().length > 0);
  const advancedStatCards = [
    {
      key: "prompt_template_version",
      label: text.promptTemplateVersion,
      value: statMap.prompt_template_version,
    },
    {
      key: "provider_template",
      label: text.providerTemplate,
      value: statMap.provider_template,
    },
    {
      key: "model_family",
      label: text.modelFamily,
      value: statMap.model_family,
    },
    {
      key: "prompt_layers",
      label: text.promptLayers,
      value: statMap.prompt_layers,
    },
    {
      key: "prompt_system_layers",
      label: text.promptSystemLayers,
      value: statMap.prompt_system_layers,
    },
    {
      key: "prompt_history_messages",
      label: text.promptHistoryMessages,
      value: statMap.prompt_history_messages,
    },
    {
      key: "prompt_attachment_context_injected",
      label: text.promptAttachmentContextInjected,
      value: formatBooleanStat(statMap.prompt_attachment_context_injected),
    },
    {
      key: "prompt_prefix_hash",
      label: text.promptPrefixHash,
      value: statMap.prompt_prefix_hash,
    },
    {
      key: "prompt_prefix_tokens",
      label: text.promptPrefixTokens,
      value: statMap.prompt_prefix_tokens,
    },
    {
      key: "prompt_total_tokens",
      label: text.promptTotalTokens,
      value: statMap.prompt_total_tokens,
    },
    {
      key: "prompt_recent_history_tokens",
      label: text.promptRecentHistoryTokens,
      value: statMap.prompt_recent_history_tokens,
    },
    {
      key: "prompt_prefix_reused_last_turn",
      label: text.promptPrefixReusedLastTurn,
      value: formatBooleanStat(statMap.prompt_prefix_reused_last_turn),
    },
    {
      key: "prompt_image_messages",
      label: text.promptImageMessages,
      value: statMap.prompt_image_messages,
    },
    {
      key: "tokenizer_encoding",
      label: text.tokenizerEncoding,
      value: statMap.tokenizer_encoding,
    },
    {
      key: "attachment_files_seen",
      label: text.attachmentFilesSeen,
      value: statMap.attachment_files_seen,
    },
    {
      key: "attachment_chunks_total",
      label: text.attachmentChunksTotal,
      value: statMap.attachment_chunks_total,
    },
    {
      key: "attachment_truncated_chunks",
      label: text.attachmentTruncatedChunks,
      value: statMap.attachment_truncated_chunks,
    },
    {
      key: "attachment_truncated_chars",
      label: text.attachmentTruncatedChars,
      value: statMap.attachment_truncated_chars,
    },
    {
      key: "summary_refresh_source_tokens",
      label: text.summaryRefreshSourceTokens,
      value: statMap.summary_refresh_source_tokens,
    },
    {
      key: "summary_compression_ratio",
      label: text.summaryCompressionRatio,
      value: statMap.summary_compression_ratio,
    },
  ].filter((item) => typeof item.value === "string" && item.value.trim().length > 0);
  const hasContextDiagnostics =
    overviewStatCards.length > 0 ||
    advancedStatCards.length > 0 ||
    attachmentChunkDetails.length > 0 ||
    contextInfo?.notices.length;

  const activeConversationId = localConversationId ?? initialConversationId;
  const latestAssistantMessage = [...threadMessages].reverse().find((message) => message.role === "assistant");
  const latestUserMessage = [...threadMessages].reverse().find((message) => message.role === "user");
  const latestUserMessageId = latestUserMessage?.id ?? null;
  const latestAssistantMessageId = latestAssistantMessage?.id ?? null;
  const activeStreamingAssistantMessage = [...threadMessages]
    .reverse()
    .find((message) => message.role === "assistant" && message.status === "streaming");
  const displayError = localError;
  const hasServerStreamingMessage = Boolean(activeStreamingAssistantMessage);
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
        : hasServerStreamingMessage
          ? uiLanguage === "zh-CN"
            ? "当前会话中仍有回答正在生成，请稍候或稍后刷新。"
            : "There is still an in-flight answer in this conversation. Please wait or refresh later."
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
    if (!highlightedMessageId) {
      return;
    }
    const element = document.getElementById(`message-${highlightedMessageId}`);
    element?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [highlightedMessageId, threadMessages.length]);

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

    const maxHeight = 168;
    textarea.style.height = "auto";
    const nextHeight = Math.min(textarea.scrollHeight, maxHeight);
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
  }, [composer]);

  useEffect(() => {
    const textarea = editTextareaRef.current;
    if (!textarea || !editingUserMessageId) {
      return;
    }

    const maxHeight = 240;
    textarea.style.height = "auto";
    const nextHeight = Math.min(textarea.scrollHeight, maxHeight);
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
  }, [editingContent, editingUserMessageId]);

  useEffect(() => {
    if (!editingUserMessageId) {
      return;
    }

    editTextareaRef.current?.focus();
    const length = editTextareaRef.current?.value.length ?? 0;
    editTextareaRef.current?.setSelectionRange(length, length);
  }, [editingUserMessageId]);

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
        project_id: projectId,
      }),
    });

    setLocalConversationId(created.id);
    activeConversationIdRef.current = created.id;
    shouldSelectAfterFinishRef.current = true;
    return created.id;
  }

  async function uploadFiles(files: FileList | null) {
    if (!files || files.length === 0) {
      return [];
    }

    for (const file of Array.from(files)) {
      const kind = classifyClientFile(file);
      if (!kind) {
        throw new Error(text.uploadUnsupportedType);
      }
      if (kind === "image" && file.size > MAX_IMAGE_BYTES) {
        throw new Error(text.uploadImageTooLarge);
      }
      if (kind === "file" && file.size > MAX_FILE_BYTES) {
        throw new Error(text.uploadFileTooLarge);
      }
    }

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

    return (await response.json()) as UploadItem[];
  }

  async function handleUpload(files: FileList | null) {
    if (!files || files.length === 0) {
      return;
    }

    setIsUploading(true);
    setLocalError(null);

    try {
      const data = await uploadFiles(files);
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

  async function handleEditUpload(files: FileList | null) {
    if (!files || files.length === 0) {
      return;
    }

    setIsEditingUploading(true);
    setLocalError(null);

    try {
      const data = await uploadFiles(files);
      setEditingAttachments((current) => [...current, ...data]);
    } catch (uploadError) {
      setLocalError(`上传失败：${requestErrorMessage(uploadError)}`);
    } finally {
      setIsEditingUploading(false);
      if (editFileInputRef.current) {
        editFileInputRef.current.value = "";
      }
    }
  }

  function removeEditingAttachment(itemId: string) {
    setEditingAttachments((current) => current.filter((item) => item.id !== itemId));
  }

  async function reloadConversationMessages() {
    await onConversationMessagesChanged(activeConversationIdRef.current);
  }

  async function handleBulkDeleteMessages() {
    const conversationId = activeConversationIdRef.current;
    if (!conversationId || selectedMessageIds.length === 0 || isGenerating || isDeletingMessages) {
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
      setIsDeleteMessagesDialogOpen(false);
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

  function toggleAttachmentChunk(chunkKey: string) {
    setExpandedChunkKeys((current) =>
      current.includes(chunkKey) ? current.filter((key) => key !== chunkKey) : [...current, chunkKey]
    );
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

  async function streamIntoExistingAssistant(
    endpoint: string,
    payload: Record<string, unknown>,
    assistantMessageId: string
  ) {
    const conversationId = activeConversationIdRef.current;
    if (!conversationId || isGenerating) {
      return;
    }

    setLocalError(null);
    didSyncAfterRequestRef.current = false;
    shouldSelectAfterFinishRef.current = false;
    setStreamingStartedAt(Date.now());
    setStreamingElapsedSeconds(0);
    setExpandedChunkKeys([]);
    setIsGenerating(true);
    onContextInfoChange(null, conversationId);
    setThreadMessages((current) =>
      current.map((message) =>
        message.id === assistantMessageId
          ? {
              ...message,
              content: "",
              reasoningContent: "",
              externalSources: [],
              toolEvents: [],
              status: "streaming",
            }
          : message
      )
    );

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify(payload),
        cache: "no-store",
        signal: controller.signal,
      });

      onContextInfoChange(
        {
          notices: parseContextNoticesHeader(response.headers.get("x-context-notices")),
          stats: parseContextStatsHeader(response.headers.get("x-context-stats")),
          details: parseContextDetailsHeader(response.headers.get("x-context-details")),
        },
        response.headers.get("x-conversation-id") ?? conversationId
      );

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
      let reasoningText = "";
      let externalSources: ExternalSource[] = [];
      let toolEvents: ToolTraceEvent[] = [];
      let eventBuffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }
        if (!value) {
          continue;
        }

        eventBuffer += decoder.decode(value, { stream: true });
        const parsed = parseStreamEvents(eventBuffer);
        eventBuffer = parsed.rest;
        for (const streamEvent of parsed.events) {
          if (streamEvent.type === "answer_delta") {
            assistantText += streamEvent.text;
          } else if (streamEvent.type === "reasoning_delta") {
            reasoningText += streamEvent.text;
          } else if (streamEvent.type === "tool_sources") {
            externalSources = streamEvent.sources ?? [];
          } else if (isToolTraceEvent(streamEvent)) {
            toolEvents = [...toolEvents, streamEvent];
          }
        }
        setThreadMessages((current) =>
          current.map((message) =>
            message.id === assistantMessageId
              ? {
                  ...message,
                  content: assistantText,
                  reasoningContent: reasoningText,
                  externalSources,
                  toolEvents,
                  status: "streaming",
                }
              : message
          )
        );
      }

      eventBuffer += decoder.decode();
      const finalParsed = parseStreamEvents(`${eventBuffer}\n`);
      for (const streamEvent of finalParsed.events) {
        if (streamEvent.type === "answer_delta") {
          assistantText += streamEvent.text;
        } else if (streamEvent.type === "reasoning_delta") {
          reasoningText += streamEvent.text;
        } else if (streamEvent.type === "tool_sources") {
          externalSources = streamEvent.sources ?? [];
        } else if (isToolTraceEvent(streamEvent)) {
          toolEvents = [...toolEvents, streamEvent];
        }
      }
      setThreadMessages((current) =>
        current.map((message) =>
          message.id === assistantMessageId
            ? {
                ...message,
                content: assistantText,
                reasoningContent: reasoningText,
                externalSources,
                toolEvents,
                status: "done",
              }
            : message
        )
      );

      setStreamingStartedAt(null);
      setStreamingElapsedSeconds(0);
      syncAfterRequest();
    } catch (error) {
      const isAbort = error instanceof DOMException && error.name === "AbortError";
      setStreamingStartedAt(null);
      setStreamingElapsedSeconds(0);
      setThreadMessages((current) =>
        current.map((message) =>
          message.id === assistantMessageId
            ? {
                ...message,
                status: isAbort ? "cancelled" : "failed",
              }
            : message
        )
      );
      if (!isAbort) {
        setLocalError(requestErrorMessage(error));
      }
      syncAfterRequest();
      throw error;
    } finally {
      setIsGenerating(false);
      abortControllerRef.current = null;
    }
  }

  async function handleRegenerateLastAssistant(assistantMessageId: string) {
    const conversationId = activeConversationIdRef.current;
    if (!conversationId || !latestAssistantMessageId || assistantMessageId !== latestAssistantMessageId) {
      return;
    }

    try {
      await streamIntoExistingAssistant("/api/chat/regenerate", {
        conversationId,
        assistantMessageId,
        modelName: selectedModel,
        systemPrompt,
        thinkingEnabled: isDeepThinkingEnabled,
        webSearchEnabled: isWebSearchEnabled,
      }, assistantMessageId);
    } catch {
      // localError 已在 helper 中设置
    }
  }

  function beginEditLastUser(message: ThreadMessage) {
    if (
      isGenerating ||
      message.role !== "user" ||
      message.id !== latestUserMessageId ||
      latestAssistantMessageId === null
    ) {
      return;
    }

    setIsManageMode(false);
    setSelectedMessageIds([]);
    setEditingUserMessageId(message.id);
    setEditingContent(message.content);
    setEditingAttachments(cloneUploadItems(message.attachments));
    setLocalError(null);
  }

  function cancelEditLastUser() {
    if (isSubmittingEdit) {
      return;
    }

    setEditingUserMessageId(null);
    setEditingContent("");
    setEditingAttachments([]);
    if (editFileInputRef.current) {
      editFileInputRef.current.value = "";
    }
  }

  async function handleEditLastUserSubmit(messageId: string) {
    const conversationId = activeConversationIdRef.current;
    if (
      !conversationId ||
      !latestUserMessageId ||
      !latestAssistantMessageId ||
      messageId !== latestUserMessageId
    ) {
      return;
    }

    const nextContent = editingContent.trim();
    if (!nextContent || isSubmittingEdit) {
      return;
    }

    const nextAttachments = cloneUploadItems(editingAttachments);
    setIsSubmittingEdit(true);
    setEditingUserMessageId(null);
    setThreadMessages((current) =>
      current.map((message) =>
        message.id === messageId
          ? {
              ...message,
              content: nextContent,
              attachments: nextAttachments,
            }
          : message
      )
    );

    try {
      await streamIntoExistingAssistant("/api/chat/edit-last-user", {
        conversationId,
        userMessageId: messageId,
        assistantMessageId: latestAssistantMessageId,
        content: nextContent,
        attachments: nextAttachments,
        modelName: selectedModel,
        systemPrompt,
        thinkingEnabled: isDeepThinkingEnabled,
        webSearchEnabled: isWebSearchEnabled,
      }, latestAssistantMessageId);
    } catch {
      // 用户消息在后端已被更新，失败时保留编辑结果，只标记回答失败。
    } finally {
      setIsSubmittingEdit(false);
      setEditingContent("");
      setEditingAttachments([]);
      if (editFileInputRef.current) {
        editFileInputRef.current.value = "";
      }
    }
  }

  async function handleSubmit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();

    const content = composer.trim();
    if (!content || isGenerating || Boolean(editingUserMessageId)) {
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
    setExpandedChunkKeys([]);
    setIsGenerating(true);
    onContextInfoChange(null, requestConversationId);

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
        reasoningContent: "",
        externalSources: [],
        toolEvents: [],
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
          thinkingEnabled: isDeepThinkingEnabled,
          webSearchEnabled: isWebSearchEnabled,
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

      const responseConversationId =
        response.headers.get("x-conversation-id") ?? requestConversationId;
      onContextInfoChange(
        {
          notices: parseContextNoticesHeader(response.headers.get("x-context-notices")),
          stats: parseContextStatsHeader(response.headers.get("x-context-stats")),
          details: parseContextDetailsHeader(response.headers.get("x-context-details")),
        },
        responseConversationId
      );
      if (
        !response.headers.get("x-context-stats") &&
        !response.headers.get("x-context-notices") &&
        !response.headers.get("x-context-details")
      ) {
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
      let reasoningText = "";
      let externalSources: ExternalSource[] = [];
      let toolEvents: ToolTraceEvent[] = [];
      let eventBuffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }

        if (!value) {
          continue;
        }

        eventBuffer += decoder.decode(value, { stream: true });
        const parsed = parseStreamEvents(eventBuffer);
        eventBuffer = parsed.rest;
        for (const streamEvent of parsed.events) {
          if (streamEvent.type === "answer_delta") {
            assistantText += streamEvent.text;
          } else if (streamEvent.type === "reasoning_delta") {
            reasoningText += streamEvent.text;
          } else if (streamEvent.type === "tool_sources") {
            externalSources = streamEvent.sources ?? [];
          } else if (isToolTraceEvent(streamEvent)) {
            toolEvents = [...toolEvents, streamEvent];
          }
        }
        setThreadMessages((current) =>
          current.map((message) =>
            message.id === tempAssistantMessageId
              ? {
                  ...message,
                  content: assistantText,
                  reasoningContent: reasoningText,
                  externalSources,
                  toolEvents,
                  status: "streaming",
                }
              : message
          )
        );
      }

      eventBuffer += decoder.decode();
      const finalParsed = parseStreamEvents(`${eventBuffer}\n`);
      for (const streamEvent of finalParsed.events) {
        if (streamEvent.type === "answer_delta") {
          assistantText += streamEvent.text;
        } else if (streamEvent.type === "reasoning_delta") {
          reasoningText += streamEvent.text;
        } else if (streamEvent.type === "tool_sources") {
          externalSources = streamEvent.sources ?? [];
        } else if (isToolTraceEvent(streamEvent)) {
          toolEvents = [...toolEvents, streamEvent];
        }
      }
      setThreadMessages((current) =>
        current.map((message) =>
          message.id === tempAssistantMessageId
            ? {
                ...message,
                content: assistantText,
                reasoningContent: reasoningText,
                externalSources,
                toolEvents,
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
      if (!composer.trim() || isGenerating || Boolean(editingUserMessageId)) {
        return;
      }
      await handleSubmit();
    }
  }

  return (
    <>
      <div className="chat-scroll min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-5">
        <div className="mx-auto flex w-full max-w-[74rem] flex-col gap-4">
          {threadMessages.length === 0 && !isLoadingMessages ? (
            <div className="rounded-[22px] border border-dashed border-[var(--control-border)] bg-[var(--soft-bg)] px-5 py-6 text-center">
              <p className="text-lg font-medium">{text.emptyTitle}</p>
              <p className="mt-2 text-sm text-[var(--ink-soft)]">{text.emptySubtitle}</p>
            </div>
          ) : null}

          {isLoadingMessages ? (
            <div className="rounded-2xl border border-[var(--control-border)] bg-[var(--soft-bg)] px-5 py-3 text-sm text-[var(--ink-soft)]">
              {text.loadingHistory}
            </div>
          ) : null}

          {threadMessages.map((message) => {
            const isUser = message.role === "user";
            const isEditingThisUser = isUser && editingUserMessageId === message.id;
            const isStreamingAssistant = message.role === "assistant" && message.status === "streaming";
            const isLatestStreamingAssistant = message.id === activeStreamingAssistantMessage?.id;
            const isWaitingAssistant = isStreamingAssistant && !message.content.trim();
            const isSelected = selectedMessageIds.includes(message.id);
            const showAssistantActionBar = !isGenerating && !isStreamingAssistant && message.role === "assistant";
            const showUserActionBar =
              !isGenerating &&
              message.role === "user" &&
              message.id === latestUserMessageId &&
              latestAssistantMessageId !== null &&
              !isEditingThisUser;
            const messageStatus = message.status;
            const messageTime = formatMessageTime(message.created_at, uiLanguage);
            const shouldShowWaitingPlaceholder = isWaitingAssistant;
            const shouldShowStreamingIndicator = isStreamingAssistant && message.content.trim();
            const isHighlighted = highlightedMessageId === message.id;

            return (
              <article
                key={message.id}
                id={`message-${message.id}`}
                className={`message-card group max-w-[96%] rounded-[22px] px-4 py-3.5 lg:max-w-[86%] ${
                  isUser
                    ? "message-user ml-auto"
                    : "message-assistant"
                } ${
                  isSelected || isHighlighted
                    ? "ring-2 ring-[var(--accent-strong)] ring-offset-2 ring-offset-transparent"
                    : ""
                }`}
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
                {isEditingThisUser ? (
                  <div className="rounded-[20px] border border-white/14 bg-white/10 p-3">
                    <p className="mb-2 text-[11px] uppercase tracking-[0.18em] text-white/55">
                      {text.editingMessage}
                    </p>
                    <textarea
                      ref={editTextareaRef}
                      value={editingContent}
                      onChange={(event) => setEditingContent(event.target.value)}
                      rows={3}
                      className="min-h-[84px] w-full resize-none border-none bg-transparent text-sm leading-6 text-white outline-none placeholder:text-white/35 sm:text-[15px]"
                      placeholder={text.inputPlaceholder}
                    />

                    {editingAttachments.length > 0 ? (
                      <div className="mt-3 flex flex-wrap gap-3">
                        {editingAttachments.map((attachment) =>
                          isImageAttachment(attachment) ? (
                            <div
                              key={attachment.id}
                              onClick={() => setPreviewItem(attachment)}
                              onKeyDown={(event) => {
                                if (event.key === "Enter" || event.key === " ") {
                                  event.preventDefault();
                                  setPreviewItem(attachment);
                                }
                              }}
                              role="button"
                              tabIndex={0}
                              className="group relative block cursor-pointer overflow-hidden rounded-2xl border border-white/14 bg-white/8 text-left"
                            >
                              <img
                                src={buildAttachmentUrl(attachment.storage_key)}
                                alt={attachment.file_name}
                                className="h-28 w-28 object-cover transition group-hover:scale-[1.02]"
                              />
                              <div className="flex w-28 flex-col gap-0.5 px-2.5 py-2 text-[11px]">
                                <span className="truncate text-white/88">{attachment.file_name}</span>
                                <span className="text-white/45">{formatFileSize(attachment.file_size ?? 0)}</span>
                              </div>
                              <button
                                type="button"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  removeEditingAttachment(attachment.id);
                                }}
                                className="absolute right-2 top-2 rounded-full bg-[rgba(16,31,24,0.72)] px-2 py-1 text-[10px] text-white/88"
                              >
                                {uiLanguage === "en-US" ? "Remove" : "移除"}
                              </button>
                            </div>
                          ) : (
                            <div
                              key={attachment.id}
                              onClick={() => setPreviewItem(attachment)}
                              onKeyDown={(event) => {
                                if (event.key === "Enter" || event.key === " ") {
                                  event.preventDefault();
                                  setPreviewItem(attachment);
                                }
                              }}
                              role="button"
                              tabIndex={0}
                              className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-white/14 bg-white/10 px-3 py-2 text-xs text-white/88"
                            >
                              <span>{attachmentKindLabel(attachment.kind, uiLanguage)}</span>
                              <span className="max-w-[240px] truncate">{attachment.file_name}</span>
                              <span>{formatFileSize(attachment.file_size ?? 0)}</span>
                              <button
                                type="button"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  removeEditingAttachment(attachment.id);
                                }}
                                className="rounded-full border border-white/14 px-2 py-0.5 text-[10px] text-white/75"
                              >
                                {uiLanguage === "en-US" ? "Remove" : "移除"}
                              </button>
                            </div>
                          )
                        )}
                      </div>
                    ) : null}

                    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-white/12 pt-3">
                      <button
                        type="button"
                        onClick={() => editFileInputRef.current?.click()}
                        disabled={isEditingUploading || isSubmittingEdit}
                        className="rounded-full border border-white/16 bg-white/10 px-3 py-1.5 text-xs text-white/82 transition hover:bg-white/14 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {isEditingUploading ? text.uploading : text.uploadAttachment}
                      </button>
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          onClick={cancelEditLastUser}
                          disabled={isSubmittingEdit}
                          className="rounded-full border border-white/16 bg-transparent px-3 py-1.5 text-xs text-white/72 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {text.cancel}
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleEditLastUserSubmit(message.id)}
                          disabled={!editingContent.trim() || isSubmittingEdit}
                          className="rounded-full bg-white px-3.5 py-1.5 text-xs font-medium text-[#16221b] transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {isSubmittingEdit ? text.sending : text.saveAndRetry}
                        </button>
                      </div>
                    </div>
                  </div>
                ) : isUser ? (
                  <div className="whitespace-pre-wrap text-sm leading-6 sm:text-[15px]">
                    {message.content || (shouldShowWaitingPlaceholder ? text.waitPlaceholder : "")}
                  </div>
                ) : (
                  <MessageMarkdown
                    content={message.content || (shouldShowWaitingPlaceholder ? text.waitPlaceholder : "")}
                    isStreaming={Boolean(shouldShowStreamingIndicator && isLatestStreamingAssistant)}
                  />
                )}
                {!isUser && message.reasoningContent?.trim() ? (
                  <details className="reasoning-panel mt-3 rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-3 py-2 text-xs text-[var(--ink-soft)]">
                    <summary className="cursor-pointer select-none font-medium text-[var(--ink-strong)]">
                      {text.reasoningTitle}
                    </summary>
                    <div className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap leading-5">
                      {message.reasoningContent}
                    </div>
                  </details>
                ) : null}
                {!isUser && message.toolEvents && message.toolEvents.length > 0 ? (
                  <ToolTracePanel
                    events={message.toolEvents}
                    title={text.toolTraceTitle}
                    uiLanguage={uiLanguage}
                  />
                ) : null}
                {!isUser && message.externalSources && message.externalSources.length > 0 ? (
                  <details className="reasoning-panel mt-3 rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-3 py-2 text-xs text-[var(--ink-soft)]">
                    <summary className="cursor-pointer select-none font-medium text-[var(--ink-strong)]">
                      {text.sourcesTitle} · {message.externalSources.length}
                    </summary>
                    <div className="mt-2 grid gap-2">
                      {message.externalSources.slice(0, 6).map((source, index) => (
                        <ExternalSourceCard
                          key={`${source.provider}-${source.title}-${index}`}
                          source={source}
                          index={index}
                        />
                      ))}
                    </div>
                  </details>
                ) : null}
                {!isEditingThisUser && message.attachments.length > 0 ? (
                  <div className="mt-4 flex flex-wrap gap-3">
                    {message.attachments.map((attachment) =>
                      isImageAttachment(attachment) ? (
                        <button
                          key={attachment.id}
                          type="button"
                          onClick={() => setPreviewItem(attachment)}
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
                            <span className={isUser ? "text-white/45" : "text-[var(--ink-muted)]"}>
                              {formatFileSize(attachment.file_size ?? 0)}
                            </span>
                          </div>
                        </button>
                      ) : (
                        <button
                          key={attachment.id}
                          type="button"
                          onClick={() => setPreviewItem(attachment)}
                          className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs ${
                            isUser
                              ? "border-white/14 bg-white/10 text-white/82"
                              : "border-[rgba(22,34,27,0.1)] bg-[rgba(248,244,234,0.92)] text-[var(--ink-soft)]"
                          }`}
                        >
                          <span>{attachmentKindLabel(attachment.kind, uiLanguage)}</span>
                          <span className="max-w-[260px] truncate">{attachment.file_name}</span>
                          <span>{formatFileSize(attachment.file_size ?? 0)}</span>
                        </button>
                      )
                    )}
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
                {showAssistantActionBar ? (
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
                    {message.id === latestAssistantMessageId ? (
                      <button
                        type="button"
                        onClick={() => void handleRegenerateLastAssistant(message.id)}
                        className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-[rgba(22,34,27,0.12)] bg-[rgba(248,244,234,0.92)] text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] hover:text-[var(--ink-strong)]"
                        aria-label={text.regenerateAnswer}
                        title={text.regenerateAnswer}
                      >
                        <svg viewBox="0 0 24 24" className="h-4 w-4 fill-none stroke-current stroke-2">
                          <path d="M20 11a8 8 0 1 1-2.34-5.66" strokeLinecap="round" strokeLinejoin="round" />
                          <path d="M20 4v7h-7" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </button>
                    ) : null}
                  </div>
                ) : null}
                {showUserActionBar ? (
                  <div className="mt-2.5 flex items-center gap-2 opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100">
                    <button
                      type="button"
                      onClick={() => beginEditLastUser(message)}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-white/18 bg-white/10 text-white/82 transition hover:bg-white/16"
                      aria-label={text.editLastUser}
                      title={text.editLastUser}
                    >
                      <svg viewBox="0 0 24 24" className="h-4 w-4 fill-none stroke-current stroke-2">
                        <path d="M12 20h9" strokeLinecap="round" />
                        <path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5Z" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </button>
                  </div>
                ) : null}
              </article>
            );
          })}

          {isGenerating && !activeStreamingAssistantMessage ? (
            <article className="message-card message-assistant max-w-[96%] rounded-[22px] px-4 py-3.5 text-[var(--ink-strong)] lg:max-w-[86%]">
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

      <footer className="composer-footer border-t px-3 py-2.5 sm:px-5">
        {displayError ? (
          <div className="mx-auto mb-2.5 w-full max-w-[74rem] rounded-2xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-4 py-3 text-sm text-[var(--danger-text)]">
            {displayError}
          </div>
        ) : null}

        {isManageMode ? (
          <div className="mx-auto mb-2.5 flex w-full max-w-[74rem] flex-wrap items-center justify-between gap-3 rounded-[20px] border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2.5 shadow-[var(--composer-shadow)]">
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
                onClick={() => setIsDeleteMessagesDialogOpen(true)}
                disabled={selectedMessageIds.length === 0 || isDeletingMessages}
                className="rounded-full border border-[rgba(185,66,42,0.16)] bg-[rgba(255,238,231,0.95)] px-3 py-1.5 text-xs text-[#8f3524] transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {text.deleteSelected}
              </button>
            </div>
          </div>
        ) : null}

        <ChatComposer
          text={text}
          uiLanguage={uiLanguage}
          composer={composer}
          uploadedItems={uploadedItems}
          isUploading={isUploading}
          isGenerating={isGenerating}
          isEditingUserMessage={Boolean(editingUserMessageId)}
          streamingStatusLabel={streamingStatusLabel}
          isWebSearchEnabled={isWebSearchEnabled}
          isDeepThinkingEnabled={isDeepThinkingEnabled}
          contextInfo={contextInfo}
          hasContextDiagnostics={Boolean(hasContextDiagnostics)}
          overviewStatCards={overviewStatCards}
          advancedStatCards={advancedStatCards}
          attachmentChunkDetails={attachmentChunkDetails}
          expandedChunkKeys={expandedChunkKeys}
          isContextPanelOpen={isContextPanelOpen}
          fileInputRef={fileInputRef}
          editFileInputRef={editFileInputRef}
          composerRef={composerRef}
          onComposerChange={setComposer}
          onUpload={handleUpload}
          onEditUpload={handleEditUpload}
          onPreviewAttachment={setPreviewItem}
          onRemoveUploadedItem={removeUploadedItem}
          onWebSearchEnabledChange={onWebSearchEnabledChange}
          onDeepThinkingEnabledChange={onDeepThinkingEnabledChange}
          onToggleContextPanel={() => setIsContextPanelOpen((current) => !current)}
          onCloseContextPanel={() => setIsContextPanelOpen(false)}
          onToggleAttachmentChunk={toggleAttachmentChunk}
          onComposerKeyDown={handleComposerKeyDown}
          onSubmit={handleSubmit}
          onStopGenerating={() => abortControllerRef.current?.abort()}
        />
      </footer>

      {isDeleteMessagesDialogOpen ? (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-[rgba(16,31,24,0.42)] p-4 backdrop-blur-sm">
          <div className="w-full max-w-md overflow-hidden rounded-[28px] border border-[var(--panel-border)] bg-[var(--modal-bg)] shadow-[var(--panel-shadow)]">
            <div className="border-b border-[var(--hairline)] px-5 py-4">
              <p className="text-xs uppercase tracking-[0.22em] text-[var(--ink-muted)]">
                {text.deleteSelected}
              </p>
              <h3 className="mt-1 text-2xl font-semibold text-[var(--ink-strong)]">
                {text.deleteMessagesTitle}
              </h3>
              <p className="mt-2 text-sm leading-6 text-[var(--ink-soft)]">
                {text.deleteMessagesDescription}
              </p>
            </div>
            <div className="px-5 py-5">
              <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-4 py-3 text-sm text-[var(--ink-soft)]">
                {text.selected} {selectedMessageIds.length} / {threadMessages.length}
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-[var(--hairline)] px-5 py-4">
              <button
                type="button"
                onClick={() => setIsDeleteMessagesDialogOpen(false)}
                disabled={isDeletingMessages}
                className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] disabled:cursor-not-allowed disabled:opacity-55"
              >
                {text.cancel}
              </button>
              <button
                type="button"
                onClick={() => void handleBulkDeleteMessages()}
                disabled={selectedMessageIds.length === 0 || isDeletingMessages}
                className="rounded-full border border-[rgba(185,66,42,0.18)] bg-[var(--danger-bg)] px-5 py-2 text-sm font-medium text-[var(--danger-text)] transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-55"
              >
                {text.confirm}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <AttachmentPreviewModal
        item={previewItem}
        openOriginalText={text.openOriginal}
        closeText={text.closePreview}
        onClose={() => setPreviewItem(null)}
      />
    </>
  );
}

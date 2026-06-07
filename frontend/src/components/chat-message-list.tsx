"use client";
/* eslint-disable @next/next/no-img-element */

import type { RefObject } from "react";

import { ExternalSourceCard } from "@/components/external-source-card";
import { MessageMarkdown } from "@/components/message-markdown";
import { ToolTracePanel } from "@/components/tool-trace-panel";
import {
  attachmentKindLabel,
  buildAttachmentUrl,
  formatFileSize,
  isImageAttachment,
} from "@/lib/attachments";
import type { UILanguage } from "@/lib/settings";
import type { ExternalSource, ToolTraceEvent, UploadItem } from "@/lib/types";

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

type ChatMessageListText = {
  user: string;
  assistant: string;
  thinking: string;
  loadingHistory: string;
  emptyTitle: string;
  emptySubtitle: string;
  waitPlaceholder: string;
  firstToken: string;
  firstTokenGenerating: string;
  streaming: string;
  reasoningTitle: string;
  toolTraceTitle: string;
  sourcesTitle: string;
  replyFailed: string;
  replyStopped: string;
  copyAnswer: string;
  copied: string;
  regenerateAnswer: string;
  editLastUser: string;
  saveAndRetry: string;
  editingMessage: string;
  inputPlaceholder: string;
  uploadAttachment: string;
  uploading: string;
  cancel: string;
  sending: string;
  enterDeleteMode: string;
};

type ChatMessageListProps = {
  messages: ThreadMessage[];
  text: ChatMessageListText;
  uiLanguage: UILanguage;
  isLoadingMessages: boolean;
  isGenerating: boolean;
  activeStreamingAssistantMessageId: string | null;
  editingUserMessageId: string | null;
  latestUserMessageId: string | null;
  latestAssistantMessageId: string | null;
  highlightedMessageId: string | null;
  selectedMessageIds: string[];
  copiedMessageId: string | null;
  isManageMode: boolean;
  isDeletingMessages: boolean;
  editingContent: string;
  editingAttachments: UploadItem[];
  isEditingUploading: boolean;
  isSubmittingEdit: boolean;
  editTextareaRef: RefObject<HTMLTextAreaElement | null>;
  editFileInputRef: RefObject<HTMLInputElement | null>;
  messageEndRef: RefObject<HTMLDivElement | null>;
  onEditingContentChange: (value: string) => void;
  onToggleSelectMessage: (messageId: string) => void;
  onPreviewAttachment: (attachment: UploadItem) => void;
  onRemoveEditingAttachment: (attachmentId: string) => void;
  onCancelEditLastUser: () => void;
  onSubmitEditLastUser: (messageId: string) => void | Promise<void>;
  onCopyMessage: (messageId: string, content: string) => void | Promise<void>;
  onEnterManageMode: (messageId?: string) => void;
  onRegenerateLastAssistant: (assistantMessageId: string) => void | Promise<void>;
  onBeginEditLastUser: (message: ThreadMessage) => void;
  formatMessageTime: (value: string, uiLanguage: UILanguage) => string;
};

export function ChatMessageList({
  messages,
  text,
  uiLanguage,
  isLoadingMessages,
  isGenerating,
  activeStreamingAssistantMessageId,
  editingUserMessageId,
  latestUserMessageId,
  latestAssistantMessageId,
  highlightedMessageId,
  selectedMessageIds,
  copiedMessageId,
  isManageMode,
  isDeletingMessages,
  editingContent,
  editingAttachments,
  isEditingUploading,
  isSubmittingEdit,
  editTextareaRef,
  editFileInputRef,
  messageEndRef,
  onEditingContentChange,
  onToggleSelectMessage,
  onPreviewAttachment,
  onRemoveEditingAttachment,
  onCancelEditLastUser,
  onSubmitEditLastUser,
  onCopyMessage,
  onEnterManageMode,
  onRegenerateLastAssistant,
  onBeginEditLastUser,
  formatMessageTime,
}: ChatMessageListProps) {
  return (
    <div className="chat-scroll min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-5">
      <div className="mx-auto flex w-full max-w-[74rem] flex-col gap-4">
        {messages.length === 0 && !isLoadingMessages ? (
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

        {messages.map((message) => (
          <MessageItem
            key={message.id}
            message={message}
            text={text}
            uiLanguage={uiLanguage}
            isGenerating={isGenerating}
            activeStreamingAssistantMessageId={activeStreamingAssistantMessageId}
            editingUserMessageId={editingUserMessageId}
            latestUserMessageId={latestUserMessageId}
            latestAssistantMessageId={latestAssistantMessageId}
            highlightedMessageId={highlightedMessageId}
            selectedMessageIds={selectedMessageIds}
            copiedMessageId={copiedMessageId}
            isManageMode={isManageMode}
            isDeletingMessages={isDeletingMessages}
            editingContent={editingContent}
            editingAttachments={editingAttachments}
            isEditingUploading={isEditingUploading}
            isSubmittingEdit={isSubmittingEdit}
            editTextareaRef={editTextareaRef}
            editFileInputRef={editFileInputRef}
            onEditingContentChange={onEditingContentChange}
            onToggleSelectMessage={onToggleSelectMessage}
            onPreviewAttachment={onPreviewAttachment}
            onRemoveEditingAttachment={onRemoveEditingAttachment}
            onCancelEditLastUser={onCancelEditLastUser}
            onSubmitEditLastUser={onSubmitEditLastUser}
            onCopyMessage={onCopyMessage}
            onEnterManageMode={onEnterManageMode}
            onRegenerateLastAssistant={onRegenerateLastAssistant}
            onBeginEditLastUser={onBeginEditLastUser}
            formatMessageTime={formatMessageTime}
          />
        ))}

        {isGenerating && !activeStreamingAssistantMessageId ? (
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
  );
}

function MessageItem({
  message,
  text,
  uiLanguage,
  isGenerating,
  activeStreamingAssistantMessageId,
  editingUserMessageId,
  latestUserMessageId,
  latestAssistantMessageId,
  highlightedMessageId,
  selectedMessageIds,
  copiedMessageId,
  isManageMode,
  isDeletingMessages,
  editingContent,
  editingAttachments,
  isEditingUploading,
  isSubmittingEdit,
  editTextareaRef,
  editFileInputRef,
  onEditingContentChange,
  onToggleSelectMessage,
  onPreviewAttachment,
  onRemoveEditingAttachment,
  onCancelEditLastUser,
  onSubmitEditLastUser,
  onCopyMessage,
  onEnterManageMode,
  onRegenerateLastAssistant,
  onBeginEditLastUser,
  formatMessageTime,
}: Omit<ChatMessageListProps, "messages" | "isLoadingMessages" | "messageEndRef"> & {
  message: ThreadMessage;
}) {
  const isUser = message.role === "user";
  const isEditingThisUser = isUser && editingUserMessageId === message.id;
  const isStreamingAssistant = message.role === "assistant" && message.status === "streaming";
  const isLatestStreamingAssistant = message.id === activeStreamingAssistantMessageId;
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
  const shouldShowStreamingIndicator = Boolean(isStreamingAssistant && message.content.trim());
  const isHighlighted = highlightedMessageId === message.id;

  return (
    <article
      id={`message-${message.id}`}
      className={`message-card group max-w-[96%] rounded-[22px] px-4 py-3.5 lg:max-w-[86%] ${
        isUser ? "message-user ml-auto" : "message-assistant"
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
              onChange={() => onToggleSelectMessage(message.id)}
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
        <MessageEditPanel
          text={text}
          uiLanguage={uiLanguage}
          messageId={message.id}
          editingContent={editingContent}
          editingAttachments={editingAttachments}
          isEditingUploading={isEditingUploading}
          isSubmittingEdit={isSubmittingEdit}
          editTextareaRef={editTextareaRef}
          editFileInputRef={editFileInputRef}
          onEditingContentChange={onEditingContentChange}
          onPreviewAttachment={onPreviewAttachment}
          onRemoveEditingAttachment={onRemoveEditingAttachment}
          onCancelEditLastUser={onCancelEditLastUser}
          onSubmitEditLastUser={onSubmitEditLastUser}
        />
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
        <ToolTracePanel events={message.toolEvents} title={text.toolTraceTitle} uiLanguage={uiLanguage} />
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
                sourceId={
                  typeof source.metadata?.call_id === "string"
                    ? `source-call-${source.metadata.call_id}-${index + 1}`
                    : `source-${message.id}-${index + 1}`
                }
              />
            ))}
          </div>
        </details>
      ) : null}

      {!isEditingThisUser && message.attachments.length > 0 ? (
        <MessageAttachments
          attachments={message.attachments}
          isUser={isUser}
          uiLanguage={uiLanguage}
          onPreviewAttachment={onPreviewAttachment}
        />
      ) : null}

      {shouldShowWaitingPlaceholder ? (
        <StreamingBadge label={text.firstTokenGenerating} />
      ) : null}
      {shouldShowStreamingIndicator ? (
        <StreamingBadge label={text.streaming} />
      ) : null}
      {!isUser && (messageStatus === "failed" || messageStatus === "cancelled") ? (
        <div className="mt-2.5 rounded-2xl border border-[rgba(185,66,42,0.14)] bg-[rgba(255,238,231,0.92)] px-3 py-2 text-xs text-[#8f3524]">
          {messageStatus === "failed" ? text.replyFailed : text.replyStopped}
        </div>
      ) : null}

      <MessageActions
        message={message}
        text={text}
        copiedMessageId={copiedMessageId}
        showAssistantActionBar={showAssistantActionBar}
        showUserActionBar={showUserActionBar}
        latestAssistantMessageId={latestAssistantMessageId}
        isDeletingMessages={isDeletingMessages}
        onCopyMessage={onCopyMessage}
        onEnterManageMode={onEnterManageMode}
        onRegenerateLastAssistant={onRegenerateLastAssistant}
        onBeginEditLastUser={onBeginEditLastUser}
      />
    </article>
  );
}

function MessageEditPanel({
  text,
  uiLanguage,
  messageId,
  editingContent,
  editingAttachments,
  isEditingUploading,
  isSubmittingEdit,
  editTextareaRef,
  editFileInputRef,
  onEditingContentChange,
  onPreviewAttachment,
  onRemoveEditingAttachment,
  onCancelEditLastUser,
  onSubmitEditLastUser,
}: {
  text: ChatMessageListText;
  uiLanguage: UILanguage;
  messageId: string;
  editingContent: string;
  editingAttachments: UploadItem[];
  isEditingUploading: boolean;
  isSubmittingEdit: boolean;
  editTextareaRef: RefObject<HTMLTextAreaElement | null>;
  editFileInputRef: RefObject<HTMLInputElement | null>;
  onEditingContentChange: (value: string) => void;
  onPreviewAttachment: (attachment: UploadItem) => void;
  onRemoveEditingAttachment: (attachmentId: string) => void;
  onCancelEditLastUser: () => void;
  onSubmitEditLastUser: (messageId: string) => void | Promise<void>;
}) {
  return (
    <div className="rounded-[20px] border border-white/14 bg-white/10 p-3">
      <p className="mb-2 text-[11px] uppercase tracking-[0.18em] text-white/55">
        {text.editingMessage}
      </p>
      <textarea
        ref={editTextareaRef}
        value={editingContent}
        onChange={(event) => onEditingContentChange(event.target.value)}
        rows={3}
        className="min-h-[84px] w-full resize-none border-none bg-transparent text-sm leading-6 text-white outline-none placeholder:text-white/35 sm:text-[15px]"
        placeholder={text.inputPlaceholder}
      />

      {editingAttachments.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-3">
          {editingAttachments.map((attachment) => (
            <EditingAttachmentChip
              key={attachment.id}
              attachment={attachment}
              uiLanguage={uiLanguage}
              onPreviewAttachment={onPreviewAttachment}
              onRemoveEditingAttachment={onRemoveEditingAttachment}
            />
          ))}
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
            onClick={onCancelEditLastUser}
            disabled={isSubmittingEdit}
            className="rounded-full border border-white/16 bg-transparent px-3 py-1.5 text-xs text-white/72 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {text.cancel}
          </button>
          <button
            type="button"
            onClick={() => void onSubmitEditLastUser(messageId)}
            disabled={!editingContent.trim() || isSubmittingEdit}
            className="rounded-full bg-white px-3.5 py-1.5 text-xs font-medium text-[#16221b] transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isSubmittingEdit ? text.sending : text.saveAndRetry}
          </button>
        </div>
      </div>
    </div>
  );
}

function EditingAttachmentChip({
  attachment,
  uiLanguage,
  onPreviewAttachment,
  onRemoveEditingAttachment,
}: {
  attachment: UploadItem;
  uiLanguage: UILanguage;
  onPreviewAttachment: (attachment: UploadItem) => void;
  onRemoveEditingAttachment: (attachmentId: string) => void;
}) {
  if (isImageAttachment(attachment)) {
    return (
      <div
        onClick={() => onPreviewAttachment(attachment)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onPreviewAttachment(attachment);
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
            onRemoveEditingAttachment(attachment.id);
          }}
          className="absolute right-2 top-2 rounded-full bg-[rgba(16,31,24,0.72)] px-2 py-1 text-[10px] text-white/88"
        >
          {uiLanguage === "en-US" ? "Remove" : "移除"}
        </button>
      </div>
    );
  }

  return (
    <div
      onClick={() => onPreviewAttachment(attachment)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onPreviewAttachment(attachment);
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
          onRemoveEditingAttachment(attachment.id);
        }}
        className="rounded-full border border-white/14 px-2 py-0.5 text-[10px] text-white/75"
      >
        {uiLanguage === "en-US" ? "Remove" : "移除"}
      </button>
    </div>
  );
}

function MessageAttachments({
  attachments,
  isUser,
  uiLanguage,
  onPreviewAttachment,
}: {
  attachments: UploadItem[];
  isUser: boolean;
  uiLanguage: UILanguage;
  onPreviewAttachment: (attachment: UploadItem) => void;
}) {
  return (
    <div className="mt-4 flex flex-wrap gap-3">
      {attachments.map((attachment) =>
        isImageAttachment(attachment) ? (
          <button
            key={attachment.id}
            type="button"
            onClick={() => onPreviewAttachment(attachment)}
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
            onClick={() => onPreviewAttachment(attachment)}
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
  );
}

function StreamingBadge({ label }: { label: string }) {
  return (
    <div className="mt-2.5 inline-flex items-center gap-2 rounded-full border border-[rgba(22,34,27,0.08)] bg-[rgba(248,244,234,0.8)] px-3 py-1 text-[11px] text-[var(--ink-muted)]">
      <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent-strong)] animate-pulse" />
      <span>{label}</span>
    </div>
  );
}

function MessageActions({
  message,
  text,
  copiedMessageId,
  showAssistantActionBar,
  showUserActionBar,
  latestAssistantMessageId,
  isDeletingMessages,
  onCopyMessage,
  onEnterManageMode,
  onRegenerateLastAssistant,
  onBeginEditLastUser,
}: {
  message: ThreadMessage;
  text: ChatMessageListText;
  copiedMessageId: string | null;
  showAssistantActionBar: boolean;
  showUserActionBar: boolean;
  latestAssistantMessageId: string | null;
  isDeletingMessages: boolean;
  onCopyMessage: (messageId: string, content: string) => void | Promise<void>;
  onEnterManageMode: (messageId?: string) => void;
  onRegenerateLastAssistant: (assistantMessageId: string) => void | Promise<void>;
  onBeginEditLastUser: (message: ThreadMessage) => void;
}) {
  if (showAssistantActionBar) {
    return (
      <div className="mt-2.5 flex items-center gap-2 opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100">
        <button
          type="button"
          onClick={() => void onCopyMessage(message.id, message.content)}
          className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-[rgba(22,34,27,0.12)] bg-[rgba(248,244,234,0.92)] text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] hover:text-[var(--ink-strong)]"
          aria-label={text.copyAnswer}
          title={copiedMessageId === message.id ? text.copied : text.copyAnswer}
        >
          {copiedMessageId === message.id ? <CheckIcon /> : <CopyIcon />}
        </button>
        <button
          type="button"
          onClick={() => onEnterManageMode(message.id)}
          disabled={isDeletingMessages}
          className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-[rgba(185,66,42,0.14)] bg-[rgba(255,238,231,0.92)] text-[#8f3524] transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={text.enterDeleteMode}
          title={text.enterDeleteMode}
        >
          <DeleteIcon />
        </button>
        {message.id === latestAssistantMessageId ? (
          <button
            type="button"
            onClick={() => void onRegenerateLastAssistant(message.id)}
            className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-[rgba(22,34,27,0.12)] bg-[rgba(248,244,234,0.92)] text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] hover:text-[var(--ink-strong)]"
            aria-label={text.regenerateAnswer}
            title={text.regenerateAnswer}
          >
            <RegenerateIcon />
          </button>
        ) : null}
      </div>
    );
  }

  if (showUserActionBar) {
    return (
      <div className="mt-2.5 flex items-center gap-2 opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100">
        <button
          type="button"
          onClick={() => onBeginEditLastUser(message)}
          className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-white/18 bg-white/10 text-white/82 transition hover:bg-white/16"
          aria-label={text.editLastUser}
          title={text.editLastUser}
        >
          <EditIcon />
        </button>
      </div>
    );
  }

  return null;
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4 fill-none stroke-current stroke-2">
      <path d="M5 13l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function CopyIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4 fill-none stroke-current stroke-2">
      <rect x="9" y="9" width="10" height="10" rx="2" />
      <path d="M15 9V7a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2" />
    </svg>
  );
}

function DeleteIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4 fill-none stroke-current stroke-2">
      <path d="M4 7h16" strokeLinecap="round" />
      <path d="M10 11v6M14 11v6" strokeLinecap="round" />
      <path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" />
      <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  );
}

function RegenerateIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4 fill-none stroke-current stroke-2">
      <path d="M20 11a8 8 0 1 1-2.34-5.66" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M20 4v7h-7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function EditIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4 fill-none stroke-current stroke-2">
      <path d="M12 20h9" strokeLinecap="round" />
      <path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5Z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

"use client";
/* eslint-disable @next/next/no-img-element */

import type { FormEvent, KeyboardEvent, RefObject } from "react";

import { ContextDiagnosticsPopover } from "@/components/context-diagnostics-popover";
import {
  attachmentKindLabel,
  buildAttachmentUrl,
  formatFileSize,
  isImageAttachment,
} from "@/lib/attachments";
import type { UILanguage } from "@/lib/settings";
import type {
  ContextAttachmentChunk,
  ContextGovernanceInfo,
  KnowledgeBase,
  UploadItem,
} from "@/lib/types";

type StatCard = {
  key: string;
  label: string;
  value?: string;
};

type ChatComposerText = {
  inputPlaceholder: string;
  uploadAttachment: string;
  uploading: string;
  webSearch: string;
  deepThinking: string;
  knowledgeBase: string;
  noKnowledgeBase: string;
  stopGenerating: string;
  sending: string;
  send: string;
  contextButton: string;
  contextPanelTitle: string;
  closeContextPanel: string;
  contextOverviewTitle: string;
  contextNoticesTitle: string;
  attachmentPreviewTitle: string;
  attachmentPreviewMeta: string;
  attachmentPreviewCollapse: string;
  attachmentPreviewExpand: string;
  contextAdvancedTitle: string;
};

type ChatComposerProps = {
  text: ChatComposerText;
  uiLanguage: UILanguage;
  composer: string;
  uploadedItems: UploadItem[];
  isUploading: boolean;
  isGenerating: boolean;
  isEditingUserMessage: boolean;
  streamingStatusLabel: string | null;
  knowledgeBases: KnowledgeBase[];
  selectedKnowledgeBaseId: string;
  isWebSearchEnabled: boolean;
  isDeepThinkingEnabled: boolean;
  contextInfo: ContextGovernanceInfo | null;
  hasContextDiagnostics: boolean;
  overviewStatCards: StatCard[];
  advancedStatCards: StatCard[];
  attachmentChunkDetails: ContextAttachmentChunk[];
  expandedChunkKeys: string[];
  isContextPanelOpen: boolean;
  fileInputRef: RefObject<HTMLInputElement | null>;
  editFileInputRef: RefObject<HTMLInputElement | null>;
  composerRef: RefObject<HTMLTextAreaElement | null>;
  onComposerChange: (value: string) => void;
  onUpload: (files: FileList | null) => void | Promise<void>;
  onEditUpload: (files: FileList | null) => void | Promise<void>;
  onPreviewAttachment: (item: UploadItem) => void;
  onRemoveUploadedItem: (itemId: string) => void;
  onSelectedKnowledgeBaseIdChange: (knowledgeBaseId: string) => void;
  onWebSearchEnabledChange: (enabled: boolean) => void;
  onDeepThinkingEnabledChange: (enabled: boolean) => void;
  onToggleContextPanel: () => void;
  onCloseContextPanel: () => void;
  onToggleAttachmentChunk: (chunkKey: string) => void;
  onComposerKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void | Promise<void>;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void | Promise<void>;
  onStopGenerating: () => void;
};

export function ChatComposer({
  text,
  uiLanguage,
  composer,
  uploadedItems,
  isUploading,
  isGenerating,
  isEditingUserMessage,
  streamingStatusLabel,
  knowledgeBases,
  selectedKnowledgeBaseId,
  isWebSearchEnabled,
  isDeepThinkingEnabled,
  contextInfo,
  hasContextDiagnostics,
  overviewStatCards,
  advancedStatCards,
  attachmentChunkDetails,
  expandedChunkKeys,
  isContextPanelOpen,
  fileInputRef,
  editFileInputRef,
  composerRef,
  onComposerChange,
  onUpload,
  onEditUpload,
  onPreviewAttachment,
  onRemoveUploadedItem,
  onSelectedKnowledgeBaseIdChange,
  onWebSearchEnabledChange,
  onDeepThinkingEnabledChange,
  onToggleContextPanel,
  onCloseContextPanel,
  onToggleAttachmentChunk,
  onComposerKeyDown,
  onSubmit,
  onStopGenerating,
}: ChatComposerProps) {
  return (
    <form onSubmit={(event) => void onSubmit(event)} className="mx-auto w-full max-w-[74rem]">
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/*,.txt,.md,.markdown,.pdf,.docx"
        className="hidden"
        onChange={(event) => void onUpload(event.target.files)}
      />
      <input
        ref={editFileInputRef}
        type="file"
        multiple
        accept="image/*,.txt,.md,.markdown,.pdf,.docx"
        className="hidden"
        onChange={(event) => void onEditUpload(event.target.files)}
      />

      <div className="chat-composer rounded-[22px] border px-3 py-2 backdrop-blur">
        {uploadedItems.length > 0 ? (
          <div className="mb-2 flex flex-wrap gap-2">
            {uploadedItems.map((item) => (
              <AttachmentChip
                key={item.id}
                item={item}
                uiLanguage={uiLanguage}
                onPreview={() => onPreviewAttachment(item)}
                onRemove={() => onRemoveUploadedItem(item.id)}
              />
            ))}
          </div>
        ) : null}

        <textarea
          ref={composerRef}
          value={composer}
          onChange={(event) => onComposerChange(event.target.value)}
          onKeyDown={(event) => void onComposerKeyDown(event)}
          placeholder={text.inputPlaceholder}
          rows={1}
          disabled={isEditingUserMessage}
          className="chat-composer-textarea min-h-[34px] w-full resize-none border-none bg-transparent text-[15px] leading-6 outline-none placeholder:text-[var(--ink-muted)]"
        />

        <div className="mt-1.5 flex flex-col gap-1.5 border-t border-[var(--hairline)] pt-1.5">
          {streamingStatusLabel ? (
            <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-4 py-2 text-sm text-[var(--ink-soft)]">
              {streamingStatusLabel}
            </div>
          ) : null}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={isEditingUserMessage}
                className="tool-chip rounded-full border px-3 py-1.5 text-xs transition hover:border-[var(--accent-strong)]"
              >
                {text.uploadAttachment}
              </button>
              <select
                value={selectedKnowledgeBaseId}
                onChange={(event) => onSelectedKnowledgeBaseIdChange(event.target.value)}
                disabled={isEditingUserMessage || isGenerating || knowledgeBases.length === 0}
                aria-label={text.knowledgeBase}
                className="tool-chip min-w-[9rem] rounded-full border px-3 py-1.5 text-xs outline-none transition hover:border-[var(--accent-strong)] disabled:cursor-not-allowed disabled:opacity-55"
              >
                <option value="">{text.noKnowledgeBase}</option>
                {knowledgeBases.map((knowledgeBase) => (
                  <option key={knowledgeBase.id} value={knowledgeBase.id}>
                    {knowledgeBase.name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => onWebSearchEnabledChange(!isWebSearchEnabled)}
                disabled={isEditingUserMessage || isGenerating}
                className={`tool-chip rounded-full border px-3 py-1.5 text-xs transition ${
                  isWebSearchEnabled ? "is-active" : "hover:border-[var(--accent-strong)]"
                }`}
              >
                {text.webSearch}
              </button>
              <button
                type="button"
                onClick={() => onDeepThinkingEnabledChange(!isDeepThinkingEnabled)}
                disabled={isEditingUserMessage || isGenerating}
                className={`tool-chip rounded-full border px-3 py-1.5 text-xs transition ${
                  isDeepThinkingEnabled ? "is-active" : "hover:border-[var(--accent-strong)]"
                }`}
              >
                {text.deepThinking}
              </button>
              {contextInfo && hasContextDiagnostics ? (
                <ContextDiagnosticsPopover
                  isOpen={isContextPanelOpen}
                  text={text}
                  notices={contextInfo.notices}
                  overviewStatCards={overviewStatCards}
                  advancedStatCards={advancedStatCards}
                  attachmentChunks={attachmentChunkDetails}
                  expandedChunkKeys={expandedChunkKeys}
                  onToggleOpen={onToggleContextPanel}
                  onClose={onCloseContextPanel}
                  onToggleChunk={onToggleAttachmentChunk}
                />
              ) : null}
              {isUploading ? (
                <span className="text-xs text-[var(--ink-muted)]">{text.uploading}</span>
              ) : null}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {isGenerating ? (
                <button
                  type="button"
                  onClick={onStopGenerating}
                  className="inline-flex items-center justify-center rounded-full border border-[var(--danger-border)] bg-[var(--danger-bg)] px-4 py-1.5 text-sm font-medium text-[var(--danger-text)] transition hover:brightness-95"
                >
                  {text.stopGenerating}
                </button>
              ) : null}
              <button
                type="submit"
                disabled={isGenerating || !composer.trim() || isEditingUserMessage}
                className="primary-action inline-flex items-center justify-center rounded-full px-5 py-1.5 text-sm font-medium transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-55"
              >
                {isGenerating ? text.sending : text.send}
              </button>
            </div>
          </div>
        </div>
      </div>
    </form>
  );
}

function AttachmentChip({
  item,
  uiLanguage,
  onPreview,
  onRemove,
}: {
  item: UploadItem;
  uiLanguage: UILanguage;
  onPreview: () => void;
  onRemove: () => void;
}) {
  const removeLabel = uiLanguage === "en-US" ? "Remove" : "移除";

  if (isImageAttachment(item)) {
    return (
      <div
        onClick={onPreview}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onPreview();
          }
        }}
        role="button"
        tabIndex={0}
        className="cursor-pointer overflow-hidden rounded-2xl border border-[rgba(22,34,27,0.12)] bg-[rgba(248,244,234,0.9)]"
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
              onClick={(event) => {
                event.stopPropagation();
                onRemove();
              }}
              className="mt-2 text-[var(--ink-muted)] transition hover:text-[var(--ink-strong)]"
            >
              {removeLabel}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      onClick={onPreview}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onPreview();
        }
      }}
      role="button"
      tabIndex={0}
      className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-[rgba(22,34,27,0.12)] bg-[rgba(248,244,234,0.9)] px-3 py-2 text-xs text-[var(--ink-soft)]"
    >
      <span>{attachmentKindLabel(item.kind, uiLanguage)}</span>
      <span className="max-w-[260px] truncate">{item.file_name}</span>
      <span>{formatFileSize(item.file_size)}</span>
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onRemove();
        }}
        className="text-[var(--ink-muted)] transition hover:text-[var(--ink-strong)]"
      >
        {removeLabel}
      </button>
    </div>
  );
}

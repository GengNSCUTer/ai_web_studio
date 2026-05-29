"use client";
/* eslint-disable @next/next/no-img-element */

import {
  buildAttachmentUrl,
  formatFileSize,
  isImageAttachment,
  isPdfAttachment,
} from "@/lib/attachments";
import type { UploadItem } from "@/lib/types";

type AttachmentPreviewModalProps = {
  item: UploadItem | null;
  openOriginalText: string;
  closeText: string;
  onClose: () => void;
};

export function AttachmentPreviewModal({
  item,
  openOriginalText,
  closeText,
  onClose,
}: AttachmentPreviewModalProps) {
  if (!item) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-[rgba(16,31,24,0.55)] p-4 backdrop-blur-sm">
      <div className="flex h-[min(86vh,56rem)] w-[min(92vw,72rem)] flex-col overflow-hidden rounded-[28px] border border-white/70 bg-[rgba(255,250,242,0.98)] shadow-[0_28px_90px_rgba(16,31,24,0.22)]">
        <div className="flex items-center justify-between gap-3 border-b border-[rgba(22,34,27,0.08)] px-5 py-4">
          <div className="min-w-0">
            <p className="truncate text-lg font-semibold text-[var(--ink-strong)]">{item.file_name}</p>
            <p className="mt-1 text-xs text-[var(--ink-muted)]">
              {formatFileSize(item.file_size ?? 0)}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <a
              href={buildAttachmentUrl(item.storage_key)}
              target="_blank"
              rel="noreferrer"
              className="rounded-full border border-[rgba(22,34,27,0.12)] bg-white px-3 py-1.5 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
            >
              {openOriginalText}
            </a>
            <button
              type="button"
              onClick={onClose}
              className="rounded-full border border-[rgba(22,34,27,0.12)] bg-white px-3 py-1.5 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
            >
              {closeText}
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-4">
          {isImageAttachment(item) ? (
            <div className="flex h-full items-center justify-center">
              <img
                src={buildAttachmentUrl(item.storage_key)}
                alt={item.file_name}
                className="max-h-full max-w-full rounded-2xl object-contain"
              />
            </div>
          ) : isPdfAttachment(item) ? (
            <iframe
              src={buildAttachmentUrl(item.storage_key)}
              title={item.file_name}
              className="h-full min-h-[60vh] w-full rounded-2xl border border-[rgba(22,34,27,0.08)] bg-white"
            />
          ) : (
            <div className="rounded-2xl border border-[rgba(22,34,27,0.08)] bg-white p-4">
              <pre className="whitespace-pre-wrap break-words text-sm leading-6 text-[var(--ink-strong)]">
                {item.parsed_text?.trim() || item.file_name}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

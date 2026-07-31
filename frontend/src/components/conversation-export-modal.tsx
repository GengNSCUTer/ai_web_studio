"use client";

import type { Conversation } from "@/lib/types";

type ExportOptions = {
  format: "markdown" | "json" | "jsonl";
  range: "all" | "loaded";
  include_attachments: boolean;
  include_attachment_files: boolean;
  include_context: boolean;
  as_zip: boolean;
};

type ExportText = {
  exportOptions: string;
  close: string;
  exportRange: string;
  exportRangeAll: string;
  exportRangeSelected: string;
  exportIncludeAttachmentMetadata: string;
  exportIncludeContext: string;
  exportAsZip: string;
  exportIncludeAttachmentFiles: string;
  exportRun: string;
};

type ConversationExportModalProps = {
  conversation: Conversation | null;
  exportOptions: ExportOptions;
  text: ExportText;
  onClose: () => void;
  onOptionsChange: (updater: (current: ExportOptions) => ExportOptions) => void;
  onExport: () => void | Promise<void>;
};

export function ConversationExportModal({
  conversation,
  exportOptions,
  text,
  onClose,
  onOptionsChange,
  onExport,
}: ConversationExportModalProps) {
  if (!conversation) {
    return null;
  }

  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center rounded-[32px] bg-[var(--overlay-bg)] p-4 backdrop-blur-sm">
      <div className="w-full max-w-xl overflow-hidden rounded-[28px] border border-[var(--panel-border)] bg-[var(--modal-bg)] shadow-[var(--panel-shadow)]">
        <div className="flex items-center justify-between border-b border-[var(--hairline)] px-5 py-4">
          <div>
            <p className="text-xs uppercase tracking-[0.22em] text-[var(--ink-muted)]">
              {text.exportOptions}
            </p>
            <h3 className="mt-1 text-2xl font-semibold text-[var(--ink-strong)]">
              {conversation.title}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
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
                onOptionsChange((current) => ({
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
          <div className="grid gap-3 sm:grid-cols-3">
            <button
              type="button"
              onClick={() => onOptionsChange((current) => ({ ...current, format: "markdown" }))}
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
              onClick={() => onOptionsChange((current) => ({ ...current, format: "json" }))}
              className={`rounded-2xl border px-4 py-3 text-sm ${
                exportOptions.format === "json"
                  ? "border-[var(--accent-strong)] bg-[var(--soft-bg)]"
                  : "border-[var(--control-border)] bg-[var(--control-bg)]"
              }`}
            >
              JSON
            </button>
            <button
              type="button"
              onClick={() => onOptionsChange((current) => ({ ...current, format: "jsonl" }))}
              className={`rounded-2xl border px-4 py-3 text-sm ${
                exportOptions.format === "jsonl"
                  ? "border-[var(--accent-strong)] bg-[var(--soft-bg)]"
                  : "border-[var(--control-border)] bg-[var(--control-bg)]"
              }`}
            >
              JSONL
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
                checked={Boolean(exportOptions[key as keyof ExportOptions])}
                onChange={(event) =>
                  onOptionsChange((current) => ({
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
                onOptionsChange((current) => ({
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
            onClick={() => void onExport()}
            className="primary-action rounded-full px-5 py-2 text-sm font-medium"
          >
            {text.exportRun}
          </button>
        </div>
      </div>
    </div>
  );
}

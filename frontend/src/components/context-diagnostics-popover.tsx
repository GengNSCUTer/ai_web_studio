"use client";

import type { ContextAttachmentChunk } from "@/lib/types";

type StatCard = {
  key: string;
  label: string;
  value?: string;
};

type ContextDiagnosticsText = {
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

type ContextDiagnosticsPopoverProps = {
  isOpen: boolean;
  text: ContextDiagnosticsText;
  notices: string[];
  overviewStatCards: StatCard[];
  advancedStatCards: StatCard[];
  attachmentChunks: ContextAttachmentChunk[];
  expandedChunkKeys: string[];
  onToggleOpen: () => void;
  onClose: () => void;
  onToggleChunk: (chunkKey: string) => void;
};

export function ContextDiagnosticsPopover({
  isOpen,
  text,
  notices,
  overviewStatCards,
  advancedStatCards,
  attachmentChunks,
  expandedChunkKeys,
  onToggleOpen,
  onClose,
  onToggleChunk,
}: ContextDiagnosticsPopoverProps) {
  return (
    <div className="relative">
      <button
        type="button"
        onClick={onToggleOpen}
        className="context-chip rounded-full border px-3 py-1.5 text-xs transition hover:border-[var(--accent-strong)]"
      >
        {text.contextButton}
      </button>

      {isOpen ? (
        <div className="absolute bottom-[calc(100%+0.75rem)] left-0 z-20 flex max-h-[min(72vh,34rem)] w-[min(92vw,44rem)] flex-col overflow-hidden rounded-[24px] border border-[var(--control-border)] bg-[var(--modal-bg)] shadow-[var(--panel-shadow)] backdrop-blur">
          <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--hairline)] px-4 py-3">
            <p className="text-xs uppercase tracking-[0.18em] text-[var(--ink-muted)]">
              {text.contextPanelTitle}
            </p>
            <button
              type="button"
              onClick={onClose}
              className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-1 text-[11px] text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
            >
              {text.closeContextPanel}
            </button>
          </div>

          <div className="min-h-0 overflow-y-auto p-4">
            {overviewStatCards.length > 0 ? (
              <StatSection title={text.contextOverviewTitle} cards={overviewStatCards} />
            ) : null}

            {notices.length > 0 ? (
              <div className="mt-3 rounded-2xl border border-dashed border-[var(--control-border)] bg-[var(--soft-bg)] px-3 py-2">
                <p className="text-xs uppercase tracking-[0.18em] text-[var(--ink-muted)]">
                  {text.contextNoticesTitle}
                </p>
                <div className="mt-2 flex flex-col gap-1 text-xs leading-5 text-[var(--ink-soft)]">
                  {notices.map((notice) => (
                    <span key={notice}>{notice}</span>
                  ))}
                </div>
              </div>
            ) : null}

            {attachmentChunks.length > 0 ? (
              <div className="mt-3 rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-3 py-3">
                <p className="text-xs uppercase tracking-[0.18em] text-[var(--ink-muted)]">
                  {text.attachmentPreviewTitle}
                </p>
                <div className="mt-2 space-y-2">
                  {attachmentChunks.map((chunk) => {
                    const chunkKey = `${chunk.file_name}-${chunk.index}-${chunk.score}`;
                    const isExpanded = expandedChunkKeys.includes(chunkKey);
                    return (
                      <div
                        key={chunkKey}
                        className="rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-3 py-2"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium text-[var(--ink-strong)]">
                              {chunk.file_name}
                            </p>
                            <p className="mt-1 text-[11px] text-[var(--ink-muted)]">
                              {text.attachmentPreviewMeta} #{chunk.index} · score={chunk.score} ·{" "}
                              {chunk.char_count} chars
                            </p>
                          </div>
                          <button
                            type="button"
                            onClick={() => onToggleChunk(chunkKey)}
                            className="shrink-0 rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-1 text-[11px] text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
                          >
                            {isExpanded ? text.attachmentPreviewCollapse : text.attachmentPreviewExpand}
                          </button>
                        </div>
                        <p className="mt-2 whitespace-pre-wrap break-words text-xs leading-5 text-[var(--ink-soft)]">
                          {isExpanded ? chunk.expanded_preview : chunk.preview}
                        </p>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : null}

            {advancedStatCards.length > 0 ? (
              <details className="mt-3 rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-3 py-2">
                <summary className="cursor-pointer text-xs uppercase tracking-[0.18em] text-[var(--ink-muted)]">
                  {text.contextAdvancedTitle}
                </summary>
                <StatGrid cards={advancedStatCards} className="mt-2" />
              </details>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function StatSection({ title, cards }: { title: string; cards: StatCard[] }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">{title}</p>
      <StatGrid cards={cards} className="mt-2" />
    </div>
  );
}

function StatGrid({ cards, className = "" }: { cards: StatCard[]; className?: string }) {
  return (
    <div className={`grid gap-2 sm:grid-cols-2 ${className}`}>
      {cards.map((item) => (
        <div
          key={item.key}
          className="rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-3 py-2"
        >
          <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--ink-muted)]">
            {item.label}
          </p>
          <p className="mt-1 break-all text-sm font-medium text-[var(--ink-strong)]">{item.value}</p>
        </div>
      ))}
    </div>
  );
}

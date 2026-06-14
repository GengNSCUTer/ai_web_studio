"use client";

import { MessageMarkdown } from "@/components/message-markdown";
import type { KnowledgeMarkdownChunk, KnowledgeMarkdownPreview, KnowledgeRetrievalLog } from "@/lib/types";

export type KnowledgeSourcePreviewTarget = {
  knowledgeBaseId: string;
  documentId: string;
  chunkId?: string;
  chunkIndex?: number | null;
  title?: string;
};

type KnowledgeSourcePreviewDialogProps = {
  preview: KnowledgeMarkdownPreview | null;
  retrievalLog: KnowledgeRetrievalLog | null;
  target: KnowledgeSourcePreviewTarget | null;
  isLoading: boolean;
  error: string | null;
  closeText: string;
  onClose: () => void;
};

const CONTEXT_CHARS = 1800;

function findTargetChunk(
  preview: KnowledgeMarkdownPreview | null,
  target: KnowledgeSourcePreviewTarget | null
) {
  if (!preview || !target) {
    return null;
  }
  if (target.chunkId) {
    const byId = preview.chunks.find((chunk) => chunk.chunk_id === target.chunkId);
    if (byId) {
      return byId;
    }
  }
  if (typeof target.chunkIndex === "number") {
    return preview.chunks.find((chunk) => chunk.chunk_index === target.chunkIndex) ?? null;
  }
  return null;
}

function buildFocusedMarkdown(preview: KnowledgeMarkdownPreview, chunk: KnowledgeMarkdownChunk | null) {
  if (!chunk) {
    return {
      before: "",
      hit: preview.markdown.slice(0, CONTEXT_CHARS * 2),
      after: "",
      locationLabel: "未找到精确 Chunk，展示文档开头",
    };
  }

  const locatedByOffset =
    typeof chunk.source_start === "number" &&
    typeof chunk.source_end === "number" &&
    chunk.source_end > chunk.source_start &&
    preview.markdown.slice(chunk.source_start, chunk.source_end) === chunk.content;
  const fallbackIndex = locatedByOffset ? -1 : preview.markdown.indexOf(chunk.content);
  if (!locatedByOffset && fallbackIndex < 0) {
    return {
      before: "",
      hit: chunk.content,
      after: "",
      locationLabel: `Chunk #${chunk.chunk_index}，原文偏移缺失，展示命中片段`,
    };
  }

  const start = locatedByOffset ? Math.max(0, chunk.source_start ?? 0) : fallbackIndex;
  const end = locatedByOffset
    ? Math.min(preview.markdown.length, chunk.source_end ?? start + chunk.content.length)
    : Math.min(preview.markdown.length, fallbackIndex + chunk.content.length);

  if (start < 0 || end <= start) {
    return {
      before: "",
      hit: chunk.content,
      after: "",
      locationLabel: `Chunk #${chunk.chunk_index}，原文偏移缺失，展示命中片段`,
    };
  }

  return {
    before: preview.markdown.slice(Math.max(0, start - CONTEXT_CHARS), start),
    hit: preview.markdown.slice(start, end),
    after: preview.markdown.slice(end, Math.min(preview.markdown.length, end + CONTEXT_CHARS)),
    locationLabel: `Chunk #${chunk.chunk_index} · ${start}-${end}`,
  };
}

export function KnowledgeSourcePreviewDialog({
  preview,
  retrievalLog,
  target,
  isLoading,
  error,
  closeText,
  onClose,
}: KnowledgeSourcePreviewDialogProps) {
  if (!target) {
    return null;
  }

  const chunk = findTargetChunk(preview, target);
  const focused = preview ? buildFocusedMarkdown(preview, chunk) : null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay-bg)] p-4 backdrop-blur-sm">
      <section className="flex h-[min(88vh,58rem)] w-[min(94vw,76rem)] flex-col overflow-hidden rounded-[30px] border border-[var(--panel-border)] bg-[var(--modal-bg)] shadow-[var(--panel-shadow)]">
        <div className="flex items-center justify-between gap-3 border-b border-[var(--hairline)] px-5 py-4">
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-[0.24em] text-[var(--ink-muted)]">
              Knowledge Source
            </p>
            <h3 className="mt-1 truncate text-2xl font-semibold text-[var(--ink-strong)]">
              {preview?.file_name || target.title || "知识库来源"}
            </h3>
            {focused ? (
              <p className="mt-1 text-xs text-[var(--ink-muted)]">{focused.locationLabel}</p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
          >
            {closeText}
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {isLoading ? (
            <div className="rounded-2xl border border-[var(--control-border)] bg-[var(--soft-bg)] px-4 py-3 text-sm text-[var(--ink-soft)]">
              正在加载知识库文档...
            </div>
          ) : error ? (
            <div className="rounded-2xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-4 py-3 text-sm text-[var(--danger-text)]">
              {error}
            </div>
          ) : focused ? (
            <div className="grid gap-4">
              {retrievalLog ? (
                <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-4 py-3 text-xs text-[var(--ink-soft)]">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium text-[var(--ink-strong)]">本轮检索日志</span>
                    <span>
                      {retrievalLog.status} · {retrievalLog.elapsed_ms ?? "--"}ms · Top {retrievalLog.top_k}
                    </span>
                  </div>
                  <div className="mt-2 line-clamp-2">
                    Query：{retrievalLog.query}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <span className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5">
                      候选 {retrievalLog.candidates.length}
                    </span>
                    <span className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5">
                      注入 {retrievalLog.selected.length}
                    </span>
                    <span className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5">
                      Rerank {retrievalLog.rerank_enabled ? retrievalLog.rerank_model || "已启用" : "未启用"}
                    </span>
                  </div>
                </div>
              ) : null}
              {focused.before ? (
                <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-4 py-3 opacity-75">
                  <MessageMarkdown content={focused.before} />
                </div>
              ) : null}
              <div className="rounded-2xl border-2 border-[var(--accent-strong)] bg-[var(--accent-soft)] px-4 py-3 shadow-[0_16px_44px_rgba(89,116,69,0.18)]">
                <div className="mb-3 inline-flex rounded-full bg-[var(--control-bg)] px-3 py-1 text-xs font-medium text-[var(--accent-strong)]">
                  命中片段
                </div>
                <MessageMarkdown content={focused.hit} />
              </div>
              {focused.after ? (
                <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-4 py-3 opacity-75">
                  <MessageMarkdown content={focused.after} />
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}

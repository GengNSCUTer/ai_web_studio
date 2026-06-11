"use client";

import type { ExternalSource } from "@/lib/types";

function sourceMeta(source: ExternalSource, key: string) {
  const value = source.metadata?.[key];
  if (value === null || value === undefined) {
    return "";
  }
  return String(value).trim();
}

function sourceKindLabel(source: ExternalSource) {
  const tool = sourceMeta(source, "tool");
  if (source.source_type === "knowledge") {
    return "知识库";
  }
  if (source.source_type === "weather") {
    return "天气";
  }
  if (tool.includes("route")) {
    return "路线";
  }
  if (tool.includes("poi")) {
    return "地点";
  }
  if (tool.includes("district")) {
    return "行政区";
  }
  if (source.source_type === "map") {
    return "地图";
  }
  return "网页";
}

export function ExternalSourceCard({ source, index, sourceId }: { source: ExternalSource; index: number; sourceId?: string }) {
  const label = source.citation_label ?? `[${index + 1}]`;
  const providerLabel = source.provider;
  const kindLabel = sourceKindLabel(source);
  const weather = sourceMeta(source, "weather");
  const temperature = sourceMeta(source, "temperature");
  const humidity = sourceMeta(source, "humidity");
  const windDirection = sourceMeta(source, "winddirection");
  const windPower = sourceMeta(source, "windpower");
  const reportTime = sourceMeta(source, "reporttime");
  const address = sourceMeta(source, "address") || sourceMeta(source, "formatted_address");
  const distance = sourceMeta(source, "distance");
  const mapType = sourceMeta(source, "type");
  const origin = sourceMeta(source, "origin");
  const destination = sourceMeta(source, "destination");
  const domain = sourceMeta(source, "domain");
  const knowledgeBaseName = sourceMeta(source, "knowledge_base_name");
  const fileName = sourceMeta(source, "file_name");
  const chunkIndex = sourceMeta(source, "chunk_index");
  const rankSource = sourceMeta(source, "rank_source");
  const rerankScore = sourceMeta(source, "rerank_score");
  const vectorScore = sourceMeta(source, "vector_score");
  const toolDisplayName = sourceMeta(source, "tool_display_name");
  const callId = sourceMeta(source, "call_id");
  const contentPreview = source.display_text;

  const cardBody =
    source.source_type === "knowledge" ? (
      <div className="mt-2 grid gap-1.5">
        <div>知识库：{knowledgeBaseName || "未知"}</div>
        {fileName ? <div>文档：{fileName}</div> : null}
        <div>
          Chunk：{chunkIndex || "--"}；排序：{rankSource || "--"}；分数：
          {rerankScore && rerankScore !== "None" ? rerankScore : vectorScore || source.score?.toFixed(4) || "--"}
        </div>
        <div className="line-clamp-3 leading-5">{contentPreview}</div>
      </div>
    ) : source.source_type === "weather" ? (
      <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
        <div>天气：{weather || "未知"}</div>
        <div>气温：{temperature ? `${temperature}°C` : "未知"}</div>
        <div>湿度：{humidity ? `${humidity}%` : "未知"}</div>
        <div>
          风力：{windDirection || "未知"} {windPower || ""}
        </div>
        <div className="sm:col-span-2">发布时间：{reportTime || "未知"}</div>
      </div>
    ) : source.source_type === "map" ? (
      <div className="mt-2 grid gap-1.5">
        {origin || destination ? (
          <div>
            路线：{origin || "起点"} {"->"} {destination || "终点"}
          </div>
        ) : null}
        {address ? <div>地址：{address}</div> : null}
        {mapType ? <div>类型：{mapType}</div> : null}
        {distance ? <div>距离：{distance}</div> : null}
        <div className="line-clamp-3 leading-5">{contentPreview}</div>
      </div>
    ) : (
      <div className="mt-2 grid gap-1.5">
        {domain ? <div>域名：{domain}</div> : null}
        <div className="line-clamp-3 leading-5">{contentPreview}</div>
      </div>
    );

  return (
    <div
      id={sourceId}
      className="rounded-xl border border-[var(--hairline)] bg-[var(--control-bg)] px-3 py-2 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="font-medium text-[var(--ink-strong)]">
          {label} {source.title}
        </span>
        <span className="flex shrink-0 items-center gap-1.5">
          <span className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5 text-[10px] text-[var(--ink-soft)]">
            {kindLabel}
          </span>
          <span className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5 text-[10px] text-[var(--ink-soft)]">
            {providerLabel}
          </span>
        </span>
      </div>
      {cardBody}
      <div className="mt-2 flex flex-wrap gap-2">
        {toolDisplayName ? (
          <span className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5 text-[10px] text-[var(--ink-muted)]">
            工具：{toolDisplayName}
          </span>
        ) : null}
        {callId ? (
          <a
            href={`#tool-call-${callId}`}
            className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5 text-[10px] text-[var(--ink-muted)] hover:text-[var(--accent-strong)]"
          >
            跳到工具调用
          </a>
        ) : null}
        {source.url ? (
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer"
            className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5 text-[10px] text-[var(--ink-muted)] hover:text-[var(--accent-strong)]"
          >
            打开来源
          </a>
        ) : null}
      </div>
      <details className="mt-2 rounded-lg border border-[var(--hairline)] bg-[var(--soft-bg)] px-2 py-1">
        <summary className="cursor-pointer select-none text-[10px] font-medium text-[var(--ink-strong)]">
          查看原始结果
        </summary>
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-[var(--control-bg)] p-2 text-[10px] leading-4 text-[var(--ink-strong)]">
          {JSON.stringify({ ...source, metadata: source.metadata ?? {} }, null, 2)}
        </pre>
      </details>
    </div>
  );
}

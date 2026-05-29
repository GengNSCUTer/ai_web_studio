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

export function ExternalSourceCard({ source, index }: { source: ExternalSource; index: number }) {
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
  const contentPreview = source.display_text;

  const cardBody =
    source.source_type === "weather" ? (
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

  const inner = (
    <>
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
    </>
  );

  const className = `rounded-xl border border-[var(--hairline)] bg-[var(--control-bg)] px-3 py-2 text-xs text-[var(--ink-soft)] transition ${
    source.url ? "hover:border-[var(--accent-strong)]" : "cursor-default"
  }`;

  if (source.url) {
    return (
      <a href={source.url} target="_blank" rel="noreferrer" className={className}>
        {inner}
      </a>
    );
  }
  return <div className={className}>{inner}</div>;
}

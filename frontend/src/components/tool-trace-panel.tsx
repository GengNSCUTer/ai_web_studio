"use client";

import type { UILanguage } from "@/lib/settings";
import type { ToolTraceEvent } from "@/lib/types";

export function toolEventKey(event: ToolTraceEvent, index: number) {
  if ("call_id" in event && event.call_id) {
    return `${event.type}-${event.call_id}-${index}`;
  }
  if (event.type === "tool_call_fallback") {
    return `${event.type}-${event.from_call_id ?? "from"}-${event.to_call_id ?? "to"}-${index}`;
  }
  return `${event.type}-${index}`;
}

export function formatToolEvent(event: ToolTraceEvent, uiLanguage: UILanguage) {
  const isChinese = uiLanguage === "zh-CN";
  if (event.type === "tool_plan") {
    const calls = event.plan?.calls ?? [];
    if (calls.length === 0) {
      return isChinese ? "本轮不需要调用外部工具" : "No external tool is needed for this turn";
    }
    const names = calls.map((call) => call.display_name || call.tool_key || "tool").join("、");
    return isChinese ? `已生成工具计划：${names}` : `Tool plan created: ${names}`;
  }
  if (event.type === "tool_call_start") {
    return isChinese
      ? `开始调用 ${event.display_name ?? event.tool_key ?? "工具"}`
      : `Calling ${event.display_name ?? event.tool_key ?? "tool"}`;
  }
  if (event.type === "tool_call_end") {
    const elapsed = typeof event.elapsed_ms === "number" ? `${event.elapsed_ms}ms` : "-";
    const count = typeof event.sources_count === "number" ? event.sources_count : 0;
    return isChinese
      ? `${event.display_name ?? event.tool_key ?? "工具"} 调用完成，耗时 ${elapsed}，返回 ${count} 个来源`
      : `${event.display_name ?? event.tool_key ?? "Tool"} finished in ${elapsed}, ${count} source(s)`;
  }
  if (event.type === "tool_call_error") {
    const elapsed = typeof event.elapsed_ms === "number" ? `${event.elapsed_ms}ms` : "-";
    return isChinese
      ? `${event.display_name ?? event.tool_key ?? "工具"} 调用失败，耗时 ${elapsed}：${event.error ?? "未知错误"}`
      : `${event.display_name ?? event.tool_key ?? "Tool"} failed in ${elapsed}: ${event.error ?? "unknown error"}`;
  }
  return isChinese
    ? `工具回退：${event.from_tool_key ?? "原工具"} -> ${event.to_tool_key ?? "备用工具"}`
    : `Tool fallback: ${event.from_tool_key ?? "primary"} -> ${event.to_tool_key ?? "fallback"}`;
}

export function ToolTracePanel({
  events,
  title,
  uiLanguage,
}: {
  events: ToolTraceEvent[];
  title: string;
  uiLanguage: UILanguage;
}) {
  if (events.length === 0) {
    return null;
  }

  return (
    <details className="reasoning-panel mt-3 rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-3 py-2 text-xs text-[var(--ink-soft)]">
      <summary className="cursor-pointer select-none font-medium text-[var(--ink-strong)]">
        {title} · {events.length}
      </summary>
      <div className="mt-2 grid gap-2">
        {events.map((event, index) => (
          <div
            key={toolEventKey(event, index)}
            className="rounded-xl border border-[var(--hairline)] bg-[var(--control-bg)] px-3 py-2 leading-5"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5 text-[10px] font-medium text-[var(--ink-strong)]">
                {event.type}
              </span>
              <span>{formatToolEvent(event, uiLanguage)}</span>
            </div>
          </div>
        ))}
      </div>
    </details>
  );
}

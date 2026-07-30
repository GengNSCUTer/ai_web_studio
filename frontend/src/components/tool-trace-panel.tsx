"use client";

import { useState } from "react";

import type { UILanguage } from "@/lib/settings";
import type { ToolTraceEvent } from "@/lib/types";

function valueToText(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function selectedToolNames(event: ToolTraceEvent) {
  const tools = event.selected_tools ?? event.plan?.calls ?? [];
  return tools.map((tool) => tool.display_name || tool.tool_key || "tool").join("、");
}

export function toolEventKey(event: ToolTraceEvent, index: number) {
  if ("call_id" in event && event.call_id) {
    return `${event.type}-${event.call_id}-${index}`;
  }
  if (event.type === "tool_call_fallback" || event.type === "tool_fallback") {
    return `${event.type}-${event.from_call_id ?? "from"}-${event.to_call_id ?? "to"}-${index}`;
  }
  return `${event.type}-${index}`;
}

export function formatToolEvent(event: ToolTraceEvent, uiLanguage: UILanguage) {
  const isChinese = uiLanguage === "zh-CN";
  if (event.type === "tool_planner_start") {
    return isChinese
      ? `开始工具规划：${event.planner ?? "planner"}，候选工具 ${valueToText(event.available_tools_count)} 个`
      : `Tool planning started: ${event.planner ?? "planner"}, ${valueToText(event.available_tools_count)} candidate tool(s)`;
  }
  if (event.type === "tool_planner_llm_output") {
    return isChinese ? "LLM 工具规划已返回原始输出" : "LLM tool planner returned raw output";
  }
  if (event.type === "tool_agent_round_start") {
    return isChinese
      ? `工具规划第 ${valueToText(event.round)} 轮开始，已有观察 ${valueToText(event.observations_count)} 条`
      : `Tool round ${valueToText(event.round)} started with ${valueToText(event.observations_count)} observation(s)`;
  }
  if (event.type === "tool_agent_round_end") {
    return isChinese
      ? `工具规划第 ${valueToText(event.round)} 轮结束，来源 ${valueToText(event.sources_count)} 个，继续规划：${valueToText(event.need_more_rounds)}`
      : `Tool round ${valueToText(event.round)} finished, ${valueToText(event.sources_count)} source(s), continue: ${valueToText(event.need_more_rounds)}`;
  }
  if (event.type === "tool_candidate_selection") {
    return isChinese
      ? `候选工具选择完成：${valueToText(event.selected_count)} 个候选`
      : `Tool candidates selected: ${valueToText(event.selected_count)} candidate(s)`;
  }
  if (event.type === "tool_planner_end") {
    const tools = selectedToolNames(event);
    if (event.strategy === "disabled") {
      return isChinese ? "外部工具未启用，本轮不调用工具" : "External tools are disabled for this turn";
    }
    return isChinese
      ? `工具规划完成：${event.strategy ?? event.planner ?? "planner"}${tools ? `，选择 ${tools}` : "，未选择工具"}`
      : `Tool planning finished: ${event.strategy ?? event.planner ?? "planner"}${tools ? `, selected ${tools}` : ", no tool selected"}`;
  }
  if (event.type === "tool_schema_validation") {
    const status = event.status === "passed" ? (isChinese ? "通过" : "passed") : isChinese ? "失败" : "failed";
    return isChinese
      ? `参数 schema 校验${status}：${event.display_name ?? event.tool_key ?? "工具"}`
      : `Schema validation ${status}: ${event.display_name ?? event.tool_key ?? "tool"}`;
  }
  if (event.type === "tool_policy_check") {
    const status = event.status === "passed" ? (isChinese ? "通过" : "passed") : event.status === "denied" ? (isChinese ? "拒绝" : "denied") : isChinese ? "检查中" : "checking";
    return isChinese
      ? `工具权限检查${status}：${event.display_name ?? event.tool_key ?? "工具"}`
      : `Tool policy check ${status}: ${event.display_name ?? event.tool_key ?? "tool"}`;
  }
  if (event.type === "tool_confirmation_required") {
    return isChinese
      ? `需要用户确认：${event.display_name ?? event.tool_key ?? "工具"}${event.status === "waiting_approval" ? " 已生成持久化 Diff" : " 已被阻断"}`
      : `Confirmation required: ${event.display_name ?? event.tool_key ?? "tool"}${event.status === "waiting_approval" ? " has a durable Diff" : " was blocked"}`;
  }
  if (event.type === "tool_query_rewrite") {
    return isChinese
      ? `问题已改写：${event.rewritten_query ?? "未提供改写结果"}`
      : `Query rewritten: ${event.rewritten_query ?? "not provided"}`;
  }
  if (event.type === "tool_fallback") {
    return isChinese
      ? `工具规划回退：${event.from ?? "原规划器"} -> ${event.to ?? "兜底规划器"}`
      : `Tool planning fallback: ${event.from ?? "primary"} -> ${event.to ?? "fallback"}`;
  }
  if (event.type === "tool_plan") {
    const calls = event.plan?.calls ?? [];
    if (calls.length === 0) {
      return isChinese ? "本轮不需要调用外部工具" : "No external tool is needed for this turn";
    }
    const names = calls.map((call) => call.display_name || call.tool_key || "tool").join("、");
    return isChinese ? `已生成工具计划：${names}` : `Tool plan created: ${names}`;
  }
  if (event.type === "tool_workflow_start") {
    return isChinese
      ? `工具工作流开始：计划 ${valueToText(event.planned_calls)} 个，执行上限 ${valueToText(event.max_tool_calls)} 个`
      : `Tool workflow started: ${valueToText(event.planned_calls)} planned, max ${valueToText(event.max_tool_calls)}`;
  }
  if (event.type === "tool_workflow_batch") {
    return isChinese
      ? `工具执行批次 ${valueToText(event.step)}：${event.mode === "parallel" ? "并行" : "单步"}，${valueToText(event.tool_keys)}`
      : `Tool batch ${valueToText(event.step)}: ${event.mode === "parallel" ? "parallel" : "single"}, ${valueToText(event.tool_keys)}`;
  }
  if (event.type === "tool_workflow_step") {
    return isChinese
      ? `执行工具步骤 ${valueToText(event.step)}：${event.display_name ?? event.tool_key ?? "工具"}`
      : `Executing tool step ${valueToText(event.step)}: ${event.display_name ?? event.tool_key ?? "tool"}`;
  }
  if (event.type === "tool_workflow_step_skipped") {
    return isChinese
      ? `跳过工具步骤 ${valueToText(event.step)}：${event.reason ?? "重复或无效调用"}`
      : `Skipped tool step ${valueToText(event.step)}: ${event.reason ?? "duplicate or invalid call"}`;
  }
  if (event.type === "tool_workflow_end") {
    return isChinese
      ? `工具工作流结束：返回 ${valueToText(event.sources_count)} 个来源，耗时 ${valueToText(event.elapsed_ms)}ms`
      : `Tool workflow finished: ${valueToText(event.sources_count)} source(s), ${valueToText(event.elapsed_ms)}ms`;
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

function toolEventDetails(event: ToolTraceEvent, uiLanguage: UILanguage) {
  const isChinese = uiLanguage === "zh-CN";
  const rows: Array<[string, unknown]> = [];
  if (event.reason) {
    rows.push([isChinese ? "原因" : "Reason", event.reason]);
  }
  if (event.planner) {
    rows.push([isChinese ? "规划器" : "Planner", event.planner]);
  }
  if (event.strategy) {
    rows.push([isChinese ? "策略" : "Strategy", event.strategy]);
  }
  if (event.candidates !== undefined) {
    rows.push([isChinese ? "候选工具" : "Candidates", event.candidates]);
  }
  if (event.selected_count !== undefined) {
    rows.push([isChinese ? "候选数量" : "Candidate count", event.selected_count]);
  }
  if (event.step !== undefined) {
    rows.push([isChinese ? "步骤" : "Step", event.step]);
  }
  if (event.workflow) {
    rows.push([isChinese ? "工作流" : "Workflow", event.workflow]);
  }
  if (event.risk_level) {
    rows.push([isChinese ? "风险等级" : "Risk", event.risk_level]);
  }
  if (event.run_id) {
    rows.push(["Agent Run", event.run_id]);
  }
  if (event.approval_id) {
    rows.push([isChinese ? "审批 ID" : "Approval ID", event.approval_id]);
  }
  if (event.file_name) {
    rows.push([isChinese ? "文件" : "File", event.file_name]);
  }
  if (event.diff_text) {
    rows.push(["Diff", event.diff_text]);
  }
  if (typeof event.read_only === "boolean") {
    rows.push([isChinese ? "只读工具" : "Read only", event.read_only ? (isChinese ? "是" : "yes") : isChinese ? "否" : "no"]);
  }
  if (event.credential_source) {
    rows.push([isChinese ? "凭据来源" : "Credential source", event.credential_source]);
  }
  if (event.original_query || event.rewritten_query) {
    rows.push([isChinese ? "原问题" : "Original query", event.original_query]);
    rows.push([isChinese ? "改写后" : "Rewritten query", event.rewritten_query]);
  }
  if (event.raw_arguments !== undefined) {
    rows.push([isChinese ? "原始参数" : "Raw args", event.raw_arguments]);
  }
  if (event.normalized_arguments !== undefined) {
    rows.push([isChinese ? "归一化参数" : "Normalized args", event.normalized_arguments]);
  }
  if (event.arguments !== undefined) {
    rows.push([isChinese ? "调用参数" : "Arguments", event.arguments]);
  }
  if (event.adapter !== undefined) {
    rows.push([isChinese ? "适配器" : "Adapter", event.adapter]);
  }
  if (event.raw_preview !== undefined) {
    rows.push([isChinese ? "LLM 原始输出" : "LLM output", event.raw_preview]);
  }
  return rows;
}

function ApprovalActions({ event, uiLanguage }: { event: ToolTraceEvent; uiLanguage: UILanguage }) {
  const [state, setState] = useState<"pending" | "working" | "applied" | "rejected" | "error">("pending");
  const [message, setMessage] = useState("");
  const approvalId = typeof event.approval_id === "string" ? event.approval_id : "";
  if (event.type !== "tool_confirmation_required" || !approvalId || event.status !== "waiting_approval") {
    return null;
  }

  async function request(path: string, init?: RequestInit) {
    const response = await fetch(path, { ...init, cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload?.detail?.message || payload?.detail || `HTTP ${response.status}`;
      throw new Error(String(detail));
    }
    return payload;
  }

  async function approve() {
    setState("working");
    setMessage("");
    try {
      const challenge = await request(`/api/backend/agent-runtime/approvals/${approvalId}/challenge`, {
        method: "POST",
      });
      await request(`/api/backend/agent-runtime/approvals/${approvalId}/apply`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ approval_token: challenge.approval_token }),
      });
      setState("applied");
      setMessage(uiLanguage === "zh-CN" ? "已通过版本 CAS 应用修改" : "Edit applied with revision CAS");
    } catch (error) {
      setState("error");
      setMessage(error instanceof Error ? error.message : "Approval failed");
    }
  }

  async function reject() {
    setState("working");
    setMessage("");
    try {
      await request(`/api/backend/agent-runtime/approvals/${approvalId}/reject`, { method: "POST" });
      setState("rejected");
      setMessage(uiLanguage === "zh-CN" ? "已拒绝修改" : "Edit rejected");
    } catch (error) {
      setState("error");
      setMessage(error instanceof Error ? error.message : "Rejection failed");
    }
  }

  return (
    <div className="mt-2 rounded-lg border border-[var(--hairline)] bg-[var(--soft-bg)] p-2">
      {state === "pending" || state === "working" ? (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={state === "working"}
            onClick={() => void approve()}
            className="rounded-full bg-[var(--accent-strong)] px-3 py-1 text-[10px] text-white disabled:opacity-50"
          >
            {uiLanguage === "zh-CN" ? "确认并应用 Diff" : "Approve and apply"}
          </button>
          <button
            type="button"
            disabled={state === "working"}
            onClick={() => void reject()}
            className="rounded-full border border-[var(--hairline)] px-3 py-1 text-[10px] disabled:opacity-50"
          >
            {uiLanguage === "zh-CN" ? "拒绝" : "Reject"}
          </button>
        </div>
      ) : null}
      {message ? <p className={`mt-1 text-[10px] ${state === "error" ? "text-[var(--danger-text)]" : ""}`}>{message}</p> : null}
    </div>
  );
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
            id={event.call_id ? `tool-call-${event.call_id}` : undefined}
            className="rounded-xl border border-[var(--hairline)] bg-[var(--control-bg)] px-3 py-2 leading-5"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5 text-[10px] font-medium text-[var(--ink-strong)]">
                {event.type}
              </span>
              <span>{formatToolEvent(event, uiLanguage)}</span>
              {event.call_id && (event.type === "tool_call_end" || event.type === "tool_call_error") ? (
                <a
                  href={`#source-call-${event.call_id}-1`}
                  className="rounded-full bg-[var(--soft-bg)] px-2 py-0.5 text-[10px] text-[var(--ink-muted)] hover:text-[var(--accent-strong)]"
                >
                  {uiLanguage === "zh-CN" ? "看来源" : "Source"}
                </a>
              ) : null}
            </div>
            {toolEventDetails(event, uiLanguage).length > 0 ? (
              <details className="mt-2 rounded-lg border border-[var(--hairline)] bg-[var(--soft-bg)] px-2 py-1">
                <summary className="cursor-pointer select-none text-[10px] font-medium text-[var(--ink-strong)]">
                  {uiLanguage === "zh-CN" ? "查看详情" : "Details"}
                </summary>
                <div className="mt-2 grid gap-2">
                  {toolEventDetails(event, uiLanguage).map(([label, value]) => (
                    <div key={label} className="grid gap-1">
                      <div className="text-[10px] font-medium text-[var(--ink-soft)]">{label}</div>
                      <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-[var(--control-bg)] p-2 text-[10px] leading-4 text-[var(--ink-strong)]">
                        {valueToText(value)}
                      </pre>
                    </div>
                  ))}
                </div>
              </details>
            ) : null}
            <ApprovalActions event={event} uiLanguage={uiLanguage} />
          </div>
        ))}
      </div>
    </details>
  );
}

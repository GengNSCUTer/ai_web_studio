import { useState } from "react";
import type { Project } from "@/lib/types";

type MaintenanceJob = {
  id: string;
  version: number;
  status: string;
  error_code?: string | null;
  proposals: {
    id: string;
    kind: "duplicate" | "merge" | "correct";
    status: string;
    target_title?: string;
    target_content: string | null;
    related_content: string | null;
    proposed_content: string | null;
    source_quote: string | null;
  }[];
  reviewed_count: number;
  accepted_count: number;
  model_ms: number | null;
  estimated_input_tokens: number | null;
  estimated_output_tokens: number | null;
};

export function MemoryMaintenancePanel({ projects, enabled, chinese, request, onApplied }: {
  projects: Project[];
  enabled: boolean;
  chinese: boolean;
  request: <T>(path: string, init?: RequestInit) => Promise<T>;
  onApplied: () => Promise<void>;
}) {
  const [selection, setSelection] = useState("");
  const projectId = selection || projects[0]?.id || "";
  const [job, setJob] = useState<MaintenanceJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const base = "/api/backend/memories/maintenance";
  const labels = chinese ? {
    pending: "等待后台处理", running: "后台整理中", ready: "有建议待审核", completed: "整理完成",
    failed: "整理失败，可以重试", disabled: "长期记忆已关闭", duplicate: "归档重复项", merge: "合并", correct: "更正",
    stale: "记忆或来源已变化", applied: "已应用", dismissed: "已忽略", memory_disabled: "任务执行期间长期记忆已关闭",
    invalid_model_output: "模型未返回可解析的整理建议", provider_unavailable: "模型服务暂不可用", attempts_exhausted: "重试次数已用完",
  } : {
    pending: "Queued", running: "Running", ready: "Proposals ready for review", completed: "Completed",
    failed: "Maintenance failed. You can retry.", disabled: "Long-term memory is disabled", duplicate: "Archive duplicate", merge: "Merge", correct: "Correct",
    stale: "Memory or source changed", applied: "Applied", dismissed: "Dismissed", memory_disabled: "Long-term memory was disabled during processing",
    invalid_model_output: "The model returned no parseable proposals", provider_unavailable: "The model service is unavailable", attempts_exhausted: "Retry limit reached",
  };

  function label(value: string) {
    return labels[value as keyof typeof labels] ?? value;
  }

  async function run(action: "start" | "refresh" | "apply" | "dismiss", proposalId?: string) {
    setBusy(true);
    setNotice("");
    async function latest() {
      return request<MaintenanceJob | null>(`${base}/latest?project_id=${encodeURIComponent(projectId)}`);
    }
    try {
      if (action === "refresh") {
        const current = await latest();
        setJob(current);
        if (!current) setNotice(chinese ? "当前项目还没有整理任务。" : "This project has no maintenance jobs yet.");
      } else if (action === "start") {
        setJob(await request<MaintenanceJob>(base, {
          method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ project_id: projectId }),
        }));
      } else if (job && proposalId) {
        setJob(await request<MaintenanceJob>(`${base}/${job.id}/proposals/${proposalId}`, {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ decision: action, expected_version: job.version }),
        }));
        if (action === "apply") await onApplied();
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : (chinese ? "操作失败" : "Request failed"));
      if (proposalId) await latest().then(setJob).catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  if (projects.length === 0) return null;
  const button = "rounded-full border border-[var(--hairline)] px-3 py-1.5 text-xs disabled:opacity-50";
  return <section className="space-y-3 rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
    <h2 className="text-sm font-semibold text-[var(--ink-strong)]">{chinese ? "项目记忆整理" : "Project memory review"}</h2>
    <p className="text-xs text-[var(--ink-soft)]">{chinese
      ? "检查所选项目的最多 20 条有效记忆。确认建议后才会更新记忆。"
      : "Review up to 20 active memories in the selected project. Changes require your approval."}</p>
    <div className="flex flex-wrap items-center gap-2">
      <select aria-label={chinese ? "选择要整理的项目" : "Project to review"} value={projectId} disabled={busy}
        className="min-w-0 max-w-full rounded-xl border border-[var(--hairline)] bg-[var(--control-bg)] px-3 py-1.5 text-xs"
        onChange={(event) => { setSelection(event.target.value); setJob(null); setNotice(""); }}>
        {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
      </select>
      <button type="button" className={button} disabled={busy || !enabled || ["pending", "running"].includes(job?.status ?? "")}
        onClick={() => void run("start")}>{chinese ? "整理当前项目记忆" : "Review project memories"}</button>
      <button type="button" className={button} disabled={busy} onClick={() => void run("refresh")}>
        {chinese ? "查看整理结果 / 刷新" : "Show results / Refresh"}
      </button>
    </div>
    {notice && <p role="status" className="text-xs text-[var(--ink-soft)]">{notice}</p>}
    {job && <div className="space-y-3" data-testid="memory-maintenance-result">
      <p className="text-xs text-[var(--ink-soft)]">{label(job.status)}
        {job.status === "completed" && job.proposals.length === 0 && (chinese ? " · 本次没有可供确认的建议" : " · No proposals")}
        {job.proposals.length > 0 && (chinese ? ` · 已应用 ${job.accepted_count} / 已审核 ${job.reviewed_count}`
          : ` · ${job.accepted_count} applied / ${job.reviewed_count} reviewed`)}</p>
      {job.error_code && job.error_code in labels && <p className="text-xs text-[var(--ink-muted)]">{label(job.error_code)}</p>}
      {job.model_ms !== null && <p className="text-xs text-[var(--ink-muted)]">{chinese ? "模型耗时" : "Model time"} {job.model_ms} ms
        {job.estimated_input_tokens !== null && job.estimated_output_tokens !== null &&
          ` · ${chinese ? "估算输入 / 输出" : "Estimated input / output"} ${job.estimated_input_tokens} / ${job.estimated_output_tokens} tokens`}</p>}
      {job.proposals.map((item) => <div key={item.id} className="space-y-2 rounded-xl border border-[var(--hairline)] bg-[var(--control-bg)] p-3 text-xs">
        <p className="font-medium">{label(item.kind)}{item.target_title && ` · ${item.target_title}`}</p>
        {item.target_content && <p className="whitespace-pre-wrap break-words">{chinese ? "保留项当前正文" : "Current text"} · {item.target_content}</p>}
        {item.related_content && <p className="whitespace-pre-wrap break-words">{chinese ? "拟归档正文" : "Text to archive"} · {item.related_content}</p>}
        {item.proposed_content && <p className="whitespace-pre-wrap break-words">{chinese ? "建议正文" : "Proposed text"} · {item.proposed_content}</p>}
        {item.source_quote && <p className="whitespace-pre-wrap break-words text-[var(--ink-muted)]">{chinese ? "支持原话" : "Evidence"} · {item.source_quote}</p>}
        {item.status === "pending" ? <div className="flex flex-wrap gap-2">
          <button type="button" className={button} disabled={busy || !enabled} onClick={() => void run("apply", item.id)}>
            {chinese ? "确认应用整理建议" : "Apply proposal"}</button>
          <button type="button" className={button} disabled={busy || !enabled} onClick={() => void run("dismiss", item.id)}>
            {chinese ? "忽略" : "Dismiss"}</button>
        </div> : <p className="text-[var(--ink-muted)]">{label(item.status)}</p>}
      </div>)}
    </div>}
  </section>;
}

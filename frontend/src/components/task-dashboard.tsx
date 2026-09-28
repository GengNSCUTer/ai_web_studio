"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

export type DurableRun = {
  id: string;
  conversation_id: string | null;
  status: string;
  current_step: number;
  max_steps: number;
  created_at: string;
  finished_at: string | null;
};

type Step = {
  id: string;
  sequence: number;
  tool_key: string;
  status: string;
  attempts: number;
  max_attempts: number;
  error_message: string | null;
};

type Artifact = { id: string; preview: string; artifact_type: string };
type Snapshot = { run: DurableRun; steps: Step[]; artifacts: Artifact[] };

export type RuntimeMetrics = {
  durable_health?: {
    dead_letter_steps: number;
    expired_running_events: number;
    workers?: {
      online_workers: number;
      busy_workers: number;
      stale_workers: number;
      pending_events: number;
      overdue_events: number;
      last_heartbeat_at: string | null;
    };
    alerts: Array<{ code: string; severity: string; count: number }>;
  };
};

const statusLabels: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  succeeded: "已完成",
  failed: "失败",
  dead_letter: "待人工处理",
  cancelled: "已取消",
  pending: "等待执行",
  skipped: "已跳过",
};

const alertLabels: Record<string, string> = {
  durable_no_online_worker: "有待执行任务，但没有在线 Worker",
  durable_worker_stale: "Worker 心跳已过期",
  durable_queue_overdue: "有任务排队超过 10 分钟",
  durable_lease_expired: "有步骤租约已过期，等待接管",
  durable_dlq_nonempty: "有失败步骤需要处理",
};

function dateText(value: string | null) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload?.detail?.message || payload?.detail || `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : "请求失败");
  }
  return payload as T;
}

export function TaskDashboard({
  initialRuns,
  initialMetrics,
  initialRunId,
}: {
  initialRuns: DurableRun[];
  initialMetrics: RuntimeMetrics | null;
  initialRunId: string | null;
}) {
  const [runs, setRuns] = useState(initialRuns);
  const [hasMore, setHasMore] = useState(initialRuns.length === 50);
  const [metrics, setMetrics] = useState(initialMetrics);
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<Snapshot | null>(null);
  const [confirmStepId, setConfirmStepId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!initialRunId) return;
    let active = true;
    void jsonRequest<Snapshot>(`/api/backend/agent-runtime/runs/${encodeURIComponent(initialRunId)}`)
      .then((snapshot) => { if (active) setSelected(snapshot); })
      .catch(() => { if (active) setError("无法读取该任务，或当前账号无权访问。"); });
    return () => { active = false; };
  }, [initialRunId]);

  const selectedRunId = selected?.run.id;
  const hasActiveRun = runs.length <= 50 && runs.some((run) => run.status === "queued" || run.status === "running");
  useEffect(() => {
    if (!hasActiveRun) return;
    let active = true;
    const timer = window.setInterval(() => {
      const query = filter === "all" ? "" : `?status=${encodeURIComponent(filter)}`;
      void Promise.all([
        jsonRequest<DurableRun[]>(`/api/backend/agent-runtime/tool-runs${query}`),
        jsonRequest<RuntimeMetrics>("/api/backend/agent-runtime/metrics"),
        selectedRunId
          ? jsonRequest<Snapshot>(`/api/backend/agent-runtime/runs/${encodeURIComponent(selectedRunId)}`)
          : Promise.resolve(null),
      ]).then(([nextRuns, nextMetrics, snapshot]) => {
        if (!active) return;
        setRuns(nextRuns);
        setMetrics(nextMetrics);
        if (snapshot) setSelected(snapshot);
      }).catch(() => undefined);
    }, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, [filter, hasActiveRun, selectedRunId]);

  async function refresh(nextFilter = filter, selectedRunId = selected?.run.id) {
    setBusy(true);
    setError("");
    try {
      const query = nextFilter === "all" ? "" : `?status=${encodeURIComponent(nextFilter)}`;
      const [nextRuns, nextMetrics] = await Promise.all([
        jsonRequest<DurableRun[]>(`/api/backend/agent-runtime/tool-runs${query}`),
        jsonRequest<RuntimeMetrics>("/api/backend/agent-runtime/metrics"),
      ]);
      setRuns(nextRuns);
      setHasMore(nextRuns.length === 50);
      setMetrics(nextMetrics);
      if (selectedRunId) {
        const snapshot = await jsonRequest<Snapshot>(
          `/api/backend/agent-runtime/runs/${encodeURIComponent(selectedRunId)}`
        );
        setSelected(snapshot);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "任务状态刷新失败");
    } finally {
      setBusy(false);
    }
  }

  async function loadMore() {
    setBusy(true);
    setError("");
    try {
      const filterQuery = filter === "all" ? "" : `&status=${encodeURIComponent(filter)}`;
      const page = await jsonRequest<DurableRun[]>(
        `/api/backend/agent-runtime/tool-runs?offset=${runs.length}${filterQuery}`
      );
      setRuns((current) => [...current, ...page.filter((run) => !current.some((item) => item.id === run.id))]);
      setHasMore(page.length === 50);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "更多任务加载失败");
    } finally {
      setBusy(false);
    }
  }

  async function openRun(runId: string) {
    setError("");
    setConfirmStepId(null);
    try {
      setSelected(await jsonRequest<Snapshot>(`/api/backend/agent-runtime/runs/${encodeURIComponent(runId)}`));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取任务详情");
    }
  }

  async function replay(step: Step) {
    if (!selected || confirmStepId !== step.id) return;
    setBusy(true);
    setError("");
    try {
      await jsonRequest(
        `/api/backend/agent-runtime/tool-runs/${encodeURIComponent(selected.run.id)}/steps/${encodeURIComponent(step.id)}/replay`,
        { method: "POST" }
      );
      setConfirmStepId(null);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "重放失败");
      setBusy(false);
    }
  }

  async function exportAudit() {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(
        `/api/backend/agent-runtime/tool-runs/${encodeURIComponent(selected.run.id)}/audit.jsonl`,
        { cache: "no-store" },
      );
      if (!response.ok) throw new Error("审计记录下载失败");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `agent-run-${selected.run.id}.jsonl`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "审计记录下载失败");
    } finally {
      setBusy(false);
    }
  }

  const health = metrics?.durable_health;
  const workers = health?.workers;

  return (
    <main className="min-h-screen bg-[var(--app-bg)] px-4 py-6 text-[var(--ink-strong)] sm:px-6">
      <div className="mx-auto max-w-6xl">
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--hairline)] pb-5">
          <div>
            <p className="text-xs text-[var(--ink-muted)]">AI Web Studio / Durable</p>
            <h1 className="mt-1 text-2xl font-semibold">后台任务</h1>
          </div>
          <nav className="flex items-center gap-4 text-sm text-[var(--ink-soft)]">
            <Link href="/chat" className="hover:text-[var(--accent-strong)]">返回会话</Link>
            <Link href="/" className="hover:text-[var(--accent-strong)]">工作台</Link>
            <button type="button" onClick={() => void refresh()} disabled={busy} className="rounded-md border border-[var(--control-border)] px-3 py-2 disabled:opacity-50">
              {busy ? "刷新中" : "刷新"}
            </button>
          </nav>
        </header>

        <section aria-label="运行状态" className="grid gap-4 border-b border-[var(--hairline)] py-5 sm:grid-cols-3">
          <div><div className="text-xs text-[var(--ink-muted)]">在线 Worker</div><div className="mt-1 text-xl font-semibold">{workers?.online_workers ?? "--"}</div><div className="mt-1 text-xs text-[var(--ink-muted)]">执行中 {workers?.busy_workers ?? "--"} · 最后心跳 {dateText(workers?.last_heartbeat_at ?? null)}</div></div>
          <div><div className="text-xs text-[var(--ink-muted)]">待执行事件</div><div className="mt-1 text-xl font-semibold">{workers?.pending_events ?? "--"}</div></div>
          <div><div className="text-xs text-[var(--ink-muted)]">待人工处理</div><div className="mt-1 text-xl font-semibold">{health?.dead_letter_steps ?? "--"}</div></div>
        </section>
        {health?.alerts?.length ? (
          <section aria-label="运行告警" className="border-b border-[var(--hairline)] py-3 text-sm text-[var(--danger-text)]">
            {health.alerts.map((alert) => <p key={alert.code}>{alertLabels[alert.code] ?? alert.code}（{alert.count}）</p>)}
          </section>
        ) : null}
        {error ? <p role="alert" className="py-3 text-sm text-[var(--danger-text)]">{error}</p> : null}

        <div className="grid gap-6 py-5 lg:grid-cols-[minmax(280px,0.9fr)_minmax(0,1.6fr)]">
          <section aria-label="任务列表" className="min-w-0">
            <div className="mb-4 flex flex-wrap gap-2">
              {([ ["all", "全部"], ["queued", "排队"], ["running", "执行中"], ["succeeded", "完成"], ["dead_letter", "待处理"], ["failed", "失败"] ] as const).map(([value, label]) => (
                <button key={value} type="button" onClick={() => { setFilter(value); void refresh(value, undefined); }}
                  aria-pressed={filter === value}
                  className={`rounded-md border px-3 py-1.5 text-xs ${filter === value ? "border-[var(--accent-strong)] bg-[var(--accent-soft)] text-[var(--accent-strong)]" : "border-[var(--control-border)] text-[var(--ink-soft)]"}`}>
                  {label}
                </button>
              ))}
            </div>
            <div className="space-y-2">
              {runs.length ? runs.map((run) => (
                <button key={run.id} type="button" onClick={() => void openRun(run.id)}
                  className={`w-full rounded-md border p-3 text-left ${selected?.run.id === run.id ? "border-[var(--accent-strong)]" : "border-[var(--panel-border)]"} bg-[var(--panel-bg)]`}>
                  <div className="flex items-center justify-between gap-3 text-sm font-medium"><span>{statusLabels[run.status] ?? run.status}</span><span className="text-xs text-[var(--ink-muted)]">{dateText(run.created_at)}</span></div>
                  <div className="mt-2 truncate font-mono text-xs text-[var(--ink-soft)]">{run.id}</div>
                  <div className="mt-1 text-xs text-[var(--ink-muted)]">步骤 {run.current_step}/{run.max_steps}</div>
                </button>
              )) : <p className="py-10 text-center text-sm text-[var(--ink-muted)]">当前没有任务</p>}
              {hasMore ? <button type="button" onClick={() => void loadMore()} disabled={busy} className="w-full rounded-md border border-[var(--control-border)] py-2 text-sm text-[var(--ink-soft)] disabled:opacity-50">加载更多</button> : null}
            </div>
          </section>

          <section aria-label="任务详情" className="min-w-0 border-t border-[var(--hairline)] pt-5 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
            {selected ? <>
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--hairline)] pb-4">
                <div><h2 className="text-base font-semibold">任务详情 · {statusLabels[selected.run.status] ?? selected.run.status}</h2>
                  <p className="mt-1 break-all font-mono text-xs text-[var(--ink-muted)]">{selected.run.id}</p></div>
                <div className="flex flex-wrap items-center gap-3">
                  <button type="button" onClick={() => void exportAudit()} disabled={busy} className="text-sm text-[var(--accent-strong)] disabled:opacity-50">导出审计 JSONL</button>
                  {selected.run.conversation_id ? <Link className="text-sm text-[var(--accent-strong)]" href={`/chat?conversation=${encodeURIComponent(selected.run.conversation_id)}`}>查看原会话</Link> : null}
                </div>
              </div>
              <div className="divide-y divide-[var(--hairline)]">
                {selected.steps.map((step) => <div key={step.id} className="py-4 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2"><div><span className="font-medium">{step.sequence}. {step.tool_key}</span><span className="ml-2 text-xs text-[var(--ink-muted)]">{statusLabels[step.status] ?? step.status} · 尝试 {step.attempts}/{step.max_attempts}</span></div>
                    {step.status === "dead_letter" ? <button type="button" disabled={busy} onClick={() => setConfirmStepId(step.id)} className="text-xs text-[var(--accent-strong)]">受控重放</button> : null}</div>
                  {step.error_message ? <p className="mt-2 whitespace-pre-wrap break-words text-xs text-[var(--danger-text)]">{step.error_message}</p> : null}
                  {confirmStepId === step.id ? <div className="mt-3 flex items-center gap-3 text-xs"><span>重新执行此只读步骤及其依赖后续步骤？</span><button type="button" disabled={busy} onClick={() => void replay(step)} className="rounded-md bg-[var(--accent-strong)] px-2 py-1 text-white">确认重放</button><button type="button" onClick={() => setConfirmStepId(null)}>取消</button></div> : null}
                </div>)}
              </div>
              {selected.artifacts.length ? <section className="border-t border-[var(--hairline)] py-4"><h3 className="text-sm font-medium">产物</h3>{selected.artifacts.map((artifact) => <pre key={artifact.id} className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words bg-[var(--soft-bg)] p-3 text-xs text-[var(--ink-soft)]">{artifact.preview}</pre>)}</section> : null}
            </> : <p className="py-10 text-sm text-[var(--ink-muted)]">选择一项任务查看步骤、失败原因和产物。</p>}
          </section>
        </div>
      </div>
    </main>
  );
}

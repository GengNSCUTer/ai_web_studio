"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { MessageMarkdown } from "@/components/message-markdown";
import type { Project } from "@/lib/types";

export type DocumentTask = { id: string; project_id: string; conversation_id: string | null; file_name: string;
  status: string; current_step: number;
  steps: { id: string; stage: string; status: string; attempts: number; error_message: string | null }[];
  artifacts: { id: string; stage: string; preview: string; download_url: string | null }[];
  approvals: { id: string; status: string; action: string }[] };

async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`/api/backend${path}`, { method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "操作失败，请刷新状态。");
  return data as T;
}
const BTN = "rounded-xl border border-[var(--hairline)] px-3 py-2 text-sm hover:bg-[var(--soft-bg)] disabled:opacity-40";
const STAGES: Record<string, string> = { evidence: "取证", draft: "生成草稿", export: "导出", delivery: "审批交付" };

export function DocumentTasks({ initialTasks, projects, initialRunId }: { initialTasks: DocumentTask[]; projects: Project[]; initialRunId: string | null }) {
  const [tasks, setTasks] = useState(initialTasks);
  const [selectedId, setSelectedId] = useState(initialRunId ?? initialTasks[0]?.id ?? null);
  const [projectId, setProjectId] = useState("");
  const [files, setFiles] = useState<{ id: string; file_name: string }[]>([]);
  const [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const [instruction, setInstruction] = useState("请根据资料整理一份结构清晰的中文说明，列出来源与尚不确定的内容。");
  const [name, setName] = useState("资料整理");
  const [format, setFormat] = useState("docx");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const selected = tasks.find((x) => x.id === selectedId);
  const active = tasks.some((x) => ["queued", "running"].includes(x.status));

  useEffect(() => {
    if (!active) return;
    let alive = true;
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      void request<DocumentTask[]>("/document-tasks").then((data) => { if (alive) setTasks(data); }).catch(() => undefined);
    }, 3000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [active]);

  async function act(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError("");
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : "操作失败"); }
    finally { setBusy(false); }
  }
  async function refresh() { setTasks(await request("/document-tasks")); }

  return <main className="min-h-screen bg-[var(--page-bg)] px-4 py-8 text-[var(--ink-strong)]">
    <div className="mx-auto max-w-6xl space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-2xl font-semibold">文档任务与产物</h1>
        <p className="mt-2 text-sm text-[var(--ink-muted)]">分阶段保存进度，断线后可回看；保存工作区需再次批准，已生成产物可先下载。</p></div>
        <nav className="flex gap-4 text-sm"><Link href="/chat">问答台</Link><Link href="/plugins">插件中心</Link><Link href="/tasks">工具任务</Link></nav></header>
      {error ? <p role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-800">{error}</p> : null}
      <details className="rounded-2xl border border-[var(--hairline)] p-4"><summary className="cursor-pointer font-medium">从工作区资料创建文档任务</summary>
        <form className="mt-4 space-y-3" onSubmit={(e) => { e.preventDefault(); void act(async () => {
          const task = await request<DocumentTask>("/document-tasks", "POST", { project_id: projectId, file_ids: selectedFiles,
            instruction, file_name: `${name}.${format === "docx" ? "docx" : "md"}`, format, idempotency_key: crypto.randomUUID() });
          setSelectedId(task.id); setTasks((old) => [task, ...old]);
        }); }}>
          <select aria-label="文档工作区" required value={projectId} disabled={busy} className="max-w-full rounded-xl border p-2" onChange={(e) => {
            const id = e.target.value; setProjectId(id); setSelectedFiles([]); setFiles([]);
            if (id) void act(async () => setFiles(await request(`/projects/${id}/files`)));
          }}><option value="">选择工作区</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
          <div className="max-h-48 overflow-auto">{files.map((f) => <label key={f.id} className="flex gap-2 py-1 text-sm"><input type="checkbox" checked={selectedFiles.includes(f.id)} onChange={(e) => setSelectedFiles((old) => e.target.checked ? [...old, f.id] : old.filter((x) => x !== f.id))} />{f.file_name}</label>)}</div>
          <textarea aria-label="文档任务要求" required maxLength={2000} value={instruction} onChange={(e) => setInstruction(e.target.value)} className="min-h-24 w-full rounded-xl border p-3 text-sm" />
          <div className="flex flex-wrap gap-3"><input aria-label="文档名称" required maxLength={150} value={name} onChange={(e) => setName(e.target.value)} className="min-w-0 rounded-xl border p-2" />
            <select aria-label="文档格式" value={format} onChange={(e) => setFormat(e.target.value)} className="rounded-xl border p-2"><option value="docx">Word</option><option value="markdown">Markdown</option></select>
            <button className={BTN} disabled={busy || !selectedFiles.length}>创建后台文档任务</button></div>
        </form></details>
      <div className="grid gap-5 md:grid-cols-[240px_minmax(0,1fr)]"><aside className="space-y-2">
        <button className={BTN} disabled={busy} onClick={() => void act(refresh)}>刷新任务</button>
        {tasks.map((t) => <button key={t.id} className={`block w-full break-words rounded-xl border p-3 text-left text-sm ${selectedId === t.id ? "border-[var(--accent-strong)]" : "border-[var(--hairline)]"}`} onClick={() => { setSelectedId(t.id); setPreview(null); }}>
          {t.file_name}<span className="mt-1 block text-xs text-[var(--ink-muted)]">{t.status} · {t.current_step}/4</span></button>)}</aside>
        <section className="min-w-0 rounded-2xl border border-[var(--hairline)] p-4">
          {selected ? <><h2 className="font-semibold">{selected.file_name}</h2><p className="mt-2 text-sm">状态：{selected.status}</p>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">{selected.steps.map((s) => <div key={s.id} className="rounded-xl bg-[var(--soft-bg)] p-3 text-sm"><p>{STAGES[s.stage] || s.stage} · {s.status}</p>
              <p className="mt-1 text-xs">尝试 {s.attempts} 次</p>{s.error_message ? <p className="mt-1 break-words text-xs text-red-800">{s.error_message}</p> : null}</div>)}</div>
            <div className="mt-4 flex flex-wrap gap-2">{!["succeeded", "failed", "dead_letter", "cancelled"].includes(selected.status) ? <button className={BTN} disabled={busy} onClick={() => void act(async () => { await request(`/document-tasks/${selected.id}/cancel`, "POST"); await refresh(); })}>取消文档任务</button> : null}
              {["failed", "dead_letter"].includes(selected.status) ? <button className={BTN} disabled={busy} onClick={() => void act(async () => { await request(`/document-tasks/${selected.id}/resume`, "POST"); await refresh(); })}>确认重试失败阶段</button> : null}
              {selected.status === "waiting_approval" ? selected.approvals.filter((a) => a.status === "pending").map((a) => <button key={a.id} className={BTN} disabled={busy} onClick={() => void act(async () => {
                const challenge = await request<{ approval_token: string }>(`/agent-runtime/approvals/${a.id}/challenge`, "POST");
                await request(`/agent-runtime/approvals/${a.id}/apply`, "POST", { approval_token: challenge.approval_token }); await refresh();
              })}>批准保存到工作区（创建新文件）</button>) : null}
            </div>
            <div className="mt-5 space-y-3">{selected.artifacts.map((a) => <article key={a.id} className="rounded-xl border border-[var(--hairline)] p-3 text-sm"><p>{a.stage.replace("document_", "")} · 已保存产物</p>
              <p className="mt-1 line-clamp-3 whitespace-pre-wrap break-words text-xs text-[var(--ink-muted)]">{a.preview}</p>
              <div className="mt-2 flex gap-3"><button className="text-[var(--accent-strong)]" disabled={busy} onClick={() => void act(async () => {
                const result = await request<{ content: { text?: string; sources?: { title: string; content: string }[]; file_name?: string } }>(`/document-tasks/${selected.id}/artifacts/${a.id}`);
                setPreview(result.content.text || result.content.sources?.map((x) => `## ${x.title}\n\n${x.content}`).join("\n\n") || result.content.file_name || "阶段结果已保存");
              })}>打开产物</button>{a.download_url ? <a href={a.download_url.replace(/^\/api\//, "/api/backend/")} className="text-[var(--accent-strong)]">下载导出文件</a> : null}</div></article>)}</div>
            {preview ? <div className="mt-5 max-h-[560px] overflow-auto rounded-xl bg-[var(--soft-bg)] p-4"><MessageMarkdown content={preview} /></div> : null}
          </> : <p className="text-sm text-[var(--ink-muted)]">从左侧选择任务，或从问答消息保存为文档。</p>}
        </section></div>
    </div></main>;
}

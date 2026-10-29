"use client";

import Link from "next/link";
import { useState } from "react";
import type { Project } from "@/lib/types";

type Preflight = { catalog_revision: string; permissions: string[]; dependencies: string[]; domains: string[]; missing_tool_keys: string[]; can_enable: boolean; notice: string };
type Plugin = { plugin_key: string; version: string; name: string; description: string; skills: string[];
  permissions: string[]; examples: string[]; artifact_types: string[]; scripts: string[]; tool_keys: string[];
  source: string; review_status: string; compatibility: string; is_installed: boolean;
  installed_version: string | null; installed_release_id: string | null; is_project_enabled: boolean; update_available: boolean; preflight: Preflight };
export type PluginCenterData = { plugins: Plugin[]; sandbox: { available: boolean; reason: string | null } };
type ScriptJob = { id: string; status: string; error: string | null; domains: string[]; install_domains: string[]; dependencies: string[];
  files: { id: string; name: string; bytes: number }[]; limits: Record<string, string | number>;
  wheel_lock: { requirement: string; sha256: string }[]; runtime: { available: boolean; reason: string | null };
  notice: string; result: { artifacts: { name: string; content: string }[] } | null };

async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`/api/backend${path}`, { method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(typeof result.detail === "string" ? result.detail : `请求失败：${response.status}`);
  }
  return response.json() as Promise<T>;
}

const BUTTON = "rounded-xl border border-[var(--hairline)] px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40 hover:bg-[var(--soft-bg)]";

export function PluginCenter({ initialData, projects }: { initialData: PluginCenterData | null; projects: Project[] }) {
  const [data, setData] = useState(initialData);
  const [projectId, setProjectId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialData ? null : "插件目录加载失败，请刷新。");
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState<{ plugin: Plugin; check: Preflight } | null>(null);
  const [files, setFiles] = useState<{ id: string; file_name: string; parsed_text: string | null }[]>([]);
  const [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const [job, setJob] = useState<ScriptJob | null>(null);
  const [useTabulate, setUseTabulate] = useState(false);
  const [submission, setSubmission] = useState({ name: "", kind: "skill", source_url: "" });

  async function act(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(null); setNotice("");
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : "操作失败"); }
    finally { setBusy(false); }
  }
  async function reload(id = projectId) {
    const result = await api<PluginCenterData>(`/plugins${id ? `?project_id=${encodeURIComponent(id)}` : ""}`);
    setData(result);
  }
  async function changeProject(id: string) {
    setProjectId(id); setFiles([]); setSelectedFiles([]); setJob(null); setPreview(null);
    await reload(id);
    if (id) setFiles(await api(`/projects/${id}/files`));
  }
  const organize = data?.plugins.find((p) => p.plugin_key === "knowledge.organize");

  return <main className="min-h-screen bg-[var(--page-bg)] px-4 py-8 text-[var(--ink-strong)] sm:px-8">
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div><h1 className="text-2xl font-semibold">审核插件中心</h1><p className="mt-2 text-sm text-[var(--ink-muted)]">按任务组织已有工具、Skill 和文档产物。安装、会话激活、具体写入审批分别进行。</p></div>
        <nav className="flex flex-wrap gap-3 text-sm"><Link href="/">工作台</Link><Link href="/chat">问答台</Link><Link href="/documents">文档任务</Link><Link href="/settings?tab=tools">工具设置</Link></nav>
      </header>
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-[var(--hairline)] p-4">
        <label htmlFor="plugin-project">当前工作区</label>
        <select id="plugin-project" value={projectId} disabled={busy} className="max-w-full rounded-lg border p-2 text-sm" onChange={(e) => void act(() => changeProject(e.target.value))}>
          <option value="">仅浏览 / 安装到账号</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button className={BUTTON} disabled={busy} onClick={() => void act(() => reload())}>刷新状态</button>
      </div>
      {error ? <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p> : null}
      {notice ? <p role="status" className="rounded-xl bg-[var(--soft-bg)] p-3 text-sm">{notice}</p> : null}
      <section className="grid gap-4 md:grid-cols-2">
        {data?.plugins.map((plugin) => <article key={plugin.plugin_key} className="min-w-0 rounded-2xl border border-[var(--hairline)] p-5">
          <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">{plugin.name}</h2><span className="text-xs">已审核 · v{plugin.version}</span></div>
          <p className="mt-3 text-sm leading-6">{plugin.description}</p>
          <p className="mt-2 text-xs text-[var(--ink-muted)]">{plugin.source} · {plugin.compatibility}</p>
          <p className="mt-2 text-xs">产物：{plugin.artifact_types.join(" / ")}</p>
          <details className="mt-3 text-sm"><summary className="cursor-pointer">能力、权限与使用示例</summary>
            <div className="mt-2 space-y-2 break-words text-xs leading-5">
              <p>Skill：{plugin.skills.join("、")}</p><p>工具：{plugin.tool_keys.join("、")}</p>
              <p>声明权限：{plugin.permissions.join("、")}</p>{plugin.examples.map((e) => <p key={e}>示例：{e}</p>)}
            </div></details>
          {plugin.preflight.missing_tool_keys.length ? <p className="mt-3 text-xs text-amber-800">待配置：{plugin.preflight.missing_tool_keys.join("、")}</p> : null}
          <p className="mt-3 text-xs">{plugin.is_installed ? `已安装 ${plugin.installed_version}；${plugin.is_project_enabled ? "当前工作区已启用" : "当前工作区未启用"}` : "未安装"}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button className={BUTTON} disabled={busy} onClick={() => void act(async () => setPreview({ plugin, check: await api(`/plugins/${plugin.plugin_key}/preflight${projectId ? `?project_id=${projectId}` : ""}`) }))}>安装预检</button>
            {plugin.is_installed ? <>
              <button className={BUTTON} disabled={busy || !projectId || (!plugin.is_project_enabled && !plugin.preflight.can_enable)} onClick={() => void act(async () => {
                await api(`/plugins/${plugin.plugin_key}/project`, "PUT", { project_id: projectId, enabled: !plugin.is_project_enabled, expected_release_id: plugin.installed_release_id }); await reload();
                setNotice("项目启用已更新。请回到问答台显式选择 Skill；文件修改仍需单次审批。");
              })}>{plugin.is_project_enabled ? "停用项目" : "在项目启用"}</button>
              <button className={BUTTON} disabled={busy || !plugin.update_available} onClick={() => void act(async () => setPreview({ plugin, check: await api(`/plugins/${plugin.plugin_key}/preflight`) }))}>升级预检</button>
              <button className={BUTTON} disabled={busy} onClick={() => void act(async () => { await api(`/plugins/${plugin.plugin_key}/rollback`, "POST"); await reload(); setNotice("已回滚，需重新审核并启用项目。"); })}>回滚</button>
              <button className={BUTTON} disabled={busy} onClick={() => void act(async () => { await api(`/plugins/${plugin.plugin_key}`, "DELETE"); await reload(); })}>卸载</button>
            </> : null}
          </div>
        </article>)}
      </section>
      {preview ? <section aria-label="安装预检" className="rounded-2xl border-2 border-[var(--accent-strong)] p-5">
        <h2 className="font-semibold">确认安装：{preview.plugin.name}</h2>
        <p className="mt-2 text-sm">权限：{preview.check.permissions.join("、")}</p>
        <p className="mt-2 text-sm">依赖：{preview.check.dependencies.join("、") || "无动态依赖"}；联网域名：{preview.check.domains.join("、") || "脚本不联网"}</p>
        <p className="mt-2 text-sm">{preview.check.notice}</p>
        <div className="mt-3 flex gap-3"><button className={BUTTON} disabled={busy} onClick={() => void act(async () => {
          await api(`/plugins/${preview.plugin.plugin_key}/${preview.plugin.is_installed ? "upgrade" : "install"}`, "POST", { expected_revision: preview.check.catalog_revision });
          setPreview(null); await reload(); setNotice("已安装审核版本，请单独启用工作区并在会话选择 Skill。");
        })}>确认安装审核版本</button><button className={BUTTON} onClick={() => setPreview(null)}>取消</button></div>
      </section> : null}
      <section className="rounded-2xl border border-[var(--hairline)] p-5">
        <h2 className="font-semibold">隔离 Python 脚本：资料清单</h2>
        <p className="mt-2 text-sm">{data?.sandbox.available ? "独立 Worker 就绪" : data?.sandbox.reason || "运行环境状态未知"}</p>
        <p className="mt-2 text-xs text-[var(--ink-muted)]">只传入勾选文件的正文副本，生成 JSON / Markdown 清单；不覆盖原文件。容器不等于任意恶意代码安全保证。</p>
        <div className="mt-3 max-h-48 space-y-2 overflow-auto">{files.filter((f) => Boolean(f.parsed_text)).map((f) => <label key={f.id} className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={selectedFiles.includes(f.id)} disabled={busy} onChange={(e) => { setJob(null); setSelectedFiles((old) => e.target.checked ? [...old, f.id] : old.filter((id) => id !== f.id)); }} />{f.file_name}
        </label>)}</div>
        <label className="mt-3 flex items-center gap-2 text-xs"><input type="checkbox" checked={useTabulate} onChange={(e) => { setUseTabulate(e.target.checked); setJob(null); }} />可选：安装审核依赖 tabulate==0.9.0，生成表格清单（先解析完整依赖，再二次确认）。</label>
        <button className={`${BUTTON} mt-3`} disabled={busy || !projectId || !organize?.is_project_enabled || !selectedFiles.length} onClick={() => void act(async () => {
          setJob(await api("/plugins/sandbox/preview", "POST", { project_id: projectId, plugin_key: "knowledge.organize", script_key: "document_inventory", file_ids: selectedFiles, domains: [], dependencies: useTabulate ? ["tabulate==0.9.0"] : [] }));
        })}>预览本次脚本授权</button>
        {job ? <div className="mt-4 space-y-2 rounded-xl bg-[var(--soft-bg)] p-4 text-sm">
          <p>任务状态：{job.status}</p><p>授权副本：{job.files.map((f) => f.name).join("、")}</p>
          <p>本次域名：{job.domains.join("、") || "不联网"}；依赖：{job.dependencies.join("、") || "无"}</p>
          <p>安装阶段域名：{job.install_domains.join("、") || "不联网"}（不传入资料）</p>
          {job.wheel_lock.map((x) => <p className="break-all text-xs" key={x.requirement}>完整依赖：{x.requirement} · {x.sha256}</p>)}
          <p className="text-xs">{job.notice}</p>{job.error ? <p role="alert">{job.error}</p> : null}
          <div className="flex flex-wrap gap-2"><button className={BUTTON} disabled={busy || !job.runtime.available || !["awaiting_approval", "awaiting_dependency_approval"].includes(job.status)} onClick={() => void act(async () => setJob(await api(`/plugins/sandbox/${job.id}/confirm`, "POST", { approved: true })))}>
            {job.status === "awaiting_dependency_approval" ? "确认完整依赖并执行" : "批准本次隔离执行"}</button>
            <button className={BUTTON} disabled={busy} onClick={() => void act(async () => setJob(await api(`/plugins/sandbox/jobs/${job.id}`)))}>刷新任务</button></div>
          {!job.runtime.available ? <p className="text-amber-800">{job.runtime.reason}</p> : null}
          {job.result?.artifacts.map((a, index) => <details key={a.name}><summary>{a.name}</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs">{a.content}</pre>
            <button className={BUTTON} disabled={busy} onClick={() => void act(async () => {
              const result = await api<{ download_url: string }>(`/plugins/sandbox/jobs/${job.id}/artifacts/${index}/save`, "POST");
              window.open(result.download_url.replace(/^\/api\//, "/api/backend/"), "_blank", "noreferrer");
              setNotice("已创建新的工作区草稿，原文件未被覆盖。");
            })}>确认保存为工作区草稿并下载</button></details>)}
        </div> : null}
      </section>
      <form className="rounded-2xl border border-[var(--hairline)] p-5" onSubmit={(e) => { e.preventDefault(); void act(async () => { const result = await api<{ notice: string }>("/plugins/submissions", "POST", submission); setNotice(result.notice); }); }}>
        <h2 className="font-semibold">外部来源待审核</h2><p className="mt-2 text-sm">只登记 Skill / MCP / 脚本的来源；不会自动抓取、安装或授予权限，首版不支持公共发布。</p>
        <div className="mt-3 flex flex-wrap gap-2"><input aria-label="来源名称" required maxLength={128} className="rounded-lg border p-2 text-sm" placeholder="名称" value={submission.name} onChange={(e) => setSubmission({ ...submission, name: e.target.value })} />
          <select aria-label="来源类型" className="rounded-lg border p-2 text-sm" value={submission.kind} onChange={(e) => setSubmission({ ...submission, kind: e.target.value })}><option value="skill">Skill</option><option value="mcp">MCP</option><option value="script">脚本</option></select>
          <input aria-label="HTTPS 来源" type="url" required className="min-w-0 flex-1 rounded-lg border p-2 text-sm" placeholder="https://..." value={submission.source_url} onChange={(e) => setSubmission({ ...submission, source_url: e.target.value })} />
          <button className={BUTTON} disabled={busy}>登记待审核来源</button></div>
      </form>
    </div>
  </main>;
}

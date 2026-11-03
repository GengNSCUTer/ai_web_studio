"use client";

import { useState } from "react";

export type McpResourceSelection = { server_id: string; uri: string };
type Resource = { uri: string; name: string; description: string; mimeType: string };

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : "MCP资料请求失败");
  }
  return response.json();
}

export function McpResourcePicker({ projectId, selected, onChange, disabled }: {
  projectId: string | null; selected: McpResourceSelection[];
  onChange: (value: McpResourceSelection[]) => void; disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [servers, setServers] = useState<Array<{ id: string; name: string }>>([]);
  const [serverId, setServerId] = useState("");
  const [resources, setResources] = useState<Resource[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState("");
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";

  async function toggleOpen() {
    if (open) { setOpen(false); return; }
    setOpen(true); setLoading(true); setError("");
    try { setServers(await request(`/api/backend/tools/mcp-resources/servers${query}`)); }
    catch (e) { setError(e instanceof Error ? e.message : "资料服务列表加载失败"); }
    finally { setLoading(false); }
  }

  async function chooseServer(id: string) {
    setServerId(id); setResources([]); setPreview(""); setError("");
    if (!id) return;
    setLoading(true);
    try {
      const data = await request<{ resources: Resource[]; resources_supported: boolean; protocol_version: string }>(
        `/api/backend/tools/mcp-resources/servers/${encodeURIComponent(id)}${query}`);
      setResources(data.resources);
      if (!data.resources_supported) setError("此服务未提供 MCP Resources，可以继续使用其工具能力。");
    } catch (e) { setError(e instanceof Error ? e.message : "资源列表加载失败"); }
    finally { setLoading(false); }
  }

  function select(resource: Resource) {
    const exists = selected.some((item) => item.server_id === serverId && item.uri === resource.uri);
    if (exists) onChange(selected.filter((item) => item.server_id !== serverId || item.uri !== resource.uri));
    else if (selected.length < 5) onChange([...selected, { server_id: serverId, uri: resource.uri }]);
  }

  async function read(resource: Resource) {
    setLoading(true); setError(""); setPreview("");
    try {
      const source = await request<{ display_text: string }>(`/api/backend/tools/mcp-resources/read${query}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server_id: serverId, uri: resource.uri }),
      });
      setPreview(source.display_text);
    } catch (e) { setError(e instanceof Error ? e.message : "预览失败"); }
    finally { setLoading(false); }
  }

  return <div className="mb-2 text-xs">
    <button type="button" data-testid="mcp-resource-picker" disabled={disabled} onClick={() => void toggleOpen()}
      className="rounded-full border border-[var(--hairline)] px-3 py-1.5 disabled:opacity-50">
      MCP资料 · {selected.length} {open ? "收起" : "选择"}
    </button>
    {selected.length ? <button type="button" disabled={disabled} onClick={() => onChange([])} className="ml-2 underline">清空选择</button> : null}
    {open ? <div className="mt-2 rounded-xl border border-[var(--hairline)] bg-[var(--control-bg)] p-3" aria-label="MCP资料选择">
      <p className="mb-2 text-[var(--ink-muted)]">选择最多5份文本资料用于本轮问答；发送和重生成时会重新检查授权并读取。</p>
      <select aria-label="MCP资料服务" value={serverId} disabled={loading || disabled}
        onChange={(event) => void chooseServer(event.target.value)} className="w-full rounded-lg border border-[var(--hairline)] p-2">
        <option value="">选择MCP服务</option>
        {servers.map((server) => <option key={server.id} value={server.id}>{server.name}</option>)}
      </select>
      {!servers.length && !loading ? <p className="mt-2">暂无已启用服务，请先在设置中心添加MCP服务。</p> : null}
      {loading ? <p className="mt-2">正在读取资料服务…</p> : null}
      {error ? <p role="alert" className="mt-2 text-[var(--danger-text)]">{error}</p> : null}
      <div className="mt-2 max-h-48 space-y-2 overflow-auto">
        {resources.map((resource) => <div key={resource.uri} className="rounded-lg bg-[var(--soft-bg)] p-2">
          <label className="flex items-start gap-2">
            <input type="checkbox" disabled={disabled || loading || (selected.length >= 5 && !selected.some((item) => item.server_id === serverId && item.uri === resource.uri))}
              checked={selected.some((item) => item.server_id === serverId && item.uri === resource.uri)} onChange={() => select(resource)} />
            <span className="min-w-0 break-all">{resource.name}<span className="block text-[var(--ink-muted)]">{resource.uri}</span></span>
          </label>
          {resource.description ? <p className="mt-1 line-clamp-2">{resource.description}</p> : null}
          <button type="button" disabled={loading || disabled} onClick={() => void read(resource)} className="mt-1 underline">预览文本</button>
        </div>)}
      </div>
      {preview ? <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-[var(--soft-bg)] p-2">{preview}</pre> : null}
    </div> : null}
  </div>;
}

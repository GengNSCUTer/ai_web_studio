"use client";

import { useEffect, useRef, useState } from "react";

type ActivityItem = {
  event_id: string;
  memory_id: string;
  title: string;
  content: string;
  status: string;
  version: number;
};
type Activity = { conversation_id: string; has_pending_jobs: boolean; items: ActivityItem[] };

export function MemoryActivityPanel({ conversationId, refreshKey, uiLanguage }: {
  conversationId: string;
  refreshKey: string;
  uiLanguage: "zh-CN" | "en-US";
}) {
  const [activity, setActivity] = useState<Activity | null>(null);
  const [error, setError] = useState(false);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const mutationEpoch = useRef(0);
  const zh = uiLanguage === "zh-CN";

  useEffect(() => {
    let disposed = false;
    let inFlight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    async function refresh() {
      if (disposed || document.hidden || inFlight) return;
      inFlight = true;
      controller = new AbortController();
      const requestTimeout = setTimeout(() => controller?.abort(), 10000);
      const epoch = mutationEpoch.current;
      let delay = 10000;
      try {
        const response = await fetch(`/api/backend/memories/activity/${encodeURIComponent(conversationId)}`, {
          cache: "no-store", signal: controller.signal,
        });
        if (!response.ok) throw new Error("memory_activity_unavailable");
        const result = await response.json() as Activity;
        if (!disposed && epoch === mutationEpoch.current && result.conversation_id === conversationId) {
          setActivity(result);
          setError(false);
        }
        delay = result.has_pending_jobs ? 2000 : 10000;
      } catch {
        if (!disposed && !document.hidden) setError(true);
        delay = 30000;
      } finally {
        clearTimeout(requestTimeout);
        inFlight = false;
        if (!disposed) timer = setTimeout(() => void refresh(), delay);
      }
    }
    function onVisibility() {
      if (document.hidden) {
        if (timer) clearTimeout(timer);
        controller?.abort();
      } else {
        if (timer) clearTimeout(timer);
        void refresh();
      }
    }
    void refresh();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [conversationId, refreshKey, revision]);

  async function revoke(item: ActivityItem) {
    mutationEpoch.current += 1;
    setRevokingId(item.memory_id);
    try {
      const response = await fetch(`/api/backend/memories/${encodeURIComponent(item.memory_id)}/forget`, { method: "POST" });
      if (!response.ok) throw new Error("memory_revoke_failed");
      const result = await response.json() as { status: string; version: number };
      setActivity((current) => current ? { ...current, items: current.items.map((row) =>
        row.memory_id === item.memory_id ? { ...row, status: result.status, version: result.version } : row) } : current);
      setError(false);
    } catch {
      setError(true);
    } finally {
      mutationEpoch.current += 1;
      setRevokingId(null);
      setRevision((value) => value + 1);
    }
  }

  if (!activity?.items.length && !activity?.has_pending_jobs && !error) return null;
  const labels: Record<string, string> = zh ? {
    active: "已记住", pending: "待确认，尚未生效", revoked: "已撤销",
    rejected: "已拒绝", superseded: "已更正，旧版本不再使用", expired: "已过期", disabled: "已停用",
  } : {
    active: "Remembered", pending: "Needs review, not active", revoked: "Revoked",
    rejected: "Rejected", superseded: "Superseded", expired: "Expired", disabled: "Disabled",
  };
  return (
    <section aria-label={zh ? "后台记忆通知" : "Background memory updates"} aria-live="polite"
      className="mx-auto mb-2.5 max-h-44 w-full max-w-[74rem] overflow-y-auto rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-4 py-2 text-xs text-[var(--ink-soft)]">
      <div className="flex items-center justify-between gap-3">
        <span>{zh ? "后台记忆" : "Background memory"}</span>
        <a className="underline" href="/settings">{zh ? "管理记忆" : "Manage memory"}</a>
      </div>
      {activity?.has_pending_jobs ? <p className="mt-1">{zh ? "正在后台整理记忆，完成后会在这里通知。" : "Organizing memories in the background. Updates will appear here."}</p> : null}
      {activity?.items.slice(0, 8).map((item) => (
        <div key={item.event_id} data-testid={`memory-notice-${item.memory_id}`} className="mt-2 border-t border-[var(--hairline)] pt-2">
          <div className="flex items-center justify-between gap-3">
            <span>{labels[item.status] ?? item.status}：{item.title}</span>
            {item.status === "active" ? <button type="button" disabled={revokingId !== null}
              className="shrink-0 underline disabled:opacity-50" onClick={() => void revoke(item)}>
              {revokingId === item.memory_id ? (zh ? "正在撤销…" : "Revoking…") : (zh ? "撤销记忆" : "Revoke memory")}
            </button> : null}
          </div>
          {item.content ? <p className="mt-1 whitespace-pre-wrap break-words">{item.content}</p> : null}
        </div>
      ))}
      {error ? <p role="status" className="mt-1">{zh ? "记忆通知更新或操作失败，正在重试；请勿据此认为记忆已保存或撤销。" : "Memory updates or action failed. Retrying; saved or revoked status is not confirmed."}</p> : null}
    </section>
  );
}

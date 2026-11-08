import type { CitationAudit, ContextGovernanceInfo, ExternalSource } from "@/lib/types";

export type ChatRunSnapshot = {
  id: string;
  conversation_id: string;
  assistant_message_id: string;
  status: string;
  content: string;
  reasoning_content: string;
  external_sources: ExternalSource[];
  context_info: ContextGovernanceInfo;
  citation_audit: CitationAudit | null;
  last_event_id: number;
};

export function runMessageStatus(status: string) {
  if (status === "succeeded") return "done";
  if (status === "cancelled") return "cancelled";
  if (["failed", "expired", "interrupted"].includes(status)) return "failed";
  return "streaming";
}

export async function subscribeChatRun(
  runId: string,
  signal: AbortSignal,
  onEvent: (event: Record<string, unknown>) => void,
) {
  let cursor = 0;
  // 只重试订阅，绝不重发创建或模型请求；慢客户端不阻塞Worker。
  for (let attempt = 0; ; attempt += 1) {
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    try {
      const response = await fetch(`/api/backend/chat/runs/${encodeURIComponent(runId)}/events?after=${cursor}`, {
        signal, cache: "no-store", headers: { accept: "text/event-stream" },
      });
      if (!response.ok || !response.body) throw new Error("订阅暂时不可用");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let terminal = false;
      try {
        while (true) {
          const { done, value } = await reader.read();
          buffer += decoder.decode(value, { stream: !done });
          const frames = buffer.split(/\r?\n\r?\n/);
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            const data = frame.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
            if (!data) continue;
            const event = JSON.parse(data) as Record<string, unknown>;
            const sequence = Number(event.event_id);
            if (sequence <= cursor && event.type !== "run_snapshot") continue;
            cursor = sequence;
            onEvent(event);
            const state = event.type === "run_snapshot" ? (event.run as ChatRunSnapshot).status : String(event.status ?? "");
            if (event.type === "done" || event.type === "model_error" || runMessageStatus(state) !== "streaming") terminal = true;
          }
          if (done) break;
        }
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
      if (terminal) return;
      throw new Error("订阅连接中断");
    } catch (error) {
      if (signal.aborted || attempt >= 4) throw error;
      await new Promise<void>((resolve, reject) => {
        const finish = () => { signal.removeEventListener("abort", abort); resolve(); };
        const timer = window.setTimeout(finish, Math.min(4000, 500 * 2 ** attempt));
        const abort = () => { window.clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); };
        signal.addEventListener("abort", abort, { once: true });
      });
    }
  }
}

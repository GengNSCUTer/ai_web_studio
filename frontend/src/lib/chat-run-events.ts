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
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  return new Promise<void>((resolve, reject) => {
    // 同源Cookie由Next代理转换鉴权；SSE分帧、断线重连和Last-Event-ID由浏览器负责。
    const source = new EventSource(`/api/backend/chat/runs/${encodeURIComponent(runId)}/events`);
    let lastHandled = 0, consecutiveErrors = 0, settled = false;

    function finish(error?: unknown) {
      if (settled) return;
      settled = true;
      source.close();
      source.removeEventListener("chat", handleMessage);
      source.removeEventListener("error", handleError);
      signal.removeEventListener("abort", abort);
      if (error) reject(error);
      else resolve();
    }

    function abort() { finish(new DOMException("Aborted", "AbortError")); }

    function handleMessage(message: Event) {
      try {
        const frame = message as MessageEvent<string>;
        const event = JSON.parse(frame.data) as Record<string, unknown>;
        const sequence = event?.event_id;
        if (!Number.isSafeInteger(sequence) || Number(sequence) < 0 || frame.lastEventId !== `${runId}:${sequence}`) {
          throw new Error("订阅事件游标无效");
        }
        // 此序号只做业务去重；续传游标由EventSource保存，不用它手动重建连接。
        if (Number(sequence) < lastHandled || (sequence === lastHandled && event.type !== "run_snapshot")) return;
        onEvent(event);
        lastHandled = Number(sequence);
        consecutiveErrors = 0;
        const state = event.type === "run_snapshot" ? (event.run as ChatRunSnapshot).status : String(event.status ?? "");
        if (event.type === "done" || event.type === "model_error" || runMessageStatus(state) !== "streaming") finish();
      } catch (error) {
        // 原生游标可能已前进，坏事件不能忽略后继续，须显式失败后由页面恢复。
        finish(error);
      }
    }

    function handleError() {
      consecutiveErrors += 1;
      // 不手写重试/退避；仅防止没有业务进展时无限占用界面。
      if (source.readyState === EventSource.CLOSED || consecutiveErrors >= 5) {
        finish(new Error("聊天订阅已中断，请检查登录或网络后重新打开会话。"));
      }
    }

    source.addEventListener("chat", handleMessage);
    source.addEventListener("error", handleError);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

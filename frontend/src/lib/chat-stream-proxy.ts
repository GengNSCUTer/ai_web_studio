import { fetchBackend } from "@/lib/backend";

const SAFE_STREAM_HEADERS = [
  "x-conversation-id",
  "x-assistant-message-id",
  "x-context-notices",
  "x-context-stats",
  "x-context-details",
] as const;

function safeHeaderValue(value: string | null, maxLength = 4096) {
  if (!value) {
    return "";
  }
  return value.length <= maxLength ? value : "";
}

function ndjsonError(type: string, error: string) {
  return `${JSON.stringify({ type, error })}\n`;
}

export async function proxyBackendChatStream(
  path: string,
  token: string,
  body: unknown,
  signal?: AbortSignal
) {
  let upstream: Response;
  try {
    upstream = await fetchBackend(path, {
      method: "POST",
      headers: {
        authorization: `Bearer ${token}`,
        "content-type": "application/json",
        accept: "application/x-ndjson",
      },
      body: JSON.stringify(body),
      signal,
    });
  } catch {
    // fetch 异常可能包含内部后端地址；客户端只需要稳定、可操作的公开错误文案。
    return new Response(ndjsonError("stream_error", "后端流式服务暂时不可用，请稍后重试。"), {
      status: 200,
      headers: streamHeaders(),
    });
  }

  if (!upstream.ok) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
      },
    });
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: streamHeaders(upstream.headers),
  });
}

function streamHeaders(upstreamHeaders?: Headers) {
  const headers: Record<string, string> = {
    "content-type": "application/x-ndjson; charset=utf-8",
    "cache-control": "no-cache, no-transform",
    "x-accel-buffering": "no",
  };
  for (const key of SAFE_STREAM_HEADERS) {
    headers[key] = safeHeaderValue(upstreamHeaders?.get(key) ?? null);
  }
  return headers;
}

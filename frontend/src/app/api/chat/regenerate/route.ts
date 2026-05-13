import { NextRequest } from "next/server";

import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { fetchBackend } from "@/lib/backend";

export const runtime = "nodejs";

type RegenerateRequestPayload = {
  conversationId?: string;
  assistantMessageId?: string;
  modelName?: string;
  systemPrompt?: string | null;
};

export async function POST(request: NextRequest) {
  const token = request.cookies.get(AUTH_COOKIE_NAME)?.value;
  if (!token) {
    return new Response(JSON.stringify({ detail: "Not authenticated" }), {
      status: 401,
      headers: {
        "content-type": "application/json",
      },
    });
  }

  const payload = (await request.json()) as RegenerateRequestPayload;
  if (!payload.conversationId || !payload.assistantMessageId) {
    return new Response(JSON.stringify({ detail: "conversationId and assistantMessageId are required" }), {
      status: 400,
      headers: {
        "content-type": "application/json",
      },
    });
  }

  const upstream = await fetchBackend("/api/chat/regenerate-last-stream", {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
      accept: "text/plain",
    },
    body: JSON.stringify({
      conversation_id: payload.conversationId,
      assistant_message_id: payload.assistantMessageId,
      model_name: payload.modelName,
      system_prompt: payload.systemPrompt,
    }),
  });

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
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      "x-accel-buffering": "no",
      "x-conversation-id": upstream.headers.get("x-conversation-id") ?? "",
      "x-assistant-message-id": upstream.headers.get("x-assistant-message-id") ?? "",
      "x-context-notices": upstream.headers.get("x-context-notices") ?? "",
      "x-context-stats": upstream.headers.get("x-context-stats") ?? "",
      "x-context-details": upstream.headers.get("x-context-details") ?? "",
    },
  });
}

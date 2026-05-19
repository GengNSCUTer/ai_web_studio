import { NextRequest } from "next/server";

import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { fetchBackend } from "@/lib/backend";

export const runtime = "nodejs";

type EditLastUserRequestPayload = {
  conversationId?: string;
  userMessageId?: string;
  assistantMessageId?: string;
  content?: string;
  attachments?: Array<{
    id: string;
    file_name: string;
    mime_type?: string | null;
    file_size?: number | null;
    kind: string;
    storage_key: string;
    parsed_text?: string | null;
  }>;
  modelName?: string;
  systemPrompt?: string | null;
  thinkingEnabled?: boolean;
  webSearchEnabled?: boolean;
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

  const payload = (await request.json()) as EditLastUserRequestPayload;
  if (!payload.conversationId || !payload.userMessageId || !payload.assistantMessageId) {
    return new Response(
      JSON.stringify({ detail: "conversationId, userMessageId and assistantMessageId are required" }),
      {
        status: 400,
        headers: {
          "content-type": "application/json",
        },
      }
    );
  }

  if (!payload.content?.trim()) {
    return new Response(JSON.stringify({ detail: "content is required" }), {
      status: 400,
      headers: {
        "content-type": "application/json",
      },
    });
  }

  const upstream = await fetchBackend("/api/chat/edit-last-user-stream", {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
      accept: "application/x-ndjson",
    },
    body: JSON.stringify({
      conversation_id: payload.conversationId,
      user_message_id: payload.userMessageId,
      assistant_message_id: payload.assistantMessageId,
      content: payload.content,
      attachments: payload.attachments ?? [],
      model_name: payload.modelName,
      system_prompt: payload.systemPrompt,
      thinking_enabled: Boolean(payload.thinkingEnabled),
      web_search_enabled: Boolean(payload.webSearchEnabled),
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
      "content-type": "application/x-ndjson; charset=utf-8",
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

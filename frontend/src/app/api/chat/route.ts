import { NextRequest } from "next/server";

import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { fetchBackend } from "@/lib/backend";

export const runtime = "nodejs";

type UIMessagePart = {
  type: string;
  text?: string;
};

type UIMessage = {
  role: string;
  parts: UIMessagePart[];
};

type ChatRequestPayload = {
  messages?: UIMessage[];
  conversationId?: string;
  modelName?: string;
  systemPrompt?: string | null;
  title?: string;
  attachments?: Array<{
    id: string;
    file_name: string;
    mime_type?: string | null;
    file_size?: number | null;
    kind: string;
    storage_key: string;
  }>;
};

function extractMessageText(message: UIMessage | undefined) {
  if (!message) {
    return "";
  }

  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("")
    .trim();
}

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

  const payload = (await request.json()) as ChatRequestPayload;
  const messages = payload.messages ?? [];
  const lastUserMessage = [...messages].reverse().find((message) => message.role === "user");
  const content = extractMessageText(lastUserMessage);

  if (!content) {
    return new Response(JSON.stringify({ detail: "Message content is required" }), {
      status: 400,
      headers: {
        "content-type": "application/json",
      },
    });
  }

  const upstream = await fetchBackend("/api/chat/text-stream", {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
      accept: "text/plain",
    },
    body: JSON.stringify({
      conversation_id: payload.conversationId,
      content,
      model_name: payload.modelName,
      system_prompt: payload.systemPrompt,
      title: payload.title,
      attachments: payload.attachments ?? [],
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
    },
  });
}

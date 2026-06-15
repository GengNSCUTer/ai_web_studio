import { NextRequest } from "next/server";

import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { proxyBackendChatStream } from "@/lib/chat-stream-proxy";

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
  thinkingEnabled?: boolean;
  webSearchEnabled?: boolean;
  knowledgeBaseId?: string | null;
  knowledgeBaseIds?: string[];
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

  return proxyBackendChatStream("/api/chat/events-stream", token, {
    conversation_id: payload.conversationId,
    content,
    model_name: payload.modelName,
    system_prompt: payload.systemPrompt,
    title: payload.title,
    attachments: payload.attachments ?? [],
    thinking_enabled: Boolean(payload.thinkingEnabled),
    web_search_enabled: Boolean(payload.webSearchEnabled),
    knowledge_base_id: payload.knowledgeBaseId || null,
    knowledge_base_ids: payload.knowledgeBaseIds ?? [],
  });
}

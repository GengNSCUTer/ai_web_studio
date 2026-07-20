import { NextRequest } from "next/server";

import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { proxyBackendChatStream } from "@/lib/chat-stream-proxy";

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
  knowledgeBaseId?: string | null;
  knowledgeBaseIds?: string[];
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

  return proxyBackendChatStream("/api/chat/edit-last-user-stream", token, {
    conversation_id: payload.conversationId,
    user_message_id: payload.userMessageId,
    assistant_message_id: payload.assistantMessageId,
    content: payload.content,
    attachments: payload.attachments ?? [],
    model_name: payload.modelName,
    system_prompt: payload.systemPrompt,
    thinking_enabled: Boolean(payload.thinkingEnabled),
    web_search_enabled: Boolean(payload.webSearchEnabled),
    knowledge_base_id: payload.knowledgeBaseId || null,
    knowledge_base_ids: payload.knowledgeBaseIds ?? [],
  }, request.signal);
}

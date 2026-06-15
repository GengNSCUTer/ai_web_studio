import { NextRequest } from "next/server";

import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { proxyBackendChatStream } from "@/lib/chat-stream-proxy";

export const runtime = "nodejs";

type RegenerateRequestPayload = {
  conversationId?: string;
  assistantMessageId?: string;
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

  const payload = (await request.json()) as RegenerateRequestPayload;
  if (!payload.conversationId || !payload.assistantMessageId) {
    return new Response(JSON.stringify({ detail: "conversationId and assistantMessageId are required" }), {
      status: 400,
      headers: {
        "content-type": "application/json",
      },
    });
  }

  return proxyBackendChatStream("/api/chat/regenerate-last-stream", token, {
    conversation_id: payload.conversationId,
    assistant_message_id: payload.assistantMessageId,
    model_name: payload.modelName,
    system_prompt: payload.systemPrompt,
    thinking_enabled: Boolean(payload.thinkingEnabled),
    web_search_enabled: Boolean(payload.webSearchEnabled),
    knowledge_base_id: payload.knowledgeBaseId || null,
    knowledge_base_ids: payload.knowledgeBaseIds ?? [],
  });
}

import { cookies } from "next/headers";

import { AuthScreen } from "@/components/auth-screen";
import { ChatApp } from "@/components/chat-app";
import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { fetchBackendJson, fetchBackendJsonOrNull } from "@/lib/server-backend";
import type {
  Conversation,
  KnowledgeBase,
  Message,
  ProviderInfo,
  Project,
  User,
  UserSettings,
} from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ChatPage({
  searchParams,
}: {
  searchParams: Promise<{ conversation?: string | string[] }>;
}) {
  const cookieStore = await cookies();
  const token = cookieStore.get(AUTH_COOKIE_NAME)?.value;

  if (!token) {
    return <AuthScreen />;
  }

  let currentUser: User | null = null;
  let initialProviderInfo: ProviderInfo | null = null;
  let initialSettings: UserSettings | null = null;
  let initialConversations: Conversation[] = [];
  let initialMessages: Message[] = [];
  let initialProjects: Project[] = [];
  let initialKnowledgeBases: KnowledgeBase[] = [];

  try {
    currentUser = await fetchBackendJson<User>("/api/auth/me", token);
  } catch {
    return <AuthScreen initialError="登录状态已失效，请重新登录。" />;
  }

  const [providerInfoResult, settingsResult, conversationsResult, projectsResult, knowledgeBasesResult] = await Promise.all([
    fetchBackendJsonOrNull<ProviderInfo>("/api/models", token),
    fetchBackendJsonOrNull<UserSettings>("/api/settings", token),
    fetchBackendJsonOrNull<Conversation[]>("/api/conversations", token),
    fetchBackendJsonOrNull<Project[]>("/api/projects", token),
    fetchBackendJsonOrNull<KnowledgeBase[]>("/api/knowledge-bases", token),
  ]);

  initialProviderInfo = providerInfoResult;
  initialSettings = settingsResult;
  initialConversations = conversationsResult ?? [];
  initialProjects = projectsResult ?? [];
  initialKnowledgeBases = knowledgeBasesResult ?? [];
  const requestedConversation = (await searchParams).conversation;
  const requestedId = typeof requestedConversation === "string" ? requestedConversation : null;
  const initialConversationId = initialConversations.some((item) => item.id === requestedId)
    ? requestedId
    : initialConversations[0]?.id ?? null;
  if (initialConversationId) {
    initialMessages =
      (await fetchBackendJsonOrNull<Message[]>(
        `/api/conversations/${initialConversationId}/messages`,
        token
      )) ?? [];
  }

  return (
    <ChatApp
      initialUser={currentUser}
      initialConversations={initialConversations}
      initialConversationId={initialConversationId}
      initialMessages={initialMessages}
      initialProviderInfo={initialProviderInfo}
      initialSettings={initialSettings}
      initialProjects={initialProjects}
      initialKnowledgeBases={initialKnowledgeBases}
    />
  );
}

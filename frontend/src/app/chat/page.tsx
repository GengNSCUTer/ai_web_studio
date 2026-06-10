import { cookies } from "next/headers";

import { AuthScreen } from "@/components/auth-screen";
import { ChatApp } from "@/components/chat-app";
import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { fetchBackendJson, fetchBackendJsonOrNull } from "@/lib/server-backend";
import type {
  Conversation,
  Message,
  ProviderInfo,
  Project,
  User,
  UserSettings,
} from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ChatPage() {
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

  try {
    currentUser = await fetchBackendJson<User>("/api/auth/me", token);
  } catch {
    return <AuthScreen initialError="登录状态已失效，请重新登录。" />;
  }

  const [providerInfoResult, settingsResult, conversationsResult, projectsResult] = await Promise.all([
    fetchBackendJsonOrNull<ProviderInfo>("/api/models", token),
    fetchBackendJsonOrNull<UserSettings>("/api/settings", token),
    fetchBackendJsonOrNull<Conversation[]>("/api/conversations", token),
    fetchBackendJsonOrNull<Project[]>("/api/projects", token),
  ]);

  initialProviderInfo = providerInfoResult;
  initialSettings = settingsResult;
  initialConversations = conversationsResult ?? [];
  initialProjects = projectsResult ?? [];
  if (initialConversations[0]?.id) {
    initialMessages =
      (await fetchBackendJsonOrNull<Message[]>(
        `/api/conversations/${initialConversations[0].id}/messages`,
        token
      )) ?? [];
  }

  return (
    <ChatApp
      initialUser={currentUser}
      initialConversations={initialConversations}
      initialMessages={initialMessages}
      initialProviderInfo={initialProviderInfo}
      initialSettings={initialSettings}
      initialProjects={initialProjects}
    />
  );
}

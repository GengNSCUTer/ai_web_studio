import { cookies } from "next/headers";

import { AuthScreen } from "@/components/auth-screen";
import { ChatApp } from "@/components/chat-app";
import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { fetchBackend } from "@/lib/backend";
import type {
  Conversation,
  Message,
  ProviderInfo,
  User,
  UserSettings,
} from "@/lib/types";

export const dynamic = "force-dynamic";

async function fetchBackendJson<T>(path: string, token?: string): Promise<T> {
  const response = await fetchBackend(path, {
    headers: token
      ? {
          authorization: `Bearer ${token}`,
        }
      : undefined,
  });

  if (!response.ok) {
    throw new Error(`Backend request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

async function fetchBackendJsonOrNull<T>(path: string, token?: string): Promise<T | null> {
  try {
    return await fetchBackendJson<T>(path, token);
  } catch {
    return null;
  }
}

export default async function Home() {
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

  try {
    currentUser = await fetchBackendJson<User>("/api/auth/me", token);
  } catch {
    return <AuthScreen initialError="登录状态已失效，请重新登录。" />;
  }

  const [providerInfoResult, settingsResult, conversationsResult] =
    await Promise.all([
      fetchBackendJsonOrNull<ProviderInfo>("/api/models", token),
      fetchBackendJsonOrNull<UserSettings>("/api/settings", token),
      fetchBackendJsonOrNull<Conversation[]>("/api/conversations", token),
    ]);

  initialProviderInfo = providerInfoResult;
  initialSettings = settingsResult;
  initialConversations = conversationsResult ?? [];
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
    />
  );
}

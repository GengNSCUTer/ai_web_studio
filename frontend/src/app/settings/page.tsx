import { cookies } from "next/headers";

import { AuthScreen } from "@/components/auth-screen";
import { SettingsCenter } from "@/components/settings-center";
import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { fetchBackendJson, fetchBackendJsonOrNull } from "@/lib/server-backend";
import type {
  Project,
  KnowledgeCredential,
  PromptTemplate,
  ProviderInfo,
  ToolSettings,
  User,
  UserMemory,
  UserSettings,
} from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  const cookieStore = await cookies();
  const token = cookieStore.get(AUTH_COOKIE_NAME)?.value;

  if (!token) {
    return <AuthScreen initialError="登录状态已失效，请重新登录。" />;
  }

  let currentUser: User | null = null;
  try {
    currentUser = await fetchBackendJson<User>("/api/auth/me", token);
  } catch {
    return <AuthScreen initialError="登录状态已失效，请重新登录。" />;
  }

  const [settings, providerInfo, projects, toolSettings, memories, promptTemplates, mineruCredential] = await Promise.all([
    fetchBackendJsonOrNull<UserSettings>("/api/settings", token),
    fetchBackendJsonOrNull<ProviderInfo>("/api/models", token),
    fetchBackendJsonOrNull<Project[]>("/api/projects", token),
    fetchBackendJsonOrNull<ToolSettings>("/api/tools/settings", token),
    fetchBackendJsonOrNull<UserMemory[]>("/api/memories", token),
    fetchBackendJsonOrNull<PromptTemplate[]>("/api/prompt-templates", token),
    fetchBackendJsonOrNull<KnowledgeCredential>("/api/knowledge/credentials/mineru", token),
  ]);

  if (!settings) {
    return <AuthScreen initialError="设置加载失败，请重新登录后重试。" />;
  }

  return (
    <SettingsCenter
      currentUser={currentUser}
      initialSettings={settings}
      initialProviderInfo={providerInfo}
      initialProjects={projects ?? []}
      initialToolSettings={toolSettings}
      initialMemories={memories ?? []}
      initialPromptTemplates={promptTemplates ?? []}
      initialMineruCredential={mineruCredential}
    />
  );
}

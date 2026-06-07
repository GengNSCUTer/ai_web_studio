import { cookies } from "next/headers";

import { AuthScreen } from "@/components/auth-screen";
import { KnowledgeWorkspace } from "@/components/knowledge/knowledge-workspace";
import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { fetchBackendJson, fetchBackendJsonOrNull } from "@/lib/server-backend";
import type { KnowledgeBase, Project, User } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function KnowledgePage() {
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

  const [knowledgeBases, projects] = await Promise.all([
    fetchBackendJsonOrNull<KnowledgeBase[]>("/api/knowledge-bases", token),
    fetchBackendJsonOrNull<Project[]>("/api/projects", token),
  ]);

  return (
    <KnowledgeWorkspace
      currentUser={currentUser}
      initialKnowledgeBases={knowledgeBases ?? []}
      initialProjects={projects ?? []}
    />
  );
}

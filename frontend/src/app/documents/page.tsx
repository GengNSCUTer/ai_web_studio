import { cookies } from "next/headers";
import { AuthScreen } from "@/components/auth-screen";
import { DocumentTasks, type DocumentTask } from "@/components/document-tasks";
import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { fetchBackendJson, fetchBackendJsonOrNull } from "@/lib/server-backend";
import type { Project, User } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function DocumentsPage({ searchParams }: { searchParams: Promise<{ run?: string }> }) {
  const token = (await cookies()).get(AUTH_COOKIE_NAME)?.value;
  if (!token) return <AuthScreen />;
  try { await fetchBackendJson<User>("/api/auth/me", token); }
  catch { return <AuthScreen initialError="登录已失效，请重新登录。" />; }
  const [tasks, projects, params] = await Promise.all([
    fetchBackendJsonOrNull<DocumentTask[]>("/api/document-tasks", token),
    fetchBackendJsonOrNull<Project[]>("/api/projects", token), searchParams]);
  const initialTasks = tasks ?? [];
  const runId = typeof params.run === "string" && /^[0-9a-f-]{36}$/i.test(params.run) ? params.run : null;
  // 会话里的旧产物链接仍应打开该任务，不能受最近 50 个任务列表限制。
  if (runId && !initialTasks.some((task) => task.id === runId)) {
    const task = await fetchBackendJsonOrNull<DocumentTask>(`/api/document-tasks/${encodeURIComponent(runId)}`, token);
    if (task) initialTasks.push(task);
  }
  return <DocumentTasks initialTasks={initialTasks} projects={projects ?? []} initialRunId={runId} />;
}

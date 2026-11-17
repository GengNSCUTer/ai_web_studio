import { cookies } from "next/headers";
import { AuthScreen } from "@/components/auth-screen";
import { PluginCenter, type PluginCenterData, type ScriptJob } from "@/components/plugin-center";
import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { fetchBackendJson, fetchBackendJsonOrNull } from "@/lib/server-backend";
import type { Project, User } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function PluginsPage({ searchParams }: { searchParams: Promise<{ job?: string }> }) {
  const token = (await cookies()).get(AUTH_COOKIE_NAME)?.value;
  if (!token) return <AuthScreen />;
  try {
    await fetchBackendJson<User>("/api/auth/me", token);
  } catch {
    return <AuthScreen initialError="登录已失效，请重新登录。" />;
  }
  const [data, projects] = await Promise.all([
    fetchBackendJsonOrNull<PluginCenterData>("/api/plugins", token),
    fetchBackendJsonOrNull<Project[]>("/api/projects", token),
  ]);
  const { job } = await searchParams;
  const initialJob = job ? await fetchBackendJsonOrNull<ScriptJob>(`/api/plugins/sandbox/jobs/${encodeURIComponent(job)}`, token) : null;
  return <PluginCenter initialData={data} projects={projects ?? []} initialJob={initialJob} />;
}

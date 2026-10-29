import { cookies } from "next/headers";
import { AuthScreen } from "@/components/auth-screen";
import { PluginCenter, type PluginCenterData } from "@/components/plugin-center";
import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { fetchBackendJson, fetchBackendJsonOrNull } from "@/lib/server-backend";
import type { Project, User } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function PluginsPage() {
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
  return <PluginCenter initialData={data} projects={projects ?? []} />;
}

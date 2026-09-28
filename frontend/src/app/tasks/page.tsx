import { cookies } from "next/headers";

import { AuthScreen } from "@/components/auth-screen";
import { TaskDashboard, type DurableRun, type RuntimeMetrics } from "@/components/task-dashboard";
import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { fetchBackendJson, fetchBackendJsonOrNull } from "@/lib/server-backend";
import type { User } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function TasksPage({
  searchParams,
}: {
  searchParams: Promise<{ run?: string | string[] }>;
}) {
  const token = (await cookies()).get(AUTH_COOKIE_NAME)?.value;
  if (!token) return <AuthScreen />;
  try {
    await fetchBackendJson<User>("/api/auth/me", token);
  } catch {
    return <AuthScreen initialError="登录状态已失效，请重新登录。" />;
  }
  const [runs, metrics] = await Promise.all([
    fetchBackendJsonOrNull<DurableRun[]>("/api/agent-runtime/tool-runs", token),
    fetchBackendJsonOrNull<RuntimeMetrics>("/api/agent-runtime/metrics", token),
  ]);
  const requestedRun = (await searchParams).run;
  const runId = typeof requestedRun === "string" && /^[0-9a-f-]{36}$/i.test(requestedRun)
    ? requestedRun
    : null;
  return <TaskDashboard initialRuns={runs ?? []} initialMetrics={metrics} initialRunId={runId} />;
}

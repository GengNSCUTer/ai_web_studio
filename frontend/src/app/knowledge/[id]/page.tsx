import Link from "next/link";
import { cookies } from "next/headers";

import { AuthScreen } from "@/components/auth-screen";
import { KnowledgeWorkspace } from "@/components/knowledge/knowledge-workspace";
import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { fetchBackendJson, fetchBackendJsonOrNull } from "@/lib/server-backend";
import type {
  KnowledgeBase,
  KnowledgeCredential,
  KnowledgeDocument,
  KnowledgeJob,
  Project,
  User,
  UserSettings,
} from "@/lib/types";

export const dynamic = "force-dynamic";

type KnowledgeDetailPageProps = {
  params: Promise<{ id: string }>;
};

export default async function KnowledgeDetailPage({ params }: KnowledgeDetailPageProps) {
  const { id } = await params;
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

  const [knowledgeBases, projects, activeKnowledgeBase, documents, jobs, mineruCredential, userSettings] = await Promise.all([
    fetchBackendJsonOrNull<KnowledgeBase[]>("/api/knowledge-bases", token),
    fetchBackendJsonOrNull<Project[]>("/api/projects", token),
    fetchBackendJsonOrNull<KnowledgeBase>(`/api/knowledge-bases/${encodeURIComponent(id)}`, token),
    fetchBackendJsonOrNull<KnowledgeDocument[]>(
      `/api/knowledge-bases/${encodeURIComponent(id)}/documents`,
      token
    ),
    fetchBackendJsonOrNull<KnowledgeJob[]>(`/api/knowledge-bases/${encodeURIComponent(id)}/jobs`, token),
    fetchBackendJsonOrNull<KnowledgeCredential>("/api/knowledge/credentials/mineru", token),
    fetchBackendJsonOrNull<UserSettings>("/api/settings", token),
  ]);

  if (!activeKnowledgeBase) {
    return (
      <main className="min-h-screen bg-[var(--app-bg)] px-4 py-8 text-[var(--ink-strong)]">
        <section className="mx-auto max-w-2xl rounded-[28px] border border-[var(--panel-border)] bg-[var(--panel-bg)] p-8 text-center shadow-[var(--panel-shadow)]">
          <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-muted)]">Knowledge Base</p>
          <h1 className="mt-3 text-3xl font-semibold">知识库不存在</h1>
          <p className="mt-3 text-sm text-[var(--ink-soft)]">该知识库可能已删除，或你没有访问权限。</p>
          <Link
            href="/knowledge"
            className="primary-action mt-6 inline-flex rounded-full px-5 py-2 text-sm font-medium"
          >
            返回知识库列表
          </Link>
        </section>
      </main>
    );
  }

  return (
    <KnowledgeWorkspace
      currentUser={currentUser}
      initialKnowledgeBases={knowledgeBases ?? []}
      initialProjects={projects ?? []}
      activeKnowledgeBase={activeKnowledgeBase}
      initialDocuments={documents ?? []}
      initialJobs={jobs ?? []}
      initialMineruCredential={mineruCredential}
      initialSettings={userSettings}
    />
  );
}

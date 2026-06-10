import Link from "next/link";
import { cookies } from "next/headers";
import type { ReactNode } from "react";

import { AuthScreen } from "@/components/auth-screen";
import { AUTH_COOKIE_NAME } from "@/lib/auth";
import { fetchBackendJson, fetchBackendJsonOrNull } from "@/lib/server-backend";
import type { Conversation, KnowledgeBase, Project, ProviderInfo, User } from "@/lib/types";

export const dynamic = "force-dynamic";

function formatDateTime(value: string | null) {
  if (!value) {
    return "--";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export default async function Home() {
  const cookieStore = await cookies();
  const token = cookieStore.get(AUTH_COOKIE_NAME)?.value;

  if (!token) {
    return <AuthScreen />;
  }

  let currentUser: User | null = null;
  try {
    currentUser = await fetchBackendJson<User>("/api/auth/me", token);
  } catch {
    return <AuthScreen initialError="登录状态已失效，请重新登录。" />;
  }

  const [providerInfo, conversations, projects, knowledgeBases] = await Promise.all([
    fetchBackendJsonOrNull<ProviderInfo>("/api/models", token),
    fetchBackendJsonOrNull<Conversation[]>("/api/conversations", token),
    fetchBackendJsonOrNull<Project[]>("/api/projects", token),
    fetchBackendJsonOrNull<KnowledgeBase[]>("/api/knowledge-bases", token),
  ]);

  const latestConversation = conversations?.[0] ?? null;
  const latestKnowledgeBase = knowledgeBases?.[0] ?? null;
  const documentCount = (knowledgeBases ?? []).reduce((total, item) => total + item.document_count, 0);

  return (
    <main className="min-h-screen bg-[var(--app-bg)] px-4 py-5 text-[var(--ink-strong)] sm:px-6">
      <div className="mx-auto flex min-h-[calc(100vh-2.5rem)] max-w-7xl flex-col gap-4">
        <header className="overflow-hidden rounded-[34px] border border-[var(--panel-border)] bg-[var(--panel-bg)] shadow-[var(--panel-shadow)]">
          <div className="grid gap-6 p-6 lg:grid-cols-[1.12fr_0.88fr] lg:p-8">
            <section className="rounded-[28px] border border-[var(--control-border)] bg-[linear-gradient(135deg,rgba(21,44,35,0.96),rgba(55,72,48,0.88),rgba(168,125,43,0.72))] p-7 text-white shadow-[0_24px_80px_rgba(18,29,23,0.22)]">
              <p className="text-xs uppercase tracking-[0.42em] text-white/52">AI Web Studio</p>
              <h1 className="mt-5 max-w-2xl text-4xl font-semibold leading-tight sm:text-5xl">
                选择你的 AI 工作入口
              </h1>
              <p className="mt-5 max-w-3xl text-base leading-8 text-white/70">
                当前系统已经从单一聊天页升级为个人 AI 工作空间：智能问答负责对话，知识库负责沉淀资料，
                设置中心负责模型、工具、上下文和凭据配置。
              </p>
              <div className="mt-8 grid gap-3 sm:grid-cols-3">
                <HeroMetric label="会话" value={`${conversations?.length ?? 0}`} />
                <HeroMetric label="知识库" value={`${knowledgeBases?.length ?? 0}`} />
                <HeroMetric label="工作区" value={`${projects?.length ?? 0}`} />
              </div>
            </section>

            <section className="rounded-[28px] border border-[var(--control-border)] bg-[var(--soft-bg)] p-6">
              <p className="text-xs uppercase tracking-[0.28em] text-[var(--ink-muted)]">Account</p>
              <h2 className="mt-3 text-2xl font-semibold">{currentUser?.username || "未命名用户"}</h2>
              <p className="mt-2 break-all text-sm text-[var(--ink-soft)]">{currentUser?.email ?? "--"}</p>
              <div className="mt-5 space-y-3 text-sm text-[var(--ink-soft)]">
                <InfoLine label="当前 Provider" value={providerInfo?.provider ?? "读取中"} />
                <InfoLine label="默认模型" value={providerInfo?.default_model ?? "未配置"} />
                <InfoLine label="最近会话" value={latestConversation?.title ?? "暂无会话"} />
                <InfoLine label="最近知识库" value={latestKnowledgeBase?.name ?? "暂无知识库"} />
              </div>
            </section>
          </div>
        </header>

        <section className="grid gap-4 lg:grid-cols-3">
          <WorkspaceCard
            eyebrow="Chat"
            title="智能问答台"
            description="进入多轮对话、图片/文档输入、联网搜索、深度思考、上下文治理和工具调用。"
            href="/chat"
            action="进入问答"
            meta={latestConversation ? `最近更新：${formatDateTime(latestConversation.updated_at)}` : "还没有历史会话"}
          />
          <WorkspaceCard
            eyebrow="Knowledge"
            title="个人知识库"
            description="创建知识库、配置解析/分块/Embedding/Rerank，上传文档并预览解析后的 Markdown。"
            href="/knowledge"
            action="管理知识库"
            meta={`${knowledgeBases?.length ?? 0} 个知识库 · ${documentCount} 个文档`}
          />
          <WorkspaceCard
            eyebrow="Settings"
            title="模型与工具设置"
            description="管理问答模型、知识库模型、API Key、MCP 工具、上下文策略、长期记忆、Prompt 模板和主题。"
            href="/settings"
            action="打开设置"
            meta={providerInfo?.base_url ?? "模型服务地址读取中"}
          />
        </section>

        <section className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[1.05fr_0.95fr]">
          <Panel title="最近会话" actionHref="/chat" actionLabel="查看全部">
            {latestConversation ? (
              <div className="rounded-3xl border border-[var(--control-border)] bg-[var(--control-bg)] p-4">
                <p className="line-clamp-2 text-base font-semibold">{latestConversation.title}</p>
                <p className="mt-2 text-sm text-[var(--ink-soft)]">{latestConversation.model_name}</p>
                <p className="mt-3 text-xs text-[var(--ink-muted)]">
                  更新时间：{formatDateTime(latestConversation.updated_at)}
                </p>
              </div>
            ) : (
              <EmptyHint text="还没有会话。进入智能问答台发送第一条消息后，这里会展示最近会话。" />
            )}
          </Panel>

          <Panel title="知识库状态" actionHref="/knowledge" actionLabel="进入知识库">
            {(knowledgeBases ?? []).length > 0 ? (
              <div className="space-y-2">
                {(knowledgeBases ?? []).slice(0, 3).map((item) => (
                  <Link
                    key={item.id}
                    href={`/knowledge/${item.id}`}
                    className="block rounded-3xl border border-[var(--control-border)] bg-[var(--control-bg)] p-4 transition hover:border-[var(--accent-strong)]"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold">{item.name}</p>
                        <p className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--ink-soft)]">
                          {item.description || "暂无描述"}
                        </p>
                      </div>
                      <span className="shrink-0 rounded-full bg-[var(--accent-soft)] px-3 py-1 text-xs text-[var(--accent-strong)]">
                        {item.document_count} 文档
                      </span>
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
              <EmptyHint text="还没有知识库。建议先创建一个课程资料或项目资料知识库，再上传文档解析。" />
            )}
          </Panel>
        </section>
      </div>
    </main>
  );
}

function HeroMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-3xl border border-white/12 bg-white/10 p-4 backdrop-blur">
      <p className="text-xs uppercase tracking-[0.22em] text-white/50">{label}</p>
      <p className="mt-2 text-3xl font-semibold">{value}</p>
    </div>
  );
}

function InfoLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3">
      <span className="shrink-0 text-xs text-[var(--ink-muted)]">{label}</span>
      <span className="min-w-0 truncate text-sm font-medium text-[var(--ink-strong)]">{value}</span>
    </div>
  );
}

function WorkspaceCard({
  eyebrow,
  title,
  description,
  href,
  action,
  meta,
}: {
  eyebrow: string;
  title: string;
  description: string;
  href: string;
  action: string;
  meta: string;
}) {
  return (
    <Link
      href={href}
      className="group rounded-[28px] border border-[var(--panel-border)] bg-[var(--panel-bg)] p-5 shadow-[var(--panel-shadow)] transition hover:-translate-y-0.5 hover:border-[var(--accent-strong)]"
    >
      <p className="text-xs uppercase tracking-[0.28em] text-[var(--ink-muted)]">{eyebrow}</p>
      <h2 className="mt-3 text-2xl font-semibold">{title}</h2>
      <p className="mt-3 min-h-20 text-sm leading-7 text-[var(--ink-soft)]">{description}</p>
      <div className="mt-5 flex items-center justify-between gap-3">
        <span className="text-xs text-[var(--ink-muted)]">{meta}</span>
        <span className="primary-action rounded-full px-4 py-2 text-sm font-medium transition group-hover:brightness-105">
          {action}
        </span>
      </div>
    </Link>
  );
}

function Panel({
  title,
  actionHref,
  actionLabel,
  children,
}: {
  title: string;
  actionHref: string;
  actionLabel: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-[28px] border border-[var(--panel-border)] bg-[var(--panel-bg)] p-5 shadow-[var(--panel-shadow)]">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="text-xl font-semibold">{title}</h2>
        <Link
          href={actionHref}
          className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] hover:text-[var(--ink-strong)]"
        >
          {actionLabel}
        </Link>
      </div>
      {children}
    </section>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="rounded-3xl border border-dashed border-[var(--control-border)] bg-[var(--soft-bg)] p-6 text-sm leading-7 text-[var(--ink-soft)]">
      {text}
    </div>
  );
}

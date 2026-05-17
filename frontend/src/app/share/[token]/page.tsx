import { MessageMarkdown } from "@/components/message-markdown";
import { fetchBackend } from "@/lib/backend";
import type { PublicConversationShare } from "@/lib/types";

export const dynamic = "force-dynamic";

type SharePageProps = {
  params: Promise<{ token: string }>;
};

async function fetchShare(token: string): Promise<PublicConversationShare | null> {
  const response = await fetchBackend(`/api/shares/${encodeURIComponent(token)}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    return null;
  }
  return response.json() as Promise<PublicConversationShare>;
}

function formatShareTime(value: string | null) {
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

export default async function SharePage({ params }: SharePageProps) {
  const { token } = await params;
  const share = await fetchShare(token);

  if (!share) {
    return (
      <main className="min-h-screen bg-[var(--background)] px-4 py-10 text-[var(--foreground)]">
        <div className="mx-auto max-w-3xl rounded-[28px] border border-[var(--panel-border)] bg-[var(--panel-bg)] p-8 text-center shadow-[var(--panel-shadow)]">
          <p className="text-sm uppercase tracking-[0.24em] text-[var(--ink-muted)]">AI Web Studio</p>
          <h1 className="mt-3 text-3xl font-semibold">分享链接不可用</h1>
          <p className="mt-3 text-sm text-[var(--ink-soft)]">该链接可能已过期、被撤销，或不存在。</p>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[var(--app-bg)] px-4 py-6 text-[var(--foreground)]">
      <section className="mx-auto max-w-5xl overflow-hidden rounded-[32px] border border-[var(--panel-border)] bg-[var(--panel-bg)] shadow-[var(--panel-shadow)] backdrop-blur">
        <header className="border-b border-[var(--hairline)] bg-[var(--panel-header-bg)] px-6 py-5">
          <p className="text-xs uppercase tracking-[0.24em] text-[var(--ink-muted)]">Shared Conversation</p>
          <h1 className="mt-2 text-3xl font-semibold text-[var(--ink-strong)]">{share.title}</h1>
          <p className="mt-2 text-sm text-[var(--ink-soft)]">
            {share.model_name} · {formatShareTime(share.updated_at || share.created_at)}
          </p>
        </header>
        <div className="space-y-4 px-4 py-5 sm:px-6">
          {share.messages.map((message) => {
            const isUser = message.role === "user";
            const attachments = message.attachments ?? [];
            return (
              <article
                key={message.id}
                className={`max-w-[92%] rounded-[24px] px-4 py-3.5 ${
                  isUser
                    ? "ml-auto bg-[linear-gradient(135deg,_#16221b_0%,_#254636_100%)] text-white"
                    : "border border-[var(--hairline)] bg-[var(--control-bg)] text-[var(--ink-strong)]"
                }`}
              >
                <div className="mb-2 flex items-center justify-between gap-3 text-xs uppercase tracking-[0.18em]">
                  <span className={isUser ? "text-white/65" : "text-[var(--ink-muted)]"}>
                    {isUser ? "用户" : "助手"}
                  </span>
                  <span className={isUser ? "text-white/45" : "text-[var(--ink-muted)]"}>
                    {formatShareTime(message.created_at)}
                  </span>
                </div>
                {isUser ? (
                  <div className="whitespace-pre-wrap text-sm leading-6">{message.content}</div>
                ) : (
                  <MessageMarkdown content={message.content} />
                )}
                {attachments.length > 0 ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {attachments.map((attachment) => (
                      <span
                        key={attachment.id}
                        className={`rounded-full border px-3 py-1 text-xs ${
                          isUser
                            ? "border-white/14 bg-white/10 text-white/78"
                            : "border-[var(--hairline)] bg-[var(--soft-bg)] text-[var(--ink-soft)]"
                        }`}
                      >
                        {attachment.file_name}
                      </span>
                    ))}
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      </section>
    </main>
  );
}

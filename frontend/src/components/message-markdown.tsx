"use client";

import { useEffect, useState } from "react";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type MessageMarkdownProps = {
  content: string;
  isStreaming?: boolean;
};

type CodeBlockProps = {
  className?: string;
  children?: React.ReactNode;
};

function normalizeCode(children: React.ReactNode) {
  const text = Array.isArray(children) ? children.join("") : String(children ?? "");
  return text.replace(/\n$/, "");
}

function CopyCodeButton({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  useEffect(() => {
    if (!copied) {
      return;
    }

    const timer = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(timer);
  }, [copied]);

  return (
    <button
      type="button"
      onClick={() => void handleCopy()}
      className="rounded-full border border-white/10 bg-white/6 px-3 py-1 text-[11px] font-medium text-white/72 transition hover:bg-white/12"
    >
      {copied ? "已复制" : "复制代码"}
    </button>
  );
}

function CodeBlock({ className, children }: CodeBlockProps) {
  const code = normalizeCode(children);
  const language = className?.replace("language-", "") ?? "";

  return (
    <div className="my-4 overflow-hidden rounded-2xl border border-[rgba(24,35,29,0.08)] bg-[#101a16] text-[#f7f3ea] shadow-[0_18px_42px_rgba(16,31,24,0.18)]">
      <div className="flex items-center justify-between border-b border-white/8 px-4 py-2.5">
        <span className="text-[11px] uppercase tracking-[0.18em] text-white/45">
          {language || "code"}
        </span>
        <CopyCodeButton code={code} />
      </div>
      <pre className="overflow-x-auto px-4 py-4 text-[13px] leading-6">
        <code className={className}>{code}</code>
      </pre>
    </div>
  );
}

export function MessageMarkdown({
  content,
  isStreaming = false,
}: MessageMarkdownProps) {
  return (
    <div className="chat-markdown text-sm leading-7 sm:text-[15px]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h1 className="mt-6 mb-3 text-2xl font-semibold leading-tight first:mt-0">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="mt-5 mb-3 text-xl font-semibold leading-tight first:mt-0">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="mt-4 mb-2 text-lg font-semibold leading-tight first:mt-0">
              {children}
            </h3>
          ),
          p: ({ children }) => <p className="my-3 first:mt-0 last:mb-0">{children}</p>,
          ul: ({ children }) => (
            <ul className="my-3 list-disc space-y-1.5 pl-6 marker:text-[var(--accent-strong)]">
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol className="my-3 list-decimal space-y-1.5 pl-6 marker:text-[var(--accent-strong)]">
              {children}
            </ol>
          ),
          li: ({ children }) => <li className="pl-1">{children}</li>,
          blockquote: ({ children }) => (
            <blockquote className="my-4 rounded-r-2xl border-l-4 border-[var(--accent-strong)] bg-[rgba(199,122,37,0.08)] px-4 py-3 text-[var(--ink-soft)]">
              {children}
            </blockquote>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="font-medium text-[#8f5a1d] underline decoration-[rgba(143,90,29,0.35)] underline-offset-4 transition hover:text-[#6f4513]"
            >
              {children}
            </a>
          ),
          table: ({ children }) => (
            <div className="my-4 overflow-x-auto rounded-2xl border border-[rgba(24,35,29,0.08)]">
              <table className="min-w-full border-collapse bg-white/72 text-left text-sm">
                {children}
              </table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-[rgba(24,35,29,0.06)]">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="border-b border-[rgba(24,35,29,0.08)] px-4 py-3 font-semibold">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border-b border-[rgba(24,35,29,0.08)] px-4 py-3 align-top last:border-b-0">
              {children}
            </td>
          ),
          hr: () => <hr className="my-5 border-[rgba(24,35,29,0.1)]" />,
          code: ({ className, children, ...props }) => {
            const isInline = !className;
            if (isInline) {
              return (
                <code
                  {...props}
                  className="rounded-md bg-[rgba(24,35,29,0.08)] px-1.5 py-0.5 font-mono text-[0.92em] text-[#8f5a1d]"
                >
                  {children}
                </code>
              );
            }

            return <CodeBlock className={className}>{children}</CodeBlock>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
      {isStreaming ? (
        <span className="ml-1 inline-block h-5 w-2 animate-pulse rounded-sm bg-[var(--accent-strong)] align-[-0.2em]" />
      ) : null}
    </div>
  );
}

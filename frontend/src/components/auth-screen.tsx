"use client";

import { FormEvent, useState } from "react";

type AuthMode = "login" | "register";

type AuthScreenProps = {
  initialError?: string | null;
};

type AuthFormState = {
  username: string;
  email: string;
  password: string;
};

const INITIAL_FORM: AuthFormState = {
  username: "",
  email: "",
  password: "",
};

function resolveAuthErrorMessage(error: unknown) {
  if (!(error instanceof Error)) {
    return "操作失败，请稍后重试。";
  }

  const rawMessage = error.message.trim();
  let detail = rawMessage;

  try {
    const parsed = JSON.parse(rawMessage) as { detail?: string };
    if (typeof parsed.detail === "string" && parsed.detail.trim()) {
      detail = parsed.detail.trim();
    }
  } catch {
    // keep raw message
  }

  if (detail === "Invalid email or password") {
    return "邮箱或密码错误，请重新输入。";
  }

  if (detail === "Username or email already exists") {
    return "用户名或邮箱已存在，请更换后重试。";
  }

  if (detail === "Not authenticated") {
    return "当前未登录，请重新登录。";
  }

  if (detail.startsWith("Authentication failed:")) {
    return "登录或注册失败，请稍后重试。";
  }

  return detail || "操作失败，请稍后重试。";
}

export function AuthScreen({ initialError = null }: AuthScreenProps) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [form, setForm] = useState<AuthFormState>(INITIAL_FORM);
  const [errorMessage, setErrorMessage] = useState<string | null>(initialError);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setErrorMessage(null);

    try {
      const endpoint =
        mode === "login" ? "/api/session/login" : "/api/session/register";
      const payload =
        mode === "login"
          ? {
              email: form.email,
              password: form.password,
            }
          : {
              username: form.username,
              email: form.email,
              password: form.password,
            };

      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `Authentication failed: ${response.status}`);
      }

      window.location.reload();
    } catch (error) {
      setErrorMessage(resolveAuthErrorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(64,145,108,0.22),_transparent_32%),radial-gradient(circle_at_top_right,_rgba(240,196,25,0.18),_transparent_28%),linear-gradient(180deg,_#f8f4ea_0%,_#f1ecde_100%)] px-4 py-6 text-[var(--ink-strong)] sm:px-6">
      <div className="mx-auto grid min-h-[calc(100vh-3rem)] max-w-6xl items-center gap-6 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="rounded-[36px] border border-white/70 bg-[rgba(16,31,24,0.94)] p-8 text-white shadow-[0_28px_110px_rgba(16,31,24,0.32)] sm:p-10">
          <p className="text-xs uppercase tracking-[0.42em] text-white/45">
            AI Web Studio
          </p>
          <h1 className="mt-5 max-w-xl text-4xl font-semibold leading-tight sm:text-5xl">
            本地模型智能问答工作台
          </h1>
          <p className="mt-5 max-w-2xl text-base leading-8 text-white/68 sm:text-lg">
            这一版已经具备用户登录、会话历史、用户级设置、文件上传入口和流式聊天链路。
            当前模型如果被别的实验占用，页面也能先把工作台搭起来。
          </p>

          <div className="mt-10 grid gap-3 sm:grid-cols-3">
            <div className="rounded-3xl border border-white/10 bg-white/6 p-4">
              <p className="text-sm font-medium">用户隔离</p>
              <p className="mt-2 text-sm text-white/52">会话、设置、上传目录都按登录用户隔离。</p>
            </div>
            <div className="rounded-3xl border border-white/10 bg-white/6 p-4">
              <p className="text-sm font-medium">本地 Ollama</p>
              <p className="mt-2 text-sm text-white/52">前后端已围绕本地模型服务做接入。</p>
            </div>
            <div className="rounded-3xl border border-white/10 bg-white/6 p-4">
              <p className="text-sm font-medium">前后端分离</p>
              <p className="mt-2 text-sm text-white/52">浏览器通过 Next.js BFF 访问 FastAPI。</p>
            </div>
          </div>
        </section>

        <section className="rounded-[32px] border border-white/75 bg-[rgba(255,250,242,0.92)] p-6 shadow-[0_24px_80px_rgba(112,96,56,0.18)] sm:p-8">
          <div className="inline-flex rounded-full border border-[rgba(22,34,27,0.1)] bg-white p-1">
            <button
              type="button"
              onClick={() => setMode("login")}
              className={`rounded-full px-4 py-2 text-sm transition ${
                mode === "login"
                  ? "bg-[var(--ink-strong)] text-[var(--inverse-ink)]"
                  : "text-[var(--ink-muted)]"
              }`}
            >
              登录
            </button>
            <button
              type="button"
              onClick={() => setMode("register")}
              className={`rounded-full px-4 py-2 text-sm transition ${
                mode === "register"
                  ? "bg-[var(--ink-strong)] text-[var(--inverse-ink)]"
                  : "text-[var(--ink-muted)]"
              }`}
            >
              注册
            </button>
          </div>

          <h2 className="mt-6 text-3xl font-semibold">{mode === "login" ? "登录你的工作台" : "创建一个账号"}</h2>
          <p className="mt-2 text-sm leading-7 text-[var(--ink-soft)]">
            {mode === "login"
              ? "登录后可查看自己的历史会话、设置和上传内容。"
              : "注册成功后会自动登录，并写入浏览器会话 cookie。"}
          </p>

          {errorMessage ? (
            <div className="mt-5 rounded-2xl border border-[rgba(185,66,42,0.16)] bg-[rgba(255,238,231,0.95)] px-4 py-3 text-sm text-[#8f3524]">
              {errorMessage}
            </div>
          ) : null}

          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            {mode === "register" ? (
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-[var(--ink-soft)]">
                  用户名
                </span>
                <input
                  value={form.username}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      username: event.target.value,
                    }))
                  }
                  className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 text-sm outline-none transition focus:border-[var(--accent-strong)]"
                  placeholder="请输入用户名"
                  required
                />
              </label>
            ) : null}

            <label className="block">
              <span className="mb-2 block text-sm font-medium text-[var(--ink-soft)]">
                邮箱
              </span>
              <input
                type="email"
                value={form.email}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    email: event.target.value,
                  }))
                }
                className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 text-sm outline-none transition focus:border-[var(--accent-strong)]"
                placeholder="you@example.com"
                required
              />
            </label>

            <label className="block">
              <span className="mb-2 block text-sm font-medium text-[var(--ink-soft)]">
                密码
              </span>
              <input
                type="password"
                value={form.password}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    password: event.target.value,
                  }))
                }
                className="w-full rounded-2xl border border-[rgba(22,34,27,0.12)] bg-white px-4 py-3 text-sm outline-none transition focus:border-[var(--accent-strong)]"
                placeholder="至少 8 位"
                minLength={8}
                required
              />
            </label>

            <button
              type="submit"
              disabled={isSubmitting}
              className="inline-flex w-full items-center justify-center rounded-full bg-[linear-gradient(135deg,_#d38d2d_0%,_#be6f24_100%)] px-6 py-3 text-sm font-medium text-white transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-55"
            >
              {isSubmitting
                ? "提交中..."
                : mode === "login"
                  ? "登录"
                  : "注册并进入"}
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}

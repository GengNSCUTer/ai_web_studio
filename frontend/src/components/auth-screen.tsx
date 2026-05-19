"use client";

import { FormEvent, useEffect, useState } from "react";

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
  const [themeMode, setThemeMode] = useState<"system" | "light" | "dark">("system");
  const [systemPrefersDark, setSystemPrefersDark] = useState(false);
  const resolvedTheme =
    themeMode === "system" ? (systemPrefersDark ? "dark" : "light") : themeMode;

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const updatePreference = () => setSystemPrefersDark(mediaQuery.matches);
    updatePreference();
    mediaQuery.addEventListener("change", updatePreference);
    return () => mediaQuery.removeEventListener("change", updatePreference);
  }, []);

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
    <main
      data-theme={resolvedTheme}
      className="auth-shell min-h-screen px-4 py-6 text-[var(--ink-strong)] sm:px-6"
    >
      <div className="mx-auto mb-4 flex max-w-6xl justify-end">
        <div className="theme-mode-switch inline-flex rounded-full border p-1">
          {[
            ["system", "跟随系统"],
            ["light", "浅色"],
            ["dark", "深色"],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setThemeMode(value as "system" | "light" | "dark")}
              className={`theme-mode-button rounded-full px-3 py-1.5 text-xs transition ${
                themeMode === value ? "is-active" : ""
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="mx-auto grid min-h-[calc(100vh-3rem)] max-w-6xl items-center gap-6 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="auth-hero rounded-[32px] border p-8 shadow-[var(--panel-shadow)] sm:p-10">
          <p className="text-xs uppercase tracking-[0.42em] text-white/45">
            AI Web Studio
          </p>
          <h1 className="mt-5 max-w-xl text-4xl font-semibold leading-tight sm:text-5xl">
            面向个人知识库的智能问答工作台
          </h1>
          <p className="mt-5 max-w-2xl text-base leading-8 text-white/68 sm:text-lg">
            支持本地 Ollama 与 OpenAI 兼容服务，围绕会话、附件、联网搜索、深度思考和上下文治理，
            构建可持续扩展的个人 AI 工作空间。
          </p>

          <div className="mt-10 grid gap-3 sm:grid-cols-3">
            <div className="auth-hero-card rounded-3xl border p-4">
              <p className="text-sm font-medium">多模型接入</p>
              <p className="mt-2 text-sm text-white/52">可切换本地模型和在线 API，按会话保存模型配置。</p>
            </div>
            <div className="auth-hero-card rounded-3xl border p-4">
              <p className="text-sm font-medium">知识上下文</p>
              <p className="mt-2 text-sm text-white/52">图片、文档、长期记忆和附件片段可进入对话上下文。</p>
            </div>
            <div className="auth-hero-card rounded-3xl border p-4">
              <p className="text-sm font-medium">可观测治理</p>
              <p className="mt-2 text-sm text-white/52">提供上下文预算、摘要、缓存命中和外部来源诊断。</p>
            </div>
          </div>
        </section>

        <section className="auth-form-card rounded-[28px] border p-6 sm:p-8">
          <div className="auth-mode-switch inline-flex rounded-full border p-1">
            <button
              type="button"
              onClick={() => setMode("login")}
              className={`auth-mode-button rounded-full px-4 py-2 text-sm transition ${
                mode === "login" ? "is-active" : ""
              }`}
            >
              登录
            </button>
            <button
              type="button"
              onClick={() => setMode("register")}
              className={`auth-mode-button rounded-full px-4 py-2 text-sm transition ${
                mode === "register" ? "is-active" : ""
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
              className="primary-action inline-flex w-full items-center justify-center rounded-full px-6 py-3 text-sm font-medium transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-55"
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

import type { UserSettings } from "@/lib/types";

export type UILanguage = "zh-CN" | "en-US";
export type ThemeMode = "system" | "light" | "dark";

export const PROVIDER_PRESETS = {
  ollama: {
    baseUrl: "http://127.0.0.1:11435",
    model: "qwen3.5:27b-q8_0",
    modelContextWindow: 100000,
  },
  "openai-compatible": {
    baseUrl: "https://api.siliconflow.cn/v1",
    model: "Qwen/Qwen3.5-35B-A3B",
    modelContextWindow: 128000,
  },
} as const;

export function buildProviderPreset(providerType: string) {
  return (
    PROVIDER_PRESETS[providerType as keyof typeof PROVIDER_PRESETS] ?? PROVIDER_PRESETS.ollama
  );
}

export function normalizeUserSettings(settings: UserSettings): UserSettings {
  return {
    ...settings,
    memory_enabled: settings.memory_enabled ?? true,
    memory_max_chars: settings.memory_max_chars || 4000,
    theme_mode: settings.theme_mode || "system",
  };
}

export function normalizeThemeMode(value: string | null | undefined): ThemeMode {
  return value === "light" || value === "dark" || value === "system" ? value : "system";
}

export function buildSettingsPayload(settings: UserSettings) {
  return {
    provider_type: settings.provider_type,
    default_model: settings.default_model,
    ollama_base_url: settings.ollama_base_url,
    api_key: settings.api_key,
    temperature: settings.temperature,
    top_p: settings.top_p,
    max_tokens: settings.max_tokens,
    system_prompt: settings.system_prompt,
    model_context_window: settings.model_context_window,
    context_mode: settings.context_mode,
    memory_enabled: settings.memory_enabled,
    memory_max_chars: settings.memory_max_chars,
    ui_language: settings.ui_language,
    theme_mode: settings.theme_mode,
  };
}

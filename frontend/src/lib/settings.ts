import type { UserSettings } from "@/lib/types";

export type UILanguage = "zh-CN" | "en-US";
export type ThemeMode = "system" | "light" | "dark";

export const PROVIDER_PRESETS = {
  ollama: {
    ollamaBaseUrl: "http://127.0.0.1:11435",
    apiBaseUrl: "https://api.siliconflow.cn/v1",
    model: "qwen3.5:27b-q8_0",
    modelContextWindow: 100000,
  },
  "openai-compatible": {
    ollamaBaseUrl: "http://127.0.0.1:11435",
    apiBaseUrl: "https://api.siliconflow.cn/v1",
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
  const preset = buildProviderPreset(settings.provider_type);
  return {
    ...settings,
    ollama_base_url: settings.ollama_base_url || preset.ollamaBaseUrl,
    api_base_url: settings.api_base_url || preset.apiBaseUrl,
    memory_enabled: settings.memory_enabled ?? true,
    memory_max_chars: settings.memory_max_chars || 4000,
    theme_mode: settings.theme_mode || "system",
    knowledge_parser_provider: settings.knowledge_parser_provider || "local_basic",
    knowledge_embedding_provider: settings.knowledge_embedding_provider || "siliconflow",
    knowledge_embedding_base_url: settings.knowledge_embedding_base_url || "https://api.siliconflow.cn/v1",
    knowledge_embedding_model: settings.knowledge_embedding_model || "BAAI/bge-m3",
    knowledge_embedding_dimensions: settings.knowledge_embedding_dimensions || 1024,
    knowledge_rerank_enabled: settings.knowledge_rerank_enabled ?? true,
    knowledge_rerank_provider: settings.knowledge_rerank_provider || "siliconflow",
    knowledge_rerank_base_url: settings.knowledge_rerank_base_url || "https://api.siliconflow.cn/v1",
    knowledge_rerank_model: settings.knowledge_rerank_model || "BAAI/bge-reranker-v2-m3",
    knowledge_embedding_api_key: null,
    knowledge_embedding_has_api_key: settings.knowledge_embedding_has_api_key ?? false,
    knowledge_embedding_api_key_masked: settings.knowledge_embedding_api_key_masked ?? null,
    knowledge_rerank_api_key: null,
    knowledge_rerank_has_api_key: settings.knowledge_rerank_has_api_key ?? false,
    knowledge_rerank_api_key_masked: settings.knowledge_rerank_api_key_masked ?? null,
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
    api_base_url: settings.api_base_url,
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
    knowledge_parser_provider: settings.knowledge_parser_provider,
    knowledge_embedding_provider: settings.knowledge_embedding_provider,
    knowledge_embedding_base_url: settings.knowledge_embedding_base_url,
    knowledge_embedding_model: settings.knowledge_embedding_model,
    knowledge_embedding_dimensions: settings.knowledge_embedding_dimensions,
    knowledge_rerank_enabled: settings.knowledge_rerank_enabled,
    knowledge_rerank_provider: settings.knowledge_rerank_provider,
    knowledge_rerank_base_url: settings.knowledge_rerank_base_url,
    knowledge_rerank_model: settings.knowledge_rerank_model,
    knowledge_embedding_api_key: settings.knowledge_embedding_api_key,
    knowledge_rerank_api_key: settings.knowledge_rerank_api_key,
  };
}

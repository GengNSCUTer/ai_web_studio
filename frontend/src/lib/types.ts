export type Conversation = {
  id: string;
  title: string;
  model_name: string;
  is_archived: boolean;
  created_at: string;
  updated_at: string | null;
  system_prompt?: string | null;
  context_summary?: string | null;
  context_summary_boundary_message_id?: string | null;
  context_summary_updated_at?: string | null;
  user_id?: string | null;
};

export type ContextGovernanceInfo = {
  notices: string[];
  stats: Record<string, string>;
};

export type Message = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system" | string;
  content: string;
  status: "done" | "streaming" | "failed" | "cancelled" | string;
  created_at: string;
  updated_at: string | null;
  attachments?: UploadItem[];
};

export type ProviderInfo = {
  provider: string;
  base_url: string;
  default_model: string;
  models: string[];
};

export type ChatBootstrap = {
  conversation_id: string;
  user_message_id: string;
  assistant_message_id: string;
  model_name: string;
};

export type User = {
  id: string;
  username: string | null;
  email: string | null;
  created_at: string;
};

export type UserSettings = {
  id: string;
  user_id: string | null;
  provider_type: string;
  default_model: string;
  ollama_base_url: string;
  api_key: string | null;
  temperature: number;
  top_p: number;
  max_tokens: number | null;
  system_prompt: string | null;
  model_context_window: number;
  context_mode: string;
  ui_language: string;
  updated_at: string | null;
};

export type ProviderConnectionTestResult = {
  ok: boolean;
  provider: string;
  base_url: string;
  models: string[];
  default_model: string | null;
  message: string;
};

export type UploadItem = {
  id: string;
  file_name: string;
  mime_type: string | null;
  file_size: number;
  kind: string;
  storage_key: string;
  parsed_text?: string | null;
};

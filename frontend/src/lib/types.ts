export type Conversation = {
  id: string;
  project_id: string | null;
  title: string;
  model_name: string;
  is_pinned: boolean;
  is_archived: boolean;
  created_at: string;
  updated_at: string | null;
  system_prompt?: string | null;
  context_summary?: string | null;
  context_summary_boundary_message_id?: string | null;
  context_summary_updated_at?: string | null;
  user_id?: string | null;
};

export type Project = {
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  default_model: string | null;
  system_prompt: string | null;
  created_at: string;
  updated_at: string | null;
};

export type ProjectFile = {
  id: string;
  project_id: string;
  file_name: string;
  mime_type: string | null;
  file_size: number | null;
  kind: string;
  storage_key: string;
  parsed_text: string | null;
  created_at: string;
};

export type ProjectStats = {
  project_id: string;
  conversation_count: number;
  message_count: number;
  file_count: number;
  prompt_template_count: number;
  total_file_size: number;
};

export type ContextAttachmentChunk = {
  attachment_id: string | null;
  file_name: string;
  index: number;
  score: number;
  char_count: number;
  preview: string;
  expanded_preview: string;
};

export type ContextDiagnosticDetails = {
  attachment_chunks?: ContextAttachmentChunk[];
};

export type ContextGovernanceInfo = {
  notices: string[];
  stats: Record<string, string>;
  details?: ContextDiagnosticDetails | null;
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
  memory_enabled: boolean;
  memory_max_chars: number;
  ui_language: string;
  theme_mode: string;
  updated_at: string | null;
};

export type PromptTemplate = {
  id: string;
  user_id: string;
  project_id: string | null;
  name: string;
  description: string | null;
  content: string;
  default_model: string | null;
  category: string | null;
  variables: string | null;
  is_default: boolean;
  created_at: string;
  updated_at: string | null;
};

export type ConversationShare = {
  id: string;
  token: string;
  conversation_id: string;
  is_enabled: boolean;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
  updated_at: string | null;
};

export type PublicConversationShare = {
  token: string;
  title: string;
  model_name: string;
  created_at: string | null;
  updated_at: string | null;
  messages: Message[];
};

export type UserMemory = {
  id: string;
  user_id: string;
  memory_type: string;
  title: string;
  content: string;
  source: string;
  source_conversation_id: string | null;
  source_conversation_title: string | null;
  source_message_ids: string | null;
  confidence: string | null;
  is_enabled: boolean;
  created_at: string;
  updated_at: string | null;
};

export type MemorySuggestion = {
  memory_type: string;
  title: string;
  content: string;
  reason: string | null;
  duplicate_memory_id: string | null;
  conflict_memory_id: string | null;
  risk_level: string;
  risk_reason: string | null;
  source_conversation_id: string | null;
  source_message_ids: string | null;
  confidence: string | null;
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

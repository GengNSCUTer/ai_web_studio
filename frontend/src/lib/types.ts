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

export type ToolDefinition = {
  tool_key: string;
  provider: string;
  category: string;
  display_name: string;
  description: string;
  source_type?: string;
  adapter_type?: string;
  risk_level?: string;
  input_schema?: Record<string, unknown>;
  read_only: boolean;
  enabled_by_default: boolean;
  credential_required: boolean;
  credential_provider?: string | null;
};

export type UserToolCredential = {
  provider_key: string;
  credential_name: string;
  is_enabled: boolean;
  has_api_key: boolean;
  api_key_masked: string | null;
  source: string;
};

export type WorkspaceToolSetting = {
  project_id: string;
  tool_key: string;
  is_enabled: boolean;
};

export type WorkspaceAgentPolicy = {
  project_id: string;
  permission_mode: "read_only" | "ask" | "full_workspace";
};

export type SkillInstallation = {
  skill_key: string;
  version: string;
  display_name: string;
  description: string;
  instructions: string[];
  output_contract: string[];
  required_tool_keys: string[];
  optional_tool_keys: string[];
  requires_project: boolean;
  requires_tool_execution: boolean;
  activation_examples: string[];
  risk_declaration: string;
  is_installed: boolean;
  is_enabled: boolean;
  installed_version: string | null;
  missing_tool_keys: string[];
  available_optional_tool_keys: string[];
  is_ready: boolean;
  unavailable_reason: string | null;
  installed_manifest_digest?: string | null;
  manifest_digest?: string | null;
  source_kind?: string;
  source_publisher?: string;
  signature_status?: string;
  security_review_status?: string;
  compatibility?: Record<string, string>;
  durable_eligible?: boolean;
  update_available?: boolean;
};

export type SkillRecommendation = {
  skill_key: string;
  display_name: string;
  description: string;
  score: number;
  reasons: string[];
  requires_confirmation: boolean;
  is_ready: boolean;
};

export type ToolSettings = {
  tools: ToolDefinition[];
  credentials: UserToolCredential[];
  workspace_settings: WorkspaceToolSetting[];
  workspace_policy: WorkspaceAgentPolicy | null;
  mcp_servers: McpServer[];
  mcp_tools: McpTool[];
  skills: SkillInstallation[];
};

export type ToolConnectionTestResult = {
  ok: boolean;
  provider_key: string;
  message: string;
  raw?: Record<string, unknown> | null;
};

export type McpServer = {
  id: string;
  server_key: string;
  name: string;
  description: string | null;
  url: string;
  transport_type: string;
  auth_type: string;
  credential_provider: string | null;
  project_id: string | null;
  trust_level: string;
  is_enabled: boolean;
  last_sync_at: string | null;
  last_error: string | null;
};

export type McpTool = {
  id: string;
  server_id: string;
  server_key?: string | null;
  raw_name: string;
  tool_key: string;
  display_name: string;
  description: string | null;
  description_override: string | null;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  fixed_arguments: Record<string, unknown>;
  category: string;
  risk_level: string;
  read_only: boolean;
  remote_read_only_hint: boolean | null;
  risk_reviewed: boolean;
  is_enabled: boolean;
  last_seen_at: string | null;
};

export type McpSyncResult = {
  ok: boolean;
  server: McpServer;
  tools: McpTool[];
  message: string;
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

export type FileRevision = {
  id: string;
  project_file_id: string;
  revision_number: number;
  content_hash: string;
  created_by: string;
  source_run_id: string | null;
  source_step_id: string | null;
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

export type KnowledgeBase = {
  id: string;
  user_id: string;
  project_id: string | null;
  name: string;
  description: string | null;
  visibility: string;
  parser_provider: string;
  chunk_mode: string;
  chunk_size: number;
  chunk_overlap: number;
  chunk_delimiter: string;
  parent_chunk_size: number | null;
  child_chunk_size: number | null;
  child_chunk_overlap: number | null;
  embedding_provider: string;
  embedding_model: string;
  embedding_dimensions: number;
  rerank_enabled: boolean;
  rerank_provider: string;
  rerank_model: string;
  retrieval_mode: string;
  retrieval_top_k: number;
  rerank_top_n: number;
  score_threshold: number;
  max_context_chunks: number;
  max_context_chars: number;
  strict_knowledge_answer: boolean;
  document_count: number;
  created_at: string;
  updated_at: string | null;
};

export type KnowledgeDocument = {
  id: string;
  knowledge_base_id: string;
  user_id: string;
  project_id: string | null;
  file_name: string;
  mime_type: string | null;
  file_size: number | null;
  storage_key: string;
  parser_provider: string;
  parse_status: string;
  index_status: string;
  document_version: number;
  content_hash: string | null;
  parsed_markdown_path: string | null;
  parsed_assets_json: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string | null;
};

export type KnowledgeJob = {
  id: string;
  user_id: string;
  knowledge_base_id: string;
  document_id: string | null;
  job_type: string;
  status: string;
  payload_json: string | null;
  retry_count: number;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string | null;
};

export type KnowledgeCredential = {
  provider_key: string;
  credential_name: string;
  is_enabled: boolean;
  has_api_key: boolean;
  api_key_masked: string | null;
  source: string;
};

export type KnowledgeConnectionTestResult = {
  ok: boolean;
  provider_key: string;
  message: string;
};

export type KnowledgeDocumentParseResult = {
  document: KnowledgeDocument;
  job: KnowledgeJob;
  markdown_preview: string | null;
};

export type KnowledgeDocumentIndexResult = {
  document: KnowledgeDocument;
  job: KnowledgeJob;
  chunk_count: number;
  index_path: string | null;
};

export type KnowledgeMarkdownPreview = {
  document_id: string;
  file_name: string;
  markdown: string;
  chunks: KnowledgeMarkdownChunk[];
};

export type KnowledgeMarkdownChunk = {
  chunk_id: string;
  chunk_index: number;
  source_start: number | null;
  source_end: number | null;
  content: string;
};

export type KnowledgeRetrievalChunk = {
  chunk_id: string;
  document_id: string;
  file_name: string;
  chunk_index: number;
  score: number;
  vector_score: number;
  rerank_score: number | null;
  rank_source: string;
  content: string;
  metadata: Record<string, unknown> | null;
};

export type KnowledgeRetrievalTestResult = {
  query: string;
  top_k: number;
  total_chunks: number;
  rerank_enabled: boolean;
  rerank_model: string | null;
  filters: Record<string, unknown>;
  results: KnowledgeRetrievalChunk[];
};

export type KnowledgeRetrievalTestRequest = {
  query: string;
  top_k?: number | null;
  document_ids?: string[];
  file_types?: string[];
  page_start?: number | null;
  page_end?: number | null;
  section_query?: string | null;
};

export type KnowledgeRetrievalLog = {
  id: string;
  user_id: string;
  conversation_id: string | null;
  user_message_id: string | null;
  assistant_message_id: string | null;
  knowledge_base_id: string;
  query: string;
  retrieval_mode: string;
  top_k: number;
  rerank_enabled: boolean;
  rerank_model: string | null;
  candidates: Record<string, unknown>[];
  selected: Record<string, unknown>[];
  diagnostics: Record<string, unknown>;
  sources: Record<string, unknown>[];
  status: string;
  error_message: string | null;
  elapsed_ms: number | null;
  created_at: string;
};

export type KnowledgeEvalSet = {
  id: string;
  user_id: string;
  knowledge_base_id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string | null;
};

export type KnowledgeEvalCase = {
  id: string;
  user_id: string;
  knowledge_base_id: string;
  eval_set_id: string;
  query: string;
  expected_document_id: string | null;
  expected_chunk_id: string | null;
  expected_answer_keywords: string[];
  difficulty: string | null;
  tags: string[];
  created_at: string;
  updated_at: string | null;
};

export type KnowledgeEvalRun = {
  id: string;
  user_id: string;
  knowledge_base_id: string;
  eval_set_id: string;
  status: string;
  retrieval_mode: string;
  top_k: number;
  rerank_enabled: boolean;
  metrics: Record<string, number | string | boolean | null>;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type KnowledgeEvalResult = {
  id: string;
  user_id: string;
  knowledge_base_id: string;
  run_id: string;
  case_id: string;
  query: string;
  retrieved: Record<string, unknown>[];
  expected_document_id: string | null;
  expected_chunk_id: string | null;
  hit_at_k: boolean;
  mrr: number | null;
  context_precision: number | null;
  context_recall: number | null;
  ndcg_at_k: number | null;
  expected_keyword_recall: number | null;
  expected_keyword_hits: string[];
  created_at: string;
};

export type KnowledgeEvalOutcome = {
  run: KnowledgeEvalRun;
  results: KnowledgeEvalResult[];
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

export type ExternalSource = {
  source_type: string;
  provider: string;
  title: string;
  display_text: string;
  url?: string | null;
  rank?: number | null;
  score?: number | null;
  used_in_prompt?: boolean;
  citation_label?: string | null;
  metadata?: Record<string, unknown>;
};

export type ToolTraceEvent = {
  type:
    | "tool_planner_start"
    | "tool_planner_llm_output"
    | "tool_planner_end"
    | "tool_agent_round_start"
    | "tool_agent_round_end"
    | "tool_candidate_selection"
    | "tool_schema_validation"
    | "tool_policy_check"
    | "tool_confirmation_required"
    | "tool_plan"
    | "tool_workflow_start"
    | "tool_workflow_batch"
    | "tool_workflow_step"
    | "tool_workflow_step_skipped"
    | "tool_workflow_end"
    | "tool_call_start"
    | "tool_call_end"
    | "tool_call_error"
    | "tool_call_fallback"
    | "tool_fallback"
    | "tool_query_rewrite";
  plan?: ToolPlanPayload;
  call_id?: string;
  tool_key?: string;
  provider?: string;
  category?: string;
  display_name?: string;
  arguments?: Record<string, unknown>;
  raw_arguments?: unknown;
  normalized_arguments?: unknown;
  selected_tools?: ToolPlanPayload["calls"];
  elapsed_ms?: number;
  sources_count?: number;
  status?: string;
  error?: string;
  reason?: string;
  run_id?: string;
  step_id?: string;
  patch_draft_id?: string;
  approval_id?: string;
  file_id?: string;
  file_name?: string;
  diff_text?: string;
  arguments_hash?: string;
  expires_at?: string;
  planner?: string;
  strategy?: string;
  confidence?: number;
  risk_level?: string;
  read_only?: boolean;
  credential_source?: string;
  credential_provider?: string;
  requires_confirmation?: boolean;
  from?: string;
  to?: string;
  from_call_id?: string;
  from_tool_key?: string;
  to_call_id?: string;
  to_tool_key?: string;
  original_query?: string;
  rewritten_query?: string;
  extracted_places?: string[];
  [key: string]: unknown;
};

export type ToolPlanPayload = {
  plan_id?: string;
  router?: string;
  external_context_allowed?: boolean;
  should_use_tools?: boolean;
  fallback_tool_key?: string | null;
  calls?: Array<{
    call_id?: string;
    tool_key?: string;
    provider?: string;
    category?: string;
    display_name?: string;
    confidence?: number;
    reason?: string;
    arguments?: Record<string, unknown>;
  }>;
};

export type ContextDiagnosticDetails = {
  attachment_chunks?: ContextAttachmentChunk[];
  external_sources?: ExternalSource[];
  active_skill?: {
    skill_key?: string;
    version?: string;
    display_name?: string;
    allowed_tool_keys?: string[];
  } | null;
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
  reasoning_content?: string | null;
  external_sources?: string | null;
  tool_events?: ToolTraceEvent[];
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

export type KnowledgeModelOptions = {
  ok: boolean;
  provider: string;
  base_url: string;
  model_kind: "embedding" | "rerank" | string;
  models: string[];
  source: string;
  message: string;
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
  api_base_url: string;
  api_key: string | null;
  has_api_key?: boolean;
  api_key_masked?: string | null;
  temperature: number;
  top_p: number;
  max_tokens: number | null;
  system_prompt: string | null;
  model_context_window: number;
  context_mode: string;
  memory_enabled: boolean;
  memory_max_chars: number;
  memory_auto_candidate_enabled: boolean;
  memory_auto_candidate_turn_interval: number;
  ui_language: string;
  theme_mode: string;
  knowledge_parser_provider: string;
  knowledge_embedding_provider: string;
  knowledge_embedding_base_url: string;
  knowledge_embedding_model: string;
  knowledge_embedding_dimensions: number;
  knowledge_rerank_enabled: boolean;
  knowledge_rerank_provider: string;
  knowledge_rerank_base_url: string;
  knowledge_rerank_model: string;
  knowledge_embedding_api_key: string | null;
  knowledge_embedding_has_api_key?: boolean;
  knowledge_embedding_api_key_masked?: string | null;
  knowledge_rerank_api_key: string | null;
  knowledge_rerank_has_api_key?: boolean;
  knowledge_rerank_api_key_masked?: string | null;
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
  status: string;
  project_id: string | null;
  importance: number;
  sensitivity: string;
  risk_level: string;
  candidate_reason: string | null;
  supersedes_memory_id: string | null;
  expires_at: string | null;
  review_at: string | null;
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

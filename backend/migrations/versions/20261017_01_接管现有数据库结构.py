"""接管现有数据库结构。"""
from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy
from sqlalchemy.dialects import postgresql

revision = '20261017_01'
down_revision = None
branch_labels = None
depends_on = None
legacy_schema = {'knowledge_bases': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'project_id': ('VARCHAR(36)', True), 'name': ('VARCHAR(128)', False), 'description': ('TEXT', True), 'visibility': ('VARCHAR(32)', False), 'parser_provider': ('VARCHAR(32)', False), 'chunk_mode': ('VARCHAR(32)', False), 'chunk_size': ('INTEGER', False), 'chunk_overlap': ('INTEGER', False), 'chunk_delimiter': ('VARCHAR(64)', False), 'parent_chunk_size': ('INTEGER', True), 'child_chunk_size': ('INTEGER', True), 'child_chunk_overlap': ('INTEGER', True), 'embedding_provider': ('VARCHAR(32)', False), 'embedding_model': ('VARCHAR(128)', False), 'embedding_dimensions': ('INTEGER', False), 'rerank_enabled': ('BOOLEAN', False), 'rerank_provider': ('VARCHAR(32)', False), 'rerank_model': ('VARCHAR(128)', False), 'retrieval_mode': ('VARCHAR(32)', False), 'retrieval_top_k': ('INTEGER', False), 'rerank_top_n': ('INTEGER', False), 'score_threshold': ('DOUBLE PRECISION', False), 'max_context_chunks': ('INTEGER', False), 'max_context_chars': ('INTEGER', False), 'strict_knowledge_answer': ('BOOLEAN', False), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False), 'active_index_generation': ('VARCHAR(64)', False)}, 'indexes': ['ix_knowledge_bases_project_id', 'ix_knowledge_bases_user_id'], 'foreign_keys': [['project_id'], ['user_id']]}, 'knowledge_documents': {'columns': {'id': ('VARCHAR(36)', False), 'knowledge_base_id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'project_id': ('VARCHAR(36)', True), 'file_name': ('VARCHAR(255)', False), 'mime_type': ('VARCHAR(128)', True), 'file_size': ('INTEGER', True), 'storage_key': ('TEXT', False), 'parser_provider': ('VARCHAR(32)', False), 'parse_status': ('VARCHAR(32)', False), 'index_status': ('VARCHAR(32)', False), 'document_version': ('INTEGER', False), 'content_hash': ('VARCHAR(64)', True), 'parsed_markdown_path': ('TEXT', True), 'parsed_assets_json': ('TEXT', True), 'error_message': ('TEXT', True), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False)}, 'indexes': ['ix_knowledge_documents_knowledge_base_id', 'ix_knowledge_documents_project_id', 'ix_knowledge_documents_user_id'], 'foreign_keys': [['knowledge_base_id'], ['project_id'], ['user_id']]}, 'knowledge_eval_sets': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'knowledge_base_id': ('VARCHAR(36)', False), 'name': ('VARCHAR(128)', False), 'description': ('TEXT', True), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False)}, 'indexes': ['ix_knowledge_eval_sets_knowledge_base_id', 'ix_knowledge_eval_sets_user_id'], 'foreign_keys': [['knowledge_base_id'], ['user_id']]}, 'conversations': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', True), 'title': ('VARCHAR(255)', False), 'model_name': ('VARCHAR(128)', False), 'system_prompt': ('TEXT', True), 'is_archived': ('BOOLEAN', False), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False), 'context_summary': ('TEXT', True), 'context_summary_updated_at': ('TIMESTAMP', True), 'context_summary_boundary_message_id': ('VARCHAR(36)', True), 'last_prompt_prefix_hash': ('VARCHAR(64)', True), 'last_prompt_prefix_token_count': ('INTEGER', True), 'is_pinned': ('BOOLEAN', True), 'project_id': ('VARCHAR(36)', True), 'memory_extraction_cursor': ('INTEGER', False)}, 'indexes': [], 'foreign_keys': [['user_id']]}, 'knowledge_eval_runs': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'knowledge_base_id': ('VARCHAR(36)', False), 'eval_set_id': ('VARCHAR(36)', False), 'status': ('VARCHAR(32)', False), 'retrieval_mode': ('VARCHAR(32)', False), 'top_k': ('INTEGER', False), 'rerank_enabled': ('BOOLEAN', False), 'metrics_json': ('TEXT', True), 'error_message': ('TEXT', True), 'started_at': ('TIMESTAMP', True), 'finished_at': ('TIMESTAMP', True), 'created_at': ('TIMESTAMP', False)}, 'indexes': ['ix_knowledge_eval_runs_eval_set_id', 'ix_knowledge_eval_runs_knowledge_base_id', 'ix_knowledge_eval_runs_user_id'], 'foreign_keys': [['eval_set_id'], ['knowledge_base_id'], ['user_id']]}, 'knowledge_eval_cases': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'knowledge_base_id': ('VARCHAR(36)', False), 'eval_set_id': ('VARCHAR(36)', False), 'query': ('TEXT', False), 'expected_document_id': ('VARCHAR(36)', True), 'expected_chunk_id': ('VARCHAR(36)', True), 'expected_answer_keywords_json': ('TEXT', True), 'difficulty': ('VARCHAR(32)', True), 'tags_json': ('TEXT', True), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False), 'expected_document_ids_json': ('TEXT', True), 'expected_chunk_ids_json': ('TEXT', True)}, 'indexes': ['ix_knowledge_eval_cases_eval_set_id', 'ix_knowledge_eval_cases_expected_chunk_id', 'ix_knowledge_eval_cases_expected_document_id', 'ix_knowledge_eval_cases_knowledge_base_id', 'ix_knowledge_eval_cases_user_id'], 'foreign_keys': [['eval_set_id'], ['expected_chunk_id'], ['expected_document_id'], ['knowledge_base_id'], ['user_id']]}, 'knowledge_retrieval_logs': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'conversation_id': ('VARCHAR(36)', True), 'user_message_id': ('VARCHAR(36)', True), 'assistant_message_id': ('VARCHAR(36)', True), 'knowledge_base_id': ('VARCHAR(36)', False), 'query': ('TEXT', False), 'retrieval_mode': ('VARCHAR(32)', False), 'top_k': ('INTEGER', False), 'rerank_enabled': ('BOOLEAN', False), 'rerank_model': ('VARCHAR(128)', True), 'candidates_json': ('TEXT', True), 'selected_json': ('TEXT', True), 'diagnostics_json': ('TEXT', True), 'sources_json': ('TEXT', True), 'status': ('VARCHAR(32)', False), 'error_message': ('TEXT', True), 'elapsed_ms': ('INTEGER', True), 'created_at': ('TIMESTAMP', False)}, 'indexes': ['ix_knowledge_retrieval_logs_assistant_message_id', 'ix_knowledge_retrieval_logs_conversation_id', 'ix_knowledge_retrieval_logs_knowledge_base_id', 'ix_knowledge_retrieval_logs_user_id', 'ix_knowledge_retrieval_logs_user_message_id'], 'foreign_keys': [['assistant_message_id'], ['conversation_id'], ['knowledge_base_id'], ['user_id'], ['user_message_id']]}, 'mcp_servers': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'project_id': ('VARCHAR(36)', True), 'server_key': ('VARCHAR(96)', False), 'name': ('VARCHAR(128)', False), 'description': ('TEXT', True), 'transport_type': ('VARCHAR(32)', False), 'url': ('TEXT', False), 'auth_type': ('VARCHAR(32)', False), 'credential_provider': ('VARCHAR(96)', True), 'trust_level': ('VARCHAR(32)', False), 'is_enabled': ('BOOLEAN', False), 'last_sync_at': ('TIMESTAMP', True), 'last_error': ('TEXT', True), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False)}, 'indexes': ['ix_mcp_servers_credential_provider', 'ix_mcp_servers_project_id', 'ix_mcp_servers_server_key', 'ix_mcp_servers_user_id', 'uq_mcp_server_user_key'], 'foreign_keys': []}, 'attachments': {'columns': {'id': ('VARCHAR(36)', False), 'message_id': ('VARCHAR(36)', False), 'kind': ('VARCHAR(32)', False), 'file_name': ('VARCHAR(255)', False), 'file_ext': ('VARCHAR(32)', True), 'mime_type': ('VARCHAR(128)', True), 'file_size': ('INTEGER', True), 'storage_path': ('TEXT', False), 'parsed_text': ('TEXT', True), 'created_at': ('TIMESTAMP', False)}, 'indexes': ['ix_attachments_message_id'], 'foreign_keys': [['message_id']]}, 'knowledge_eval_results': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'knowledge_base_id': ('VARCHAR(36)', False), 'run_id': ('VARCHAR(36)', False), 'case_id': ('VARCHAR(36)', False), 'query': ('TEXT', False), 'retrieved_json': ('TEXT', True), 'expected_document_id': ('VARCHAR(36)', True), 'expected_chunk_id': ('VARCHAR(36)', True), 'hit_at_k': ('BOOLEAN', False), 'mrr': ('DOUBLE PRECISION', True), 'context_precision': ('DOUBLE PRECISION', True), 'context_recall': ('DOUBLE PRECISION', True), 'created_at': ('TIMESTAMP', False), 'ndcg_at_k': ('DOUBLE PRECISION', True), 'expected_keyword_recall': ('DOUBLE PRECISION', True), 'expected_keyword_hits_json': ('TEXT', True), 'expected_document_ids_json': ('TEXT', True), 'expected_chunk_ids_json': ('TEXT', True), 'matched_chunk_ids_json': ('TEXT', True)}, 'indexes': ['ix_knowledge_eval_results_case_id', 'ix_knowledge_eval_results_knowledge_base_id', 'ix_knowledge_eval_results_run_id', 'ix_knowledge_eval_results_user_id'], 'foreign_keys': [['case_id'], ['knowledge_base_id'], ['run_id'], ['user_id']]}, 'projects': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'name': ('VARCHAR(128)', False), 'description': ('TEXT', True), 'default_model': ('VARCHAR(128)', True), 'system_prompt': ('TEXT', True), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False)}, 'indexes': ['ix_projects_user_id'], 'foreign_keys': [['user_id']]}, 'prompt_templates': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'name': ('VARCHAR(128)', False), 'description': ('TEXT', True), 'content': ('TEXT', False), 'default_model': ('VARCHAR(128)', True), 'is_default': ('BOOLEAN', False), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False), 'project_id': ('VARCHAR(36)', True), 'category': ('VARCHAR(64)', True), 'variables': ('TEXT', True)}, 'indexes': ['ix_prompt_templates_user_id'], 'foreign_keys': [['user_id']]}, 'project_files': {'columns': {'id': ('VARCHAR(36)', False), 'project_id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'kind': ('VARCHAR(32)', False), 'file_name': ('VARCHAR(255)', False), 'mime_type': ('VARCHAR(128)', True), 'file_size': ('INTEGER', True), 'storage_key': ('TEXT', False), 'parsed_text': ('TEXT', True), 'created_at': ('TIMESTAMP', False)}, 'indexes': ['ix_project_files_project_id', 'ix_project_files_user_id'], 'foreign_keys': [['project_id'], ['user_id']]}, 'messages': {'columns': {'id': ('VARCHAR(36)', False), 'conversation_id': ('VARCHAR(36)', False), 'role': ('VARCHAR(32)', False), 'content': ('TEXT', False), 'status': ('VARCHAR(32)', False), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False), 'reasoning_content': ('TEXT', True), 'external_sources': ('TEXT', True), 'sequence': ('INTEGER', True), 'generation_id': ('VARCHAR(36)', False)}, 'indexes': ['ix_messages_conversation_id'], 'foreign_keys': [['conversation_id']]}, 'tool_call_runs': {'columns': {'id': ('VARCHAR(36)', False), 'route_run_id': ('VARCHAR(36)', False), 'call_id': ('VARCHAR(64)', False), 'tool_key': ('VARCHAR(128)', False), 'provider': ('VARCHAR(64)', False), 'category': ('VARCHAR(64)', False), 'display_name': ('VARCHAR(128)', True), 'arguments_json': ('TEXT', True), 'status': ('VARCHAR(32)', False), 'started_at': ('TIMESTAMP', True), 'finished_at': ('TIMESTAMP', True), 'elapsed_ms': ('INTEGER', True), 'error_message': ('TEXT', True), 'sources_count': ('INTEGER', False), 'sources_json': ('TEXT', True), 'created_at': ('TIMESTAMP', False)}, 'indexes': ['ix_tool_call_runs_call_id', 'ix_tool_call_runs_category', 'ix_tool_call_runs_provider', 'ix_tool_call_runs_route_run_id', 'ix_tool_call_runs_tool_key'], 'foreign_keys': []}, 'tool_route_runs': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'conversation_id': ('VARCHAR(36)', False), 'user_message_id': ('VARCHAR(36)', True), 'assistant_message_id': ('VARCHAR(36)', True), 'router_type': ('VARCHAR(64)', False), 'query': ('TEXT', False), 'external_context_allowed': ('BOOLEAN', False), 'plan_json': ('TEXT', True), 'selected_tools_json': ('TEXT', True), 'events_json': ('TEXT', True), 'sources_json': ('TEXT', True), 'status': ('VARCHAR(32)', False), 'elapsed_ms': ('INTEGER', True), 'created_at': ('TIMESTAMP', False)}, 'indexes': ['ix_tool_route_runs_assistant_message_id', 'ix_tool_route_runs_conversation_id', 'ix_tool_route_runs_user_id', 'ix_tool_route_runs_user_message_id'], 'foreign_keys': []}, 'user_tool_credentials': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'provider_key': ('VARCHAR(64)', False), 'credential_name': ('VARCHAR(128)', False), 'api_key': ('TEXT', True), 'is_enabled': ('BOOLEAN', False), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False)}, 'indexes': ['ix_user_tool_credentials_provider_key', 'ix_user_tool_credentials_user_id', 'uq_user_tool_provider'], 'foreign_keys': []}, 'workspace_tool_settings': {'columns': {'id': ('VARCHAR(36)', False), 'project_id': ('VARCHAR(36)', False), 'tool_key': ('VARCHAR(128)', False), 'is_enabled': ('BOOLEAN', False), 'config_json': ('TEXT', True), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False)}, 'indexes': ['ix_workspace_tool_settings_project_id', 'ix_workspace_tool_settings_tool_key', 'uq_workspace_tool_key'], 'foreign_keys': []}, 'conversation_shares': {'columns': {'id': ('VARCHAR(36)', False), 'token': ('VARCHAR(64)', False), 'conversation_id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'is_enabled': ('BOOLEAN', False), 'expires_at': ('TIMESTAMP', True), 'revoked_at': ('TIMESTAMP', True), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False)}, 'indexes': ['ix_conversation_shares_conversation_id', 'ix_conversation_shares_token', 'ix_conversation_shares_user_id'], 'foreign_keys': [['conversation_id'], ['user_id']]}, 'users': {'columns': {'id': ('VARCHAR(36)', False), 'username': ('VARCHAR(64)', True), 'email': ('VARCHAR(255)', True), 'password_hash': ('VARCHAR(255)', True), 'created_at': ('TIMESTAMP', False)}, 'indexes': ['ux_users_email_lower', 'ux_users_username_lower'], 'foreign_keys': []}, 'knowledge_chunks': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'knowledge_base_id': ('VARCHAR(36)', False), 'document_id': ('VARCHAR(36)', False), 'chunk_index': ('INTEGER', False), 'vector_id': ('INTEGER', False), 'content': ('TEXT', False), 'content_hash': ('VARCHAR(64)', False), 'char_count': ('INTEGER', False), 'token_estimate': ('INTEGER', False), 'source_start': ('INTEGER', True), 'source_end': ('INTEGER', True), 'metadata_json': ('TEXT', True), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False), 'index_generation': ('VARCHAR(64)', False), 'embedding': ('VECTOR', True), 'embedding_provider': ('VARCHAR(32)', True), 'embedding_model': ('VARCHAR(128)', True), 'embedding_dimensions': ('INTEGER', True), 'embedding_version': ('VARCHAR(32)', True)}, 'indexes': ['ix_knowledge_chunks_document_id', 'ix_knowledge_chunks_knowledge_base_id', 'ix_knowledge_chunks_user_id', 'ix_knowledge_chunks_vector_id', 'uq_knowledge_chunks_generation_vector'], 'foreign_keys': [['document_id'], ['knowledge_base_id'], ['user_id']]}, 'outbox_events': {'columns': {'id': ('VARCHAR(36)', False), 'event_key': ('VARCHAR(192)', False), 'aggregate_type': ('VARCHAR(64)', False), 'aggregate_id': ('VARCHAR(36)', False), 'event_type': ('VARCHAR(64)', False), 'payload_json': ('TEXT', False), 'status': ('VARCHAR(32)', False), 'attempt_count': ('INTEGER', False), 'available_at': ('TIMESTAMP', True), 'lease_owner': ('VARCHAR(128)', True), 'lease_expires_at': ('TIMESTAMP', True), 'published_at': ('TIMESTAMP', True), 'error_message': ('TEXT', True), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False)}, 'indexes': ['ix_outbox_events_aggregate_id', 'ix_outbox_events_available_at', 'ix_outbox_events_event_key', 'ix_outbox_events_lease_expires_at', 'ix_outbox_events_lease_owner', 'ix_outbox_events_status'], 'foreign_keys': [['aggregate_id']]}, 'knowledge_jobs': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'knowledge_base_id': ('VARCHAR(36)', False), 'document_id': ('VARCHAR(36)', True), 'job_type': ('VARCHAR(32)', False), 'status': ('VARCHAR(32)', False), 'payload_json': ('TEXT', True), 'retry_count': ('INTEGER', False), 'error_message': ('TEXT', True), 'started_at': ('TIMESTAMP', True), 'finished_at': ('TIMESTAMP', True), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False), 'error_code': ('VARCHAR(64)', True), 'idempotency_key': ('VARCHAR(192)', True), 'result_json': ('TEXT', True), 'max_attempts': ('INTEGER', False), 'available_at': ('TIMESTAMP', True), 'lease_owner': ('VARCHAR(128)', True), 'lease_expires_at': ('TIMESTAMP', True), 'lease_version': ('INTEGER', False), 'heartbeat_at': ('TIMESTAMP', True), 'dead_lettered_at': ('TIMESTAMP', True)}, 'indexes': ['ix_knowledge_jobs_document_id', 'ix_knowledge_jobs_knowledge_base_id', 'ix_knowledge_jobs_user_id', 'ux_knowledge_jobs_idempotency_key'], 'foreign_keys': [['document_id'], ['knowledge_base_id'], ['user_id']]}, 'knowledge_index_generations': {'columns': {'id': ('VARCHAR(64)', False), 'user_id': ('VARCHAR(36)', False), 'knowledge_base_id': ('VARCHAR(36)', False), 'base_generation_id': ('VARCHAR(64)', True), 'status': ('VARCHAR(32)', False), 'manifest_hash': ('VARCHAR(64)', True), 'error_message': ('TEXT', True), 'created_at': ('TIMESTAMP', False), 'activated_at': ('TIMESTAMP', True), 'job_id': ('VARCHAR(36)', True), 'chunk_count': ('INTEGER', True)}, 'indexes': ['ix_knowledge_index_generations_base_generation_id', 'ix_knowledge_index_generations_knowledge_base_id', 'ix_knowledge_index_generations_status', 'ix_knowledge_index_generations_user_id'], 'foreign_keys': [['knowledge_base_id'], ['user_id']]}, 'agent_runs': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'project_id': ('VARCHAR(36)', True), 'conversation_id': ('VARCHAR(36)', True), 'assistant_message_id': ('VARCHAR(36)', True), 'status': ('VARCHAR(32)', False), 'input_json': ('TEXT', False), 'planner_state_json': ('TEXT', False), 'idempotency_key': ('VARCHAR(192)', False), 'state_version': ('INTEGER', False), 'max_steps': ('INTEGER', False), 'current_step': ('INTEGER', False), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False), 'finished_at': ('TIMESTAMP', True), 'runtime_kind': ('VARCHAR(48)', False)}, 'indexes': ['ix_agent_runs_assistant_message_id', 'ix_agent_runs_conversation_id', 'ix_agent_runs_idempotency_key', 'ix_agent_runs_project_id', 'ix_agent_runs_status', 'ix_agent_runs_user_id'], 'foreign_keys': [['assistant_message_id'], ['conversation_id'], ['project_id'], ['user_id']]}, 'memory_extraction_jobs': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'conversation_id': ('VARCHAR(36)', False), 'project_id': ('VARCHAR(36)', True), 'idempotency_key': ('VARCHAR(192)', False), 'source_message_ids': ('TEXT', False), 'status': ('VARCHAR(24)', False), 'attempts': ('INTEGER', False), 'max_attempts': ('INTEGER', False), 'available_at': ('TIMESTAMP', True), 'lease_owner': ('VARCHAR(128)', True), 'lease_version': ('INTEGER', False), 'lease_expires_at': ('TIMESTAMP', True), 'error_code': ('VARCHAR(64)', True), 'error_message': ('TEXT', True), 'result_count': ('INTEGER', False), 'started_at': ('TIMESTAMP', True), 'finished_at': ('TIMESTAMP', True), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False), 'source_snapshot': ('TEXT', True), 'cursor_end': ('INTEGER', False)}, 'indexes': ['ix_memory_extraction_jobs_available_at', 'ix_memory_extraction_jobs_conversation_id', 'ix_memory_extraction_jobs_idempotency_key', 'ix_memory_extraction_jobs_lease_expires_at', 'ix_memory_extraction_jobs_lease_owner', 'ix_memory_extraction_jobs_project_id', 'ix_memory_extraction_jobs_status', 'ix_memory_extraction_jobs_user_id'], 'foreign_keys': [['conversation_id'], ['project_id'], ['user_id']]}, 'agent_steps': {'columns': {'id': ('VARCHAR(36)', False), 'run_id': ('VARCHAR(36)', False), 'sequence': ('INTEGER', False), 'call_id': ('VARCHAR(128)', False), 'tool_key': ('VARCHAR(160)', False), 'arguments_json': ('TEXT', False), 'arguments_hash': ('VARCHAR(64)', False), 'status': ('VARCHAR(32)', False), 'result_json': ('TEXT', True), 'error_code': ('VARCHAR(64)', True), 'error_message': ('TEXT', True), 'attempts': ('INTEGER', False), 'depends_on_json': ('TEXT', False), 'lease_owner': ('VARCHAR(128)', True), 'lease_version': ('INTEGER', False), 'lease_expires_at': ('TIMESTAMP', True), 'started_at': ('TIMESTAMP', True), 'finished_at': ('TIMESTAMP', True), 'created_at': ('TIMESTAMP', False), 'result_bindings_json': ('TEXT', False), 'available_at': ('TIMESTAMP', True), 'max_attempts': ('INTEGER', False), 'heartbeat_at': ('TIMESTAMP', True), 'dead_lettered_at': ('TIMESTAMP', True)}, 'indexes': ['ix_agent_steps_arguments_hash', 'ix_agent_steps_run_id', 'ix_agent_steps_status', 'ix_agent_steps_tool_key', 'uq_agent_steps_run_call', 'uq_agent_steps_run_sequence'], 'foreign_keys': [['run_id']]}, 'agent_checkpoints': {'columns': {'id': ('VARCHAR(36)', False), 'run_id': ('VARCHAR(36)', False), 'step_sequence': ('INTEGER', False), 'state_version': ('INTEGER', False), 'planner_state_json': ('TEXT', False), 'observations_json': ('TEXT', False), 'remaining_budget_json': ('TEXT', False), 'created_at': ('TIMESTAMP', False)}, 'indexes': ['ix_agent_checkpoints_run_id', 'uq_agent_checkpoint_version'], 'foreign_keys': [['run_id']]}, 'file_revisions': {'columns': {'id': ('VARCHAR(36)', False), 'project_file_id': ('VARCHAR(36)', False), 'revision_number': ('INTEGER', False), 'content_hash': ('VARCHAR(64)', False), 'parsed_text': ('TEXT', False), 'created_by': ('VARCHAR(64)', False), 'source_run_id': ('VARCHAR(36)', True), 'source_step_id': ('VARCHAR(36)', True), 'created_at': ('TIMESTAMP', False)}, 'indexes': ['ix_file_revisions_content_hash', 'ix_file_revisions_project_file_id', 'uq_file_revision_number'], 'foreign_keys': [['project_file_id'], ['source_run_id'], ['source_step_id']]}, 'patch_drafts': {'columns': {'id': ('VARCHAR(36)', False), 'run_id': ('VARCHAR(36)', False), 'step_id': ('VARCHAR(36)', False), 'project_file_id': ('VARCHAR(36)', False), 'base_revision_id': ('VARCHAR(36)', False), 'base_content_hash': ('VARCHAR(64)', False), 'old_string': ('TEXT', False), 'new_string': ('TEXT', False), 'proposed_content_hash': ('VARCHAR(64)', False), 'diff_text': ('TEXT', False), 'arguments_hash': ('VARCHAR(64)', False), 'status': ('VARCHAR(32)', False), 'created_at': ('TIMESTAMP', False), 'expires_at': ('TIMESTAMP', False), 'applied_at': ('TIMESTAMP', True)}, 'indexes': ['ix_patch_drafts_expires_at', 'ix_patch_drafts_project_file_id', 'ix_patch_drafts_run_id', 'ix_patch_drafts_status', 'ix_patch_drafts_step_id'], 'foreign_keys': [['base_revision_id'], ['project_file_id'], ['run_id'], ['step_id']]}, 'user_settings': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', True), 'default_model': ('VARCHAR(128)', False), 'ollama_base_url': ('VARCHAR(255)', False), 'temperature': ('DOUBLE PRECISION', False), 'top_p': ('DOUBLE PRECISION', False), 'max_tokens': ('INTEGER', True), 'system_prompt': ('TEXT', True), 'updated_at': ('TIMESTAMP', False), 'provider_type': ('VARCHAR(32)', True), 'api_key': ('TEXT', True), 'model_context_window': ('INTEGER', True), 'context_mode': ('VARCHAR(32)', True), 'ui_language': ('VARCHAR(16)', True), 'memory_enabled': ('BOOLEAN', True), 'memory_max_chars': ('INTEGER', True), 'theme_mode': ('VARCHAR(16)', True), 'knowledge_parser_provider': ('VARCHAR(32)', True), 'knowledge_embedding_provider': ('VARCHAR(32)', True), 'knowledge_embedding_base_url': ('VARCHAR(255)', True), 'knowledge_embedding_model': ('VARCHAR(128)', True), 'knowledge_embedding_dimensions': ('INTEGER', True), 'knowledge_rerank_enabled': ('BOOLEAN', True), 'knowledge_rerank_provider': ('VARCHAR(32)', True), 'knowledge_rerank_base_url': ('VARCHAR(255)', True), 'knowledge_rerank_model': ('VARCHAR(128)', True), 'knowledge_api_key': ('TEXT', True), 'knowledge_embedding_api_key': ('TEXT', True), 'knowledge_rerank_api_key': ('TEXT', True), 'api_base_url': ('VARCHAR(255)', True), 'memory_auto_candidate_enabled': ('BOOLEAN', True), 'memory_auto_candidate_turn_interval': ('INTEGER', True), 'memory_auto_activate_enabled': ('BOOLEAN', False)}, 'indexes': ['user_settings_user_id_key'], 'foreign_keys': [['user_id']]}, 'agent_artifacts': {'columns': {'id': ('VARCHAR(36)', False), 'run_id': ('VARCHAR(36)', False), 'step_id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'artifact_type': ('VARCHAR(64)', False), 'content_hash': ('VARCHAR(64)', False), 'preview': ('TEXT', False), 'content_json': ('TEXT', False), 'char_count': ('INTEGER', False), 'prompt_state': ('VARCHAR(32)', False), 'created_at': ('TIMESTAMP', False)}, 'indexes': ['ix_agent_artifacts_content_hash', 'ix_agent_artifacts_prompt_state', 'ix_agent_artifacts_run_id', 'ix_agent_artifacts_step_id', 'ix_agent_artifacts_user_id'], 'foreign_keys': [['run_id'], ['step_id'], ['user_id']]}, 'user_memories': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'memory_type': ('VARCHAR(32)', False), 'title': ('VARCHAR(120)', False), 'content': ('TEXT', False), 'source': ('VARCHAR(32)', False), 'is_enabled': ('BOOLEAN', False), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False), 'source_conversation_id': ('VARCHAR(36)', True), 'source_message_ids': ('TEXT', True), 'confidence': ('VARCHAR(16)', True), 'status': ('VARCHAR(24)', False), 'project_id': ('VARCHAR(36)', True), 'importance': ('DOUBLE PRECISION', False), 'sensitivity': ('VARCHAR(24)', False), 'risk_level': ('VARCHAR(32)', False), 'candidate_reason': ('TEXT', True), 'content_hash': ('VARCHAR(64)', True), 'supersedes_memory_id': ('VARCHAR(36)', True), 'expires_at': ('TIMESTAMP', True), 'review_at': ('TIMESTAMP', True), 'fact_key': ('VARCHAR(180)', True), 'fact_value': ('TEXT', True), 'version': ('INTEGER', False), 'evidence_quote': ('TEXT', True), 'embedding_vector': ('TEXT', True), 'embedding_signature': ('VARCHAR(64)', True), 'embedding_text_hash': ('VARCHAR(64)', True), 'extraction_job_id': ('VARCHAR(36)', True)}, 'indexes': ['ix_user_memories_user_id'], 'foreign_keys': [['user_id']]}, 'workspace_agent_policies': {'columns': {'project_id': ('VARCHAR(36)', False), 'permission_mode': ('VARCHAR(32)', False), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False)}, 'indexes': [], 'foreign_keys': []}, 'agent_approvals': {'columns': {'id': ('VARCHAR(36)', False), 'run_id': ('VARCHAR(36)', False), 'step_id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'action': ('VARCHAR(64)', False), 'arguments_hash': ('VARCHAR(64)', False), 'nonce_hash': ('VARCHAR(64)', True), 'status': ('VARCHAR(32)', False), 'expires_at': ('TIMESTAMP', False), 'consumed_at': ('TIMESTAMP', True), 'created_at': ('TIMESTAMP', False), 'decision_mode': ('VARCHAR(32)', False)}, 'indexes': ['ix_agent_approvals_expires_at', 'ix_agent_approvals_run_id', 'ix_agent_approvals_status', 'ix_agent_approvals_step_id', 'ix_agent_approvals_user_id'], 'foreign_keys': [['run_id'], ['step_id'], ['user_id']]}, 'agent_outbox_events': {'columns': {'id': ('VARCHAR(36)', False), 'event_key': ('VARCHAR(192)', False), 'run_id': ('VARCHAR(36)', False), 'step_id': ('VARCHAR(36)', False), 'event_type': ('VARCHAR(64)', False), 'payload_json': ('TEXT', False), 'status': ('VARCHAR(32)', False), 'attempt_count': ('INTEGER', False), 'available_at': ('TIMESTAMP', True), 'lease_owner': ('VARCHAR(128)', True), 'lease_version': ('INTEGER', False), 'lease_expires_at': ('TIMESTAMP', True), 'heartbeat_at': ('TIMESTAMP', True), 'dead_lettered_at': ('TIMESTAMP', True), 'error_code': ('VARCHAR(64)', True), 'error_message': ('TEXT', True), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False)}, 'indexes': ['ix_agent_outbox_events_available_at', 'ix_agent_outbox_events_event_key', 'ix_agent_outbox_events_lease_expires_at', 'ix_agent_outbox_events_lease_owner', 'ix_agent_outbox_events_run_id', 'ix_agent_outbox_events_status', 'ix_agent_outbox_events_step_id'], 'foreign_keys': [['run_id'], ['step_id']]}, 'chat_runtime_metrics': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'conversation_id': ('VARCHAR(36)', False), 'assistant_message_id': ('VARCHAR(36)', False), 'provider_type': ('VARCHAR(48)', False), 'model_name': ('VARCHAR(160)', False), 'status': ('VARCHAR(32)', False), 'stats_json': ('TEXT', False), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False)}, 'indexes': ['ix_chat_runtime_metrics_assistant_message_id', 'ix_chat_runtime_metrics_conversation_id', 'ix_chat_runtime_metrics_provider_type', 'ix_chat_runtime_metrics_status', 'ix_chat_runtime_metrics_user_id'], 'foreign_keys': [['assistant_message_id'], ['conversation_id'], ['user_id']]}, 'skill_installation_revisions': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'skill_key': ('VARCHAR(128)', False), 'manifest_version': ('VARCHAR(32)', False), 'manifest_digest': ('VARCHAR(64)', False), 'manifest_json': ('TEXT', False), 'source_kind': ('VARCHAR(32)', False), 'security_review_status': ('VARCHAR(32)', False), 'action': ('VARCHAR(32)', False), 'created_at': ('TIMESTAMP', False)}, 'indexes': ['ix_skill_installation_revisions_action', 'ix_skill_installation_revisions_manifest_digest', 'ix_skill_installation_revisions_manifest_version', 'ix_skill_installation_revisions_skill_key', 'ix_skill_installation_revisions_user_id'], 'foreign_keys': []}, 'user_skill_installations': {'columns': {'id': ('VARCHAR(36)', False), 'user_id': ('VARCHAR(36)', False), 'skill_key': ('VARCHAR(128)', False), 'manifest_version': ('VARCHAR(32)', False), 'is_enabled': ('BOOLEAN', False), 'installed_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False), 'manifest_digest': ('VARCHAR(64)', True)}, 'indexes': ['ix_user_skill_installations_skill_key', 'ix_user_skill_installations_user_id', 'uq_user_skill_installation'], 'foreign_keys': []}, 'mcp_tools': {'columns': {'id': ('VARCHAR(36)', False), 'server_id': ('VARCHAR(36)', False), 'raw_name': ('VARCHAR(160)', False), 'tool_key': ('VARCHAR(220)', False), 'display_name': ('VARCHAR(180)', False), 'description': ('TEXT', True), 'description_override': ('TEXT', True), 'input_schema_json': ('TEXT', True), 'output_schema_json': ('TEXT', True), 'annotations_json': ('TEXT', True), 'fixed_arguments_json': ('TEXT', True), 'category': ('VARCHAR(64)', False), 'risk_level': ('VARCHAR(32)', False), 'read_only': ('BOOLEAN', False), 'is_enabled': ('BOOLEAN', False), 'last_seen_at': ('TIMESTAMP', True), 'call_count': ('INTEGER', False), 'created_at': ('TIMESTAMP', False), 'updated_at': ('TIMESTAMP', False), 'risk_reviewed': ('BOOLEAN', False), 'onboarding_contract_json': ('TEXT', True), 'onboarding_contract_digest': ('VARCHAR(64)', True), 'onboarding_fixture_digest': ('VARCHAR(64)', True), 'onboarding_config_digest': ('VARCHAR(64)', True), 'onboarding_review_status': ('VARCHAR(32)', False), 'onboarding_reviewed_at': ('TIMESTAMP', True)}, 'indexes': ['ix_mcp_tools_raw_name', 'ix_mcp_tools_server_id', 'ix_mcp_tools_tool_key', 'uq_mcp_tool_server_raw_name'], 'foreign_keys': []}, 'agent_worker_heartbeats': {'columns': {'owner': ('VARCHAR(128)', False), 'instance_id': ('VARCHAR(36)', False), 'status': ('VARCHAR(24)', False), 'current_step_id': ('VARCHAR(36)', True), 'started_at': ('TIMESTAMP', False), 'heartbeat_at': ('TIMESTAMP', False), 'stopped_at': ('TIMESTAMP', True)}, 'indexes': [], 'foreign_keys': []}}


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # 本版本固定记录结构变更，不依赖后续 ORM 模型。
    op.create_table('agent_worker_heartbeats',
    sa.Column('owner', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('instance_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('status', sa.VARCHAR(length=24), autoincrement=False, nullable=False),
    sa.Column('current_step_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('started_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('heartbeat_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('stopped_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.PrimaryKeyConstraint('owner', name='agent_worker_heartbeats_pkey')
    )
    op.create_table('mcp_servers',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('project_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('server_key', sa.VARCHAR(length=96), autoincrement=False, nullable=False),
    sa.Column('name', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('description', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('transport_type', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('url', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('auth_type', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('credential_provider', sa.VARCHAR(length=96), autoincrement=False, nullable=True),
    sa.Column('trust_level', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('is_enabled', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('last_sync_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('last_error', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name='mcp_servers_pkey'),
    sa.UniqueConstraint('user_id', 'server_key', name='uq_mcp_server_user_key', postgresql_include=[], postgresql_nulls_not_distinct=False)
    )
    op.create_index('ix_mcp_servers_credential_provider', 'mcp_servers', ['credential_provider'], unique=False, postgresql_include=[])
    op.create_index('ix_mcp_servers_project_id', 'mcp_servers', ['project_id'], unique=False, postgresql_include=[])
    op.create_index('ix_mcp_servers_server_key', 'mcp_servers', ['server_key'], unique=False, postgresql_include=[])
    op.create_index('ix_mcp_servers_user_id', 'mcp_servers', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('mcp_tools',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('server_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('raw_name', sa.VARCHAR(length=160), autoincrement=False, nullable=False),
    sa.Column('tool_key', sa.VARCHAR(length=220), autoincrement=False, nullable=False),
    sa.Column('display_name', sa.VARCHAR(length=180), autoincrement=False, nullable=False),
    sa.Column('description', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('description_override', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('input_schema_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('output_schema_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('annotations_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('fixed_arguments_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('category', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('risk_level', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('read_only', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('is_enabled', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('last_seen_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('call_count', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('risk_reviewed', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.Column('onboarding_contract_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('onboarding_contract_digest', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('onboarding_fixture_digest', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('onboarding_config_digest', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('onboarding_review_status', sa.VARCHAR(length=32), server_default=sa.text("'not_configured'::character varying"), autoincrement=False, nullable=False),
    sa.Column('onboarding_reviewed_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.PrimaryKeyConstraint('id', name='mcp_tools_pkey'),
    sa.UniqueConstraint('server_id', 'raw_name', name='uq_mcp_tool_server_raw_name', postgresql_include=[], postgresql_nulls_not_distinct=False)
    )
    op.create_index('ix_mcp_tools_raw_name', 'mcp_tools', ['raw_name'], unique=False, postgresql_include=[])
    op.create_index('ix_mcp_tools_server_id', 'mcp_tools', ['server_id'], unique=False, postgresql_include=[])
    op.create_index('ix_mcp_tools_tool_key', 'mcp_tools', ['tool_key'], unique=True, postgresql_include=[])
    op.create_table('skill_installation_revisions',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('skill_key', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('manifest_version', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('manifest_digest', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('manifest_json', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('source_kind', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('security_review_status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('action', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name='skill_installation_revisions_pkey')
    )
    op.create_index('ix_skill_installation_revisions_action', 'skill_installation_revisions', ['action'], unique=False, postgresql_include=[])
    op.create_index('ix_skill_installation_revisions_manifest_digest', 'skill_installation_revisions', ['manifest_digest'], unique=False, postgresql_include=[])
    op.create_index('ix_skill_installation_revisions_manifest_version', 'skill_installation_revisions', ['manifest_version'], unique=False, postgresql_include=[])
    op.create_index('ix_skill_installation_revisions_skill_key', 'skill_installation_revisions', ['skill_key'], unique=False, postgresql_include=[])
    op.create_index('ix_skill_installation_revisions_user_id', 'skill_installation_revisions', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('tool_call_runs',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('route_run_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('call_id', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('tool_key', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('provider', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('category', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('display_name', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('arguments_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('started_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('finished_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('elapsed_ms', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('error_message', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('sources_count', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('sources_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name='tool_call_runs_pkey')
    )
    op.create_index('ix_tool_call_runs_call_id', 'tool_call_runs', ['call_id'], unique=False, postgresql_include=[])
    op.create_index('ix_tool_call_runs_category', 'tool_call_runs', ['category'], unique=False, postgresql_include=[])
    op.create_index('ix_tool_call_runs_provider', 'tool_call_runs', ['provider'], unique=False, postgresql_include=[])
    op.create_index('ix_tool_call_runs_route_run_id', 'tool_call_runs', ['route_run_id'], unique=False, postgresql_include=[])
    op.create_index('ix_tool_call_runs_tool_key', 'tool_call_runs', ['tool_key'], unique=False, postgresql_include=[])
    op.create_table('tool_route_runs',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('conversation_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_message_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('assistant_message_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('router_type', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('query', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('external_context_allowed', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('plan_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('selected_tools_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('events_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('sources_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('elapsed_ms', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name='tool_route_runs_pkey')
    )
    op.create_index('ix_tool_route_runs_assistant_message_id', 'tool_route_runs', ['assistant_message_id'], unique=False, postgresql_include=[])
    op.create_index('ix_tool_route_runs_conversation_id', 'tool_route_runs', ['conversation_id'], unique=False, postgresql_include=[])
    op.create_index('ix_tool_route_runs_user_id', 'tool_route_runs', ['user_id'], unique=False, postgresql_include=[])
    op.create_index('ix_tool_route_runs_user_message_id', 'tool_route_runs', ['user_message_id'], unique=False, postgresql_include=[])
    op.create_table('user_skill_installations',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('skill_key', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('manifest_version', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('is_enabled', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('installed_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('manifest_digest', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.PrimaryKeyConstraint('id', name='user_skill_installations_pkey'),
    sa.UniqueConstraint('user_id', 'skill_key', name='uq_user_skill_installation', postgresql_include=[], postgresql_nulls_not_distinct=False)
    )
    op.create_index('ix_user_skill_installations_skill_key', 'user_skill_installations', ['skill_key'], unique=False, postgresql_include=[])
    op.create_index('ix_user_skill_installations_user_id', 'user_skill_installations', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('user_tool_credentials',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('provider_key', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('credential_name', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('api_key', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('is_enabled', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name='user_tool_credentials_pkey'),
    sa.UniqueConstraint('user_id', 'provider_key', name='uq_user_tool_provider', postgresql_include=[], postgresql_nulls_not_distinct=False)
    )
    op.create_index('ix_user_tool_credentials_provider_key', 'user_tool_credentials', ['provider_key'], unique=False, postgresql_include=[])
    op.create_index('ix_user_tool_credentials_user_id', 'user_tool_credentials', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('users',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('username', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('email', sa.VARCHAR(length=255), autoincrement=False, nullable=True),
    sa.Column('password_hash', sa.VARCHAR(length=255), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name='users_pkey'),
    postgresql_ignore_search_path=False
    )
    op.create_index('ux_users_email_lower', 'users', [sa.literal_column('lower(email::text)')], unique=True, postgresql_include=[])
    op.create_index('ux_users_username_lower', 'users', [sa.literal_column('lower(username::text)')], unique=True, postgresql_include=[])
    op.create_table('workspace_agent_policies',
    sa.Column('project_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('permission_mode', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('project_id', name='workspace_agent_policies_pkey')
    )
    op.create_table('workspace_tool_settings',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('project_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('tool_key', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('is_enabled', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('config_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name='workspace_tool_settings_pkey'),
    sa.UniqueConstraint('project_id', 'tool_key', name='uq_workspace_tool_key', postgresql_include=[], postgresql_nulls_not_distinct=False)
    )
    op.create_index('ix_workspace_tool_settings_project_id', 'workspace_tool_settings', ['project_id'], unique=False, postgresql_include=[])
    op.create_index('ix_workspace_tool_settings_tool_key', 'workspace_tool_settings', ['tool_key'], unique=False, postgresql_include=[])
    op.create_table('conversations',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('title', sa.VARCHAR(length=255), autoincrement=False, nullable=False),
    sa.Column('model_name', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('system_prompt', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('is_archived', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('context_summary', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('context_summary_updated_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('context_summary_boundary_message_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('last_prompt_prefix_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('last_prompt_prefix_token_count', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('is_pinned', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=True),
    sa.Column('project_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('memory_extraction_cursor', sa.INTEGER(), server_default=sa.text('0'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='conversations_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='conversations_pkey')
    )
    op.create_table('projects',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('name', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('description', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('default_model', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('system_prompt', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='projects_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='projects_pkey'),
    postgresql_ignore_search_path=False
    )
    op.create_index('ix_projects_user_id', 'projects', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('prompt_templates',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('name', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('description', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('content', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('default_model', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('is_default', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('project_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('category', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('variables', sa.TEXT(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='prompt_templates_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='prompt_templates_pkey')
    )
    op.create_index('ix_prompt_templates_user_id', 'prompt_templates', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('user_memories',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('memory_type', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('title', sa.VARCHAR(length=120), autoincrement=False, nullable=False),
    sa.Column('content', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('is_enabled', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('source_conversation_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('source_message_ids', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('confidence', sa.VARCHAR(length=16), autoincrement=False, nullable=True),
    sa.Column('status', sa.VARCHAR(length=24), server_default=sa.text("'active'::character varying"), autoincrement=False, nullable=False),
    sa.Column('project_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('importance', sa.DOUBLE_PRECISION(precision=53), server_default=sa.text('0.5'), autoincrement=False, nullable=False),
    sa.Column('sensitivity', sa.VARCHAR(length=24), server_default=sa.text("'normal'::character varying"), autoincrement=False, nullable=False),
    sa.Column('risk_level', sa.VARCHAR(length=32), server_default=sa.text("'safe'::character varying"), autoincrement=False, nullable=False),
    sa.Column('candidate_reason', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('content_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('supersedes_memory_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('expires_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('review_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('fact_key', sa.VARCHAR(length=180), autoincrement=False, nullable=True),
    sa.Column('fact_value', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('version', sa.INTEGER(), server_default=sa.text('1'), autoincrement=False, nullable=False),
    sa.Column('evidence_quote', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('embedding_vector', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('embedding_signature', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('embedding_text_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('extraction_job_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='user_memories_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='user_memories_pkey')
    )
    op.create_index('ix_user_memories_user_id', 'user_memories', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('user_settings',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('default_model', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('ollama_base_url', sa.VARCHAR(length=255), autoincrement=False, nullable=False),
    sa.Column('temperature', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=False),
    sa.Column('top_p', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=False),
    sa.Column('max_tokens', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('system_prompt', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('provider_type', sa.VARCHAR(length=32), server_default=sa.text("'ollama'::character varying"), autoincrement=False, nullable=True),
    sa.Column('api_key', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('model_context_window', sa.INTEGER(), server_default=sa.text('128000'), autoincrement=False, nullable=True),
    sa.Column('context_mode', sa.VARCHAR(length=32), server_default=sa.text("'balanced'::character varying"), autoincrement=False, nullable=True),
    sa.Column('ui_language', sa.VARCHAR(length=16), server_default=sa.text("'zh-CN'::character varying"), autoincrement=False, nullable=True),
    sa.Column('memory_enabled', sa.BOOLEAN(), server_default=sa.text('true'), autoincrement=False, nullable=True),
    sa.Column('memory_max_chars', sa.INTEGER(), server_default=sa.text('4000'), autoincrement=False, nullable=True),
    sa.Column('theme_mode', sa.VARCHAR(length=16), server_default=sa.text("'system'::character varying"), autoincrement=False, nullable=True),
    sa.Column('knowledge_parser_provider', sa.VARCHAR(length=32), server_default=sa.text("'local_basic'::character varying"), autoincrement=False, nullable=True),
    sa.Column('knowledge_embedding_provider', sa.VARCHAR(length=32), server_default=sa.text("'siliconflow'::character varying"), autoincrement=False, nullable=True),
    sa.Column('knowledge_embedding_base_url', sa.VARCHAR(length=255), server_default=sa.text("'https://api.siliconflow.cn/v1'::character varying"), autoincrement=False, nullable=True),
    sa.Column('knowledge_embedding_model', sa.VARCHAR(length=128), server_default=sa.text("'BAAI/bge-m3'::character varying"), autoincrement=False, nullable=True),
    sa.Column('knowledge_embedding_dimensions', sa.INTEGER(), server_default=sa.text('1024'), autoincrement=False, nullable=True),
    sa.Column('knowledge_rerank_enabled', sa.BOOLEAN(), server_default=sa.text('true'), autoincrement=False, nullable=True),
    sa.Column('knowledge_rerank_provider', sa.VARCHAR(length=32), server_default=sa.text("'siliconflow'::character varying"), autoincrement=False, nullable=True),
    sa.Column('knowledge_rerank_base_url', sa.VARCHAR(length=255), server_default=sa.text("'https://api.siliconflow.cn/v1'::character varying"), autoincrement=False, nullable=True),
    sa.Column('knowledge_rerank_model', sa.VARCHAR(length=128), server_default=sa.text("'BAAI/bge-reranker-v2-m3'::character varying"), autoincrement=False, nullable=True),
    sa.Column('knowledge_api_key', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('knowledge_embedding_api_key', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('knowledge_rerank_api_key', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('api_base_url', sa.VARCHAR(length=255), server_default=sa.text("'https://api.siliconflow.cn/v1'::character varying"), autoincrement=False, nullable=True),
    sa.Column('memory_auto_candidate_enabled', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=True),
    sa.Column('memory_auto_candidate_turn_interval', sa.INTEGER(), server_default=sa.text('4'), autoincrement=False, nullable=True),
    sa.Column('memory_auto_activate_enabled', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='user_settings_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='user_settings_pkey'),
    sa.UniqueConstraint('user_id', name='user_settings_user_id_key', postgresql_include=[], postgresql_nulls_not_distinct=False)
    )
    op.create_table('conversation_shares',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('token', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('conversation_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('is_enabled', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('expires_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('revoked_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name='conversation_shares_conversation_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='conversation_shares_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='conversation_shares_pkey')
    )
    op.create_index('ix_conversation_shares_conversation_id', 'conversation_shares', ['conversation_id'], unique=False, postgresql_include=[])
    op.create_index('ix_conversation_shares_token', 'conversation_shares', ['token'], unique=True, postgresql_include=[])
    op.create_index('ix_conversation_shares_user_id', 'conversation_shares', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('knowledge_bases',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('project_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('name', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('description', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('visibility', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('parser_provider', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('chunk_mode', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('chunk_size', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('chunk_overlap', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('chunk_delimiter', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('parent_chunk_size', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('child_chunk_size', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('child_chunk_overlap', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('embedding_provider', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('embedding_model', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('embedding_dimensions', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('rerank_enabled', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('rerank_provider', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('rerank_model', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('retrieval_mode', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('retrieval_top_k', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('rerank_top_n', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('score_threshold', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=False),
    sa.Column('max_context_chunks', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('max_context_chars', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('strict_knowledge_answer', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('active_index_generation', sa.VARCHAR(length=64), server_default=sa.text("'legacy'::character varying"), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='knowledge_bases_project_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='knowledge_bases_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='knowledge_bases_pkey')
    )
    op.create_index('ix_knowledge_bases_project_id', 'knowledge_bases', ['project_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_bases_user_id', 'knowledge_bases', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('memory_extraction_jobs',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('conversation_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('project_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('idempotency_key', sa.VARCHAR(length=192), autoincrement=False, nullable=False),
    sa.Column('source_message_ids', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('status', sa.VARCHAR(length=24), autoincrement=False, nullable=False),
    sa.Column('attempts', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('max_attempts', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('available_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('lease_owner', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('lease_version', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('lease_expires_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('error_code', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('error_message', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('result_count', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('started_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('finished_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('source_snapshot', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('cursor_end', sa.INTEGER(), server_default=sa.text('0'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name='memory_extraction_jobs_conversation_id_fkey'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='memory_extraction_jobs_project_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='memory_extraction_jobs_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='memory_extraction_jobs_pkey')
    )
    op.create_index('ix_memory_extraction_jobs_available_at', 'memory_extraction_jobs', ['available_at'], unique=False, postgresql_include=[])
    op.create_index('ix_memory_extraction_jobs_conversation_id', 'memory_extraction_jobs', ['conversation_id'], unique=False, postgresql_include=[])
    op.create_index('ix_memory_extraction_jobs_idempotency_key', 'memory_extraction_jobs', ['idempotency_key'], unique=True, postgresql_include=[])
    op.create_index('ix_memory_extraction_jobs_lease_expires_at', 'memory_extraction_jobs', ['lease_expires_at'], unique=False, postgresql_include=[])
    op.create_index('ix_memory_extraction_jobs_lease_owner', 'memory_extraction_jobs', ['lease_owner'], unique=False, postgresql_include=[])
    op.create_index('ix_memory_extraction_jobs_project_id', 'memory_extraction_jobs', ['project_id'], unique=False, postgresql_include=[])
    op.create_index('ix_memory_extraction_jobs_status', 'memory_extraction_jobs', ['status'], unique=False, postgresql_include=[])
    op.create_index('ix_memory_extraction_jobs_user_id', 'memory_extraction_jobs', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('messages',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('conversation_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('role', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('content', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('reasoning_content', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('external_sources', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('sequence', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('generation_id', sa.VARCHAR(length=36), server_default=sa.text('md5(((random())::text || (clock_timestamp())::text))'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name='messages_conversation_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='messages_pkey'),
    postgresql_ignore_search_path=False
    )
    op.create_index('ix_messages_conversation_id', 'messages', ['conversation_id'], unique=False, postgresql_include=[])
    op.create_table('project_files',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('project_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('kind', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('file_name', sa.VARCHAR(length=255), autoincrement=False, nullable=False),
    sa.Column('mime_type', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('file_size', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('storage_key', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('parsed_text', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='project_files_project_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='project_files_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='project_files_pkey')
    )
    op.create_index('ix_project_files_project_id', 'project_files', ['project_id'], unique=False, postgresql_include=[])
    op.create_index('ix_project_files_user_id', 'project_files', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('agent_runs',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('project_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('conversation_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('assistant_message_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('input_json', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('planner_state_json', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('idempotency_key', sa.VARCHAR(length=192), autoincrement=False, nullable=False),
    sa.Column('state_version', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('max_steps', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('current_step', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('finished_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('runtime_kind', sa.VARCHAR(length=48), server_default=sa.text("'file_edit'::character varying"), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['assistant_message_id'], ['messages.id'], name='agent_runs_assistant_message_id_fkey'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name='agent_runs_conversation_id_fkey'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='agent_runs_project_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='agent_runs_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='agent_runs_pkey')
    )
    op.create_index('ix_agent_runs_assistant_message_id', 'agent_runs', ['assistant_message_id'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_runs_conversation_id', 'agent_runs', ['conversation_id'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_runs_idempotency_key', 'agent_runs', ['idempotency_key'], unique=True, postgresql_include=[])
    op.create_index('ix_agent_runs_project_id', 'agent_runs', ['project_id'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_runs_status', 'agent_runs', ['status'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_runs_user_id', 'agent_runs', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('attachments',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('message_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('kind', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('file_name', sa.VARCHAR(length=255), autoincrement=False, nullable=False),
    sa.Column('file_ext', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('mime_type', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('file_size', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('storage_path', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('parsed_text', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], name='attachments_message_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='attachments_pkey')
    )
    op.create_index('ix_attachments_message_id', 'attachments', ['message_id'], unique=False, postgresql_include=[])
    op.create_table('chat_runtime_metrics',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('conversation_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('assistant_message_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('provider_type', sa.VARCHAR(length=48), autoincrement=False, nullable=False),
    sa.Column('model_name', sa.VARCHAR(length=160), autoincrement=False, nullable=False),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('stats_json', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['assistant_message_id'], ['messages.id'], name='chat_runtime_metrics_assistant_message_id_fkey', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name='chat_runtime_metrics_conversation_id_fkey', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='chat_runtime_metrics_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='chat_runtime_metrics_pkey')
    )
    op.create_index('ix_chat_runtime_metrics_assistant_message_id', 'chat_runtime_metrics', ['assistant_message_id'], unique=True, postgresql_include=[])
    op.create_index('ix_chat_runtime_metrics_conversation_id', 'chat_runtime_metrics', ['conversation_id'], unique=False, postgresql_include=[])
    op.create_index('ix_chat_runtime_metrics_provider_type', 'chat_runtime_metrics', ['provider_type'], unique=False, postgresql_include=[])
    op.create_index('ix_chat_runtime_metrics_status', 'chat_runtime_metrics', ['status'], unique=False, postgresql_include=[])
    op.create_index('ix_chat_runtime_metrics_user_id', 'chat_runtime_metrics', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('knowledge_documents',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('knowledge_base_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('project_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('file_name', sa.VARCHAR(length=255), autoincrement=False, nullable=False),
    sa.Column('mime_type', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('file_size', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('storage_key', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('parser_provider', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('parse_status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('index_status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('document_version', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('content_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('parsed_markdown_path', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('parsed_assets_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('error_message', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], name='knowledge_documents_knowledge_base_id_fkey'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='knowledge_documents_project_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='knowledge_documents_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='knowledge_documents_pkey')
    )
    op.create_index('ix_knowledge_documents_knowledge_base_id', 'knowledge_documents', ['knowledge_base_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_documents_project_id', 'knowledge_documents', ['project_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_documents_user_id', 'knowledge_documents', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('knowledge_eval_sets',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('knowledge_base_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('name', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('description', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], name='knowledge_eval_sets_knowledge_base_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='knowledge_eval_sets_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='knowledge_eval_sets_pkey')
    )
    op.create_index('ix_knowledge_eval_sets_knowledge_base_id', 'knowledge_eval_sets', ['knowledge_base_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_eval_sets_user_id', 'knowledge_eval_sets', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('knowledge_index_generations',
    sa.Column('id', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('knowledge_base_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('base_generation_id', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('manifest_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('error_message', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('activated_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('job_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('chunk_count', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], name='knowledge_index_generations_knowledge_base_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='knowledge_index_generations_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='knowledge_index_generations_pkey')
    )
    op.create_index('ix_knowledge_index_generations_base_generation_id', 'knowledge_index_generations', ['base_generation_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_index_generations_knowledge_base_id', 'knowledge_index_generations', ['knowledge_base_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_index_generations_status', 'knowledge_index_generations', ['status'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_index_generations_user_id', 'knowledge_index_generations', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('knowledge_retrieval_logs',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('conversation_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('user_message_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('assistant_message_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('knowledge_base_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('query', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('retrieval_mode', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('top_k', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('rerank_enabled', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('rerank_model', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('candidates_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('selected_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('diagnostics_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('sources_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('error_message', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('elapsed_ms', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['assistant_message_id'], ['messages.id'], name='knowledge_retrieval_logs_assistant_message_id_fkey'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name='knowledge_retrieval_logs_conversation_id_fkey'),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], name='knowledge_retrieval_logs_knowledge_base_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='knowledge_retrieval_logs_user_id_fkey'),
    sa.ForeignKeyConstraint(['user_message_id'], ['messages.id'], name='knowledge_retrieval_logs_user_message_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='knowledge_retrieval_logs_pkey')
    )
    op.create_index('ix_knowledge_retrieval_logs_assistant_message_id', 'knowledge_retrieval_logs', ['assistant_message_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_retrieval_logs_conversation_id', 'knowledge_retrieval_logs', ['conversation_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_retrieval_logs_knowledge_base_id', 'knowledge_retrieval_logs', ['knowledge_base_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_retrieval_logs_user_id', 'knowledge_retrieval_logs', ['user_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_retrieval_logs_user_message_id', 'knowledge_retrieval_logs', ['user_message_id'], unique=False, postgresql_include=[])
    op.create_table('agent_checkpoints',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('run_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('step_sequence', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('state_version', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('planner_state_json', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('observations_json', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('remaining_budget_json', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name='agent_checkpoints_run_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='agent_checkpoints_pkey'),
    sa.UniqueConstraint('run_id', 'state_version', name='uq_agent_checkpoint_version', postgresql_include=[], postgresql_nulls_not_distinct=False)
    )
    op.create_index('ix_agent_checkpoints_run_id', 'agent_checkpoints', ['run_id'], unique=False, postgresql_include=[])
    op.create_table('agent_steps',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('run_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('sequence', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('call_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('tool_key', sa.VARCHAR(length=160), autoincrement=False, nullable=False),
    sa.Column('arguments_json', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('arguments_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('result_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('error_code', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('error_message', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('attempts', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('depends_on_json', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('lease_owner', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('lease_version', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('lease_expires_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('started_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('finished_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('result_bindings_json', sa.TEXT(), server_default=sa.text("'[]'::text"), autoincrement=False, nullable=False),
    sa.Column('available_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('max_attempts', sa.INTEGER(), server_default=sa.text('3'), autoincrement=False, nullable=False),
    sa.Column('heartbeat_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('dead_lettered_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name='agent_steps_run_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='agent_steps_pkey'),
    sa.UniqueConstraint('run_id', 'call_id', name='uq_agent_steps_run_call', postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('run_id', 'sequence', name='uq_agent_steps_run_sequence', postgresql_include=[], postgresql_nulls_not_distinct=False)
    )
    op.create_index('ix_agent_steps_arguments_hash', 'agent_steps', ['arguments_hash'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_steps_run_id', 'agent_steps', ['run_id'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_steps_status', 'agent_steps', ['status'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_steps_tool_key', 'agent_steps', ['tool_key'], unique=False, postgresql_include=[])
    op.create_table('knowledge_chunks',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('knowledge_base_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('document_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('chunk_index', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('vector_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('content', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('content_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('char_count', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('token_estimate', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('source_start', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('source_end', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('metadata_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('index_generation', sa.VARCHAR(length=64), server_default=sa.text("'legacy'::character varying"), autoincrement=False, nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(), autoincrement=False, nullable=True),
    sa.Column('embedding_provider', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('embedding_model', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('embedding_dimensions', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('embedding_version', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['document_id'], ['knowledge_documents.id'], name='knowledge_chunks_document_id_fkey'),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], name='knowledge_chunks_knowledge_base_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='knowledge_chunks_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='knowledge_chunks_pkey'),
    postgresql_ignore_search_path=False
    )
    op.create_index('ix_knowledge_chunks_document_id', 'knowledge_chunks', ['document_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_chunks_knowledge_base_id', 'knowledge_chunks', ['knowledge_base_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_chunks_user_id', 'knowledge_chunks', ['user_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_chunks_vector_id', 'knowledge_chunks', ['vector_id'], unique=False, postgresql_include=[])
    op.create_index('uq_knowledge_chunks_generation_vector', 'knowledge_chunks', ['knowledge_base_id', 'index_generation', 'vector_id'], unique=True, postgresql_include=[])
    op.create_table('knowledge_eval_runs',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('knowledge_base_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('eval_set_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('retrieval_mode', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('top_k', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('rerank_enabled', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('metrics_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('error_message', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('started_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('finished_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['eval_set_id'], ['knowledge_eval_sets.id'], name='knowledge_eval_runs_eval_set_id_fkey'),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], name='knowledge_eval_runs_knowledge_base_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='knowledge_eval_runs_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='knowledge_eval_runs_pkey')
    )
    op.create_index('ix_knowledge_eval_runs_eval_set_id', 'knowledge_eval_runs', ['eval_set_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_eval_runs_knowledge_base_id', 'knowledge_eval_runs', ['knowledge_base_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_eval_runs_user_id', 'knowledge_eval_runs', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('knowledge_jobs',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('knowledge_base_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('document_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('job_type', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('payload_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('retry_count', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('error_message', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('started_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('finished_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('error_code', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('idempotency_key', sa.VARCHAR(length=192), autoincrement=False, nullable=True),
    sa.Column('result_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('max_attempts', sa.INTEGER(), server_default=sa.text('3'), autoincrement=False, nullable=False),
    sa.Column('available_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('lease_owner', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('lease_expires_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('lease_version', sa.INTEGER(), server_default=sa.text('0'), autoincrement=False, nullable=False),
    sa.Column('heartbeat_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('dead_lettered_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['document_id'], ['knowledge_documents.id'], name='knowledge_jobs_document_id_fkey'),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], name='knowledge_jobs_knowledge_base_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='knowledge_jobs_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='knowledge_jobs_pkey'),
    postgresql_ignore_search_path=False
    )
    op.create_index('ix_knowledge_jobs_document_id', 'knowledge_jobs', ['document_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_jobs_knowledge_base_id', 'knowledge_jobs', ['knowledge_base_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_jobs_user_id', 'knowledge_jobs', ['user_id'], unique=False, postgresql_include=[])
    op.create_index('ux_knowledge_jobs_idempotency_key', 'knowledge_jobs', ['idempotency_key'], unique=True, postgresql_where='(idempotency_key IS NOT NULL)', postgresql_include=[])
    op.create_table('agent_approvals',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('run_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('step_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('action', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('arguments_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('nonce_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('expires_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('consumed_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('decision_mode', sa.VARCHAR(length=32), server_default=sa.text("'user_confirmation'::character varying"), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name='agent_approvals_run_id_fkey', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['step_id'], ['agent_steps.id'], name='agent_approvals_step_id_fkey', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='agent_approvals_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='agent_approvals_pkey')
    )
    op.create_index('ix_agent_approvals_expires_at', 'agent_approvals', ['expires_at'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_approvals_run_id', 'agent_approvals', ['run_id'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_approvals_status', 'agent_approvals', ['status'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_approvals_step_id', 'agent_approvals', ['step_id'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_approvals_user_id', 'agent_approvals', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('agent_artifacts',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('run_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('step_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('artifact_type', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('content_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('preview', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('content_json', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('char_count', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('prompt_state', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name='agent_artifacts_run_id_fkey', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['step_id'], ['agent_steps.id'], name='agent_artifacts_step_id_fkey', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='agent_artifacts_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='agent_artifacts_pkey')
    )
    op.create_index('ix_agent_artifacts_content_hash', 'agent_artifacts', ['content_hash'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_artifacts_prompt_state', 'agent_artifacts', ['prompt_state'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_artifacts_run_id', 'agent_artifacts', ['run_id'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_artifacts_step_id', 'agent_artifacts', ['step_id'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_artifacts_user_id', 'agent_artifacts', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('agent_outbox_events',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('event_key', sa.VARCHAR(length=192), autoincrement=False, nullable=False),
    sa.Column('run_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('step_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('event_type', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('payload_json', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('attempt_count', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('available_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('lease_owner', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('lease_version', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('lease_expires_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('heartbeat_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('dead_lettered_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('error_code', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('error_message', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name='agent_outbox_events_run_id_fkey', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['step_id'], ['agent_steps.id'], name='agent_outbox_events_step_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='agent_outbox_events_pkey')
    )
    op.create_index('ix_agent_outbox_events_available_at', 'agent_outbox_events', ['available_at'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_outbox_events_event_key', 'agent_outbox_events', ['event_key'], unique=True, postgresql_include=[])
    op.create_index('ix_agent_outbox_events_lease_expires_at', 'agent_outbox_events', ['lease_expires_at'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_outbox_events_lease_owner', 'agent_outbox_events', ['lease_owner'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_outbox_events_run_id', 'agent_outbox_events', ['run_id'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_outbox_events_status', 'agent_outbox_events', ['status'], unique=False, postgresql_include=[])
    op.create_index('ix_agent_outbox_events_step_id', 'agent_outbox_events', ['step_id'], unique=False, postgresql_include=[])
    op.create_table('file_revisions',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('project_file_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('revision_number', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('content_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('parsed_text', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('created_by', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_run_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('source_step_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['project_file_id'], ['project_files.id'], name='file_revisions_project_file_id_fkey', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_run_id'], ['agent_runs.id'], name='file_revisions_source_run_id_fkey'),
    sa.ForeignKeyConstraint(['source_step_id'], ['agent_steps.id'], name='file_revisions_source_step_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='file_revisions_pkey'),
    sa.UniqueConstraint('project_file_id', 'revision_number', name='uq_file_revision_number', postgresql_include=[], postgresql_nulls_not_distinct=False)
    )
    op.create_index('ix_file_revisions_content_hash', 'file_revisions', ['content_hash'], unique=False, postgresql_include=[])
    op.create_index('ix_file_revisions_project_file_id', 'file_revisions', ['project_file_id'], unique=False, postgresql_include=[])
    op.create_table('knowledge_eval_cases',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('knowledge_base_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('eval_set_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('query', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('expected_document_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('expected_chunk_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('expected_answer_keywords_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('difficulty', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('tags_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('expected_document_ids_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('expected_chunk_ids_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['eval_set_id'], ['knowledge_eval_sets.id'], name='knowledge_eval_cases_eval_set_id_fkey'),
    sa.ForeignKeyConstraint(['expected_chunk_id'], ['knowledge_chunks.id'], name='knowledge_eval_cases_expected_chunk_id_fkey', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['expected_document_id'], ['knowledge_documents.id'], name='knowledge_eval_cases_expected_document_id_fkey'),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], name='knowledge_eval_cases_knowledge_base_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='knowledge_eval_cases_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='knowledge_eval_cases_pkey')
    )
    op.create_index('ix_knowledge_eval_cases_eval_set_id', 'knowledge_eval_cases', ['eval_set_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_eval_cases_expected_chunk_id', 'knowledge_eval_cases', ['expected_chunk_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_eval_cases_expected_document_id', 'knowledge_eval_cases', ['expected_document_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_eval_cases_knowledge_base_id', 'knowledge_eval_cases', ['knowledge_base_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_eval_cases_user_id', 'knowledge_eval_cases', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('outbox_events',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('event_key', sa.VARCHAR(length=192), autoincrement=False, nullable=False),
    sa.Column('aggregate_type', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('aggregate_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('event_type', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('payload_json', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('attempt_count', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('available_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('lease_owner', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('lease_expires_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('published_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('error_message', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['aggregate_id'], ['knowledge_jobs.id'], name='outbox_events_aggregate_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='outbox_events_pkey')
    )
    op.create_index('ix_outbox_events_aggregate_id', 'outbox_events', ['aggregate_id'], unique=False, postgresql_include=[])
    op.create_index('ix_outbox_events_available_at', 'outbox_events', ['available_at'], unique=False, postgresql_include=[])
    op.create_index('ix_outbox_events_event_key', 'outbox_events', ['event_key'], unique=True, postgresql_include=[])
    op.create_index('ix_outbox_events_lease_expires_at', 'outbox_events', ['lease_expires_at'], unique=False, postgresql_include=[])
    op.create_index('ix_outbox_events_lease_owner', 'outbox_events', ['lease_owner'], unique=False, postgresql_include=[])
    op.create_index('ix_outbox_events_status', 'outbox_events', ['status'], unique=False, postgresql_include=[])
    op.create_table('knowledge_eval_results',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('knowledge_base_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('run_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('case_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('query', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('retrieved_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('expected_document_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('expected_chunk_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('hit_at_k', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('mrr', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=True),
    sa.Column('context_precision', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=True),
    sa.Column('context_recall', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('ndcg_at_k', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=True),
    sa.Column('expected_keyword_recall', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=True),
    sa.Column('expected_keyword_hits_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('expected_document_ids_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('expected_chunk_ids_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('matched_chunk_ids_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['case_id'], ['knowledge_eval_cases.id'], name='knowledge_eval_results_case_id_fkey'),
    sa.ForeignKeyConstraint(['knowledge_base_id'], ['knowledge_bases.id'], name='knowledge_eval_results_knowledge_base_id_fkey'),
    sa.ForeignKeyConstraint(['run_id'], ['knowledge_eval_runs.id'], name='knowledge_eval_results_run_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='knowledge_eval_results_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='knowledge_eval_results_pkey')
    )
    op.create_index('ix_knowledge_eval_results_case_id', 'knowledge_eval_results', ['case_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_eval_results_knowledge_base_id', 'knowledge_eval_results', ['knowledge_base_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_eval_results_run_id', 'knowledge_eval_results', ['run_id'], unique=False, postgresql_include=[])
    op.create_index('ix_knowledge_eval_results_user_id', 'knowledge_eval_results', ['user_id'], unique=False, postgresql_include=[])
    op.create_table('patch_drafts',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('run_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('step_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('project_file_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('base_revision_id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('base_content_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('old_string', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('new_string', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('proposed_content_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('diff_text', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('arguments_hash', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('expires_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('applied_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['base_revision_id'], ['file_revisions.id'], name='patch_drafts_base_revision_id_fkey'),
    sa.ForeignKeyConstraint(['project_file_id'], ['project_files.id'], name='patch_drafts_project_file_id_fkey', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name='patch_drafts_run_id_fkey', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['step_id'], ['agent_steps.id'], name='patch_drafts_step_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='patch_drafts_pkey')
    )
    op.create_index('ix_patch_drafts_expires_at', 'patch_drafts', ['expires_at'], unique=False, postgresql_include=[])
    op.create_index('ix_patch_drafts_project_file_id', 'patch_drafts', ['project_file_id'], unique=False, postgresql_include=[])
    op.create_index('ix_patch_drafts_run_id', 'patch_drafts', ['run_id'], unique=False, postgresql_include=[])
    op.create_index('ix_patch_drafts_status', 'patch_drafts', ['status'], unique=False, postgresql_include=[])
    op.create_index('ix_patch_drafts_step_id', 'patch_drafts', ['step_id'], unique=True, postgresql_include=[])
    # 本版本变更结束。


def downgrade():
    # 本版本固定记录结构变更，不依赖后续 ORM 模型。
    op.drop_index('ix_patch_drafts_step_id', table_name='patch_drafts', postgresql_include=[])
    op.drop_index('ix_patch_drafts_status', table_name='patch_drafts', postgresql_include=[])
    op.drop_index('ix_patch_drafts_run_id', table_name='patch_drafts', postgresql_include=[])
    op.drop_index('ix_patch_drafts_project_file_id', table_name='patch_drafts', postgresql_include=[])
    op.drop_index('ix_patch_drafts_expires_at', table_name='patch_drafts', postgresql_include=[])
    op.drop_table('patch_drafts')
    op.drop_index('ix_knowledge_eval_results_user_id', table_name='knowledge_eval_results', postgresql_include=[])
    op.drop_index('ix_knowledge_eval_results_run_id', table_name='knowledge_eval_results', postgresql_include=[])
    op.drop_index('ix_knowledge_eval_results_knowledge_base_id', table_name='knowledge_eval_results', postgresql_include=[])
    op.drop_index('ix_knowledge_eval_results_case_id', table_name='knowledge_eval_results', postgresql_include=[])
    op.drop_table('knowledge_eval_results')
    op.drop_index('ix_outbox_events_status', table_name='outbox_events', postgresql_include=[])
    op.drop_index('ix_outbox_events_lease_owner', table_name='outbox_events', postgresql_include=[])
    op.drop_index('ix_outbox_events_lease_expires_at', table_name='outbox_events', postgresql_include=[])
    op.drop_index('ix_outbox_events_event_key', table_name='outbox_events', postgresql_include=[])
    op.drop_index('ix_outbox_events_available_at', table_name='outbox_events', postgresql_include=[])
    op.drop_index('ix_outbox_events_aggregate_id', table_name='outbox_events', postgresql_include=[])
    op.drop_table('outbox_events')
    op.drop_index('ix_knowledge_eval_cases_user_id', table_name='knowledge_eval_cases', postgresql_include=[])
    op.drop_index('ix_knowledge_eval_cases_knowledge_base_id', table_name='knowledge_eval_cases', postgresql_include=[])
    op.drop_index('ix_knowledge_eval_cases_expected_document_id', table_name='knowledge_eval_cases', postgresql_include=[])
    op.drop_index('ix_knowledge_eval_cases_expected_chunk_id', table_name='knowledge_eval_cases', postgresql_include=[])
    op.drop_index('ix_knowledge_eval_cases_eval_set_id', table_name='knowledge_eval_cases', postgresql_include=[])
    op.drop_table('knowledge_eval_cases')
    op.drop_index('ix_file_revisions_project_file_id', table_name='file_revisions', postgresql_include=[])
    op.drop_index('ix_file_revisions_content_hash', table_name='file_revisions', postgresql_include=[])
    op.drop_table('file_revisions')
    op.drop_index('ix_agent_outbox_events_step_id', table_name='agent_outbox_events', postgresql_include=[])
    op.drop_index('ix_agent_outbox_events_status', table_name='agent_outbox_events', postgresql_include=[])
    op.drop_index('ix_agent_outbox_events_run_id', table_name='agent_outbox_events', postgresql_include=[])
    op.drop_index('ix_agent_outbox_events_lease_owner', table_name='agent_outbox_events', postgresql_include=[])
    op.drop_index('ix_agent_outbox_events_lease_expires_at', table_name='agent_outbox_events', postgresql_include=[])
    op.drop_index('ix_agent_outbox_events_event_key', table_name='agent_outbox_events', postgresql_include=[])
    op.drop_index('ix_agent_outbox_events_available_at', table_name='agent_outbox_events', postgresql_include=[])
    op.drop_table('agent_outbox_events')
    op.drop_index('ix_agent_artifacts_user_id', table_name='agent_artifacts', postgresql_include=[])
    op.drop_index('ix_agent_artifacts_step_id', table_name='agent_artifacts', postgresql_include=[])
    op.drop_index('ix_agent_artifacts_run_id', table_name='agent_artifacts', postgresql_include=[])
    op.drop_index('ix_agent_artifacts_prompt_state', table_name='agent_artifacts', postgresql_include=[])
    op.drop_index('ix_agent_artifacts_content_hash', table_name='agent_artifacts', postgresql_include=[])
    op.drop_table('agent_artifacts')
    op.drop_index('ix_agent_approvals_user_id', table_name='agent_approvals', postgresql_include=[])
    op.drop_index('ix_agent_approvals_step_id', table_name='agent_approvals', postgresql_include=[])
    op.drop_index('ix_agent_approvals_status', table_name='agent_approvals', postgresql_include=[])
    op.drop_index('ix_agent_approvals_run_id', table_name='agent_approvals', postgresql_include=[])
    op.drop_index('ix_agent_approvals_expires_at', table_name='agent_approvals', postgresql_include=[])
    op.drop_table('agent_approvals')
    op.drop_index('ux_knowledge_jobs_idempotency_key', table_name='knowledge_jobs', postgresql_where='(idempotency_key IS NOT NULL)', postgresql_include=[])
    op.drop_index('ix_knowledge_jobs_user_id', table_name='knowledge_jobs', postgresql_include=[])
    op.drop_index('ix_knowledge_jobs_knowledge_base_id', table_name='knowledge_jobs', postgresql_include=[])
    op.drop_index('ix_knowledge_jobs_document_id', table_name='knowledge_jobs', postgresql_include=[])
    op.drop_table('knowledge_jobs')
    op.drop_index('ix_knowledge_eval_runs_user_id', table_name='knowledge_eval_runs', postgresql_include=[])
    op.drop_index('ix_knowledge_eval_runs_knowledge_base_id', table_name='knowledge_eval_runs', postgresql_include=[])
    op.drop_index('ix_knowledge_eval_runs_eval_set_id', table_name='knowledge_eval_runs', postgresql_include=[])
    op.drop_table('knowledge_eval_runs')
    op.drop_index('uq_knowledge_chunks_generation_vector', table_name='knowledge_chunks', postgresql_include=[])
    op.drop_index('ix_knowledge_chunks_vector_id', table_name='knowledge_chunks', postgresql_include=[])
    op.drop_index('ix_knowledge_chunks_user_id', table_name='knowledge_chunks', postgresql_include=[])
    op.drop_index('ix_knowledge_chunks_knowledge_base_id', table_name='knowledge_chunks', postgresql_include=[])
    op.drop_index('ix_knowledge_chunks_document_id', table_name='knowledge_chunks', postgresql_include=[])
    op.drop_table('knowledge_chunks')
    op.drop_index('ix_agent_steps_tool_key', table_name='agent_steps', postgresql_include=[])
    op.drop_index('ix_agent_steps_status', table_name='agent_steps', postgresql_include=[])
    op.drop_index('ix_agent_steps_run_id', table_name='agent_steps', postgresql_include=[])
    op.drop_index('ix_agent_steps_arguments_hash', table_name='agent_steps', postgresql_include=[])
    op.drop_table('agent_steps')
    op.drop_index('ix_agent_checkpoints_run_id', table_name='agent_checkpoints', postgresql_include=[])
    op.drop_table('agent_checkpoints')
    op.drop_index('ix_knowledge_retrieval_logs_user_message_id', table_name='knowledge_retrieval_logs', postgresql_include=[])
    op.drop_index('ix_knowledge_retrieval_logs_user_id', table_name='knowledge_retrieval_logs', postgresql_include=[])
    op.drop_index('ix_knowledge_retrieval_logs_knowledge_base_id', table_name='knowledge_retrieval_logs', postgresql_include=[])
    op.drop_index('ix_knowledge_retrieval_logs_conversation_id', table_name='knowledge_retrieval_logs', postgresql_include=[])
    op.drop_index('ix_knowledge_retrieval_logs_assistant_message_id', table_name='knowledge_retrieval_logs', postgresql_include=[])
    op.drop_table('knowledge_retrieval_logs')
    op.drop_index('ix_knowledge_index_generations_user_id', table_name='knowledge_index_generations', postgresql_include=[])
    op.drop_index('ix_knowledge_index_generations_status', table_name='knowledge_index_generations', postgresql_include=[])
    op.drop_index('ix_knowledge_index_generations_knowledge_base_id', table_name='knowledge_index_generations', postgresql_include=[])
    op.drop_index('ix_knowledge_index_generations_base_generation_id', table_name='knowledge_index_generations', postgresql_include=[])
    op.drop_table('knowledge_index_generations')
    op.drop_index('ix_knowledge_eval_sets_user_id', table_name='knowledge_eval_sets', postgresql_include=[])
    op.drop_index('ix_knowledge_eval_sets_knowledge_base_id', table_name='knowledge_eval_sets', postgresql_include=[])
    op.drop_table('knowledge_eval_sets')
    op.drop_index('ix_knowledge_documents_user_id', table_name='knowledge_documents', postgresql_include=[])
    op.drop_index('ix_knowledge_documents_project_id', table_name='knowledge_documents', postgresql_include=[])
    op.drop_index('ix_knowledge_documents_knowledge_base_id', table_name='knowledge_documents', postgresql_include=[])
    op.drop_table('knowledge_documents')
    op.drop_index('ix_chat_runtime_metrics_user_id', table_name='chat_runtime_metrics', postgresql_include=[])
    op.drop_index('ix_chat_runtime_metrics_status', table_name='chat_runtime_metrics', postgresql_include=[])
    op.drop_index('ix_chat_runtime_metrics_provider_type', table_name='chat_runtime_metrics', postgresql_include=[])
    op.drop_index('ix_chat_runtime_metrics_conversation_id', table_name='chat_runtime_metrics', postgresql_include=[])
    op.drop_index('ix_chat_runtime_metrics_assistant_message_id', table_name='chat_runtime_metrics', postgresql_include=[])
    op.drop_table('chat_runtime_metrics')
    op.drop_index('ix_attachments_message_id', table_name='attachments', postgresql_include=[])
    op.drop_table('attachments')
    op.drop_index('ix_agent_runs_user_id', table_name='agent_runs', postgresql_include=[])
    op.drop_index('ix_agent_runs_status', table_name='agent_runs', postgresql_include=[])
    op.drop_index('ix_agent_runs_project_id', table_name='agent_runs', postgresql_include=[])
    op.drop_index('ix_agent_runs_idempotency_key', table_name='agent_runs', postgresql_include=[])
    op.drop_index('ix_agent_runs_conversation_id', table_name='agent_runs', postgresql_include=[])
    op.drop_index('ix_agent_runs_assistant_message_id', table_name='agent_runs', postgresql_include=[])
    op.drop_table('agent_runs')
    op.drop_index('ix_project_files_user_id', table_name='project_files', postgresql_include=[])
    op.drop_index('ix_project_files_project_id', table_name='project_files', postgresql_include=[])
    op.drop_table('project_files')
    op.drop_index('ix_messages_conversation_id', table_name='messages', postgresql_include=[])
    op.drop_table('messages')
    op.drop_index('ix_memory_extraction_jobs_user_id', table_name='memory_extraction_jobs', postgresql_include=[])
    op.drop_index('ix_memory_extraction_jobs_status', table_name='memory_extraction_jobs', postgresql_include=[])
    op.drop_index('ix_memory_extraction_jobs_project_id', table_name='memory_extraction_jobs', postgresql_include=[])
    op.drop_index('ix_memory_extraction_jobs_lease_owner', table_name='memory_extraction_jobs', postgresql_include=[])
    op.drop_index('ix_memory_extraction_jobs_lease_expires_at', table_name='memory_extraction_jobs', postgresql_include=[])
    op.drop_index('ix_memory_extraction_jobs_idempotency_key', table_name='memory_extraction_jobs', postgresql_include=[])
    op.drop_index('ix_memory_extraction_jobs_conversation_id', table_name='memory_extraction_jobs', postgresql_include=[])
    op.drop_index('ix_memory_extraction_jobs_available_at', table_name='memory_extraction_jobs', postgresql_include=[])
    op.drop_table('memory_extraction_jobs')
    op.drop_index('ix_knowledge_bases_user_id', table_name='knowledge_bases', postgresql_include=[])
    op.drop_index('ix_knowledge_bases_project_id', table_name='knowledge_bases', postgresql_include=[])
    op.drop_table('knowledge_bases')
    op.drop_index('ix_conversation_shares_user_id', table_name='conversation_shares', postgresql_include=[])
    op.drop_index('ix_conversation_shares_token', table_name='conversation_shares', postgresql_include=[])
    op.drop_index('ix_conversation_shares_conversation_id', table_name='conversation_shares', postgresql_include=[])
    op.drop_table('conversation_shares')
    op.drop_table('user_settings')
    op.drop_index('ix_user_memories_user_id', table_name='user_memories', postgresql_include=[])
    op.drop_table('user_memories')
    op.drop_index('ix_prompt_templates_user_id', table_name='prompt_templates', postgresql_include=[])
    op.drop_table('prompt_templates')
    op.drop_index('ix_projects_user_id', table_name='projects', postgresql_include=[])
    op.drop_table('projects')
    op.drop_table('conversations')
    op.drop_index('ix_workspace_tool_settings_tool_key', table_name='workspace_tool_settings', postgresql_include=[])
    op.drop_index('ix_workspace_tool_settings_project_id', table_name='workspace_tool_settings', postgresql_include=[])
    op.drop_table('workspace_tool_settings')
    op.drop_table('workspace_agent_policies')
    op.drop_index('ux_users_username_lower', table_name='users', postgresql_include=[])
    op.drop_index('ux_users_email_lower', table_name='users', postgresql_include=[])
    op.drop_table('users')
    op.drop_index('ix_user_tool_credentials_user_id', table_name='user_tool_credentials', postgresql_include=[])
    op.drop_index('ix_user_tool_credentials_provider_key', table_name='user_tool_credentials', postgresql_include=[])
    op.drop_table('user_tool_credentials')
    op.drop_index('ix_user_skill_installations_user_id', table_name='user_skill_installations', postgresql_include=[])
    op.drop_index('ix_user_skill_installations_skill_key', table_name='user_skill_installations', postgresql_include=[])
    op.drop_table('user_skill_installations')
    op.drop_index('ix_tool_route_runs_user_message_id', table_name='tool_route_runs', postgresql_include=[])
    op.drop_index('ix_tool_route_runs_user_id', table_name='tool_route_runs', postgresql_include=[])
    op.drop_index('ix_tool_route_runs_conversation_id', table_name='tool_route_runs', postgresql_include=[])
    op.drop_index('ix_tool_route_runs_assistant_message_id', table_name='tool_route_runs', postgresql_include=[])
    op.drop_table('tool_route_runs')
    op.drop_index('ix_tool_call_runs_tool_key', table_name='tool_call_runs', postgresql_include=[])
    op.drop_index('ix_tool_call_runs_route_run_id', table_name='tool_call_runs', postgresql_include=[])
    op.drop_index('ix_tool_call_runs_provider', table_name='tool_call_runs', postgresql_include=[])
    op.drop_index('ix_tool_call_runs_category', table_name='tool_call_runs', postgresql_include=[])
    op.drop_index('ix_tool_call_runs_call_id', table_name='tool_call_runs', postgresql_include=[])
    op.drop_table('tool_call_runs')
    op.drop_index('ix_skill_installation_revisions_user_id', table_name='skill_installation_revisions', postgresql_include=[])
    op.drop_index('ix_skill_installation_revisions_skill_key', table_name='skill_installation_revisions', postgresql_include=[])
    op.drop_index('ix_skill_installation_revisions_manifest_version', table_name='skill_installation_revisions', postgresql_include=[])
    op.drop_index('ix_skill_installation_revisions_manifest_digest', table_name='skill_installation_revisions', postgresql_include=[])
    op.drop_index('ix_skill_installation_revisions_action', table_name='skill_installation_revisions', postgresql_include=[])
    op.drop_table('skill_installation_revisions')
    op.drop_index('ix_mcp_tools_tool_key', table_name='mcp_tools', postgresql_include=[])
    op.drop_index('ix_mcp_tools_server_id', table_name='mcp_tools', postgresql_include=[])
    op.drop_index('ix_mcp_tools_raw_name', table_name='mcp_tools', postgresql_include=[])
    op.drop_table('mcp_tools')
    op.drop_index('ix_mcp_servers_user_id', table_name='mcp_servers', postgresql_include=[])
    op.drop_index('ix_mcp_servers_server_key', table_name='mcp_servers', postgresql_include=[])
    op.drop_index('ix_mcp_servers_project_id', table_name='mcp_servers', postgresql_include=[])
    op.drop_index('ix_mcp_servers_credential_provider', table_name='mcp_servers', postgresql_include=[])
    op.drop_table('mcp_servers')
    op.drop_table('agent_worker_heartbeats')
    # 本版本变更结束。

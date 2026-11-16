"""真实Prompt治理后，判定、引用与正文必须来自同一份保留证据。"""

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from app.services.context_governance_service import ContextBudgetPlanner, ContextGovernanceService
from app.services.evidence_context import build_evidence_section, final_evidence_sources
from app.services.prompt_builder_service import ContextPromptBuilder
from app.services.tools.completion_verifier import FINAL_COMPLETION_GUIDANCE, verify_final_completion
from app.services.tools.task_spec import build_task_spec
from app.services.tools.schemas import ExternalSource, ExternalContextResult, ToolTraceEvent
from app.services.chat_context_assembly_service import ChatContextAssemblyService
from app.services.tokenizer_service import TokenizerEstimator


def read_source(index=1, body=None):
    return ExternalSource(source_type="workspace_file_read", provider="workspace", title=f"notes{index}.md",
        display_text=body or ("项目采用PostgreSQL，正式保留期限为三十天。\n" * 80), citation_label=f"[T{index}]",
        metadata={"tool_key": "workspace.files.read", "file_id": f"f{index}", "revision_id": "r", "call_id": f"read-{index}"})


def prompt(sources):
    section = build_evidence_section("external_context", sources, intro="工具资料")
    return ContextPromptBuilder().build_chat_messages(messages=[SimpleNamespace(id="q", role="user", content="读取文件内容", attachments=[])],
        system_prompt=None, memory_context=None, context_summary=None, summary_boundary_message_id=None,
        external_context=section.render(), attachment_context=None, provider_type="openai-compatible",
        evidence_guidance=FINAL_COMPLETION_GUIDANCE, evidence_sections={section.layer: section})


class FinalCompletionTest(unittest.TestCase):
    def verdict(self, mapping, reason="completed"):
        return verify_final_completion(spec=build_task_spec(query="读取文件内容"), final_source_map=mapping,
            terminal_reason=reason)

    def test_real_governance_full_excerpt_and_removed_evidence(self):
        built = prompt([read_source()])
        bare = prompt([])
        svc = ContextGovernanceService()
        base = svc._estimate_messages_chars(bare.messages)
        observed = set()
        for extra in (0, 80, 400, 900, 8000):
            budget = replace(ContextBudgetPlanner.build(model_context_window=128000, context_mode="balanced"),
                max_total_chars=base + extra, max_total_tokens=100000)
            governed = ContextGovernanceService(budget).govern_messages(built.messages)
            verdict = self.verdict(governed.final_source_map)
            observed.add(verdict.status)
            self.assertEqual(verdict.assessment_stage, "after_final_prompt_governance")
            self.assertEqual(set(verdict.evidence_refs), set(governed.final_source_map))
            self.assertLessEqual(svc._estimate_messages_chars(governed.messages), base + extra)
            if not governed.final_source_map:
                self.assertEqual(verdict.status, "blocked")
            elif any(s["metadata"]["citation_body_truncated"] for s in governed.final_source_map.values()):
                self.assertEqual(verdict.status, "partial")
                self.assertIn("required_evidence_excerpt_only", verdict.reasons)
            else:
                self.assertEqual(verdict.status, "complete")
            for value in governed.final_source_map.values():
                self.assertTrue(any(value["display_text"] in m["content"] for m in governed.messages))
        self.assertEqual(observed, {"complete", "partial", "blocked"})

    def test_search_and_same_label_cannot_replace_a_removed_read(self):
        search = read_source()
        search.metadata["tool_key"] = "workspace.files.search"
        search.source_type = "workspace_file_search"
        mapping = final_evidence_sources([build_evidence_section("external_context", [search], intro="")])
        self.assertEqual(self.verdict(mapping).status, "partial")
        self.assertFalse(self.verdict(mapping).satisfied)
        self.assertFalse(self.verdict(mapping).evidence_refs)

    def test_excerpt_is_usable_for_citations_but_not_full_diff_completion(self):
        read = read_source(body="已经读取原文。")
        preview = read_source(2, body="完整Diff。")
        preview.source_type = "workspace_file_edit_preview"
        preview.metadata.update(tool_key="workspace.files.propose_edit", file_id="f1", evidence_pretruncated=True)
        mapping = final_evidence_sources([build_evidence_section("external_context", [read, preview], intro="")])
        result = verify_final_completion(spec=build_task_spec(query="读取文件并生成修改预览diff"),
            final_source_map=mapping, terminal_reason="completed")
        self.assertEqual(result.status, "partial")
        self.assertIn("workspace.files.propose_edit", result.missing)
        self.assertIn("[T2]", result.evidence_refs)

    def test_same_tool_has_complete_and_excerpt_sources(self):
        first, second = read_source(body="短而完整的一段证据。"), read_source(2)
        second.metadata["evidence_pretruncated"] = True
        mapping = final_evidence_sources([build_evidence_section("external_context", [first, second], intro="")])
        self.assertEqual(self.verdict(mapping).status, "complete")
        # 当前只验证声明的工具证据；不冒称完成所有目标文件的逐项匹配。
        self.assertFalse(self.verdict(mapping).answer_semantics_verified)

    def test_recheck_updates_trace_details_and_bounded_prompt_without_changing_map(self):
        built = prompt([read_source()])
        svc = ContextGovernanceService(replace(ContextBudgetPlanner.build(model_context_window=128000,
            context_mode="balanced"), max_total_chars=2000, max_total_tokens=100000))
        governed = svc.govern_messages(built.messages)
        old_chars = svc._estimate_messages_chars(governed.messages)
        old_tokens = svc._estimate_messages_tokens(governed.messages)
        before = json.dumps(governed.final_source_map, ensure_ascii=False)
        external = ExternalContextResult(context_text=None, sources=[read_source()], notices=[],
            diagnostics={"external_agent_terminal_reason": "completed", "external_tool_next_action": "stop"},
            details={"tool_task_completion": {"status": "complete"}},
            tool_events=[ToolTraceEvent(type="tool_task_completion", payload={"status": "complete"})])
        assembly = SimpleNamespace(external_context_result=external, governed_context=governed,
            runtime=SimpleNamespace(governance_service=svc), conversation=SimpleNamespace(id="c"),
            assistant_id="a", assistant_generation_id="g", skill_context=None)
        owner = SimpleNamespace(user_id="u", tool_trace_repo=Mock())
        ChatContextAssemblyService._recheck_final_completion(owner, assembly, build_task_spec(query="读取文件内容"))
        self.assertEqual(json.dumps(governed.final_source_map, ensure_ascii=False), before)
        self.assertLessEqual(svc._estimate_messages_chars(governed.messages), old_chars)
        self.assertLessEqual(svc._estimate_messages_tokens(governed.messages), old_tokens)
        self.assertEqual(external.details["tool_task_completion_before_governance"], {"status": "complete"})
        self.assertEqual(external.details["tool_task_completion"]["assessment_stage"], "after_final_prompt_governance")
        self.assertEqual(sum(e.type == "tool_task_completion" for e in external.tool_events), 1)
        owner.tool_trace_repo.update_final_completion.assert_called_once()
        self.assertIn("不得声称任务全部完成", json.dumps(governed.messages, ensure_ascii=False))

    def test_known_file_query_builds_completion_spec_without_skill_contract(self):
        query = "在当前工作区查找并读取 stage46a-readme.md，告诉我文件用途和第一行标题。"
        spec = build_task_spec(query=query)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.required_tools, ("workspace.files.read",))

    def test_invalid_or_empty_fragments_never_count_as_evidence(self):
        source = read_source()
        source.metadata.update(quality_status="invalid", evidence_pretruncated=True)
        mapping = final_evidence_sources([build_evidence_section("external_context", [source], intro="")])
        result = self.verdict(mapping)
        self.assertNotEqual(result.status, "complete")
        self.assertFalse(result.evidence_refs)

    def test_competing_rag_and_tool_evidence_obey_real_token_budget(self):
        tool = build_evidence_section("external_context", [read_source()], intro="工具资料")
        knowledge = read_source(2)
        knowledge.source_type, knowledge.citation_label = "knowledge", "[KB1]"
        knowledge.metadata = {"document_id": "doc", "chunk_id": "chunk"}
        kb = build_evidence_section("knowledge_context", [knowledge], intro="知识资料")
        built = ContextPromptBuilder().build_chat_messages(messages=[SimpleNamespace(id="q", role="user",
            content="读取文件内容", attachments=[])], system_prompt=None, memory_context=None, context_summary=None,
            summary_boundary_message_id=None, external_context=tool.render(), knowledge_context=kb.render(),
            attachment_context=None, provider_type="openai-compatible", evidence_guidance=FINAL_COMPLETION_GUIDANCE,
            evidence_sections={tool.layer: tool, kb.layer: kb})
        tokenizer = TokenizerEstimator(model_name="deepseek-flash")
        base = ContextGovernanceService(tokenizer=tokenizer)._estimate_messages_tokens(prompt([]).messages)
        for allowance in (10, 100, 400, 2000):
            budget = replace(ContextBudgetPlanner.build(model_context_window=128000, context_mode="balanced"),
                max_total_tokens=base+allowance, max_total_chars=100000)
            svc = ContextGovernanceService(budget, tokenizer)
            result = svc.govern_messages(built.messages)
            self.assertLessEqual(result.stats.total_tokens_estimate, base+allowance)
            verdict = self.verdict(result.final_source_map)
            # KB正文不能替代文件工具证据；只按最终[T]正文检查。
            if not any(v["source_type"] == "workspace_file_read" for v in result.final_source_map.values()):
                self.assertNotEqual(verdict.status, "complete")


if __name__ == "__main__":
    unittest.main()

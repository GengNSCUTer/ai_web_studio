"""Agent评测分层统计的确定性测试，不调用模型或工具。"""

import unittest
import io
import sys
import zipfile
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace

from app.services.agent_task_evaluation import (facts_present, inspect_trace, report, summarize, classify,
                                               check_answer, check_chat_evidence, inspect_download, render_html)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from evaluate_agent_tasks import validate_dataset, validate_results
from agent_task_batch_runner import PilotRunner, input_names, unchanged_baselines, execute_batch


class AgentTaskEvaluationTest(unittest.TestCase):
    def test_candidate_and_planner_denominator_are_separate(self):
        trace = inspect_trace(
            [{"type": "tool_candidate_selection", "candidates": [{"tool_key": "amap.maps.weather"}]},
             {"type": "tool_plan", "plan": {"calls": [{"tool_key": "amap.maps.weather"}]}}],
            [{"tool_key": "amap.maps.weather", "status": "success"}],
            [["amap.maps.weather"], ["amap.maps.direction.driving"]],
        )
        self.assertFalse(trace["candidate_complete"])
        self.assertEqual(trace["candidate_group_hits"], 1)
        self.assertEqual(trace["planner_group_hits"], 1)
        payload = report({"name": "x", "version": 1, "target_quotas": {"single_tool": 1}, "annotation": "x", "cases": []}, [
            {"id": "x", "category": "single_tool", "runner": "chat", "status": "failed", "elapsed_ms": 2, "trace": trace}
        ])
        self.assertEqual(payload["summary"]["candidate_tool_group_recall"]["numerator"], 1)
        self.assertIsNone(payload["summary"]["planner_tool_group_coverage_given_candidates"]["value"])

    def test_done_transport_and_tool_success_do_not_force_business_pass(self):
        row = {"id": "x", "category": "single_tool", "runner": "chat", "status": "partial", "elapsed_ms": 10,
               "trace": {"trace_available": True, "candidate_events_available": True, "required_groups": 1,
                         "candidate_group_hits": 1, "candidate_complete": True, "planner_group_hits": 1,
                         "forbidden_candidate_keys": [], "schema_passed": 1, "schema_total": 1,
                         "quality_valid": 0, "quality_total": 1, "call_statuses": {"success": 1}}}
        self.assertEqual(summarize([row])["task_success"]["value"], 0)
        self.assertEqual(summarize([row])["quality_pass"]["value"], 0)

    def test_facts_are_grouped_with_equivalents(self):
        self.assertEqual(facts_present("五月毛利为4,000元，约增加10%", [["4000", "4,000"], ["10%", "10 %"]]), [True, True])

    def test_missing_trace_is_not_zero_quality(self):
        row = {"id": "x", "category": "no_tool", "runner": "chat", "status": "failed", "elapsed_ms": 1,
               "trace": {"trace_available": False, "candidate_events_available": False, "required_groups": 0,
                         "candidate_group_hits": 0, "candidate_complete": False, "planner_group_hits": 0,
                         "forbidden_candidate_keys": [], "schema_passed": 0, "schema_total": 0,
                         "quality_valid": 0, "quality_total": 0, "call_statuses": {}}}
        summary = summarize([row])
        self.assertEqual(summary["missing_candidate_trace_cases"], 1)
        self.assertIsNone(summary["parameter_schema_validity"]["value"])

    def test_waiting_approval_is_not_completed_tool_execution(self):
        trace = inspect_trace([], [{'status': 'waiting_approval'}], [])
        summary = summarize([{'status': 'partial', 'category': 'file_approval', 'runner': 'chat',
                              'trace': {**trace, 'trace_available': True}}])
        self.assertEqual(summary['call_execution_success']['numerator'], 0)
        self.assertEqual(summary['call_execution_success']['denominator'], 1)
        self.assertEqual(summary['call_statuses'], {'waiting_approval': 1})

    def test_business_failure_and_safety_failure_are_distinct(self):
        self.assertEqual(classify({'format': True, 'facts': False}), 'partial')
        self.assertEqual(classify({'format': True, 'safe': False}, ['safe']), 'failed')
        self.assertEqual(classify({}), 'failed')

    def test_fact_numbers_cannot_come_from_citation_labels(self):
        self.assertFalse(check_answer('见[T3]和[KB3]', {'facts': [['3']]})['fact_1'])

    def test_only_final_injected_sources_can_pass_citations(self):
        source = {'citation_label': '[T1]', 'source_type': 'workspace_file_read', 'used_in_prompt': False,
                  'metadata': {'file_id': 'file1', 'citation_scope': 'final_prompt', 'citation_body_chars': 30}}
        expected = {'read_files': ['a.md'], 'citation_required': True}
        checks = check_chat_evidence('事实[T1]', [source], expected, {'a.md': 'file1'})
        self.assertFalse(checks['citations_present_and_legal'])
        self.assertFalse(checks['cited_file:a.md'])
        source['used_in_prompt'] = True
        self.assertTrue(all(check_chat_evidence('事实[T1]', [source], expected, {'a.md': 'file1'}).values()))
        self.assertFalse(check_chat_evidence('事实[T1][T2]', [source], expected, {'a.md': 'file1'})['citations_present_and_legal'])

    def test_parent_child_gold_uses_actual_matched_child_ids(self):
        source = {'citation_label': '[KB1]', 'source_type': 'knowledge', 'used_in_prompt': True,
                  'metadata': {'chunk_id': 'child1', 'matched_child_ids': ['child1', 'child2'],
                               'citation_scope': 'final_prompt', 'citation_body_chars': 90}}
        checks = check_chat_evidence('事实[KB1]', [source], {'knowledge_required': True}, {}, ['child2'])
        self.assertTrue(checks['gold_chunk_cited'])
        self.assertFalse(check_chat_evidence('事实', [source], {'knowledge_required': True}, {}, ['child2'])['gold_chunk_cited'])

    def test_markdown_appendix_does_not_satisfy_body_facts(self):
        artifact = inspect_download('内容没有数字\n\n## 来源\n60个测试'.encode(), 'markdown')
        self.assertNotIn('60', artifact['body'])
        self.assertIn('60', artifact['text'])

    def test_word_format_and_sources_are_inspected(self):
        from docx import Document
        document = Document()
        document.add_paragraph('正文')
        document.add_paragraph('来源')
        document.add_paragraph('11000元')
        data = io.BytesIO()
        document.save(data)
        parsed = inspect_download(data.getvalue(), 'docx')
        self.assertEqual(parsed['body'], '正文')
        self.assertIn('11000', parsed['text'])
        with self.assertRaises((ValueError, zipfile.BadZipFile)):
            inspect_download(b'fake-docx', 'docx')
        with self.assertRaises(ValueError):
            inspect_download(b'', 'markdown')

    def test_bad_dataset_expected_type_is_rejected_without_crash(self):
        self.assertTrue(validate_dataset({'cases': [{'id': 'x', 'category': 'single_tool', 'runner': 'chat',
                                                     'query': 'x', 'expected': ['bad']}]}))

    def test_dataset_id_cannot_escape_artifact_directory(self):
        self.assertTrue(validate_dataset({'cases': [{'id': '../other', 'category': 'no_tool', 'runner': 'chat',
                                                     'query': 'x', 'expected': {'no_tools': True}}]}))

    def test_document_without_export_never_passes(self):
        import httpx
        case = {'id': 'D', 'query': '整理', 'format': 'markdown', 'expected': {'facts': [['4000']]}}
        task = {'id': 'r', 'status': 'failed', 'approvals': [], 'workspace_save_status': 'planned',
                'steps': [{'stage': 'evidence', 'status': 'succeeded'}], 'artifacts': [], 'result_message_id': None}
        transport = httpx.MockTransport(lambda request: httpx.Response(202, json=task))
        db = Mock()
        db.scalars.return_value = []
        with httpx.Client(base_url='http://test', transport=transport) as client:
            runner = PilotRunner(client, 'u', {}, Path('/tmp'), 1)
            with patch('agent_task_batch_runner.SessionLocal') as session, patch.object(runner, 'safety_checks', return_value={
                    'source_files_unchanged': True, 'no_unrequested_approval': True, 'no_unrequested_revision': True}):
                session.return_value.__enter__.return_value = db
                row = runner.run_document(case, 'p', {}, 'c')
        self.assertNotEqual(row['status'], 'passed')
        self.assertFalse(row['checks']['download_valid'])
        self.assertFalse(row['checks']['fact_1'])

    def test_result_ids_and_categories_cannot_silently_change_denominator(self):
        dataset = {'cases': [{'id': 'a', 'runner': 'chat', 'category': 'no_tool'}]}
        row = {'id': 'a', 'runner': 'chat', 'category': 'no_tool', 'status': 'failed'}
        self.assertEqual(validate_results(dataset, [row]), [])
        self.assertTrue(validate_results(dataset, []))
        self.assertTrue(validate_results(dataset, [row, row]))
        self.assertTrue(validate_results(dataset, [{**row, 'id': 'b'}]))

    def test_html_model_output_is_escaped_and_group_metrics_are_present(self):
        payload = report({'name': 'x', 'version': 1, 'annotation': '<img src=x>', 'target_quotas': {'no_tool': 1}, 'cases': [{}]},
                         [{'id': 'x', 'runner': 'chat', 'category': 'no_tool', 'status': 'failed',
                           'answer': '<script>alert(1)</script>'}])
        html = render_html(payload)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('by_runner', html)

    def test_single_tool_fixture_is_resolved_without_search(self):
        self.assertEqual(input_names({'query': '直接读${file:revenue.md}', 'expected': {}}), ['revenue.md'])

    def test_read_only_baseline_is_not_mistaken_for_file_edit(self):
        baseline = SimpleNamespace(project_file_id='f', revision_number=1, created_by='baseline',
                                   source_run_id=None, source_step_id=None, parsed_text='原文')
        self.assertTrue(unchanged_baselines([baseline], {'f': '原文'}))
        self.assertFalse(unchanged_baselines([baseline], {'f': '篡改原文'}))
        self.assertFalse(unchanged_baselines([baseline, baseline], {'f': '原文'}))
        baseline.source_run_id = 'write-run'
        self.assertFalse(unchanged_baselines([baseline], {'f': '原文'}))

    def test_chat_done_with_wrong_fact_remains_partial(self):
        import httpx
        case = {'category': 'no_tool', 'mode': 'quick_chat', 'query': '苹果',
                'expected': {'no_tools': True, 'facts': [['苹果']]}}
        def respond(request):
            if request.method == "POST" and request.url.path == "/api/chat/runs":
                return httpx.Response(202, json={"id": "r"})
            self.assertEqual((request.method, request.url.path), ("GET", "/api/chat/runs/r/events"))
            return httpx.Response(200, text='id: r:1\nevent: chat\ndata: {"type":"done","event_id":1}\n\n',
                                  headers={"content-type": "text/event-stream"})
        transport = httpx.MockTransport(respond)
        snapshot = {'answer': '香蕉', 'assistant_status': 'done', 'source_map': [], 'calls': [], 'events': [], 'runtime_stats': {}}
        with httpx.Client(transport=transport, base_url='http://test') as client:
            runner = PilotRunner(client, 'u', {}, Path('/tmp'), 1)
            with patch.object(runner, 'chat_snapshot', return_value=snapshot), patch.object(runner, 'safety_checks', return_value={
                'source_files_unchanged': True, 'no_unrequested_approval': True, 'no_unrequested_revision': True}):
                row = runner.run_chat(case, 'p', {}, 'c', None)
        self.assertEqual(row['status'], 'partial')
        self.assertTrue(row['checks']['stream_and_database_done'])
        self.assertFalse(row['checks']['fact_1'])

    def test_batch_saves_failure_keeps_all_cases_and_redacts_both_reports(self):
        import httpx
        cases = [{'id': name, 'category': 'no_tool', 'business': '合成', 'runner': 'chat'} for name in ('a', 'b')]
        dataset = {'name': 'test', 'version': 1, 'target_quotas': {'no_tool': 2}, 'annotation': '合成', 'cases': cases}
        fake = Mock()
        fake.run_case.side_effect = [{**case, 'status': state, 'answer': 'API_KEY=not-for-report'}
                                    for case, state in zip(cases, ('failed', 'passed'))]
        fake.cleanup.return_value = {'status': 'cleaned', 'projects': 2}
        client = httpx.Client(base_url='http://test', transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'status': 'ready'})))
        with tempfile.TemporaryDirectory() as tmp, patch('agent_task_batch_runner.credentials', return_value=('u', 'private-token')), \
                patch('agent_task_batch_runner.PilotRunner', return_value=fake), \
                patch('agent_task_batch_runner.httpx.Client', return_value=client):
            path = Path(tmp) / 'report.json'
            result = execute_batch(dataset, base_url='http://test', email='test', output=path,
                                   html_path=path.with_suffix('.html'), timeout=1)
            self.assertEqual(result['summary']['executed'], 2)
            self.assertEqual(result['summary']['statuses'], {'failed': 1, 'passed': 1})
            self.assertEqual(json.loads(path.read_text())['cleanup']['status'], 'cleaned')
            self.assertNotIn('not-for-report', path.read_text())
            self.assertNotIn('not-for-report', path.with_suffix('.html').read_text())
            self.assertNotIn('private-token', path.read_text())
        fake.cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()

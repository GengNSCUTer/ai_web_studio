"""200题覆盖和负例验收器测试；不将本文件计入真实200题任务成功率。"""

import json
from pathlib import Path
import sys
import subprocess
from collections import Counter
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from build_agent_tasks_200 import build
from evaluate_agent_tasks import validate_dataset
from agent_task_fault_runner import run_probe
from agent_task_approval_runner import verify_repeat,draft_matches_target
from app.services.agent_task_evaluation import inspect_trace,summarize


class Agent200DatasetTest(unittest.TestCase):
    def test_business_diff_requires_exact_final_content_not_identical_parameter_granularity(self):
        baseline='项目保留7天\n备注不变。'
        edit={'old':'7天','new':'14天'}
        self.assertTrue(draft_matches_target({'old':'项目保留7天','new':'项目保留14天'},baseline,edit))
        self.assertFalse(draft_matches_target({'old':baseline,'new':'项目保留14天\n备注被改。'},baseline,edit))
        self.assertFalse(draft_matches_target({'old':'','new':'anything'},baseline,edit))
        self.assertFalse(draft_matches_target({'old':'7天','new':'14天'},'7天\n7天',edit))

    def test_pure_approval_checks_import_without_optional_browser_dependency(self):
        script = '''
import builtins, sys
sys.path.insert(0, "scripts")
original = builtins.__import__
def no_browser(name, *args, **kwargs):
    if name == "playwright" or name.startswith("playwright."):
        raise ModuleNotFoundError("browser dependency is intentionally unavailable")
    return original(name, *args, **kwargs)
builtins.__import__ = no_browser
from agent_task_approval_runner import verify_repeat
checks, status = verify_repeat(None, {}, "f", {})
assert checks == {"repeat_confirmation_attempted": False} and status is None
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[2],
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_200_cases_have_the_frozen_category_counts_and_unique_queries(self):
        dataset=build()
        self.assertEqual(validate_dataset(dataset),[])
        self.assertEqual(dict(Counter(c['category'] for c in dataset['cases'])),dataset['target_quotas'])
        self.assertEqual(len({c['id'] for c in dataset['cases']}),200)
        self.assertEqual(len({c['query'] for c in dataset['cases']}),200)

    def test_frozen_file_matches_builder_before_execution(self):
        path=Path(__file__).resolve().parents[1]/'evals/agent_tasks_200_v1.json'
        self.assertEqual(json.loads(path.read_text()),build())

    def test_file_approval_cases_require_real_browser_decisions_and_different_boundaries(self):
        edits=[c for c in build()['cases'] if c['category']=='file_approval']
        self.assertTrue(all(c['runner']=='file_approval' for c in edits))
        self.assertEqual({c['edit']['action'] for c in edits},
                         {'preview','approve','reject','stale','repeat','read_only','ambiguous','missing'})

    def test_rag_has_twenty_each_with_gold_and_not_previous_result_scores(self):
        rag=[c for c in build()['cases'] if c['category']=='knowledge_evidence']
        self.assertEqual(Counter(c['gold_file'].split('_')[0] for c in rag),{'paper':20,'chinese':20})
        self.assertTrue(all(c['expected_claims'] and c['expected']['citation_required'] for c in rag))

    def test_fault_probe_checks_reason_not_just_an_http_error(self):
        case={'id':'F16','probe':'durable_write_rejected'}
        for code,expected in [('unsafe_tool_not_supported',True),('schema_invalid',False)]:
            with self.subTest(code=code):
                client=httpx.Client(base_url='http://test',transport=httpx.MockTransport(
                    lambda request:httpx.Response(409,json={'detail':{'code':code}})))
                runner=SimpleNamespace(client=client,user_id='u',safety_checks=lambda *_:{'unchanged':True})
                db=Mock();db.scalar.return_value=0
                with patch('agent_task_fault_runner.SessionLocal') as session:
                    session.return_value.__enter__.return_value=db
                    result=run_probe(runner,case,'p',{'file.md':'f'},'c')
                client.close()
                self.assertEqual(result['checks']['specific_policy_reason'],expected)
                self.assertEqual(result['status']=='passed',expected)

    def test_correct_policy_refusal_stays_in_task_denominator_not_positive_recall(self):
        trace=inspect_trace([{'type':'tool_candidate_selection','candidates':[]}],[],[['write']])
        trace['capability_metrics_applicable']=False
        result=summarize([{'status':'passed','runner':'file_approval','category':'file_approval','trace':trace}])
        self.assertEqual(result['task_success']['denominator'],1)
        self.assertEqual(result['candidate_tool_group_recall']['denominator'],0)

    def test_repeat_requires_an_actual_idempotent_response_and_unchanged_revision(self):
        after={'approvals':[{'id':'a','status':'consumed'}],'revisions':[{'number':2}]}
        captured={'url':'http://test/approvals/a/apply','body':{'approval_token':'synthetic-placeholder'}}
        request=Mock()
        checks,status=verify_repeat(request,{},'f',after)
        self.assertFalse(checks['repeat_confirmation_attempted'])
        self.assertIsNone(status)
        request.post.assert_not_called()
        for code,body,current,expected in [
            (500,{},after,False),
            (200,{'status':'applied','file_id':'f','approval_id':'wrong'},after,False),
            (200,{'status':'applied','file_id':'f','approval_id':'a'},after,True),
            (200,{'status':'applied','file_id':'f','approval_id':'a'},{**after,'revisions':[{'number':3}]},False),
        ]:
            with self.subTest(code=code,body=body,current=current):
                request.post.return_value=SimpleNamespace(status=code,json=lambda:body)
                with patch('agent_task_approval_runner.snapshot',return_value=current):
                    checks,_=verify_repeat(request,captured,'f',after)
                self.assertEqual(all(checks.values()),expected)


if __name__=='__main__':unittest.main()

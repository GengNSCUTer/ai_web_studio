"""评审失败只能补评固定答案，不能放松评分或替换输入。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.services.knowledge_e2e_evaluation_service import (
    KnowledgeE2EEvaluationConfig, KnowledgeE2EEvaluationService, parse_judge_scores,
)
from scripts.rejudge_rag_answers import restore_sources, summarize_review


VALID = '{"answer_faithfulness":0.2,"answer_relevancy":0.3,"citation_correctness":0.4,"explanation":"不足"}'


class JudgeRepairTests(unittest.TestCase):
    def service(self, responses):
        provider = SimpleNamespace(complete_chat=AsyncMock(side_effect=responses))
        return KnowledgeE2EEvaluationService(retrieval_pipeline=None,chat_provider=provider),provider

    def review(self, service):
        return service.judge_answer(provider_type="openai-compatible",base_url="https://api.siliconflow.cn/v1",
            api_key="test-secret-credential",judge_model_name="Qwen/Qwen2.5-72B-Instruct",query="问题",answer="固定答案",
            citations=["KB1"],sources=[dict(label="KB1",file_name="a.pdf",chunk_index=1,content="原证据")])

    @patch("app.services.knowledge_e2e_evaluation_service.sleep")
    def test_invalid_json_retries_same_input_and_keeps_first_valid_low_score(self, _):
        service,provider = self.service(['{"answer_faithfulness":0.9,"explanation":"\\(x\\)"}', VALID, VALID])
        result = self.review(service)
        self.assertEqual(result["status"],"scored")
        self.assertEqual(result["judge"]["answer_faithfulness"],0.2)
        self.assertEqual(provider.complete_chat.await_count,2)
        calls = provider.complete_chat.await_args_list
        self.assertEqual(calls[0].kwargs["messages"],calls[1].kwargs["messages"])
        self.assertEqual(calls[0].kwargs["response_format"],{"type":"json_object"})
        self.assertEqual(result["judge_attempts"][0]["json_error"]["reason"],"Invalid \\escape")

    @patch("app.services.knowledge_e2e_evaluation_service.sleep")
    def test_missing_fields_exhaust_budget_without_fabricating_scores(self, _):
        service,provider = self.service(['{"faithfulness":0.8}']*3)
        result = self.review(service)
        self.assertEqual(result["status"],"judge_failed")
        self.assertEqual(provider.complete_chat.await_count,3)
        self.assertIsNone(result["judge"]["citation_correctness"])

    @patch("app.services.knowledge_e2e_evaluation_service.sleep")
    def test_timeout_is_retried_without_exposing_secret(self, _):
        service,_provider = self.service([httpx.ReadTimeout("test-secret-credential"), VALID])
        result = self.review(service)
        self.assertEqual(result["status"],"scored")
        self.assertNotIn("test-secret-credential",str(result))
        self.assertEqual(result["judge_attempts"][0]["error_type"],"ReadTimeout")

    def test_permanent_403_is_not_retried(self):
        response = httpx.Response(403,request=httpx.Request("POST","https://provider.test"))
        exc = httpx.HTTPStatusError("denied",request=response.request,response=response)
        service,provider = self.service([exc,VALID])
        result = self.review(service)
        self.assertEqual(result["status"],"judge_failed")
        self.assertEqual(provider.complete_chat.await_count,1)

    @patch("app.services.knowledge_e2e_evaluation_service.sleep")
    def test_503_http_response_is_retried(self, _):
        response = httpx.Response(503,request=httpx.Request("POST","https://provider.test"))
        exc = httpx.HTTPStatusError("temporary",request=response.request,response=response)
        service,provider = self.service([exc,VALID])
        result = self.review(service)
        self.assertEqual(result["status"],"scored")
        self.assertEqual(provider.complete_chat.await_count,2)

    @patch("app.services.knowledge_e2e_evaluation_service.sleep")
    def test_rate_limit_honors_retry_after_instead_of_tight_loop(self, wait):
        response = httpx.Response(429,headers={"retry-after":"20"},request=httpx.Request("POST","https://provider.test"))
        exc = httpx.HTTPStatusError("limited",request=response.request,response=response)
        service,provider = self.service([exc,VALID])
        result = self.review(service)
        self.assertEqual(result["status"],"scored")
        self.assertEqual(provider.complete_chat.await_count,2)
        wait.assert_called_once_with(20)

    @patch("app.services.knowledge_e2e_evaluation_service.sleep")
    def test_long_rate_limit_is_deferred_instead_of_retried_too_early(self, wait):
        response = httpx.Response(429,headers={"retry-after":"120"},request=httpx.Request("POST","https://provider.test"))
        exc = httpx.HTTPStatusError("limited",request=response.request,response=response)
        service,provider = self.service([exc,VALID])
        result = self.review(service)
        self.assertEqual(result["status"],"judge_failed")
        self.assertEqual(provider.complete_chat.await_count,1)
        self.assertEqual(result["judge_attempts"][0]["deferred_retry_after_seconds"],120)
        wait.assert_not_called()

    def test_boolean_and_truncated_json_are_not_scores(self):
        self.assertEqual(parse_judge_scores('{"answer_faithfulness":true,"answer_relevancy":1,"citation_correctness":1}')["status"],"invalid_response")
        self.assertEqual(parse_judge_scores(VALID[:-1])["status"],"invalid_response")
        with self.assertRaises(ValueError):
            KnowledgeE2EEvaluationConfig(judge_max_attempts=10)

    def test_unknown_provider_keeps_prompt_json_without_unsupported_parameter(self):
        service,provider = self.service([VALID])
        result = service.judge_answer(provider_type="openai-compatible",base_url="https://unknown.test/v1",
            api_key=None,judge_model_name="custom",query="问题",answer="答案",citations=[],sources=[])
        self.assertEqual(result["status"],"scored")
        self.assertNotIn("response_format",provider.complete_chat.await_args.kwargs)
        self.assertFalse(result["judge_attempts"][0]["json_mode"])

    @patch("app.services.knowledge_e2e_evaluation_service.sleep")
    def test_slow_judge_has_bounded_wait_and_does_not_become_score(self, _):
        async def slow(**kwargs):
            await asyncio.sleep(1)
            return VALID
        service,_provider = self.service([])
        service.chat_provider.complete_chat = slow
        result = service.judge_answer(provider_type="openai-compatible",base_url="https://provider.test/v1",
            api_key=None,judge_model_name="judge",query="问题",answer="答案",citations=[],sources=[],
            config=KnowledgeE2EEvaluationConfig(judge_timeout_seconds=0.001,judge_max_attempts=2))
        self.assertEqual(result["status"],"judge_failed")
        self.assertEqual(len(result["judge_attempts"]),2)
        self.assertIsNone(result["judge"])

    def test_reconstructed_sources_keep_parent_text_order_and_validate_scope(self):
        chunk = SimpleNamespace(user_id="u",knowledge_base_id="b",document_id="d",chunk_index=2,
            index_generation="g",metadata_json='{"parent_content":"父段完整内容"}',content="子段")
        row = {"retrieved":[dict(label="KB1",chunk_id="c",document_id="d",chunk_index=2,
                                file_name="a.pdf",content_preview="父段完整内容")]}
        def restore():return restore_sources(row,load_chunk=lambda _:chunk,user_id="u",base_id="b",generation="g")
        self.assertEqual(restore()[0]["content"],"父段完整内容")
        row["retrieved"][0]["content"] = "偷偷更换的证据"
        with self.assertRaises(ValueError):restore()
        row["retrieved"][0].pop("content")
        chunk.user_id = "other"
        with self.assertRaises(ValueError):restore()

    def test_failed_review_stays_in_denominator(self):
        scored = dict(case_id="a",status="scored",answerable=True,question_type="fact",
                      judge=dict(answer_faithfulness=0.2,answer_relevancy=0.3,citation_correctness=0.4))
        failed = dict(case_id="b",status="judge_failed",answerable=True,question_type="fact",judge=None)
        metrics = summarize_review(dict(case_count=2,cases=[scored,failed],metrics={}))
        self.assertEqual(metrics["scored_case_count"],1)
        self.assertEqual(metrics["judge_failure_count"],1)
        self.assertEqual(metrics["answer_faithfulness_all_cases"],0.1)


if __name__ == "__main__":
    unittest.main()

"""脚本导入复用正式队列，轮询网络等待前必须释放数据库事务。"""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.knowledge_import_jobs import enqueue_and_wait, wait_for_job


class KnowledgeImportJobsTest(unittest.TestCase):
    def test_wait_releases_transaction_before_sleep_and_reads_fresh_terminal(self):
        db = Mock()
        db.scalar.side_effect = [SimpleNamespace(status="pending", result_json=None),
            SimpleNamespace(status="succeeded", result_json='{"chunk_count": 3}')]
        with patch("scripts.knowledge_import_jobs.time.sleep") as sleep:
            sleep.side_effect = lambda _: self.assertEqual(db.commit.call_count, 1)
            result = wait_for_job(db, job_id="job", user_id="user")
        self.assertEqual(result["chunk_count"], 3)
        self.assertEqual(db.commit.call_count, 2)
        self.assertIn("user_id", str(db.scalar.call_args.args[0]))

    def test_failure_and_timeout_do_not_run_alternate_indexer(self):
        for status, error in (("failed", RuntimeError), ("dead_letter", RuntimeError), ("running", TimeoutError)):
            with self.subTest(status=status):
                db = Mock()
                db.scalar.return_value = SimpleNamespace(status=status, result_json=None)
                with self.assertRaises(error):
                    wait_for_job(db, job_id="job", user_id="user", timeout=0)
                db.commit.assert_called_once()

    def test_parser_failure_does_not_enqueue_index(self):
        service, db = Mock(), Mock()
        service.enqueue_parse_document.return_value.job.id = "parse-id"
        with patch("scripts.knowledge_import_jobs.wait_for_job", side_effect=RuntimeError("解析失败")):
            with self.assertRaises(RuntimeError):
                enqueue_and_wait(service, db, knowledge_base_id="kb", document_id="doc", user_id="user")
        service.enqueue_index_document.assert_not_called()

    def test_two_stages_use_account_scoped_jobs(self):
        service, db = Mock(), Mock()
        service.enqueue_parse_document.return_value.job.id = "parse-id"
        service.enqueue_index_document.return_value.job.id = "index-id"
        with patch("scripts.knowledge_import_jobs.wait_for_job", side_effect=[{}, {"chunk_count": 4}]) as wait:
            result = enqueue_and_wait(service, db, knowledge_base_id="kb", document_id="doc", user_id="user")
        self.assertEqual(result["chunk_count"], 4)
        self.assertEqual([c.kwargs["job_id"] for c in wait.call_args_list], ["parse-id", "index-id"])
        self.assertTrue(all(c.kwargs["user_id"] == "user" for c in wait.call_args_list))


if __name__ == "__main__":
    unittest.main()

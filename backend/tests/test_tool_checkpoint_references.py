"""检查点引用确实节省重复正文，且无法越界、串用户或接受篡改。"""

import json
import unittest

from app.services.chat_tool_snapshot import ChatToolSnapshotCodec
from app.services.tools import runtime_codec as codec
from app.services.tools.schemas import ExternalSource, ToolCallResult, PlannedToolCall
from app.services.tool_invocation_runtime import InvocationRuntimeError, ToolInvocationRuntime
from backend.tests import test_tool_invocation_runtime as shared


class ToolCheckpointReferencesTest(unittest.TestCase):
    setUp = shared.InvocationJournalTest.setUp
    tearDown = shared.InvocationJournalTest.tearDown
    _enqueue = shared.InvocationJournalTest._enqueue
    fixture = shared.InvocationJournalTest.fixture

    def result_fixture(self, db):
        run, step = self.fixture(db)
        source = ExternalSource("test", "test", "授权资料", "足够长的业务正文，必须完整恢复。" * 1000,
            metadata={"call_id": "a", "raw": {"content": "原始结构化内容" * 1000}})
        call = PlannedToolCall("a", step.tool_key, "workspace", "test", "test", 1, "test")
        result = ToolCallResult(call, "success", [source], 1, quality_status="valid")
        artifact = ToolInvocationRuntime.create_artifact(db, run=run, step=step, content_json=codec.dumps((result, [])),
            artifact_type="chat_tool_result")
        step.result_json = json.dumps({"artifact_id": artifact.id})
        step.status = "succeeded"
        db.commit()
        return run, step, artifact, result

    def test_roundtrip_uses_committed_artifact_and_reduces_repeated_content(self):
        with self.SessionLocal() as db:
            run, _, _, result = self.result_fixture(db)
            saved = {"result": result, "sources": result.sources,
                "observations": [{"text": result.sources[0].display_text}], "budget": {"attempts": 1}}
            encoded, stats = ChatToolSnapshotCodec(db, run).dumps(saved)
            self.assertGreater(stats["reference_count"], 0)
            self.assertLess(stats["stored_bytes"], stats["unreferenced_bytes"] / 4)
            restored = ChatToolSnapshotCodec(db, run).loads(encoded)
            self.assertEqual(restored, saved)
            self.assertEqual(restored["budget"], {"attempts": 1})

    def test_artifact_tampering_prevents_restore(self):
        with self.SessionLocal() as db:
            run, _, artifact, result = self.result_fixture(db)
            encoded, _ = ChatToolSnapshotCodec(db, run).dumps({"result": result})
            artifact.content_json = codec.dumps((result, ["篡改内容"]))
            db.commit()
            with self.assertRaisesRegex(InvocationRuntimeError, "校验失败"):
                ChatToolSnapshotCodec(db, run).loads(encoded)

    def test_deleted_artifact_is_not_silently_treated_as_empty_result(self):
        with self.SessionLocal() as db:
            run, _, artifact, result = self.result_fixture(db)
            encoded, _ = ChatToolSnapshotCodec(db, run).dumps({"result": result})
            db.delete(artifact)
            db.commit()
            with self.assertRaisesRegex(InvocationRuntimeError, "不存在"):
                ChatToolSnapshotCodec(db, run).loads(encoded)

    def test_external_reference_shaped_json_remains_plain_data(self):
        with self.SessionLocal() as db:
            run, _, _, _ = self.result_fixture(db)
            data = {"metadata": {"$artifact_ref": {"id": "foreign", "path": [], "hash": "fake"}}}
            encoded, _ = ChatToolSnapshotCodec(db, run).dumps(data)
            self.assertEqual(ChatToolSnapshotCodec(db, run).loads(encoded), data)

    def test_reference_from_other_run_is_rejected(self):
        with self.SessionLocal() as db:
            run, _, artifact, result = self.result_fixture(db)
            encoded, _ = ChatToolSnapshotCodec(db, run).dumps({"result": result})
            # 恢复另一条业务Run，不能凭已知Artifact ID读取原Run内容。
            from types import SimpleNamespace
            other = SimpleNamespace(id="other-run", user_id=run.user_id, project_id=run.project_id)
            with self.assertRaisesRegex(InvocationRuntimeError, "无权读取"):
                ChatToolSnapshotCodec(db, other).loads(encoded)

    def test_old_snapshot_is_readable_and_unknown_format_fails(self):
        with self.SessionLocal() as db:
            run, _ = self.fixture(db)
            data = {"next_node": "execute", "wall_deadline": "preserved", "observations": []}
            self.assertEqual(ChatToolSnapshotCodec(db, run).loads(codec.dumps(data)), data)
            with self.assertRaises(ValueError):
                ChatToolSnapshotCodec(db, run).loads(json.dumps({"format": "unknown", "payload": {}}))

    def test_reference_to_unlinked_artifact_is_rejected(self):
        with self.SessionLocal() as db:
            run, step, _, result = self.result_fixture(db)
            encoded, _ = ChatToolSnapshotCodec(db, run).dumps({"result": result})
            step.result_json = "{}"
            db.commit()
            with self.assertRaisesRegex(InvocationRuntimeError, "提交记录"):
                ChatToolSnapshotCodec(db, run).loads(encoded)


if __name__ == "__main__":
    unittest.main()

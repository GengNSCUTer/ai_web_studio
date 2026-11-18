"""检查点引用确实节省重复正文，且无法越界、串用户或接受篡改。"""

import json
import unittest

from app.services.chat_tool_snapshot import ChatToolSnapshotCodec, CHANNEL_FIELDS, CONTROL_FIELDS, FORMAT, dump_json
from app.services.tools import runtime_codec as codec
from app.services.tools.schemas import ExternalSource, ToolCallResult, PlannedToolCall
from app.services.tool_invocation_runtime import InvocationRuntimeError, ToolInvocationRuntime
from backend.tests import test_tool_invocation_runtime as shared


class ToolCheckpointReferencesTest(unittest.TestCase):
    setUp = shared.InvocationJournalTest.setUp
    tearDown = shared.InvocationJournalTest.tearDown
    _enqueue = shared.InvocationJournalTest._enqueue
    fixture = shared.InvocationJournalTest.fixture

    @staticmethod
    def state_fixture(*, result=None, **values):
        values.setdefault("workflow_result", result)
        return {"loop": {name: values.get(name) for names in CHANNEL_FIELDS.values()
                         for name in names if name not in CONTROL_FIELDS},
                **{name: values.get(name) for name in CONTROL_FIELDS}}

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
            saved = self.state_fixture(result=result, sources=result.sources,
                observations=[{"text": result.sources[0].display_text}], budget={"attempts": 1})
            encoded = ChatToolSnapshotCodec(db, run).pack(saved)
            self.assertIn("$artifact_ref", dump_json(encoded["evidence_state"]))
            self.assertLess(len(dump_json(encoded)), len(codec.dumps(saved)) / 4)
            restored = ChatToolSnapshotCodec(db, run).unpack(encoded)
            self.assertEqual(restored, saved)
            self.assertEqual(restored["budget"], {"attempts": 1})
            self.assertEqual(ChatToolSnapshotCodec(db, run).unpack(encoded), saved)

    def test_artifact_tampering_prevents_restore(self):
        with self.SessionLocal() as db:
            run, _, artifact, result = self.result_fixture(db)
            encoded = ChatToolSnapshotCodec(db, run).pack(self.state_fixture(result=result))
            artifact.content_json = codec.dumps((result, ["篡改内容"]))
            db.commit()
            with self.assertRaisesRegex(InvocationRuntimeError, "校验失败"):
                ChatToolSnapshotCodec(db, run).unpack(encoded)

    def test_deleted_artifact_is_not_silently_treated_as_empty_result(self):
        with self.SessionLocal() as db:
            run, _, artifact, result = self.result_fixture(db)
            encoded = ChatToolSnapshotCodec(db, run).pack(self.state_fixture(result=result))
            db.delete(artifact)
            db.commit()
            with self.assertRaisesRegex(InvocationRuntimeError, "不存在"):
                ChatToolSnapshotCodec(db, run).unpack(encoded)

    def test_external_reference_shaped_json_remains_plain_data(self):
        with self.SessionLocal() as db:
            run, _, _, _ = self.result_fixture(db)
            data = self.state_fixture(observations=[{"$artifact_ref": {"id": "foreign", "path": [], "hash": "fake"}}])
            encoded = ChatToolSnapshotCodec(db, run).pack(data)
            self.assertEqual(ChatToolSnapshotCodec(db, run).unpack(encoded), data)

    def test_reference_from_other_run_is_rejected(self):
        with self.SessionLocal() as db:
            run, _, artifact, result = self.result_fixture(db)
            encoded = ChatToolSnapshotCodec(db, run).pack(self.state_fixture(result=result))
            # 恢复另一条业务Run，不能凭已知Artifact ID读取原Run内容。
            from types import SimpleNamespace
            other = SimpleNamespace(id="other-run", user_id=run.user_id, project_id=run.project_id)
            with self.assertRaisesRegex(InvocationRuntimeError, "无权读取"):
                ChatToolSnapshotCodec(db, other).unpack(encoded)

    def test_old_snapshot_and_unknown_format_are_rejected(self):
        with self.SessionLocal() as db:
            run, _ = self.fixture(db)
            data = {"next_node": "execute", "wall_deadline": "preserved", "observations": []}
            with self.assertRaises(InvocationRuntimeError):
                ChatToolSnapshotCodec(db, run).unpack(codec.dumps(data))
            with self.assertRaises(InvocationRuntimeError):
                ChatToolSnapshotCodec(db, run).unpack(json.dumps({"format": "unknown", "payload": {}}))

    def test_legacy_envelope_and_structured_incomplete_state(self):
        with self.SessionLocal() as db:
            run, _ = self.fixture(db)
            data = self.state_fixture(observations=[{"kept": "正文"}])
            serializer = ChatToolSnapshotCodec(db, run)
            old_state = dump_json({"format": FORMAT, "payload": codec.encode(data)})
            self.assertEqual(serializer.unpack(old_state), data)
            new_state = serializer.pack(data)
            del new_state["plan_state"]
            with self.assertRaisesRegex(InvocationRuntimeError, "缺少恢复字段"):
                serializer.unpack(new_state)

    def test_reference_to_unlinked_artifact_is_rejected(self):
        with self.SessionLocal() as db:
            run, step, _, result = self.result_fixture(db)
            encoded = ChatToolSnapshotCodec(db, run).pack(self.state_fixture(result=result))
            step.result_json = "{}"
            db.commit()
            with self.assertRaisesRegex(InvocationRuntimeError, "提交记录"):
                ChatToolSnapshotCodec(db, run).unpack(encoded)


if __name__ == "__main__":
    unittest.main()

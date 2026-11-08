"""业务检查点引用已提交工具产物；保留无法重构的规划/预算/反馈。

引用仅出现在内部类型编码的值节点。外部JSON的同名键仍由$dict包裹，
不能被当成恢复引用。所有路径都局限在同用户同Run的已关联工具产物内。
"""

import hashlib
import json
import time

from sqlalchemy import select

from app.models.agent_runtime import AgentArtifact, AgentStep
from app.services.tool_invocation_runtime import InvocationRuntimeError, ToolInvocationRuntime
from app.services.tools import runtime_codec as codec


FORMAT = "chat_tool_artifact_refs_v1"
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024


def dump_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(dump_json(value).encode("utf-8")).hexdigest()


def children(value):
    """只遍历编码后的值节点，不把外部映射的容器误当内部引用。"""
    if isinstance(value, list):
        return [(index, child) for index, child in enumerate(value)]
    if not isinstance(value, dict):
        return []
    if set(value) == {"$dict"}:
        return [(("$dict", key), child) for key, child in value["$dict"].items()]
    if set(value) == {"$tuple"}:
        return [(("$tuple", index), child) for index, child in enumerate(value["$tuple"])]
    if set(value) == {"$type", "fields"}:
        return [(("fields", key), child) for key, child in value["fields"].items()]
    raise InvocationRuntimeError("checkpoint_format_invalid", "检查点包含无法识别的编码节点。")


def replace_child(container, key, value):
    if isinstance(key, tuple):
        container[key[0]][key[1]] = value
    else:
        container[key] = value


class ChatToolSnapshotCodec:
    def __init__(self, db, run):
        self.db, self.run = db, run
        self.artifacts = {}

    def _artifact(self, artifact_id):
        if artifact_id in self.artifacts:
            return self.artifacts[artifact_id]
        row = self.db.execute(select(AgentArtifact, AgentStep).join(AgentStep, AgentStep.id == AgentArtifact.step_id)
            .where(AgentArtifact.id == artifact_id, AgentArtifact.run_id == self.run.id,
                AgentArtifact.user_id == self.run.user_id, AgentArtifact.artifact_type == "chat_tool_result",
                AgentStep.run_id == self.run.id, AgentStep.sequence > 0)).first()
        if not row:
            raise InvocationRuntimeError("checkpoint_artifact_missing", "检查点引用的工具产物不存在或无权读取。")
        artifact, step = row
        ToolInvocationRuntime.validate_artifact(self.run, step, artifact)
        ToolInvocationRuntime.verify_artifact_content(artifact)
        if json.loads(step.result_json or "{}").get("artifact_id") != artifact.id:
            raise InvocationRuntimeError("checkpoint_artifact_unlinked", "检查点产物没有对应的步骤提交记录。")
        self.artifacts[artifact_id] = json.loads(artifact.content_json)
        return self.artifacts[artifact_id]

    def dumps(self, saved):
        started = time.perf_counter()
        encoded = codec.encode(codec.sanitize(saved))
        original_bytes = len(dump_json(encoded).encode())
        if original_bytes > MAX_SNAPSHOT_BYTES:
            raise InvocationRuntimeError("checkpoint_too_large", "重建后的工具恢复记录超过大小上限。")
        index = {}
        ids = list(self.db.scalars(select(AgentArtifact.id).where(AgentArtifact.run_id == self.run.id,
            AgentArtifact.user_id == self.run.user_id, AgentArtifact.artifact_type == "chat_tool_result").limit(129)))
        if len(ids) > 128:
            raise InvocationRuntimeError("checkpoint_artifact_limit", "运行产物超过检查点有界读取上限。")

        def register(value, artifact_id, path):
            if len(path) > 64:
                return
            # 短标识/计数直接存，比引用更小；大段正文/结果才引用。
            packed = dump_json(value)
            if len(packed) >= 256:
                digest = hashlib.sha256(packed.encode()).hexdigest()
                index.setdefault(digest, {"id": artifact_id, "path": path, "hash": digest})
            for key, child in children(value):
                register(child, artifact_id, path + (list(key) if isinstance(key, tuple) else [key]))

        for artifact_id in ids:
            register(self._artifact(artifact_id), artifact_id, [])
        references = 0
        referenced_ids = set()

        def compact(value):
            nonlocal references
            packed = dump_json(value)
            reference = index.get(hashlib.sha256(packed.encode()).hexdigest()) if len(packed) >= 256 else None
            if reference:
                references += 1
                referenced_ids.add(reference["id"])
                return {"$artifact_ref": reference}
            for key, child in children(value):
                replace_child(value, key, compact(child))
            return value

        payload = compact(encoded)
        metrics = {"unreferenced_bytes": original_bytes, "reference_count": references,
            "referenced_artifacts": len(referenced_ids), "loaded_artifacts": len(self.artifacts),
            "encode_ms": round((time.perf_counter() - started) * 1000, 3)}
        encoded = dump_json({"format": FORMAT, "payload": payload, "metrics": metrics})
        metrics["stored_bytes"] = len(encoded.encode())
        return encoded, metrics

    def loads(self, text):
        if len(text.encode()) > MAX_SNAPSHOT_BYTES:
            raise InvocationRuntimeError("checkpoint_too_large", "工具恢复记录超过大小上限。")
        envelope = json.loads(text)
        if not isinstance(envelope, dict) or envelope.get("format") != FORMAT:
            # 旧类型快照继续可读，重新保存时自然迁移为引用格式。
            return codec.loads(text)
        count = 0
        expanded_bytes = 0

        def expand(value):
            nonlocal count, expanded_bytes
            if isinstance(value, dict) and set(value) == {"$artifact_ref"}:
                count += 1
                if count > 4096:
                    raise InvocationRuntimeError("checkpoint_reference_limit", "检查点引用数量超过上限。")
                ref = value["$artifact_ref"]
                if (not isinstance(ref, dict) or set(ref) != {"id", "path", "hash"}
                        or not isinstance(ref["id"], str) or not isinstance(ref["path"], list) or len(ref["path"]) > 64):
                    raise InvocationRuntimeError("checkpoint_reference_invalid", "检查点引用格式不合法。")
                target = self._artifact(ref["id"])
                try:
                    for key in ref["path"]:
                        if type(key) not in (str, int) or (type(key) is int and key < 0):
                            raise KeyError()
                        target = target[key]
                except (KeyError, IndexError, TypeError):
                    raise InvocationRuntimeError("checkpoint_reference_invalid", "检查点引用路径不存在。") from None
                if fingerprint(target) != ref["hash"]:
                    raise InvocationRuntimeError("checkpoint_reference_changed", "检查点引用内容与原结果不一致。")
                expanded_bytes += len(dump_json(target).encode())
                if expanded_bytes > MAX_SNAPSHOT_BYTES:
                    raise InvocationRuntimeError("checkpoint_too_large", "恢复引用展开超过大小上限。")
                # 解码得到独立对象，恢复时预算/引用标签的变化不会修改缓存的产物。
                return target
            if value is None or type(value) in (str, int, float, bool):
                expanded_bytes += len(dump_json(value).encode())
                if expanded_bytes > MAX_SNAPSHOT_BYTES:
                    raise InvocationRuntimeError("checkpoint_too_large", "恢复内容超过大小上限。")
            for key, child in children(value):
                replace_child(value, key, expand(child))
            return value

        return codec.decode(expand(envelope["payload"]))

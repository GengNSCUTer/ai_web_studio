"""在线API四类Chat、重生成/编辑、断连和持久化图阶段验收。

固定账号，复用只读论文库；仅创建本脚本临时会话/文件，不输出凭据或模型原始异常。
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def run_browser(output: Path):
    """复用真实发送/刷新页面场景，同时核实持久化的是本轮Chat图阶段。"""
    import stage46a_real_e2e as smoke
    from langgraph_phase1_real_e2e import run_browser as tool_browser
    from app.core.database import SessionLocal
    from app.models.observability import ChatRuntimeMetric
    from app.models.conversation import Conversation

    original = smoke.collect_result

    def collect(**kwargs):
        result = original(**kwargs)
        with SessionLocal() as db:
            metric = db.scalar(select(ChatRuntimeMetric).join(Conversation,
                Conversation.id == ChatRuntimeMetric.conversation_id).where(
                    Conversation.project_id == kwargs["project_id"],
                    ChatRuntimeMetric.user_id == kwargs["user_id"]).order_by(
                        ChatRuntimeMetric.created_at.desc()).limit(1))
            stats = json.loads(metric.stats_json)
            assert stats["context_graph_phases"] == ["snapshot", "memory", "tools_subgraph", "rag",
                                                     "summary", "govern", "context_ready"]
            assert stats["chat_stream_graph_phases"] == ["generate", "persist_terminal"]
            result["context_nodes"] = stats["context_graph_phases"]
            result["stream_nodes"] = stats["chat_stream_graph_phases"]
        return result

    smoke.collect_result = collect
    try:
        report = {"scope": "真实浏览器/在线Planner/文件工具/最终回答/持久化图阶段", "browser": tool_browser()}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        smoke.collect_result = original


def run(output: Path):
    from stage46a_real_e2e import prepare_fixture, cleanup
    from app.core.database import SessionLocal
    from app.models.conversation import Conversation
    from app.models.message import Message
    from app.models.observability import ChatRuntimeMetric
    from app.models.knowledge import KnowledgeRetrievalLog

    report = {"scope": "真实在线DeepSeek-V3.2/HTTP/数据库；非性能或语义评分集", "cases": []}

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    token, uid, pid, _, _ = prepare_fixture()
    client = httpx.Client(base_url=os.getenv("AIWS_E2E_BACKEND_URL", "http://127.0.0.1:32007"),
                          headers={"Authorization": f"Bearer {token}"}, timeout=180)
    try:
        def conversation(name):
            with SessionLocal() as db:
                row = Conversation(user_id=uid, project_id=pid, title=f"LangGraph2-{name}",
                                   model_name="deepseek-ai/DeepSeek-V3.2")
                db.add(row)
                db.commit()
                return row.id

        def chat(name, path, payload, *, tool=False, rag=False):
            events = []
            started = time.monotonic()
            with client.stream("POST", path, json=payload) as response:
                assert response.is_success, f"{name}: HTTP {response.status_code}"
                aid = response.headers["x-assistant-message-id"]
                for line in response.iter_lines():
                    if line.strip():
                        events.append(json.loads(line))
            assert events[-1]["type"] == "done", f"{name}:未正常结束"
            assert not any(e["type"] == "model_error" for e in events), name
            answer = "".join(e["text"] for e in events if e["type"] == "answer_delta")
            assert answer.strip(), name
            audit = next(e["audit"] for e in events if e["type"] == "citation_audit")
            sources = next((e["sources"] for e in events if e["type"] == "tool_sources"), [])
            # 先保留失败定位所需的非敏感诊断，再做业务断言，不能清理夹具后丢失证据。
            report["latest_attempt"] = {"case": name, "source_types": [s["source_type"] for s in sources],
                "event_types": [e["type"] for e in events],
                "tool_outcomes": [{k: e.get(k) for k in ("tool_key", "status", "next_action")}
                    for e in events if e["type"] == "tool_workflow_step_outcome"]}
            save()
            with SessionLocal() as db:
                row = db.get(Message, aid)
                metric = db.scalar(select(ChatRuntimeMetric).where(ChatRuntimeMetric.assistant_message_id == aid))
                stats = json.loads(metric.stats_json)
                assert row.status == "done" and row.content == answer, name
                assert stats["context_graph_phases"] == ["snapshot", "memory", "tools_subgraph", "rag",
                                                         "summary", "govern", "context_ready"]
                assert stats["chat_stream_graph_phases"] == ["generate", "persist_terminal"]
                assert stats["chat_graph_phases"] == ["bootstrap", "runtime", "assemble", "finalize"]
                assert json.loads(row.external_sources or "[]") == sources, name
                assert {s["citation_label"] for s in sources} == set(audit["available_labels"])
                if tool:
                    assert any(s["source_type"] == "workspace_file_read" for s in sources), name
                    # 本题要求第一行和用途，文件名由来源卡片绑定；不额外要求模型重复题干文件名。
                    assert "# 阶段 4.6A 临时文件" in answer and "验证" in answer, name
                    assert any("stage46a-readme.md" in str(s.get("title", "")) for s in sources), name
                if rag:
                    assert any(s["source_type"] == "knowledge" for s in sources), name
                    logs = db.scalars(select(KnowledgeRetrievalLog).where(
                        KnowledgeRetrievalLog.assistant_message_id == aid)).all()
                    assert logs, name
                    assert {s["citation_label"] for log in logs for s in json.loads(log.sources_json or "[]")} == {
                        s["citation_label"] for s in sources if s["source_type"] == "knowledge"}
                gid = row.generation_id
            report["cases"].append({"case": name, "status": "passed", "assistant_status": "done",
                "elapsed_ms": round((time.monotonic()-started)*1000), "answer_chars": len(answer),
                "source_count": len(sources), "context_nodes": stats["context_graph_phases"],
                "stream_nodes": stats["chat_stream_graph_phases"], "citation_map_consistent": True})
            report.pop("latest_attempt", None)
            save()
            return aid, gid

        cid = conversation("普通")
        aid, old_gid = chat("普通对话", "/api/chat/events-stream", {
            "conversation_id": cid, "content": "请用一句话解释什么是数据库事务。"})
        new_aid, new_gid = chat("重生成", "/api/chat/regenerate-last-stream", {
            "conversation_id": cid, "assistant_message_id": aid})
        assert new_aid == aid and old_gid != new_gid
        with SessionLocal() as db:
            mid = db.scalar(select(Message.id).where(Message.conversation_id == cid,
                Message.role == "user").order_by(Message.sequence.desc()).limit(1))
        _, edited_gid = chat("编辑重答", "/api/chat/edit-last-user-stream", {
            "conversation_id": cid, "assistant_message_id": aid,
            "user_message_id": mid, "content": "请用一句话解释事务的原子性。"})
        assert edited_gid != new_gid
        fixture = json.loads((ROOT / "backend/evals/paper_100_chinese_20261028.json").read_text())
        question = fixture["cases"][0]["query"]
        chat("RAG", "/api/chat/events-stream", {"conversation_id": conversation("RAG"),
            "content": question, "knowledge_base_id": fixture["knowledge_base_id"]}, rag=True)
        file_query = "请搜索并读取 stage46a-readme.md，报告文件第一行及用途，只读文件，不联网不修改。"
        chat("工具", "/api/chat/events-stream", {"conversation_id": conversation("工具"),
            "content": file_query, "web_search_enabled": True, "tool_run_mode": "workspace_review",
            "skill_key": "workspace.document-review"}, tool=True)
        chat("混合RAG与工具", "/api/chat/events-stream", {"conversation_id": conversation("混合"),
            "content": f"请完成两项独立任务：1. {file_query} 2. 用已选择论文库回答：{question} 请简短分项说明并引用来源。",
            "web_search_enabled": True, "tool_run_mode": "workspace_review",
            "skill_key": "workspace.document-review",
            "knowledge_base_id": fixture["knowledge_base_id"]}, tool=True, rag=True)
        cid = conversation("停止")
        with client.stream("POST", "/api/chat/events-stream", json={"conversation_id": cid,
            "content": "请详细介绍数据库事务隔离级别及其示例，至少1500字。"}) as response:
            assert response.is_success
            aid = response.headers["x-assistant-message-id"]
            for line in response.iter_lines():
                if line and json.loads(line)["type"] == "answer_delta":
                    break
        deadline = time.monotonic() + 15
        status = "streaming"
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                row = db.get(Message, aid)
                status, chars = row.status, len(row.content or "")
            if status != "streaming":
                break
            time.sleep(0.2)
        assert status == "cancelled" and chars > 0, f"断连收口:{status}"
        report["cases"].append({"case": "真实HTTP流断连", "status": "passed",
                                "assistant_status": status, "saved_partial_chars": chars})
        save()
        return report
    except Exception as exc:
        report["failed"] = {"error_type": type(exc).__name__}
        save()
        raise
    finally:
        client.close()
        cleanup(user_id=uid, project_id=pid)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    print(json.dumps((run_browser if args.browser else run)(args.output), ensure_ascii=False, indent=2))

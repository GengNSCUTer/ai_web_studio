"""在线Planner与浏览器验证审批图：暂停后重启、用户确认/拒绝、真实版本和重复确认。"""

import argparse
import json
from chat_sse_client import chat_response, sse_events
from pathlib import Path
import subprocess
import sys
import time

import httpx
from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def run(output, semantic_edit=False):
    from playwright.sync_api import sync_playwright, expect
    from evaluate_tool_budget_experiment import fixture
    from file_edit_approval_browser_e2e import cleanup_edit
    from app.core.database import SessionLocal
    from app.models.agent_runtime import AgentApproval, AgentRun, FileRevision, PatchDraft
    from app.models.project_file import ProjectFile
    from app.models.message import Message
    from app.models.tool_trace import ToolRouteRun, ToolCallRun

    token, uid, pid, fid = fixture()
    report = {"scope": "固定账号临时项目；真实DeepSeek-V3.2、文件工具、原生PG审批图、浏览器"}
    client = httpx.Client(base_url="http://127.0.0.1:32007",
        headers={"Authorization": f"Bearer {token}"}, timeout=240)
    errors = []

    def chat(title, question, mode="edit_proposal"):
        response = client.post("/api/conversations", json={"project_id": pid, "title": title,
                                                         "model_name": "deepseek-ai/DeepSeek-V3.2"})
        response.raise_for_status()
        cid = response.json()["id"]
        response = chat_response(client, "/api/chat/runs", json={"conversation_id": cid, "content": question,
            "web_search_enabled": True, "tool_run_mode": mode})
        response.raise_for_status()
        events = sse_events(response.text)
        assert events[-1]["type"] == "done", "在线模型回答未正常结束"
        assert any(event["type"] == "tool_call_end" for event in events), "没有真实调用工具"
        # 只记录结构化步骤摘要；不保存令牌、Provider响应或用户其他资料。
        report.setdefault("chat_traces", []).append({"title": title,
            "calls": [{"tool_key": event.get("tool_key"), "status": event.get("status")}
                      for event in events if event["type"] == "tool_call_end"],
            "planner_strategies": list(dict.fromkeys(str(event.get("strategy"))
                for event in events if event["type"] == "tool_planner_end")),
            "confirmation_required": any(event["type"] == "tool_confirmation_required" for event in events)})
        return cid

    def load(page, title, terminal_message=None):
        page.goto("http://127.0.0.1:32008/chat", wait_until="networkidle")
        page.locator("select").nth(0).select_option(pid)
        page.get_by_text(title, exact=True).last.click()
        target = (page.get_by_text(terminal_message, exact=True) if terminal_message else
                  page.get_by_role("button", name="确认并应用 Diff", exact=True, include_hidden=True))
        panel = page.locator("details.reasoning-panel").filter(has=target).last
        panel.locator(":scope > summary").click()
        expect(target.last).to_be_visible(timeout=20000)
        if terminal_message:
            expect(page.get_by_role("button", name="确认并应用 Diff", exact=True)).to_have_count(0)

    try:
        question = ("请阅读 evaluation-notes.md，将第一行英文标题翻译为自然的简体中文标题，标题应包含‘评测’和‘笔记’。"
                    "其余行逐字保留。正式申请修改，请使用受控工具创建真实Diff和等待我确认的审批，不能绕过审批写入。"
                    if semantic_edit else
                    "正式申请修改 evaluation-notes.md：先读取，再把“# Evaluation note”替换为“# 图审批验收”。"
                    "请用受控修改工具创建真实Diff和等待我确认的审批；不要直接写入，也不要只在回答中给出建议。")
        chat("图审批-确认", question)
        with SessionLocal() as db:
            approval = db.scalar(select(AgentApproval).join(PatchDraft, PatchDraft.step_id == AgentApproval.step_id)
                .where(PatchDraft.project_file_id == fid, AgentApproval.user_id == uid).order_by(AgentApproval.created_at.desc()))
            assert approval and approval.status == "pending", "真实Planner未创建审批"
            aid, rid = approval.id, approval.run_id
            assert json.loads(db.get(AgentRun, rid).planner_state_json)["runtime_engine"] == "langgraph_approval_v1"
            baseline = db.get(ProjectFile, fid).parsed_text
            assert baseline.startswith("# Evaluation note\n"), "未批准前文件已被改写"
            before = db.query(FileRevision).filter_by(project_file_id=fid).count()
            draft = db.scalar(select(PatchDraft).where(PatchDraft.step_id == approval.step_id))
            expected_content = baseline.replace(draft.old_string, draft.new_string, 1)
            expected_heading = expected_content.splitlines()[0]
            assert expected_content.splitlines()[1:] == baseline.splitlines()[1:], "Diff改动了标题之外的内容"
            if semantic_edit:
                assert "评测" in expected_heading and "笔记" in expected_heading
                assert "llm_primary" in report["chat_traces"][0]["planner_strategies"], "未真实使用模型规划语义修改"
            assert db.execute(text("SELECT count(*) FROM aiws_graph.checkpoint_writes w "
                "JOIN aiws_graph.runtime_threads t ON t.thread_id=w.thread_id WHERE t.run_id=:rid AND channel='__interrupt__'"),
                {"rid": rid}).scalar() >= 1, "没有真实持久化interrupt"
        restarted = subprocess.run(["bash", "deploy/run_services.sh", "ctl", "restart", "backend"], cwd=ROOT,
                                   capture_output=True, text=True)
        assert restarted.returncode == 0, "原后端重启失败"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if client.get("/health").is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            raise AssertionError("后端重启后未就绪")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_cookies([{"name": "aiws_token", "value": token, "url": "http://127.0.0.1:32008",
                                     "httpOnly": True, "sameSite": "Lax"}])
                page = context.new_page()
                page.on("pageerror", lambda _: errors.append("pageerror"))
                page.on("console", lambda m: errors.append("console") if m.type == "error" else None)
                load(page, "图审批-确认")
                with page.expect_response(lambda r: r.url.endswith(f"/approvals/{aid}/apply")) as applied:
                    page.get_by_role("button", name="确认并应用 Diff", exact=True).last.click()
                assert applied.value.ok, "浏览器确认失败"
                repeat_body = applied.value.request.post_data_json
                assert repeat_body.get("expected_state_version") == 1, "前端没有传回本次确认的业务版本"
                expect(page.get_by_text("已通过版本 CAS 应用修改", exact=True)).to_be_visible(timeout=20000)
                duplicate = client.post(f"/api/agent-runtime/approvals/{aid}/apply", json=repeat_body)
                duplicate.raise_for_status()
                with SessionLocal() as db:
                    assert db.get(ProjectFile, fid).parsed_text == expected_content
                    assert db.query(FileRevision).filter_by(project_file_id=fid).count() == before + 1
                    assert db.get(AgentApproval, aid).status == "consumed"
                report.update(pause_survived_backend_restart=True, browser_apply="applied",
                              duplicate_confirm_no_extra_revision=True, exact_file_change=True)
                load(page, "图审批-确认", "已通过版本 CAS 应用修改")
                chat("图审批-拒绝", f"正式申请修改 evaluation-notes.md：先读取，再把“{expected_heading}”替换为“# 不应写入”。"
                     "使用受控修改工具创建真实Diff，等待我确认，不能直接写入。")
                with SessionLocal() as db:
                    rejected = db.scalar(select(AgentApproval).join(PatchDraft, PatchDraft.step_id == AgentApproval.step_id)
                        .where(PatchDraft.project_file_id == fid, AgentApproval.status == "pending"))
                    assert rejected, "没有创建待拒绝审批"
                    rejected_id = rejected.id
                load(page, "图审批-拒绝")
                with page.expect_response(lambda r: r.url.endswith(f"/approvals/{rejected_id}/reject")) as rejected_response:
                    page.get_by_role("button", name="拒绝", exact=True).last.click()
                assert rejected_response.value.ok
                expect(page.get_by_text("已拒绝修改", exact=True)).to_be_visible(timeout=20000)
                with SessionLocal() as db:
                    assert db.get(AgentApproval, rejected_id).status == "rejected"
                    assert db.get(ProjectFile, fid).parsed_text == expected_content
                    assert db.query(FileRevision).filter_by(project_file_id=fid).count() == before + 1
                load(page, "图审批-拒绝", "已拒绝修改")
                read_cid = chat("图审批-读回", "只读 evaluation-notes.md，返回当前第一行原文和备份保留天数，不联网也不修改。", "workspace_review")
                with SessionLocal() as db:
                    answer = db.scalar(select(Message).where(Message.conversation_id == read_cid, Message.role == "assistant"))
                    assert answer and answer.status == "done" and expected_heading.lstrip("# ") in answer.content and "17" in answer.content
                    read_calls = list(db.scalars(select(ToolCallRun).join(ToolRouteRun,
                        ToolCallRun.route_run_id == ToolRouteRun.id).where(ToolRouteRun.conversation_id == read_cid)))
                    assert any(call.tool_key == "workspace.files.read" and call.status == "success" for call in read_calls)
                report.update(browser_reject="rejected", rejected_did_not_write=True, online_readback_completed=True)
                report["refreshed_cards_match_database"] = True
                assert not errors, "浏览器出现错误"
                report["browser_errors"] = errors
            finally:
                browser.close()
        report["passed"] = True
    except Exception as exc:
        report["passed"] = False
        report["error_type"] = type(exc).__name__
        raise
    finally:
        client.close()
        cleanup_edit(uid, pid)
        report["temporary_data_cleaned"] = True
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--semantic-edit", action="store_true", help="使用在线LLM规划语义标题翻译，而非精确替换路由")
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.semantic_edit), ensure_ascii=False, indent=2))

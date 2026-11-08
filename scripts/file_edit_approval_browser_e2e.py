"""在线模型和真实浏览器验证文件审批：未确认不写、确认后写、版本与重复确认。"""

import json
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import delete, select

from evaluate_tool_budget_experiment import fixture, cleanup
from app.core.database import SessionLocal
from app.models.agent_runtime import AgentApproval, AgentRun, FileRevision, PatchDraft
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.project_file import ProjectFile
from app.models.tool_trace import ToolCallRun, ToolRouteRun
from app.services.tools.credentials import ToolCredentialResolver


BASE = "http://127.0.0.1:32008"
OLD = "# Evaluation note"
NEW = "# 评测笔记"


def send(page, question):
    page.get_by_placeholder("输入你的问题").fill(question)
    page.get_by_role("button", name="发送消息").click()
    page.wait_for_function("() => document.querySelector('button[type=submit]')?.disabled === true", timeout=10000)
    page.wait_for_function("() => ['发送消息','Send'].includes(document.querySelector('button[type=submit]')?.textContent?.trim())", timeout=180000)
    page.wait_for_load_state("networkidle")


def cleanup_edit(user_id, project_id):
    """先清理临时项目的审批/版本依赖，避免Run引用阻止删除会话。"""
    with SessionLocal() as db:
        project_files = list(db.scalars(select(ProjectFile.id).where(ProjectFile.project_id == project_id, ProjectFile.user_id == user_id)))
        if project_files:
            db.execute(delete(PatchDraft).where(PatchDraft.project_file_id.in_(project_files)))
            db.execute(delete(FileRevision).where(FileRevision.project_file_id.in_(project_files)))
        db.execute(delete(AgentRun).where(AgentRun.project_id == project_id, AgentRun.user_id == user_id))
        db.commit()
    cleanup(user_id, project_id)


def main(output=None):
    token, user_id, project_id, file_id = fixture()
    errors = []
    approval_request = {}
    report = {"model": "deepseek-ai/DeepSeek-V3.2", "passed": False}
    output = output or Path(__file__).resolve().parents[1] / "backend/evals/results/file_edit_approval_browser_20261104.json"
    try:
        with SessionLocal() as db:
            if ToolCredentialResolver(db).get_workspace_permission_mode(project_id=project_id) != "ask":
                raise AssertionError("临时工作区应为默认ask模式，不允许自动写入。")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_cookies([{"name": "aiws_token", "value": token, "url": BASE, "httpOnly": True, "sameSite": "Lax"}])
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(type(error).__name__))
                page.on("console", lambda event: errors.append("console_error") if event.type == "error" else None)

                def capture(request):
                    # 一次性审批令牌仅在内存中用于重复确认检查，不写报告或日志。
                    if "/agent-runtime/approvals/" in request.url and request.url.endswith("/apply"):
                        approval_request.update(url=request.url, body=request.post_data_json)

                page.on("request", capture)
                page.goto(BASE + "/chat", wait_until="networkidle", timeout=60000)
                expect(page.get_by_placeholder("输入你的问题")).to_be_visible(timeout=30000)
                page.locator("select").nth(0).select_option(project_id)
                page.get_by_role("button", name="新对话").click()
                page.get_by_test_id("tool-run-mode-selector").select_option("edit_proposal")
                toggle = page.get_by_role("button", name="联网搜索", exact=True)
                if "is-active" not in (toggle.get_attribute("class") or ""):
                    toggle.click()
                send(page, "读取 evaluation-notes.md，提出把正文标题改为‘评测笔记’的修改预览，只预览不要写入。")
                with SessionLocal() as db:
                    file_row = db.get(ProjectFile, file_id)
                    if not file_row.parsed_text.startswith(OLD + "\n"):
                        raise AssertionError("只读预览阶段文件被修改。")
                    report["preview_did_not_write"] = True
                # 普通Chat现已保存AgentStep；从真实界面查询，不依赖测试直接读取ORM对象。
                # 查询成功后按钮会改名“刷新”，用稳定事件文字定位父面板。
                panel = page.locator("details.reasoning-panel").filter(has_text="tool_persistence").first
                panel.locator(":scope > summary").click()
                panel.get_by_role("button", name="查看持久化步骤", exact=True).click()
                expect(panel.get_by_text(re.compile("实际尝试")).first).to_be_visible(timeout=15000)
                report["persistent_step_ui_verified"] = True
                send(page, "现在正式申请修改 evaluation-notes.md：把“# Evaluation note”替换为“# 评测笔记”。请使用受控修改流程创建等待我确认的审批，不要绕过审批写入。")
                with SessionLocal() as db:
                    conversation_ids = list(db.scalars(select(Conversation.id).where(Conversation.project_id == project_id)))
                    route = db.scalar(select(ToolRouteRun).where(ToolRouteRun.conversation_id.in_(conversation_ids)).order_by(ToolRouteRun.created_at.desc()))
                    if route:
                        answer = db.get(Message, route.assistant_message_id)
                        report["formal_request_trace"] = {
                            "route_status": route.status,
                            "answer_status": answer.status if answer else None,
                            "answer": answer.content if answer else None,
                            "events": json.loads(route.events_json or "[]"),
                        }
                    approval = db.scalar(select(AgentApproval).join(PatchDraft, PatchDraft.step_id == AgentApproval.step_id)
                        .where(PatchDraft.project_file_id == file_id, AgentApproval.user_id == user_id).order_by(AgentApproval.created_at.desc()))
                    if not approval or approval.status != "pending":
                        raise AssertionError("在线Planner未产生pending正式审批。")
                    approval_id = approval.id
                    file_row = db.get(ProjectFile, file_id)
                    baseline = file_row.parsed_text
                    if not baseline.startswith(OLD + "\n") or NEW in baseline:
                        raise AssertionError("用户未确认前文件已被写入。")
                    drafts = list(db.scalars(select(PatchDraft).where(PatchDraft.project_file_id == file_id)))
                    if not drafts or not any(NEW in draft.diff_text and OLD in draft.diff_text for draft in drafts):
                        raise AssertionError("正式审批缺少真实目标Diff。")
                    before_revisions = len(list(db.scalars(select(FileRevision).where(FileRevision.project_file_id == file_id))))
                    report.update(before_confirm_unchanged=True, before_approval_status=approval.status, before_revision_count=before_revisions)
                # 从用户真正能看到的ToolTrace打开审批并点击，不直接调用服务层代替UI。
                for panel in page.get_by_text(re.compile(r"工具过程 · \d+")).all():
                    panel.click()
                approve = page.get_by_role("button", name="确认并应用 Diff")
                expect(approve).to_be_visible(timeout=20000)
                approve.click()
                expect(page.get_by_text("已通过版本 CAS 应用修改")).to_be_visible(timeout=30000)
                if not approval_request:
                    raise AssertionError("浏览器未调用真实审批apply接口。")
                with SessionLocal() as db:
                    file_row = db.get(ProjectFile, file_id)
                    revisions = list(db.scalars(select(FileRevision).where(FileRevision.project_file_id == file_id).order_by(FileRevision.revision_number)))
                    approval = db.get(AgentApproval, approval_id)
                    if file_row.parsed_text != baseline.replace(OLD, NEW, 1) or approval.status != "consumed":
                        raise AssertionError("确认后正文或审批状态未正确更新。")
                    if len(revisions) != before_revisions + 1 or revisions[-1].parsed_text != file_row.parsed_text:
                        raise AssertionError("写入后没有且仅有一个新版本。")
                    report.update(after_approval_status=approval.status, apply_result="applied", after_revision_count=len(revisions), exact_change_only=True)
                replay = context.request.post(approval_request["url"], data=approval_request["body"])
                with SessionLocal() as db:
                    count = len(list(db.scalars(select(FileRevision).where(FileRevision.project_file_id == file_id))))
                    if count != before_revisions + 1 or db.get(ProjectFile, file_id).parsed_text != baseline.replace(OLD, NEW, 1):
                        raise AssertionError("重复确认造成重复修改或额外版本。")
                    report.update(repeated_apply_http_status=replay.status, repeated_apply_no_extra_revision=True)
                # 独立新会话真实读取当前内容，不仅相信写入API回包。
                page.get_by_role("button", name="新对话").click()
                page.get_by_test_id("tool-run-mode-selector").select_option("workspace_review")
                send(page, "读取 evaluation-notes.md 当前第一行标题和项目保留周期，核实目前实际保存的内容，不要联网。")
                with SessionLocal() as db:
                    conversation = db.scalar(select(Conversation).where(Conversation.project_id == project_id).order_by(Conversation.created_at.desc()))
                    route = db.scalar(select(ToolRouteRun).where(ToolRouteRun.conversation_id == conversation.id))
                    answer = db.get(Message, route.assistant_message_id)
                    calls = list(db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id == route.id)))
                    if answer.status != "done" or "评测笔记" not in answer.content or "17" not in answer.content:
                        raise AssertionError("独立读取会话没有正确回答新标题和保留事实。")
                    if not any(call.tool_key == "workspace.files.read" and call.status == "success" for call in calls):
                        raise AssertionError("新会话没有实际读取。")
                    report.update(read_back_status=answer.status, read_back_answer=answer.content, fresh_read_verified=True)
                page.reload(wait_until="networkidle")
                expect(page.get_by_text("评测笔记", exact=False).first).to_be_visible(timeout=30000)
                page.screenshot(path="/tmp/aiws-file-edit-approval-browser.png", full_page=True)
                if errors:
                    raise AssertionError(f"浏览器出现{len(errors)}个错误。")
                report.update(refresh_persisted=True, browser_errors=0, passed=True)
            finally:
                browser.close()
    finally:
        try:
            cleanup_edit(user_id, project_id)
            report["temporary_data_cleaned"] = True
        finally:
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "formal_request_trace"}, ensure_ascii=False))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    main(parser.parse_args().output)

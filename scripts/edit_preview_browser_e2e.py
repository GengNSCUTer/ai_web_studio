"""真实浏览器验证只读Diff预览：在线模型、文件工具、最终回答、刷新和未写入。"""

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

from evaluate_tool_budget_experiment import fixture, cleanup, check_task, evaluation_sources
from app.core.database import SessionLocal
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.project_file import ProjectFile
from app.models.tool_trace import ToolCallRun, ToolRouteRun


BASE = "http://127.0.0.1:32008"
QUERY = "请读取 evaluation-notes.md，并提出把标题改为‘评测笔记’的修改提案；只生成提案，不要实际写入。"


def main():
    token, user_id, project_id, file_id = fixture()
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_cookies([{"name": "aiws_token", "value": token, "url": BASE, "httpOnly": True, "sameSite": "Lax"}])
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(type(error).__name__))
                page.on("console", lambda event: errors.append("console_error") if event.type == "error" else None)
                page.goto(BASE + "/chat", wait_until="networkidle", timeout=60000)
                expect(page.get_by_placeholder("输入你的问题")).to_be_visible(timeout=30000)
                page.locator("select").nth(0).select_option(project_id)
                page.get_by_role("button", name="新对话").click()
                page.get_by_test_id("tool-run-mode-selector").select_option("edit_proposal")
                toggle = page.get_by_role("button", name="联网搜索", exact=True)
                if "is-active" not in (toggle.get_attribute("class") or ""):
                    toggle.click()
                page.get_by_placeholder("输入你的问题").fill(QUERY)
                page.get_by_role("button", name="发送消息").click()
                page.wait_for_function("() => document.querySelector('button[type=submit]')?.disabled === true", timeout=10000)
                page.wait_for_function("() => ['发送消息','Send'].includes(document.querySelector('button[type=submit]')?.textContent?.trim())", timeout=180000)
                page.wait_for_load_state("networkidle")
                with SessionLocal() as db:
                    conversation = db.scalar(select(Conversation).where(Conversation.project_id == project_id).order_by(Conversation.created_at.desc()))
                    route = db.scalar(select(ToolRouteRun).where(ToolRouteRun.conversation_id == conversation.id))
                    answer = db.get(Message, route.assistant_message_id)
                    calls = list(db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id == route.id)))
                    sources = evaluation_sources(calls)
                    file_row = db.get(ProjectFile, file_id)
                    unchanged = file_row.parsed_text.startswith("# Evaluation note\n") and "评测笔记" not in file_row.parsed_text
                    checks = check_task("edit_proposal", QUERY, answer.content, sources,
                        [{"tool_key": call.tool_key, "status": call.status} for call in calls], unchanged)
                    if answer.status != "done" or not all(checks.values()):
                        raise AssertionError(f"浏览器任务未完成：{checks}")
                    if any(call.tool_key == "workspace.files.apply_edit" for call in calls):
                        raise AssertionError("只读预览不应创建实际写入调用。")
                    report = {"model": conversation.model_name, "assistant_status": answer.status,
                        "checks": checks, "tool_keys": [call.tool_key for call in calls], "answer": answer.content}
                expect(page.get_by_text("评测笔记", exact=False).first).to_be_visible()
                page.reload(wait_until="networkidle")
                expect(page.get_by_text("评测笔记", exact=False).first).to_be_visible(timeout=30000)
                page.screenshot(path="/tmp/aiws-edit-preview-browser.png", full_page=True)
                if errors:
                    raise AssertionError(f"浏览器错误数量：{len(errors)}")
                report.update(browser_errors=0, refresh_persisted=True)
            finally:
                browser.close()
    finally:
        cleanup(user_id, project_id)
    report["temporary_data_cleaned"] = True
    output = Path(__file__).resolve().parents[1] / "backend/evals/results/edit_preview_browser_20261104.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

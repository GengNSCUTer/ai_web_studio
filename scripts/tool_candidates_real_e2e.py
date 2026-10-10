"""无 Skill 限定下的文件口语召回、真实追问与无需工具浏览器验收。

使用固定账号的 V3.2 在线配置，只创建临时只读项目，结束后删除测试数据。
检验候选、真实 Planner、文件执行、最终回答、持久化和浏览器错误。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import SessionLocal  # noqa: E402
from app.models import Conversation, Message, Project, ProjectFile, ToolCallRun, ToolRouteRun  # noqa: E402
from stage46a_real_e2e import cleanup, prepare_fixture  # noqa: E402

BASE_URL = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32018").rstrip("/")
TIMEOUT_MS = int(os.getenv("AIWS_E2E_TIMEOUT_MS", "240000"))
MODEL = "deepseek-ai/DeepSeek-V3.2"
DEEP_READ = os.getenv("AIWS_E2E_DEEP_READ") == "1"


def collect(project_id: str, file_id: str, scenario: str) -> dict:
    with SessionLocal() as db:
        conversation = db.scalars(select(Conversation).where(Conversation.project_id == project_id)).one()
        route = db.scalars(select(ToolRouteRun).where(ToolRouteRun.conversation_id == conversation.id)
                           .order_by(ToolRouteRun.created_at.desc()).limit(1)).one()
        answer = db.get(Message, route.assistant_message_id)
        if conversation.model_name != MODEL or answer.status != "done":
            raise AssertionError("模型选择或最终消息状态不符合预期。")
        events = json.loads(route.events_json or "[]")
        selections = [event for event in events if event.get("type") == "tool_candidate_selection"]
        planners = [event for event in events if event.get("type") == "tool_planner_end"]
        strategies = {event.get("strategy") for event in planners}
        if not planners or strategies - {"llm_primary", "llm", "fallback"} or (
            scenario == "colloquial_file_name" and "llm_primary" not in strategies
            or scenario == "file_followup" and DEEP_READ and "llm_primary" not in strategies
        ):
            fallback = [event.get("reason") for event in events if event.get("type") == "tool_fallback"]
            raise AssertionError(f"本场景未完整经过在线 Planner：{scenario}，"
                                 f"strategies={[event.get('strategy') for event in planners]}，fallback={fallback}，"
                                 f"query={[event.get('query_preview') for event in events if event.get('type') == 'tool_planner_start']}，"
                                 f"candidates={[event.get('candidates') for event in selections]}，answer={answer.content[:500]}")
        calls = list(db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id == route.id)).all())
        if scenario == "no_tool_needed":
            if calls or "已收到" not in answer.content:
                raise AssertionError("无需工具的消息仍执行了工具或没有正确回答。")
        else:
            if not selections or selections[0].get("scope") != "catalog":
                raise AssertionError("候选验收不允许通过 Skill 白名单预先限定文件工具。")
            # 本场景只要求文件中的事实；搜索片段足够时不强迫模型再读一遍。
            # 专门的原文读取、分页和 Worker 链路由 workspace_files_real_e2e 单独验收。
            evidence_calls = calls
            if scenario == "file_followup" and not DEEP_READ and not calls:
                # 复述上一轮已核实的事实不应强迫重查；必须先确认同会话真实证据来自目标文件。
                route_ids = db.scalars(select(ToolRouteRun.id).where(
                    ToolRouteRun.conversation_id == conversation.id, ToolRouteRun.id != route.id)).all()
                evidence_calls = list(db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id.in_(route_ids))).all())
            if not any(call.status == "success" and any(
                source.get("metadata", {}).get("file_id") == file_id
                for source in json.loads(call.sources_json or "[]")
            ) for call in evidence_calls):
                raise AssertionError(f"真实 Planner 未取得目标证据：{scenario}，"
                                     f"{[(call.tool_key, call.status) for call in calls]}")
            if scenario == "file_followup" and DEEP_READ and not any(
                call.tool_key == "workspace.files.read" and call.status == "success"
                and json.loads(call.arguments_json or "{}").get("file_id") == file_id for call in calls
            ):
                raise AssertionError("核实原文的追问必须真实读取目标文件，不能只凭搜索或历史回答。")
            if any(call.tool_key not in {"workspace.files.list", "workspace.files.search", "workspace.files.read"}
                   for call in calls):
                raise AssertionError("只读文件场景执行了无关或非只读工具。")
            hint = "current_file_name" if scenario == "colloquial_file_name" else "recent_user_file_followup"
            if not any(f"file_hint:{hint}" in candidate.get("reasons", [])
                       for candidate in selections[0]["candidates"]):
                raise AssertionError("预期的文件名/历史线索没有进入候选轨迹。")
            expected = "归档核验结束" if scenario == "file_followup" and DEEP_READ else "CedarLedger"
            if expected not in answer.content:
                raise AssertionError("最终回答没有忠实使用当前文件证据。")
        return {"scenario": scenario, "model": conversation.model_name, "assistant_status": answer.status,
                "route_status": route.status, "tool_calls": len(calls), "elapsed_ms": route.elapsed_ms,
                "tool_keys": sorted({call.tool_key for call in calls}),
                "prior_evidence_reused": scenario == "file_followup" and not DEEP_READ and not calls,
                "answer_checked": True, "explicit_skill": False, "planner_strategies": sorted(strategies)}


def run():
    token, user_id, project_id, file_id, _ = prepare_fixture()
    errors = []
    requests = []
    try:
        with SessionLocal() as db:
            project = db.get(Project, project_id)
            if project.default_model != MODEL:
                raise AssertionError("固定账号默认模型尚未切换至 V3.2。")
            project.name = f"file-step3-e2e-{project_id[:8]}"
            file = db.get(ProjectFile, file_id)
            file.file_name = "archive-notes.md"
            file.parsed_text = "# CedarLedger 归档说明\n资料归档周期为 17 天。\n只能读取，不允许修改。\n"
            if DEEP_READ:
                # 额外原文场景不与候选召回验收混淆：强制检查真实 read，而非搜索摘录。
                file.parsed_text += "普通会议记录，不包含归档核验结论。\n" * 77 + "归档核验结束\n"
            db.commit()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_cookies([{"name": "aiws_token", "value": token, "url": BASE_URL,
                                     "httpOnly": True, "sameSite": "Lax"}])
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                page.on("request", lambda request: requests.append(request.post_data_json)
                        if request.method == "POST" and request.url.rstrip("/").endswith("/api/chat") else None)
                page.goto(f"{BASE_URL}/chat", wait_until="networkidle", timeout=60_000)
                expect(page.get_by_placeholder("输入你的问题")).to_be_visible(timeout=30_000)
                page.locator("select").nth(0).select_option(project_id)
                page.get_by_role("button", name="新对话").click()
                page.get_by_test_id("tool-run-mode-selector").select_option("workspace_review")
                search_toggle = page.get_by_role("button", name="联网搜索", exact=True)
                if "is-active" not in (search_toggle.get_attribute("class") or ""):
                    search_toggle.click()
                results = []
                for scenario, question in (
                    ("colloquial_file_name", "帮我看看 archive-notes.md 讲的是哪个项目？只回答项目名称，别联网。"),
                    ("file_followup", "它的第81行原文是什么？请从里面核实，别联网。" if DEEP_READ
                     else "它讲的那个项目叫什么？用刚才核实过的信息回答，不用重复查询，别联网。"),
                    ("no_tool_needed", "谢谢，直接回复‘已收到’，不要调用任何工具。"),
                ):
                    page.wait_for_load_state("networkidle")
                    expect(page.get_by_test_id("tool-run-mode-selector")).to_be_enabled(timeout=TIMEOUT_MS)
                    page.get_by_placeholder("输入你的问题").fill(question)
                    expect(page.get_by_placeholder("输入你的问题")).to_have_value(question)
                    expect(page.get_by_role("button", name="发送消息")).to_be_enabled(timeout=10_000)
                    page.get_by_role("button", name="发送消息").click()
                    page.wait_for_function("() => document.querySelector('button[type=submit]')?.disabled === true",
                                           timeout=10_000)
                    page.wait_for_function("""() => {
                        const text = document.querySelector('button[type=submit]')?.textContent?.trim();
                        return text === '发送消息' || text === 'Send';
                    }""", timeout=TIMEOUT_MS)
                    results.append(collect(project_id, file_id, scenario))
                    page.wait_for_load_state("networkidle")
                    expect(page.get_by_test_id("tool-run-mode-selector")).to_have_value("workspace_review")
                    print(json.dumps({"completed_scenario": scenario}, ensure_ascii=False), flush=True)
                if errors or len(requests) != 3 or any(request.get("skillKey") for request in requests):
                    raise AssertionError("浏览器错误、请求数量或 Skill 状态异常。")
                page.screenshot(path="/tmp/tool-candidates-step3.png", full_page=True)
                print(json.dumps({"cases": results, "browser_errors": 0}, ensure_ascii=False, sort_keys=True))
            finally:
                browser.close()
    finally:
        cleanup(user_id=user_id, project_id=project_id)


if __name__ == "__main__":
    run()

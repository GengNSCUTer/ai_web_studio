"""真实浏览器、在线 Planner、旧文件定向/续页读取和最终回答验收。

只使用固定账号已有在线配置及只读文件 Skill。创建 126 个临时文件，验证
目标在最新 120 个之外时仍能被定位；不输出口令、模型配置或分页签名。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import SessionLocal  # noqa: E402
from app.models import (AgentArtifact, AgentCheckpoint, AgentOutboxEvent, AgentRun, AgentStep,
                        Conversation, Message, Project, ProjectFile, ToolCallRun, ToolRouteRun)  # noqa: E402
from app.services.durable_handoff_service import DurableHandoffService  # noqa: E402
from stage46a_real_e2e import prepare_fixture, cleanup  # noqa: E402

BASE_URL = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32018").rstrip("/")
TIMEOUT_MS = int(os.getenv("AIWS_E2E_TIMEOUT_MS", "240000"))


def populate_files(user_id: str, project_id: str, file_id: str):
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        project.name = f"file-step2-e2e-{project_id[:8]}"
        target = db.get(ProjectFile, file_id)
        old = datetime.now(timezone.utc) - timedelta(days=2)
        target.file_name = "archive-notes.md"
        target.parsed_text = "# CedarLedger 归档说明\nCedarLedger 的资料归档周期为 17 天。\n本次只能读取，不允许修改。\n"
        target.created_at = old
        for index in range(125):
            db.add(ProjectFile(user_id=user_id, project_id=project_id, kind="text", mime_type="text/markdown",
                file_name=f"meeting-{index:03}.md", storage_key=f"step2-e2e/{project_id}/{index}.md", file_size=40,
                parsed_text="# Routine meeting\nGeneral meeting schedule and ordinary weekly notes.\n",
                created_at=old + timedelta(seconds=index + 1)))
        db.commit()


def collect(project_id: str, file_id: str, *, scenario: str) -> dict:
    with SessionLocal() as db:
        conversation = db.scalars(select(Conversation).where(Conversation.project_id == project_id)
                                   .order_by(Conversation.created_at.desc()).limit(1)).one()
        route = db.scalars(select(ToolRouteRun).where(ToolRouteRun.conversation_id == conversation.id)
                            .order_by(ToolRouteRun.created_at.desc()).limit(1)).one()
        assistant = db.get(Message, route.assistant_message_id)
        if assistant.status != "done" or "17" not in (assistant.content or "") or "CedarLedger" not in assistant.content:
            events = json.loads(route.events_json or "[]")
            reasons = [event.get("reason") for event in events if event.get("type") in {"tool_fallback", "tool_agent_terminal"}]
            call_states = [(call.tool_key, call.status) for call in db.scalars(
                select(ToolCallRun).where(ToolCallRun.route_run_id == route.id)).all()]
            raise AssertionError(f"最终模型未依据旧文件回答：{scenario}, assistant={assistant.status}, "
                                 f"route={route.status}, reasons={reasons}, calls={call_states}")
        events = json.loads(route.events_json or "[]")
        planners = [item for item in events if item.get("type") == "tool_planner_end"]
        strategies = {item.get("strategy") for item in planners}
        # 正常停止的无调用计划使用 llm；产生调用的计划使用 llm_primary，二者都是真实模型。
        if "llm_primary" not in strategies or strategies - {"llm_primary", "llm", "fallback"}:
            raise AssertionError(f"本次验收没有完整经过真实在线 Planner：scenario={scenario}, strategies={sorted(str(value) for value in strategies)}")
        calls = list(db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id == route.id)
                                .order_by(ToolCallRun.started_at, ToolCallRun.id)).all())
        tool_keys = {call.tool_key for call in calls}
        if tool_keys - {"workspace.files.list", "workspace.files.search", "workspace.files.read"}:
            raise AssertionError("只读场景调用了文件 Skill 以外的工具。")
        if not any(call.tool_key == "workspace.files.read" and json.loads(call.arguments_json)["file_id"] == file_id
                   and call.status == "success" for call in calls):
            raise AssertionError("没有成功读取目标原文。")
        if scenario == "exact_name":
            if not any(json.loads(call.arguments_json or "{}").get("file_name") == "archive-notes.md" for call in calls):
                raise AssertionError("已知文件名时，真实 Planner 没有使用定向查找。")
        else:
            if not any(call.tool_key == "workspace.files.search" and json.loads(call.arguments_json or "{}").get("cursor")
                       and call.status == "success" for call in calls):
                raise AssertionError("真实 Planner 未依据下一页入口继续搜索。")
        records = [source for call in calls for source in json.loads(call.sources_json or "[]")]
        first_pages = [source for source in records if source.get("metadata", {}).get("has_more") is True]
        if scenario == "continue_search" and not any(source["metadata"].get("scanned_files") == 120
                                                     and "未检查" in source["display_text"] for source in first_pages):
            raise AssertionError("第一页范围没有正确保存和披露。")
        file = db.get(ProjectFile, file_id)
        if "17 天" not in file.parsed_text:
            raise AssertionError("只读场景意外修改了正文。")
        return {"scenario": scenario, "temporary_files": 126, "target_outside_first_120": True,
                "planner_strategy": "llm_primary", "planner_strategies": sorted(strategies),
                "planner_fallback_count": sum(item.get("type") == "tool_fallback" for item in events),
                "planner_rounds": sum(item.get("type") == "tool_agent_round_end" for item in events), "tool_call_count": len(calls),
                "tool_keys": sorted(tool_keys), "route_status": route.status, "assistant_status": assistant.status,
                "answer_chars": len(assistant.content), "correct_fact_used": True, "elapsed_ms": route.elapsed_ms}


def run_worker_case(user_id: str, project_id: str):
    """确认冻结计划后，由已经独立运行的真实 Worker 搜索、绑定结果、读取并回写。"""
    with SessionLocal() as db:
        conversation = Conversation(user_id=user_id, project_id=project_id, title="第二步真实 Worker 验收",
                                    model_name=db.get(Project, project_id).default_model)
        db.add(conversation)
        db.flush()
        assistant = Message(conversation_id=conversation.id, role="assistant", status="done", content="只读任务确认验收")
        db.add(assistant)
        db.commit()
        handoff = DurableHandoffService(db)
        preview = handoff.preview(user_id=user_id, project_id=project_id, conversation_id=conversation.id,
            assistant_message_id=assistant.id, skill_key="workspace.document-review", max_attempts=1,
            calls=[{"call_id": "find", "tool_key": "workspace.files.search",
                    "arguments": {"query": "CedarLedger", "file_name": "archive-notes.md"}},
                   {"call_id": "read", "tool_key": "workspace.files.read", "arguments": {}, "depends_on": ["find"],
                    "result_bindings": [
                        {"source_call_id": "find", "source_path": "/sources/0/metadata/raw/file_id", "target_argument": "file_id"},
                        {"source_call_id": "find", "source_path": "/sources/0/metadata/raw/revision_id", "target_argument": "expected_revision_id"}
                    ]}])
        run = handoff.confirm(user_id=user_id, handoff_token=preview.handoff_token)
        run_id = run.id
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                run = db.get(AgentRun, run_id)
                if run.status in {"succeeded", "failed", "cancelled"}:
                    break
            time.sleep(0.5)
        else:
            raise AssertionError("真实后台 Worker 未在验收时限内收口任务。")
        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            steps = list(db.scalars(select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.sequence)).all())
            artifacts = list(db.scalars(select(AgentArtifact).where(AgentArtifact.run_id == run_id)).all())
            message_id = json.loads(run.planner_state_json or "{}").get("durable_result_message_id")
            message = db.get(Message, message_id) if message_id else None
            if run.status != "succeeded" or [step.status for step in steps] != ["succeeded", "succeeded"]:
                raise AssertionError(f"后台文件任务失败：{[(step.status, step.error_code) for step in steps]}")
            if len(artifacts) != 2 or not message or message.status != "done" or "17 天" not in message.content:
                raise AssertionError("后台产物或原会话结果回写缺失。")
            return {"run_status": run.status, "steps": 2, "artifacts": 2, "result_message_status": message.status,
                    "correct_fact_returned": True, "execution": "independent_worker", "llm_final_answer": False}
    finally:
        with SessionLocal() as db:
            for model in (AgentArtifact, AgentOutboxEvent, AgentCheckpoint, AgentStep, AgentRun):
                column = model.id if model is AgentRun else model.run_id
                db.execute(delete(model).where(column == run_id))
            db.commit()


def run():
    token, user_id, project_id, file_id, _ = prepare_fixture()
    errors = []
    try:
        populate_files(user_id, project_id, file_id)
        if os.getenv("AIWS_E2E_WORKER_ONLY") == "1":
            print(json.dumps({"worker": run_worker_case(user_id, project_id)}, ensure_ascii=False, sort_keys=True))
            return
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": BASE_URL, "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.goto(f"{BASE_URL}/chat", wait_until="networkidle", timeout=60_000)
            expect(page.get_by_placeholder("输入你的问题")).to_be_visible(timeout=30_000)
            page.locator("select").nth(0).select_option(project_id)
            cases = [
                ("exact_name", "请在当前工作区查找并读取 archive-notes.md 的原文，告诉我 CedarLedger 的归档周期。只使用工作区文件工具，不联网、不修改。"),
                ("continue_search", "请在当前工作区文件中搜索 CedarLedger 并读取匹配原文，告诉我资料归档周期。我不知道文件名；若第一页没找到且还有未检查的文件，请继续搜索下一页。只使用文件工具，不联网、不修改。"),
            ]
            results = []
            for scenario, question in cases:
                page.get_by_role("button", name="新对话").click()
                page.get_by_test_id("tool-run-mode-selector").select_option("workspace_review")
                page.get_by_test_id("skill-selector").click()
                page.get_by_test_id("skill-option-workspace.document-review").wait_for(timeout=30_000)
                page.get_by_test_id("skill-option-workspace.document-review").click()
                page.get_by_placeholder("输入你的问题").fill(question)
                page.get_by_role("button", name="发送消息").click()
                page.wait_for_function("() => document.querySelector('button[type=submit]')?.disabled === true", timeout=10_000)
                page.wait_for_function("""() => {
                    const text = document.querySelector('button[type=submit]')?.textContent?.trim() || '';
                    return text === '发送消息' || text === 'Send';
                }""", timeout=TIMEOUT_MS)
                expect(page.get_by_text(re.compile(r"工具过程 · \d+"))).to_be_visible(timeout=10_000)
                results.append(collect(project_id, file_id, scenario=scenario))
                page.screenshot(path=f"/tmp/workspace-files-step2-{scenario}.png", full_page=True)
            if errors:
                raise AssertionError("浏览器出现页面或控制台错误。")
            browser.close()
        print(json.dumps({"cases": results, "browser_errors": 0, "online_model_only": True,
                          "worker": run_worker_case(user_id, project_id)}, ensure_ascii=False, sort_keys=True))
    finally:
        cleanup(user_id=user_id, project_id=project_id)


if __name__ == "__main__":
    run()

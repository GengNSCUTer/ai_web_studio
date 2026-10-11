"""公开资料 MCP、三个 Skill 与在线模型的真实浏览器业务验证。

只查询公开技术资料，使用固定账号已有 Provider；不输出令牌或密钥。
为账号安装已审核 Skill，临时项目、对话与文件在结束时清理。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.core.database import SessionLocal  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.message import Message  # noqa: E402
from app.models.project_file import ProjectFile  # noqa: E402
from app.models.tool_trace import ToolCallRun, ToolRouteRun  # noqa: E402
from app.services.skill_catalog import SkillCatalog  # noqa: E402
from app.services.tools.executor import ToolExecutor  # noqa: E402
from app.services.tools.schemas import PlannedToolCall  # noqa: E402
from stage46a_real_e2e import cleanup, prepare_fixture  # noqa: E402
from public_documentation_answer_regression import check_transaction_answer  # noqa: E402


BASE_URL = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32018").rstrip("/")
TIMEOUT_MS = int(os.getenv("AIWS_E2E_TIMEOUT_MS", "240000"))
URL = "https://docs.sqlalchemy.org/en/20/orm/session_basics.html"
SKILLS = ("research.source-brief", "knowledge.technical-docs", "workspace.doc-coauthoring")


async def real_tools() -> list[dict]:
    """直接通过生产 Executor 检查四个真实 MCP 工具，不以 HTTP 200 代替质量门。"""

    calls = [
        ("web.exa.search", {"query": "SQLAlchemy session flush commit official documentation", "objective": "Find official SQLAlchemy documentation explaining transactions", "numResults": 2}),
        ("web.exa.fetch", {"urls": [URL], "maxCharacters": 8000}),
        ("docs.context7.resolve", {"libraryName": "SQLAlchemy", "query": "SQLAlchemy 2.0 Session flush versus commit"}),
    ]
    executor = ToolExecutor()
    report = []
    library_id = None
    for key, arguments in calls:
        definition = executor.catalog.get(key)
        call = PlannedToolCall(call_id="live_" + key, tool_key=key, provider=definition.provider,
                               category=definition.category, display_name=definition.display_name,
                               confidence=1.0, reason="真实 MCP 接入验收", arguments=arguments)
        result, _ = await executor.execute(call)
        if result.status != "success" or result.quality_status != "valid" or not result.sources:
            raise AssertionError(f"真实 MCP 未得到有效证据：{key}，{result.status}，{result.quality_status}")
        if key == "docs.context7.resolve":
            library_id = result.sources[0].metadata["raw"]["library_id"]
        report.append({"tool_key": key, "status": result.status, "quality": result.quality_status,
                       "sources": len(result.sources), "elapsed_ms": result.elapsed_ms})
    definition = executor.catalog.get("docs.context7.query")
    result, _ = await executor.execute(PlannedToolCall(
        call_id="live_docs", tool_key=definition.tool_key, provider=definition.provider,
        category=definition.category, display_name=definition.display_name, confidence=1.0,
        reason="使用实际定位出的 ID 查证", arguments={"libraryId": library_id, "query": "Session.flush versus Session.commit and transaction boundaries", "language": "Python"},
    ))
    if result.status != "success" or result.quality_status != "valid" or not result.sources:
        raise AssertionError("真实文档查询未得到有效证据。")
    report.append({"tool_key": definition.tool_key, "status": result.status,
                   "quality": result.quality_status, "sources": len(result.sources), "elapsed_ms": result.elapsed_ms})
    return report


def collect(project_id: str, skill_key: str, expected_tools: set[str], answer_terms: tuple[str, ...]) -> dict:
    """校验线上 Planner、有效证据、最终回答与数据库终态。"""

    with SessionLocal() as db:
        route = db.scalars(select(ToolRouteRun).join(Conversation, Conversation.id == ToolRouteRun.conversation_id)
                           .where(Conversation.project_id == project_id)
                           .order_by(ToolRouteRun.created_at.desc()).limit(1)).first()
        if not route:
            raise AssertionError("真实对话没有工具轨迹。")
        events = json.loads(route.events_json or "[]")
        strategies = {event.get("strategy") for event in events if event.get("type") == "tool_planner_end"}
        if "llm_primary" not in strategies:
            diagnostic = [event for event in events if event.get("type") in {"tool_fallback", "tool_agent_terminal", "tool_planner_end"}]
            raise AssertionError(f"场景未经过在线 Planner：{skill_key}，{sorted(strategies)}，" + json.dumps(diagnostic, ensure_ascii=False))
        calls = list(db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id == route.id)).all())
        successful = {call.tool_key for call in calls if call.status == "success"}
        if not expected_tools.issubset(successful):
            terminal = [event for event in events if event.get("type") in {
                "tool_planner_end", "tool_plan", "tool_agent_terminal", "tool_agent_round_end", "tool_result_quality", "tool_fallback", "tool_schema_validation"}]
            raise AssertionError(f"业务证据步骤未完成：{skill_key}，{[(call.tool_key, call.status) for call in calls]}，"
                                 + json.dumps(terminal, ensure_ascii=False))
        valid = {event.get("tool_key") for event in events
                 if event.get("type") == "tool_result_quality" and event.get("status") == "valid"}
        if not expected_tools.issubset(valid):
            raise AssertionError("业务证据未通过持久化质量检查。")
        allowed = set(SkillCatalog()._definitions[skill_key]["required_tool_keys"] + SkillCatalog()._definitions[skill_key]["optional_tool_keys"])
        if any(call.tool_key not in allowed for call in calls):
            raise AssertionError("执行了 Skill 范围外的工具。")
        answer = db.get(Message, route.assistant_message_id)
        conversation = db.get(Conversation, route.conversation_id)
        if not answer or answer.status != "done" or conversation.model_name != "deepseek-ai/DeepSeek-V3.2":
            raise AssertionError("模型或最终消息终态不符合验收要求。")
        if not all(term.lower() in answer.content.lower() for term in answer_terms):
            raise AssertionError(f"回答未包含已核实的业务事实：{skill_key}，{answer.content[:300]}")
        sources = [source for call in calls for source in json.loads(call.sources_json or "[]")]
        if not sources:
            raise AssertionError("没有持久化实际来源。")
        if skill_key != "workspace.doc-coauthoring":
            injected = [source for source in sources if source.get("used_in_prompt")]
            labels = {str(source.get("citation_label") or "").strip("[]") for source in injected}
            cited = set(re.findall(r"(?<![A-Za-z0-9])T\d+(?![A-Za-z0-9])", answer.content))
            # Skill 输出允许来源标签或 URL；不能把正确的 URL 引用误判为无来源。
            cited_url = any(source.get("url") and source["url"] in answer.content for source in injected)
            if not cited.intersection(labels) and not cited_url:
                raise AssertionError(f"公开资料回答没有引用实际进入模型的来源：{skill_key}，{answer.content[:800]}")
        facts = None
        if skill_key == "knowledge.technical-docs":
            if "flush" in answer_terms:
                facts = check_transaction_answer(answer.content)
            else:
                text = re.sub(r"[*`\s]", "", answer.content).lower()
                facts = {
                    "depends_is_injection": "依赖注入" in text or "注入依赖" in text,
                    "no_automatic_async_conversion": bool(re.search(r"(?:不|无法|不会).{0,30}(?:异步|非阻塞)", text)),
                    "sleep_blocks_loop": bool(re.search(r"time.sleep.{0,80}(?:阻塞|阻止)", text)),
                }
            if not all(facts.values()):
                raise AssertionError(f"文档最终答案事实核验未通过：{facts}，{answer.content[:800]}")
            if any(source.get("used_in_prompt") for source in sources if source.get("source_type") == "documentation_library"):
                raise AssertionError("库候选简介不应继续挤占已有文档正文的最终回答预算。")
        if skill_key == "workspace.doc-coauthoring" and any("apply_edit" in call.tool_key for call in calls):
            raise AssertionError("仅起草任务不应进入写入审批。")
        return {"skill_key": skill_key, "model": conversation.model_name,
                "assistant_status": answer.status, "tool_keys": sorted(successful),
                "sources": len(sources), "answer_chars": len(answer.content),
                "elapsed_ms": route.elapsed_ms, "answer_checked": True, "quality_checked": True,
                "planner_strategies": sorted(strategies), "factual_checks": facts}


def run() -> None:
    direct = asyncio.run(real_tools()) if os.getenv("AIWS_E2E_SKIP_DIRECT") != "1" else []
    print(json.dumps({"direct_tools": direct}, ensure_ascii=False), flush=True)
    token, user_id, project_id, file_id, _ = prepare_fixture()
    errors = []
    requests = []
    report = []
    try:
        with SessionLocal() as db:
            file = db.get(ProjectFile, file_id)
            file.file_name = "brief-source.md"
            file.parsed_text = "# AmberKite 资料归档\n资料复核周期为 13 天。\n负责人代号为 AmberKite。\n本次只生成草稿，不修改原文件。\n"
            original = file.parsed_text
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
                page.goto(BASE_URL + "/chat", wait_until="networkidle", timeout=60_000)
                expect(page.get_by_placeholder("输入你的问题")).to_be_visible(timeout=30_000)
                settings = page.evaluate("""async () => {
                    const response = await fetch('/api/backend/tools/settings');
                    const data = await response.json();
                    return {status: response.status, providers: (data.credentials || []).map(c => ({
                        key: c.provider_key, enabled: c.is_enabled, hasKey: c.has_api_key, source: c.source}))};
                }""")
                if settings["status"] != 200 or not {"exa", "context7"}.issubset({p["key"] for p in settings["providers"]}):
                    raise AssertionError("设置 API 没有暴露新 MCP 的可选凭据入口。")
                for skill in SKILLS:
                    installed = page.evaluate("""async skill => {
                        const response = await fetch('/api/backend/tools/skills/' + encodeURIComponent(skill), {
                            method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({is_enabled: true})});
                        return {status: response.status};
                    }""", skill)
                    if installed["status"] != 200:
                        raise AssertionError(f"Skill 真实安装 API 失败：{skill}，status={installed['status']}")
                page.reload(wait_until="networkidle")
                page.locator("select").nth(0).select_option(project_id)
                cases = [
                    ("research.source-brief", "guided_research", f"请实际读取网页 {URL}，根据正文说明 Session 什么时候取得连接，什么时候把连接归还池。200 字以内，保留来源。已提供 URL，不需要搜索。", {"web.exa.fetch"}, ("Session", "连接")),
                    ("knowledge.technical-docs", "guided_research", "用 Context7 定位 SQLAlchemy 2.0 官方文档，然后实际查证 ORM Session 的 flush 和 commit 的区别，说明事务是否结束、连接是否归还、commit 是否立即开启新的数据库事务。区分 API 本身行为与示例后续操作，200 字以内并给来源。", {"docs.context7.resolve", "docs.context7.query"}, ("flush", "commit")),
                    ("knowledge.technical-docs", "guided_research", "查询 FastAPI 官方文档，解释 Depends 和 async def：Depends 是负责注入依赖还是自动把同步函数变成异步？async def 中直接调用 time.sleep 是否会阻塞事件循环？请根据文档回答，200 字以内并给来源。", {"docs.context7.resolve", "docs.context7.query"}, ("Depends", "time.sleep")),
                    ("workspace.doc-coauthoring", "workspace_review", "搜索并读取 brief-source.md，起草一份给新人看的资料归档说明，包含复核周期和负责人代号，并指出一项读者仍需确认的问题。只在聊天给草稿，不修改文件，也不要联网。", {"workspace.files.search", "workspace.files.read"}, ("13", "AmberKite")),
                ]
                if os.getenv("AIWS_E2E_CASE_INDEX"):
                    cases = [cases[int(os.environ["AIWS_E2E_CASE_INDEX"])]]
                for skill, mode, query, expected_tools, answer_terms in cases:
                    page.get_by_role("button", name="新对话").click()
                    page.get_by_test_id("tool-run-mode-selector").select_option(mode)
                    page.get_by_test_id("skill-selector").click()
                    page.get_by_test_id("skill-option-" + skill).click()
                    page.get_by_placeholder("输入你的问题").fill(query)
                    page.get_by_role("button", name="发送消息").click()
                    page.wait_for_function("() => document.querySelector('button[type=submit]')?.disabled === true", timeout=10_000)
                    page.wait_for_function("() => ['发送消息','Send'].includes(document.querySelector('button[type=submit]')?.textContent?.trim())", timeout=TIMEOUT_MS)
                    report.append(collect(project_id, skill, expected_tools, answer_terms))
                    page.screenshot(path=f"/tmp/aiws-{skill}-real-e2e.png", full_page=True)
                    print(json.dumps(report[-1], ensure_ascii=False), flush=True)
                if errors:
                    raise AssertionError("浏览器存在 console/page error：" + "; ".join(errors[:3]))
                if len(requests) != len(cases):
                    raise AssertionError("浏览器请求数量与真实场景不一致。")
            finally:
                browser.close()
        with SessionLocal() as db:
            if db.get(ProjectFile, file_id).parsed_text != original:
                raise AssertionError("起草过程改变了原文件。")
        print(json.dumps({"browser_scenarios": len(report), "browser_errors": 0,
                          "original_file_unchanged": True, "results": report}, ensure_ascii=False), flush=True)
    finally:
        cleanup(user_id=user_id, project_id=project_id)


if __name__ == "__main__":
    run()

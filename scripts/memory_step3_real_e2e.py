"""固定账号真实在线模型、多会话、记忆治理及浏览器诊断验收；不输出凭据。"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.database import SessionLocal  # noqa: E402
from app.models.observability import ChatRuntimeMetric  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_memory import UserMemory  # noqa: E402
from app.models.user_setting import UserSetting  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from stage46a_real_e2e import cleanup  # noqa: E402

BASE = os.getenv("AIWS_E2E_BACKEND_URL", "http://127.0.0.1:32017")
WEB = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32018")
FIELDS = ("memory_enabled", "memory_auto_candidate_enabled", "memory_auto_activate_enabled",
          "knowledge_embedding_base_url", "knowledge_embedding_provider")


def run():
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == os.getenv("AIWS_E2E_USER_EMAIL", "1528713326@qq.com")))
        setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        assert setting.provider_type == "openai-compatible", "仅验收现有在线 API"
        assert setting.knowledge_embedding_provider != "ollama", "不调用本地模型"
        original = {name: getattr(setting, name) for name in FIELDS}
        token = AuthService(UserRepository(db)).create_access_token(user)
        user_id, model = user.id, setting.default_model
    client = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=240)
    projects, memories, cases, errors = [], [], [], []

    def request(method, path, payload=None):
        response = client.request(method, path, json=payload)
        assert response.is_success, f"测试 API 返回异常：{method} {path} {response.status_code}"
        return response.json() if response.content else None

    def remember(title, content, project=None, kind="fact", **fields):
        row = request("POST", "/api/memories/remember", dict(title=title, content=content,
            memory_type=kind, project_id=project, **fields))
        memories.append(row["id"])
        return row

    def chat(name, question, project, expected=(), absent=(), included=(), excluded=()):
        conv = request("POST", "/api/conversations", {"title": name, "model_name": model, "project_id": project})["id"]
        started = time.monotonic()
        text, done = [], None
        with client.stream("POST", "/api/chat/events-stream", json={"conversation_id": conv,
            "content": question, "model_name": model}) as response:
            assert response.is_success, "在线 Chat 启动失败"
            details = {}
            for line in response.iter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                assert event["type"] not in {"error", "model_error", "stream_error"}, "在线 Chat 模型异常"
                if event["type"] == "answer_delta":
                    text.append(event["text"])
                elif event["type"] == "context_info":
                    details = event["info"]["details"]
                elif event["type"] == "done":
                    done = event
        answer = "".join(text)
        assert done and answer, "没有最终回答"
        diagnostics = details["memory_retrieval"]
        ids = {item["id"] for item in diagnostics["injected"]}
        assert set(included) <= ids and not set(excluded) & ids, f"{name} 注入名单不符"
        for term in expected:
            assert term.lower() in answer.lower(), f"{name} 注入符合预期，但最终回答缺少测试事实 {term}"
        for term in absent:
            assert term.lower() not in answer.lower(), f"{name} 最终回答包含失效或越界事实"
        with SessionLocal() as db:
            metric = db.scalar(select(ChatRuntimeMetric).where(ChatRuntimeMetric.assistant_message_id == done["assistant_message_id"]))
            saved = json.loads(metric.stats_json)["memory_retrieval"]
            assert saved == diagnostics, "持久化诊断与实际返回不一致"
        cases.append({"case": name, "result": "passed", "mode": diagnostics["mode"],
            "injected_count": diagnostics["injected_count"], "elapsed_ms": round((time.monotonic()-started)*1000)})
        print(json.dumps(cases[-1], ensure_ascii=False), flush=True)
        return diagnostics

    try:
        request("PATCH", "/api/settings", {"memory_enabled": True, "memory_auto_candidate_enabled": False,
            "memory_auto_activate_enabled": False})
        for label in ("a", "b"):
            projects.append(request("POST", "/api/projects", {"name": f"memory-step3-{label}-{uuid4().hex[:8]}", "default_model": model})["id"])
        a, b = projects
        db_fact = remember("持久化方案", "苍穹计划的业务数据落在 PostgreSQL 16；内部环境代号为 CedarM42。", a)
        other = remember("独立项目部署", "独立项目数据库采用 MongoDB 7，环境代号为 BirchQ93。", b)
        preference = remember("回答语言", "默认中文回答", kind="profile")
        expired = remember("过期验收代号", "过期验收代号为 ExpiredR77。", a,
            expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
        # 创建入口拒绝已经过期的内容；模拟时间经过，让注入边界验证 TTL。
        with SessionLocal() as db:
            db.get(UserMemory, expired["id"]).expires_at = datetime.now(timezone.utc)-timedelta(hours=1)
            db.commit()
        # 本脚本没有将向量返回模拟成成功：必须真实达到 hybrid，失败降级会使该断言失败。
        diagnostic = chat("同义问法召回", "苍穹计划使用什么关系型存储和版本？也请告诉我内部环境代号；不知道就明确说不知道。",
            a, expected=("PostgreSQL", "16", "CedarM42"), included=(db_fact["id"],), excluded=(other["id"], expired["id"]))
        assert diagnostic["mode"] == "hybrid", "真实在线 Embedding 未成功"
        semantic = chat("无共有词语义召回", "关系型存储引擎的名称和版本号是啥？根据已有背景简短回答。",
            a, expected=("PostgreSQL", "16"), included=(db_fact["id"],))
        target = next(item for item in semantic["injected"] if item["id"] == db_fact["id"])
        assert target["reason"] == "semantic" and target["lexical_score"] == 0, "未验证到词法漏召回后的语义补充"
        cached = chat("新会话缓存复用", "苍穹计划的业务数据存在哪里？内部环境叫啥？只根据已知背景简短回答。",
            a, expected=("PostgreSQL", "CedarM42"), included=(db_fact["id"],))
        assert cached["embedding_cache_hits"] >= 1
        chat("跨项目隔离", "苍穹计划的内部环境代号是什么？没有依据就说不知道。", b,
            absent=("CedarM42",), excluded=(db_fact["id"],))
        chat("本轮语言覆盖", "这次请用英文回答：苍穹计划使用哪种数据库？只说一句，不要中文。", a,
            expected=("PostgreSQL",), included=(db_fact["id"],), excluded=(preference["id"],))

        corrected = request("PATCH", f"/api/memories/{db_fact['id']}", {"content": "苍穹计划的业务数据落在 PostgreSQL 17；内部环境代号为 CedarM43。", "expected_version": db_fact["version"]})
        memories.append(corrected["id"])
        chat("更正后新会话", "苍穹计划数据库和版本、内部环境代号是什么？", a,
            expected=("PostgreSQL", "17", "CedarM43"), absent=("CedarM42",), included=(corrected["id"],), excluded=(db_fact["id"],))
        request("POST", f"/api/memories/{corrected['id']}/forget")
        chat("撤销后新会话", "苍穹计划内部环境代号是什么？仅依据已保存背景，没有就说不知道。", a,
            absent=("CedarM43", "CedarM42"), excluded=(corrected["id"], db_fact["id"]))
        chat("TTL失效", "过期验收代号是什么？没有依据就说不知道。", a,
            absent=("ExpiredR77",), excluded=(expired["id"],))

        fallback = remember("可用性验收", "可用性验收数据库采用 Redis 7，验收代号为 FallbackT52。", a)
        request("PATCH", "/api/settings", {"knowledge_embedding_base_url": "http://127.0.0.1:1/v1", "knowledge_embedding_provider": "openai-compatible"})
        diagnostic = chat("Embedding失效仍可回答", "可用性验收数据库及验收代号是什么？", a,
            expected=("Redis", "FallbackT52"), included=(fallback["id"],))
        assert diagnostic["mode"] == "lexical" and diagnostic["fallback_reason"]
        request("PATCH", "/api/settings", {name: original[name] for name in ("knowledge_embedding_base_url", "knowledge_embedding_provider")})
        request("PATCH", "/api/settings", {"memory_enabled": False})
        chat("关闭开关", "可用性验收代号是什么？没有背景就说不知道。", a,
            absent=("FallbackT52",), excluded=(fallback["id"],))
        request("PATCH", "/api/settings", {"memory_enabled": True})

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": WEB, "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            page.on("pageerror", lambda _: errors.append("pageerror"))
            page.on("console", lambda item: errors.append("console") if item.type == "error" else None)
            page.goto(f"{WEB}/chat", wait_until="networkidle", timeout=60000)
            page.locator("select").nth(0).select_option(a)
            page.get_by_role("button", name="新对话", exact=True).click()
            composer = page.get_by_placeholder("输入你的问题")
            composer.fill("可用性验收数据库及代号是什么？根据背景用一句话回答，不调用工具。")
            page.get_by_role("button", name="发送消息", exact=True).click()
            expect(page.get_by_text("FallbackT52", exact=False).last).to_be_visible(timeout=240000)
            # 回答结束后输入框为空，发送按钮按产品规则仍禁用；按按钮文本恢复判断收口。
            expect(page.get_by_role("button", name="发送消息", exact=True)).to_be_visible(timeout=240000)
            page.get_by_role("button", name="上下文", exact=True).click()
            expect(page.get_by_text("本轮实际注入的长期记忆", exact=True)).to_be_visible()
            expect(page.get_by_text("语义 + 词法", exact=True)).to_be_visible()
            expect(page.get_by_text(fallback["id"], exact=False)).to_be_visible()
            assert not errors, "浏览器有 console/page error"
            browser.close()
        print(json.dumps({"real_api_conversations": len(cases), "browser_chat_conversations": 1,
            "cases": cases, "browser_memory_diagnostics": "passed", "browser_errors": len(errors)}, ensure_ascii=False, indent=2))
    finally:
        try:
            request("PATCH", "/api/settings", original)
        finally:
            client.close()
            with SessionLocal() as db:
                rows = list(db.scalars(select(UserMemory).where(UserMemory.id.in_(memories), UserMemory.user_id == user_id)))
                for row in rows:
                    row.supersedes_memory_id = None
                db.flush()
                db.execute(delete(UserMemory).where(UserMemory.id.in_(memories), UserMemory.user_id == user_id))
                db.commit()
            for project in projects:
                cleanup(user_id=user_id, project_id=project)


if __name__ == "__main__":
    run()

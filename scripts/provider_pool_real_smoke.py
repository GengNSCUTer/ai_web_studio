"""通过真实浏览器连续发送两轮在线 Chat，并核对最终回答的数据库状态。

使用固定测试账号和已有在线凭据，不输出 Token/API Key，不启动本地模型。
临时会话经正式删除接口清理，工作区也只删除本脚本创建的一份。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import SessionLocal  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.message import Message  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_setting import UserSetting  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402


def main():
    frontend = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32008")
    backend = os.getenv("AIWS_E2E_BACKEND_URL", "http://127.0.0.1:32007")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "1528713326@qq.com"))
        if user is None:
            raise RuntimeError("固定测试账号不存在")
        setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        if setting is None or setting.provider_type != "openai-compatible":
            raise RuntimeError("本测试仅允许账号已配置的在线 Provider")
        project = Project(user_id=user.id, name=f"provider-pool-smoke-{uuid4().hex[:8]}")
        db.add(project)
        db.flush()
        conversation = Conversation(
            user_id=user.id, project_id=project.id, title="Provider 连接池临时验收",
            model_name="deepseek-ai/DeepSeek-V3.2",
        )
        db.add(conversation)
        db.commit()
        project_id, conversation_id = project.id, conversation.id
        token = AuthService(UserRepository(db)).create_access_token(user)

    errors = []
    result = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": frontend, "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            page.set_default_timeout(180000)
            page.on("pageerror", lambda _: errors.append("pageerror"))
            page.on("console", lambda msg: errors.append("console_error") if msg.type == "error" else None)
            page.goto(f"{frontend}/chat?conversation={conversation_id}", wait_until="networkidle")
            composer = page.get_by_placeholder("输入你的问题")
            expect(composer).to_be_visible()
            for marker in ("POOL_FIRST_OK", "POOL_SECOND_OK"):
                composer.fill(f"本次仅验证对话传输，请只回答 {marker}。不联网，不检索，不调用工具。")
                expect(page.get_by_role("button", name="发送消息")).to_be_enabled()
                with page.expect_response(lambda response: response.url.rstrip("/").endswith("/api/chat") and response.request.method == "POST") as pending:
                    page.get_by_role("button", name="发送消息").click()
                response = pending.value
                if response.status != 200:
                    raise AssertionError(f"Chat HTTP {response.status}")
                response.body()
                # 完成后输入框为空，发送按钮理应禁用；以停止按钮消失判断流收口。
                expect(page.get_by_role("button", name="停止生成")).to_have_count(0)
                with SessionLocal() as db:
                    rows = list(db.scalars(select(Message).where(Message.conversation_id == conversation_id, Message.role == "assistant").order_by(Message.created_at, Message.sequence)))
                    if not rows or rows[-1].status != "done" or marker not in (rows[-1].content or ""):
                        raise AssertionError("回答未按预期持久化为 done")
                    result[marker] = {"status": rows[-1].status, "chars": len(rows[-1].content)}
            if errors:
                raise AssertionError(f"浏览器错误数量：{len(errors)}")
            page.screenshot(path="/tmp/provider-pool-real-smoke.png", full_page=True)
            browser.close()
        print(json.dumps({"model": "deepseek-ai/DeepSeek-V3.2", "browser_errors": len(errors), "turns": result}, ensure_ascii=False))
    finally:
        with httpx.Client(base_url=backend, headers={"Authorization": f"Bearer {token}"}, timeout=30, trust_env=False) as client:
            response = client.delete(f"/api/conversations/{conversation_id}")
            if response.status_code not in (200, 204, 404):
                raise RuntimeError(f"临时会话清理失败：HTTP {response.status_code}")
        with SessionLocal() as db:
            project = db.get(Project, project_id)
            if project is not None:
                db.delete(project)
                db.commit()


if __name__ == "__main__":
    main()

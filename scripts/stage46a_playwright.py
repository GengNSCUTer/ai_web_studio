"""阶段 4.6A 前端审批交互冒烟：用受控流事件验证 Diff 展示与确认收口。"""

import json
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
import time


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        console_errors = []
        page.on("pageerror", lambda error: console_errors.append(f"pageerror: {error}"))
        page.on(
            "console",
            lambda message: console_errors.append(f"console: {message.text}")
            if message.type == "error"
            else None,
        )

        def mock_messages(route):
            """模拟流结束后的消息刷新，保留待审批工具事件。"""
            path_parts = [part for part in urlparse(route.request.url).path.split("/") if part]
            conversation_id = path_parts[-2] if len(path_parts) >= 2 else "e2e-conversation"
            now = "2026-09-21T00:00:00Z"
            tool_event = {
                "type": "tool_confirmation_required",
                "call_id": "e2e-call",
                "tool_key": "workspace.files.apply_edit",
                "display_name": "工作区文件受控修改",
                "status": "waiting_approval",
                "approval_id": "approval-e2e",
                "file_name": "notes.md",
                "diff_text": "-旧内容\n+新内容",
                "reason": "已生成持久化 Diff，需用户确认",
            }
            messages = [
                {
                    "id": "user-e2e",
                    "conversation_id": conversation_id,
                    "role": "user",
                    "content": "请把项目文档中的旧内容修改为新内容",
                    "reasoning_content": None,
                    "external_sources": None,
                    "tool_events": [],
                    "status": "done",
                    "created_at": now,
                    "updated_at": now,
                    "attachments": [],
                },
                {
                    "id": "assistant-e2e",
                    "conversation_id": conversation_id,
                    "role": "assistant",
                    "content": "已生成文件修改提案，请确认后应用。",
                    "reasoning_content": None,
                    "external_sources": None,
                    "tool_events": [tool_event],
                    "status": "done",
                    "created_at": now,
                    "updated_at": now,
                    "attachments": [],
                },
            ]
            route.fulfill(status=200, content_type="application/json", body=json.dumps(messages))

        page.route("**/api/backend/conversations/*/messages", mock_messages)

        # 页面其它初始化接口只需要可渲染的最小响应；流式请求和审批接口单独断言。
        page.route(
            "**/api/chat",
            lambda route: route.fulfill(
                status=200,
                headers={
                    "content-type": "application/x-ndjson; charset=utf-8",
                    "x-conversation-id": "e2e-conversation",
                },
                body=(
                    '{"type":"tool_confirmation_required","call_id":"e2e-call",'
                    '"tool_key":"workspace.files.apply_edit","display_name":"工作区文件受控修改",'
                    '"status":"waiting_approval","approval_id":"approval-e2e",'
                    '"file_name":"notes.md","diff_text":"-旧内容\\n+新内容",'
                    '"reason":"已生成持久化 Diff，需用户确认"}\n'
                ),
            ),
        )
        page.route(
            "**/api/backend/agent-runtime/approvals/approval-e2e/challenge",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"approval_id":"approval-e2e","approval_token":"token-e2e"}',
            ),
        )
        page.route(
            "**/api/backend/agent-runtime/approvals/approval-e2e/apply",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=(
                    '{"run_id":"run-e2e","step_id":"step-e2e",'
                    '"patch_draft_id":"draft-e2e","approval_id":"approval-e2e",'
                    '"file_id":"file-e2e","revision_id":"revision-e2e",'
                    '"revision_number":2,"status":"applied"}'
                ),
            ),
        )
        page.route(
            "**/api/backend/agent-runtime/approvals/approval-e2e/reject",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"status":"rejected"}',
            ),
        )

        page.goto("http://127.0.0.1:32008/chat")
        page.wait_for_load_state("networkidle")
        if "登录" in page.locator("body").inner_text() and page.get_by_role("button", name="注册").count():
            suffix = str(int(time.time()))
            page.get_by_role("button", name="注册").click()
            page.get_by_placeholder("请输入用户名").fill(f"stage46a_ui_{suffix}")
            page.get_by_placeholder("you@example.com").fill(f"stage46a-ui-{suffix}@gmail.com")
            page.get_by_placeholder("至少 8 位").fill("stage46a-password")
            page.get_by_role("button", name="注册并进入").click()
            page.wait_for_timeout(2000)
            page.reload(wait_until="networkidle")

        # 已登录页面应有聊天输入；真实聊天请求被受控 NDJSON 替身接管。
        if page.get_by_placeholder("输入你的问题").count() == 0:
            page.screenshot(path="/tmp/stage46a-failure.png", full_page=True)
            raise AssertionError(f"未进入聊天页：{page.url}\n{page.locator('body').inner_text()[:2000]}")
        composer = page.get_by_placeholder("输入你的问题")
        composer.wait_for(timeout=10000)
        composer.fill("请把项目文档中的旧内容修改为新内容")
        page.get_by_role("button", name="发送消息").click()
        page.get_by_text("工具过程 · 1").wait_for(timeout=10000)
        page.get_by_text("工具过程 · 1").click()
        if page.get_by_text("工作区文件受控修改").count() == 0:
            page.screenshot(path="/tmp/stage46a-stream-failure.png", full_page=True)
            raise AssertionError(page.locator("body").inner_text()[-3000:])
        page.get_by_text("工作区文件受控修改").wait_for(timeout=10000)
        page.get_by_text("查看详情").click()
        page.get_by_text("已生成持久化 Diff，需用户确认").wait_for(timeout=10000)
        page.get_by_role("button", name="确认并应用 Diff").click()
        page.get_by_text("已通过版本 CAS 应用修改").wait_for(timeout=10000)
        if console_errors:
            page.screenshot(path="/tmp/stage46a-console-failure.png", full_page=True)
            raise AssertionError("浏览器控制台出现错误：\n" + "\n".join(console_errors))
        page.screenshot(path="/tmp/stage46a-approval.png", full_page=True)
        browser.close()


if __name__ == "__main__":
    main()

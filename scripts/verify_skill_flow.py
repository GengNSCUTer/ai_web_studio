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
from app.models.message import Message  # noqa: E402
from app.models.tool_trace import ToolRouteRun  # noqa: E402
from app.models.tool_config import UserSkillInstallation  # noqa: E402
from app.models.user import User  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402


BASE_URL = os.getenv("AIWS_E2E_BASE_URL", "http://localhost:3002").rstrip("/")
USER_EMAIL = os.getenv("AIWS_E2E_USER_EMAIL", "").strip().lower()
SKILL_KEY = "research.web-brief"


def build_test_token() -> tuple[str, str]:
    with SessionLocal() as db:
        if USER_EMAIL:
            user = db.scalars(select(User).where(User.email == USER_EMAIL).limit(1)).first()
        else:
            user = db.scalars(
                select(User)
                .join(UserSkillInstallation, UserSkillInstallation.user_id == User.id)
                .where(
                    UserSkillInstallation.skill_key == SKILL_KEY,
                    UserSkillInstallation.is_enabled.is_(True),
                )
                .order_by(UserSkillInstallation.updated_at.desc())
                .limit(1)
            ).first()
        if not user:
            raise RuntimeError("Configured E2E user was not found")
        return AuthService(UserRepository(db)).create_access_token(user), user.id


def assert_persisted_trace(user_id: str) -> None:
    with SessionLocal() as db:
        route_runs = list(
            db.scalars(
                select(ToolRouteRun)
                .where(ToolRouteRun.user_id == user_id)
                .order_by(ToolRouteRun.created_at.desc())
                .limit(10)
            ).all()
        )
        matched: tuple[ToolRouteRun, list[dict]] | None = None
        for route_run in route_runs:
            events = json.loads(route_run.events_json or "[]")
            if any(
                isinstance(event, dict)
                and event.get("type") == "skill_activation"
                and event.get("skill_key") == SKILL_KEY
                for event in events
            ):
                matched = (route_run, events)
                break
        if not matched:
            raise AssertionError("No persisted Skill activation trace was found")

        route_run, events = matched
        plan = json.loads(route_run.plan_json or "{}")
        tool_keys = {
            str(call.get("tool_key"))
            for call in plan.get("calls") or []
            if isinstance(call, dict)
        }
        if not tool_keys or not tool_keys.issubset({"web.tavily.search"}):
            raise AssertionError(f"Skill plan escaped its allowlist: {sorted(tool_keys)}")
        if not any(
            isinstance(event, dict)
            and event.get("type") == "skill_result"
            and event.get("skill_key") == SKILL_KEY
            for event in events
        ):
            raise AssertionError("Persisted trace is missing skill_result")
        assistant = db.scalars(
            select(Message).where(Message.id == route_run.assistant_message_id).limit(1)
        ).first()
        if not assistant or assistant.status != "done" or not (assistant.content or "").strip():
            raise AssertionError("The final LLM answer was not persisted successfully")


def run() -> None:
    token, user_id = build_test_token()
    captured_payload: dict = {}
    console_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.add_cookies(
            [
                {
                    "name": "aiws_token",
                    "value": token,
                    "url": BASE_URL,
                    "httpOnly": True,
                    "sameSite": "Lax",
                }
            ]
        )
        page = context.new_page()
        page.on(
            "console",
            lambda message: console_errors.append(message.text) if message.type == "error" else None,
        )

        def capture_chat_request(request) -> None:
            if request.method == "POST" and request.url.rstrip("/").endswith("/api/chat"):
                try:
                    captured_payload.update(request.post_data_json or {})
                except Exception:
                    pass

        page.on("request", capture_chat_request)
        page.goto(f"{BASE_URL}/chat", wait_until="networkidle", timeout=60_000)
        skill_probe = page.evaluate(
            """async () => {
                const response = await fetch('/api/backend/tools/skills', {cache: 'no-store'});
                let body = null;
                try { body = await response.json(); } catch { body = null; }
                return {status: response.status, body};
            }"""
        )
        if skill_probe.get("status") != 200:
            raise AssertionError(f"Skill API probe failed with status {skill_probe.get('status')}")
        if not any(
            item.get("skill_key") == SKILL_KEY and item.get("is_ready")
            for item in (skill_probe.get("body") or [])
            if isinstance(item, dict)
        ):
            raise AssertionError("Research Skill is not ready in the authenticated API response")
        selector = page.get_by_test_id("skill-selector")
        expect(selector).to_be_visible(timeout=30_000)
        expect(selector).to_be_enabled(timeout=30_000)
        selector.click()
        option = page.get_by_test_id(f"skill-option-{SKILL_KEY}")
        expect(option).to_be_visible(timeout=10_000)
        option.click()
        expect(selector).to_contain_text("联网研究简报")

        composer = page.locator("textarea").last
        composer.fill("请联网核查 Python 3.14 当前最新稳定小版本，并给出简短结论、来源和不确定性。")
        submit = page.locator('button[type="submit"]')
        expect(submit).to_be_enabled()
        submit.click()
        page.wait_for_function(
            "() => document.querySelector('button[type=submit]')?.disabled === true",
            timeout=10_000,
        )
        page.wait_for_function(
            """() => {
                const button = document.querySelector('button[type=submit]');
                const text = button?.textContent?.trim() || '';
                return text === '发送消息' || text === 'Send';
            }""",
            timeout=240_000,
        )
        if captured_payload.get("skillKey") != SKILL_KEY:
            raise AssertionError("Frontend chat request did not include the selected skillKey")
        page.screenshot(path="/tmp/aiws-skill-flow-desktop.png", full_page=True)

        mobile = context.new_page()
        mobile.set_viewport_size({"width": 390, "height": 844})
        mobile.goto(f"{BASE_URL}/chat", wait_until="networkidle", timeout=60_000)
        mobile_selector = mobile.get_by_test_id("skill-selector")
        expect(mobile_selector).to_be_visible(timeout=30_000)
        mobile_selector.click()
        expect(mobile.get_by_test_id(f"skill-option-{SKILL_KEY}")).to_be_visible(timeout=10_000)
        mobile.screenshot(path="/tmp/aiws-skill-flow-mobile.png", full_page=True)
        browser.close()

    if console_errors:
        raise AssertionError(f"Browser console errors: {console_errors[:3]}")
    assert_persisted_trace(user_id)
    print("skill-flow-ok: explicit-selection, planner, workflow, final-answer, trace")


if __name__ == "__main__":
    run()

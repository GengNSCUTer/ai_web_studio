"""用测试账号已配置的在线模型验证 Planner 新旧 JSON 兼容。"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_setting import UserSetting  # noqa: E402
from app.services.secret_service import SecretService  # noqa: E402
from app.services.tools.planner import LLMToolPlanner, PlannerRuntime  # noqa: E402


async def main() -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == os.getenv("AIWS_E2E_USER_EMAIL", "1528713326@qq.com").lower()))
        if not user:
            raise RuntimeError("固定测试账号不存在。")
        setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        if not setting or setting.provider_type != "openai-compatible":
            raise RuntimeError("测试账号未配置在线 openai-compatible Provider。")
        runtime = PlannerRuntime(
            provider_type=setting.provider_type, base_url=setting.api_base_url,
            api_key=SecretService().decrypt(setting.api_key), model_name=setting.default_model,
        )
    plan = await LLMToolPlanner().plan(
        query=os.getenv("AIWS_E2E_QUERY", "分别查深圳和广州的天气，然后对比两地天气。"),
        enabled=True, runtime=runtime,
    )
    if plan.router != "llm_tool_planner_v1" or not plan.calls:
        raise AssertionError("在线模型没有返回可执行的 LLM Tool Plan。")
    if plan.execution_mode not in {"sync", "durable_candidate"}:
        raise AssertionError("在线模型执行方式建议未被正确归一化。")
    print({"planner": plan.router, "execution_mode": plan.execution_mode, "tool_keys": [call.tool_key for call in plan.calls]})


if __name__ == "__main__":
    asyncio.run(main())

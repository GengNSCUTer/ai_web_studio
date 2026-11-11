"""固定顺序对话回放触发时机；模型用受控空数组，不能解释为记忆质量评测。"""

import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import app.models  # noqa: E402,F401
from app.core.database import Base  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_setting import UserSetting  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.message import Message  # noqa: E402
from app.repositories.setting_repo import UserSettingRepository  # noqa: E402
from app.services.setting_service import SettingService  # noqa: E402
from app.services.memory_candidate_runtime import MemoryCandidateWorker, MemoryExtractionJobService  # noqa: E402
from app.services.memory_extraction_policy import explicit_remember  # noqa: E402


def run():
    cases = json.loads((ROOT / "backend/evals/memory_trigger_cases.json").read_text())
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    results = []
    try:
        for index, case in enumerate(cases):
            with sessions() as db:
                user = User(username=f"trigger-eval-{index}", email=f"trigger-{index}@example.test")
                db.add(user)
                db.flush()
                conv = Conversation(user_id=user.id, model_name="controlled-not-online")
                db.add(conv)
                db.commit()
                uid, cid = user.id, conv.id
                SettingService(UserSettingRepository(db)).get_or_create_user_settings(uid)
                setting = db.scalar(select(UserSetting).where(UserSetting.user_id == uid))
                setting.memory_auto_candidate_enabled = True
                setting.memory_auto_candidate_turn_interval = 4
                db.commit()
            provider = type("Provider", (), {"complete_chat": AsyncMock(return_value="[]")})()
            worker = MemoryCandidateWorker(session_factory=sessions, owner="trigger-eval", provider_service=provider)
            old_pending, old_turns, new_turns = [], [], []
            for turn, text in enumerate(case["messages"], 1):
                # 回放改造前的条件：累计四条用户消息，或存在显式记住；成功后清空批次。
                old_pending.append(text)
                if len(old_pending) >= 4 or any(explicit_remember(text) for text in old_pending):
                    old_turns.append(turn)
                    old_pending.clear()
                with sessions() as db:
                    source = Message(conversation_id=cid, role="user", sequence=turn * 2 - 1, content=text)
                    assistant = Message(conversation_id=cid, role="assistant", sequence=turn * 2, status="done", content="受控夹具")
                    db.add_all([source, assistant])
                    db.commit()
                    job = MemoryExtractionJobService(db).enqueue_after_turn(user_id=uid,
                        conversation_id=cid, assistant_message_id=assistant.id)
                if job:
                    new_turns.append(turn)
                    asyncio.run(worker.run_once())
            results.append({"case": case["name"], "expected_turns": case["expected_turns"],
                "old_trigger_turns": old_turns, "new_trigger_turns": new_turns,
                "new_extraction_calls": provider.complete_chat.await_count,
                "passed": new_turns == case["expected_turns"]})
        report = {"scope": "12个构造顺序对话；只比较触发时机，提取模型为受控空数组，不衡量事实提取精度或真实Token成本",
            "cases": results, "passed": sum(row["passed"] for row in results), "total": len(results),
            "user_turns": sum(len(case["messages"]) for case in cases),
            "old_extraction_calls": sum(len(row["old_trigger_turns"]) for row in results),
            "new_extraction_calls": sum(row["new_extraction_calls"] for row in results)}
        output = ROOT / "backend/evals/results/memory-trigger-20261111.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "cases"}, ensure_ascii=False))
        assert report["passed"] == report["total"], "触发回放未全部符合预期，保留原始失败结果"
    finally:
        engine.dispose()


if __name__ == "__main__":
    run()

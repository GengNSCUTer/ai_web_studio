"""用生产 Chat 上下文和真实在线模型复核技术回答，避免只验收工具与引用。

仅创建临时项目、公开技术问题和对话；不打印用户设置、密钥或其他会话。
诊断模式可保留不通过的结果，正式验收默认要求事实检查全部通过。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.core.database import SessionLocal  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.message import ChatStreamRequest  # noqa: E402
from app.services.chat_execution_service import ChatExecutionService  # noqa: E402
from app.services.chat_provider_service import ChatProviderService  # noqa: E402
from app.services.skill_catalog import SkillCatalog  # noqa: E402
from stage46a_real_e2e import prepare_fixture, cleanup  # noqa: E402

QUERY = (
    "用 Context7 定位 SQLAlchemy 2.0 官方文档，然后实际查证 ORM Session 的 flush 和 commit 的区别，"
    "说明事务是否结束、连接是否归还、commit 是否立即开启新的数据库事务。"
    "区分 API 本身行为与示例后续操作，200 字以内并给来源。"
)


def check_transaction_answer(answer: str) -> dict[str, bool]:
    """这是固定 SQLAlchemy 场景的验收规则，不进入生产业务或改写模型答案。"""

    text = re.sub(r"[*`\s]", "", answer).lower()
    denies_immediate = bool(re.search(r"(?:不会|不|并非|不是|没有|未|否).{0,15}立即.{0,15}(?:开启|开始|启动|产生|新事务)", text))
    later_trigger = bool(re.search(
        r"(?:后续|下一次|首次|下次).{0,30}(?:操作|sql|查询|execute|访问).{0,30}(?:触发|开启|开始|启动).{0,15}(?:事务|transaction)", text
    ))
    # 正确答案可以直接说明“后续操作开启事务”，不必逐字重复“不立即”。
    # 同时出现“立即开启”时仍需明确否定，不能仅凭后续关键词放过矛盾回答。
    immediate_claim = bool(re.search(r"(?:立即|马上|立刻).{0,15}(?:开启|开始|启动|产生).{0,15}(?:事务|transaction)", text))
    return {
        "flush_not_commit": bool(re.search(r"flush.{0,90}(?:不提交|未提交|仍可回滚|不结束|事务保持打开|事务仍)", text)),
        "commit_ends_transaction": bool(re.search(r"commit.{0,100}(?:结束|提交).{0,30}事务", text)),
        "connection_returned": bool(re.search(r"(?:归还|释放|返回).{0,20}(?:连接|池)", text)),
        "not_immediate_new_transaction": denies_immediate or (later_trigger and not immediate_claim),
        "subsequent_operation_trigger": bool(re.search(r"(?:后续|下一次|首次|下次).{0,80}(?:操作|sql|查询|execute|访问)", text)),
    }


async def run() -> None:
    _, user_id, project_id, _, _ = prepare_fixture()
    try:
        with SessionLocal() as db:
            catalog = SkillCatalog()
            catalog.install_or_update(db=db, user_id=user_id, skill_key="knowledge.technical-docs", is_enabled=True)
            user = db.get(User, user_id)
            conversation = Conversation(user_id=user_id, project_id=project_id, title="公共文档事实回归",
                                        model_name="deepseek-ai/DeepSeek-V3.2")
            db.add(conversation)
            db.commit()
            context = await ChatExecutionService(db=db, current_user=user).prepare_chat_execution(
                ChatStreamRequest(conversation_id=conversation.id, content=QUERY, web_search_enabled=True,
                                  skill_key="knowledge.technical-docs", tool_run_mode="guided_research")
            )
            # 最终模型调用之前先退出短事务，保存的参数只在内存传递。
            sources = context.external_sources
        answer = "".join([part async for part in ChatProviderService().stream_chat(
            provider_type=context.provider_type, base_url=context.base_url, api_key=context.api_key,
            model_name=context.resolved_model, messages=context.history_messages,
            temperature=context.temperature, top_p=context.top_p, max_tokens=context.max_tokens,
        )])
        checks = check_transaction_answer(answer)
        prompt_text = "\n".join(str(message.get("content", "")) for message in context.history_messages)
        report = {"model": context.resolved_model, "answer": answer, "checks": checks,
                  "skill_version": catalog._definitions["knowledge.technical-docs"]["version"],
                  "documentation_queries": [event.get("arguments", {}).get("query") for event in context.tool_events
                                             if event.get("type") == "tool_call_start" and event.get("tool_key") == "docs.context7.query"],
                  "sources": [{"type": source["source_type"], "title": source["title"],
                               "url": source.get("url"),
                               "used": source["used_in_prompt"], "label": source["citation_label"],
                               "chars": len(source["display_text"])} for source in sources],
                  "conditional_evidence_in_final_prompt": {
                      phrase: phrase in prompt_text for phrase in
                      ("next statement", "next time", "session.execute", "后续操作", "触发条件")
                  }}
        print(json.dumps(report, ensure_ascii=False), flush=True)
        if os.getenv("AIWS_E2E_ENFORCE_FACTS", "1") != "0" and not all(checks.values()):
            raise AssertionError("技术文档最终回答的事实回归未通过。")
    finally:
        cleanup(user_id=user_id, project_id=project_id)


if __name__ == "__main__":
    asyncio.run(run())

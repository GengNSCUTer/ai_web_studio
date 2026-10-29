"""基于原文复核证据并起草独立新题；整个流程不调用检索器。"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.knowledge import KnowledgeBase, KnowledgeDocument
from app.repositories.knowledge_repo import KnowledgeChunkRepository
from app.repositories.setting_repo import UserSettingRepository
from app.services.setting_service import SettingService
from app.services.chat_provider_service import ChatProviderService, resolve_provider_base_url
from app.services.knowledge_e2e_evaluation_service import _parse_json_object


async def run(args):
    fixture = json.loads(args.fixture.read_text())
    with SessionLocal() as db:
        base = db.get(KnowledgeBase, args.base)
        service = SettingService(UserSettingRepository(db))
        setting = service.get_or_create_user_settings(base.user_id)
        if setting.provider_type != "openai-compatible":
            raise ValueError("本次只使用已配置在线API")
        runtime = dict(provider_type=setting.provider_type,
            base_url=resolve_provider_base_url(provider_type=setting.provider_type,
                configured_api_base_url=setting.api_base_url, configured_ollama_base_url=setting.ollama_base_url),
            api_key=service.resolve_provider_api_key(base.user_id))
        documents = {}
        for doc in db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id == base.id)):
            chunks = KnowledgeChunkRepository(db).list_by_document(doc.id, base.user_id,
                index_generation=base.active_index_generation or "legacy")
            documents[doc.file_name] = {c.chunk_index: c.content for c in chunks}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = json.loads(args.output.read_text()) if args.output.exists() else {
        "name": args.output.stem, "schema_version": 6,
        "description": "原文优先、在线模型辅助标注、精确引文核验与独立审查；非专家穷尽标注",
        "labeling_policy": {"retrieval_blind": True, "draft_model": "deepseek-ai/DeepSeek-V3.2",
                            "review_model": "Qwen/Qwen2.5-72B-Instruct", "mode": args.mode},
        "cases": []}
    finished = {c["case_id"] for c in output["cases"]}
    semaphore = asyncio.Semaphore(3)

    async def complete(model, prompt, tokens=2000):
        return _parse_json_object(await asyncio.wait_for(ChatProviderService().complete_chat(
            **runtime, model_name=model, messages=[dict(role="user", content=prompt)],
            temperature=0, max_tokens=tokens), timeout=120))

    async def one(case):
        if case["case_id"] in finished:
            return
        async with semaphore:
            if not case.get("answerable", True):
                # 已有负例保留；其缺失判断不能由某几个返回片段证明。
                result = dict(case, expected_answer_claims=["当前资料不足以支持问题要求的具体信息，应明确说明证据不足。"])
            else:
                names = {s["document_file"] for s in case["expected_chunk_selectors"]}
                chunks = {(name, index): text for name in names for index, text in documents[name].items()}
                material = [{"file": name, "chunk": index, "text": text} for (name, index), text in chunks.items()]
                raw = await complete("Qwen/Qwen2.5-72B-Instruct",
                    "核对知识库题目。只能依据给定原文，不读取或推测检索排名。"
                    "生成2到5个可核验答案要点claims，并逐个列出充分支持答案的证据。"
                    "删除只是相关却不支持答案的旧目标。允许存在其他等价证据：relevance=2表示直接支持，1表示背景补充。"
                    "每段quote必须原文逐字复制30到100字符且属于该块。不要强凑多段。"
                    '返回JSON {"claims":["..."],"evidence":[{"document_file":"...","chunk_index":0,"quote":"...","relevance":2}],'
                    '"supported":true}。问题：' + case["query"] + "\n原文：" + json.dumps(material, ensure_ascii=False), 2200)
                if not raw or raw.get("supported") is not True:
                    raise ValueError("原文不足，不自动补事实")
                result = dict(case)
                evidence = raw.get("evidence", [])
                selectors = []
                accepted = []
                for e in evidence:
                    key = (e.get("document_file"), e.get("chunk_index"))
                    quote = e.get("quote", "")
                    if key not in chunks or len(quote) < 12 or quote not in chunks[key]:
                        raise ValueError("引文无法逐字核验")
                    if e.get("relevance") == 2:
                        selectors.append(dict(document_file=key[0], chunk_index=key[1], contains=quote))
                    accepted.append(e)
                if not selectors or not raw.get("claims"):
                    raise ValueError("缺少直接支持证据或答案要点")
                result.update(expected_chunk_selectors=selectors, evidence_review=accepted,
                              expected_answer_claims=raw["claims"])
            output["cases"].append(result)
            args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(dict(completed=len(output["cases"]), total=len(fixture["cases"]))), flush=True)

    results = await asyncio.gather(*(one(c) for c in fixture["cases"]), return_exceptions=True)
    errors = [type(r).__name__ for r in results if isinstance(r, Exception)]
    if errors:
        print(json.dumps({"review_errors": errors}), flush=True)
        raise RuntimeError("原文复核存在失败；重跑仅补缺失项，不删除分母")
    order = {c["case_id"]: i for i, c in enumerate(fixture["cases"])}
    output["cases"].sort(key=lambda c: order[c["case_id"]])
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", required=True)
    p.add_argument("--fixture", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--mode", default="source_review")
    asyncio.run(run(p.parse_args()))

"""从活动论文文本生成可审计的评测草稿，不根据检索结果反向选择标签。"""

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


async def draft(base_id: str, output: Path) -> None:
    with SessionLocal() as db:
        base = db.get(KnowledgeBase, base_id)
        if not base:
            raise ValueError("知识库不存在")
        service = SettingService(UserSettingRepository(db))
        setting = service.get_or_create_user_settings(base.user_id)
        if setting.provider_type not in {"openai-compatible","vllm"}:
            raise ValueError("只允许在线兼容API，不启动本地模型")
        runtime = dict(provider_type=setting.provider_type,
            base_url=resolve_provider_base_url(provider_type=setting.provider_type,
                configured_api_base_url=setting.api_base_url, configured_ollama_base_url=setting.ollama_base_url),
            api_key=service.resolve_provider_api_key(base.user_id))
        snapshots = []
        for doc in db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id == base.id)
                              .order_by(KnowledgeDocument.file_name)):
            chunks = KnowledgeChunkRepository(db).list_by_document(doc.id, base.user_id,
                index_generation=base.active_index_generation or "legacy")
            if doc.index_status != "indexed" or not chunks:
                raise ValueError("有未完成索引的文档，不能生成评测")
            # 只用正文前段，不将参考文献和长附录变成简单的作者名匹配问题。
            selected = chunks[:min(24, len(chunks))]
            snapshots.append((doc.file_name, {c.chunk_index: c.content for c in selected}))
    output.parent.mkdir(parents=True, exist_ok=True)
    audit_path = output.with_suffix(".audit.json")
    completed = json.loads(audit_path.read_text()) if audit_path.exists() else {}
    manual_path=output.with_suffix(".manual.json")
    manual=json.loads(manual_path.read_text()) if manual_path.exists() else {}
    semaphore = asyncio.Semaphore(3)

    async def one(name, chunks):
        if name in completed:
            return
        async with semaphore:
            prompt = (
                "为论文知识库设计3个彼此不同的中文问题：方法细节、适用限制、机制原因各一个。"
                "不要泛泛询问论文是什么，不要只问作者年份，不得参考任何检索排名。"
                "每题必须由恰好2个不同文本块共同支持，逐个给出整数块编号和简短支持理由；不用复制原文，代码会读取对应完整块供独立审查。"
                "问题不要泄露答案，不要虚构正文之外事实。返回JSON："
                '{"cases":[{"query":"...","difficulty":"medium或hard",'
                '"expected_answer_keywords":["..."],"evidence":[{"chunk_index":1,"reason":"..."},{"chunk_index":2,"reason":"..."}]}]}'
                f"\n论文：{name}\n文本块：\n" + json.dumps(chunks, ensure_ascii=False))
            if name in manual:
                data={"cases":manual[name]}
            else:
                raw = await asyncio.wait_for(ChatProviderService().complete_chat(**runtime,
                    model_name="deepseek-ai/DeepSeek-V3.2", messages=[{"role":"user","content":prompt}],
                    temperature=0, top_p=.1, max_tokens=3500), timeout=90)
                from app.services.knowledge_e2e_evaluation_service import _parse_json_object
                data = _parse_json_object(raw)
            cases = data.get("cases", []) if data else []
            if len(cases) != 3:
                raise ValueError(f"{name}: 草稿题数不为3")
            for case in cases:
                evidence = case.get("evidence", [])
                evidence = list({e.get("chunk_index"):e for e in evidence}.values())
                case["evidence"] = evidence
                indices = {e.get("chunk_index") for e in evidence}
                if not 2 <= len(indices) <= 4 or len(indices) != len(evidence):
                    raise ValueError(f"{name}: 证据数量不合规")
                for e in evidence:
                    source = chunks.get(e["chunk_index"], "")
                    if len(source) < 20:
                        raise ValueError(f"{name}: 证据编号不属于提供的文本块")
                    e["quote"] = source
            completed[name] = cases
            audit_path.write_text(json.dumps(completed, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"drafted":name,"cases":len(cases)},ensure_ascii=False), flush=True)

    async def logged(s):
        try:
            return await one(*s)
        except Exception as exc:
            print(json.dumps({"document":s[0],"draft_error":type(exc).__name__,"validation":str(exc) if isinstance(exc,ValueError) else "provider_error"},ensure_ascii=False),flush=True)
            raise
    outcomes = await asyncio.gather(*(logged(s) for s in snapshots), return_exceptions=True)
    errors = [str(o) for o in outcomes if isinstance(o, Exception)]
    if errors:
        print(json.dumps({"draft_errors":errors},ensure_ascii=False), flush=True)
        raise RuntimeError("草稿未完整生成，可重跑继续；未写入固定评测集")
    original = json.loads((Path(__file__).resolve().parents[1]/"evals/rag_gold_set.json").read_text())
    cases = list(original["cases"])
    for doc_number, (name, _) in enumerate(snapshots, 1):
        for number, c in enumerate(completed[name], 1):
            cases.append(dict(case_id=f"paper_expanded_{doc_number:02}_{number}",query=c["query"],
                difficulty=c["difficulty"], expected_answer_keywords=c["expected_answer_keywords"],
                expected_chunk_selectors=[dict(document_file=name,chunk_index=e["chunk_index"],contains=e["quote"])
                                          for e in c["evidence"]],tags=["source-grounded-draft","multi-evidence", "expansion-20261027"]))
    queries = [c["query"].strip() for c in cases]
    if len(set(queries)) != len(queries):
        raise ValueError("出现完全相同的问题")
    output.write_text(json.dumps(dict(schema_version=4,name="paper_expanded_20261027",
        description="保留旧20题，新增每篇3题；基于原文生成、精确摘录校验，待独立证据审查；非外部盲测。",
        labeling_policy={"method":"source-first model-assisted; exact excerpt validation; independent review required",
                         "retrieval_blind":True,"previous_cases":20},cases=cases),ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"documents":len(snapshots),"cases":len(cases)},ensure_ascii=False))


async def review(base_id: str, output: Path) -> None:
    """独立模型审查问题与引用的支持关系；不读取任何检索结果。"""
    with SessionLocal() as db:
        base=db.get(KnowledgeBase,base_id)
        service=SettingService(UserSettingRepository(db))
        s=service.get_or_create_user_settings(base.user_id)
        runtime=dict(provider_type=s.provider_type,base_url=resolve_provider_base_url(provider_type=s.provider_type,
            configured_api_base_url=s.api_base_url,configured_ollama_base_url=s.ollama_base_url),api_key=service.resolve_provider_api_key(base.user_id))
    audit=json.loads(output.with_suffix(".audit.json").read_text())
    review_path=output.with_suffix(".review.json")
    reviewed=json.loads(review_path.read_text()) if review_path.exists() else {}
    semaphore=asyncio.Semaphore(3)
    async def one(name,cases):
        if name in reviewed:return
        async with semaphore:
            raw=await asyncio.wait_for(ChatProviderService().complete_chat(**runtime,model_name="Qwen/Qwen2.5-72B-Instruct",
                messages=[dict(role="user",content="审查以下论文问答评测标注。只判断给定原文摘录是否足以支持问题的核心答案，各段是否相关。"
                    "不补充外部知识，不因问题复杂而否决。不得把相关工作中其他方法的局限当作当前论文的方法局限。"
                    "恰好三个问题，只返回index为0、1、2各一次；不要对evidence单独编号。每题返回supported布尔值、简短reason。"
                    '返回JSON {"reviews":[{"index":0,"supported":true,"reason":"..."}]}。\n'
                    +json.dumps(dict(document=name,cases=cases),ensure_ascii=False))],temperature=0,top_p=.1,max_tokens=900),timeout=90)
            from app.services.knowledge_e2e_evaluation_service import _parse_json_object
            result=(_parse_json_object(raw) or {}).get("reviews",[])
            if len(result)!=3 or {r.get("index") for r in result}!={0,1,2} or any(type(r.get("supported")) is not bool for r in result):
                review_path.with_name("review_invalid_"+str(sorted(audit).index(name))+".json").write_text(json.dumps(dict(document=name,response=raw),ensure_ascii=False,indent=2))
                raise ValueError("独立证据审查输出格式错误")
            reviewed[name]=result
            review_path.write_text(json.dumps(reviewed,ensure_ascii=False,indent=2)+"\n")
            print(json.dumps(dict(reviewed=name,supported=sum(r["supported"] for r in result)),ensure_ascii=False),flush=True)
    outcomes=await asyncio.gather(*(one(name,cases) for name,cases in audit.items()),return_exceptions=True)
    if any(isinstance(o,Exception) for o in outcomes):
        print(json.dumps(dict(review_errors=[type(o).__name__ for o in outcomes if isinstance(o,Exception)])),flush=True)
        raise ValueError("证据审查未完成，尚未冻结题集，可重跑继续")
    fixture=json.loads(output.read_text())
    # 只按原文审查去掉无法回答的问题，不使用检索分数或实际答案挑题。
    rejected={f"paper_expanded_{n:02}_{r['index']+1}" for n,name in enumerate(sorted(audit),1) for r in reviewed[name] if not r["supported"]}
    exclusion_path=output.with_suffix(".exclusions.json")
    source_review=json.loads(exclusion_path.read_text()) if exclusion_path.exists() else {}
    rejected.update(source_review)
    fixture["cases"]=[c for c in fixture["cases"] if c["case_id"] not in rejected]
    fixture["description"]="保留旧20题；新增题由DeepSeek依原文起草、逐字引用校验、独立Qwen证据审查；非人工逐题标注或外部盲测。"
    fixture["labeling_policy"].update(review_model="Qwen/Qwen2.5-72B-Instruct",rejected_before_retrieval=sorted(rejected),
        maintainer_source_review=source_review)
    output.write_text(json.dumps(fixture,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(dict(final_cases=len(fixture["cases"]),rejected=len(rejected))),flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base",required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--review",action="store_true")
    args=parser.parse_args()
    asyncio.run(review(args.base,args.output) if args.review else draft(args.base,args.output))

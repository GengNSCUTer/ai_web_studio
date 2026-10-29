"""在调参前依据原文和既有题目起草新题，不读取检索结果。"""
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
    prior=json.loads(args.dev.read_text())
    with SessionLocal() as db:
        b=db.get(KnowledgeBase,args.base)
        s=SettingService(UserSettingRepository(db)); cfg=s.get_or_create_user_settings(b.user_id)
        runtime=dict(provider_type=cfg.provider_type,base_url=resolve_provider_base_url(
            provider_type=cfg.provider_type,configured_api_base_url=cfg.api_base_url,
            configured_ollama_base_url=cfg.ollama_base_url),api_key=s.resolve_provider_api_key(b.user_id))
        docs=list(db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id==b.id).order_by(KnowledgeDocument.file_name)))
        snapshots=[]
        # 覆盖多个文档；全文提供给出题模型，不依据任何召回结果选段。
        for d in docs[:8]:
            cs=KnowledgeChunkRepository(db).list_by_document(d.id,b.user_id,index_generation=b.active_index_generation or "legacy")
            snapshots.append((d.file_name,{c.chunk_index:c.content for c in cs}))
    output=dict(name=args.output.stem,schema_version=6,description="来源优先的新增验收题；与开发集做语义重复审查，不宣称外部盲测。",
        labeling_policy=dict(retrieval_blind=True,split="holdout",draft_model="deepseek-ai/DeepSeek-V3.2",
                             review_model="Qwen/Qwen2.5-72B-Instruct"),cases=[])
    sem=asyncio.Semaphore(2)
    async def one(n,snap):
        name,chunks=snap
        async with sem:
            count=12//len(snapshots)+(n<12%len(snapshots))
            raw=await asyncio.wait_for(ChatProviderService().complete_chat(**runtime,model_name="deepseek-ai/DeepSeek-V3.2",
                messages=[dict(role="user",content="根据原文生成"+str(count)+"个中文问题，覆盖具体机制、限制、步骤，不问目录或作者年份。"
                "每题明确资料/方法主体，不能与已有题是同一事实的改写。证据取自正文，给1到3个充分相关块编号和逐字20到80字符quote。"
                '返回JSON {"cases":[{"query":"...","expected_answer_claims":["..."],"evidence":[{"chunk_index":0,"quote":"..."}]}]}。'
                +"\n已有题："+json.dumps([c["query"] for c in prior["cases"]],ensure_ascii=False)
                +"\n文档："+name+"\n原文："+json.dumps(chunks,ensure_ascii=False))],temperature=0,max_tokens=2500),timeout=120)
            data=_parse_json_object(raw)
            if not data or len(data.get("cases",[]))!=count:raise ValueError("新题数量不完整")
            reviewed=await asyncio.wait_for(ChatProviderService().complete_chat(**runtime,model_name="Qwen/Qwen2.5-72B-Instruct",
                messages=[dict(role="user",content="核对新题与旧题是否存在核心事实重合，以及新题证据是否支持claims。"
                  "只依据给定原文，不借外部事实。逐题返回index, supported, novel布尔值。"
                  'JSON {"reviews":[{"index":0,"supported":true,"novel":true}]}\n'
                  +json.dumps(dict(new=data["cases"],old=[c["query"] for c in prior["cases"]],source=chunks),ensure_ascii=False))],
                  temperature=0,max_tokens=700),timeout=120)
            review=(_parse_json_object(reviewed) or {}).get("reviews",[])
            if len(review)!=count or {r.get("index") for r in review}!=set(range(count)) or any(
                r.get("supported") is not True or r.get("novel") is not True for r in review):
                raise ValueError("新题独立支持/重复检查不通过，保留失败，不看检索结果换题")
            for i,c in enumerate(data["cases"]):
                selectors=[]
                for e in c["evidence"]:
                    quote=e["quote"];idx=e["chunk_index"]
                    if idx not in chunks or len(quote)<12 or quote not in chunks[idx]:
                        raise ValueError("新题引文未逐字匹配")
                    selectors.append(dict(document_file=name,chunk_index=idx,contains=quote))
                output["cases"].append(dict(case_id=f"holdout_{n:02d}_{i:02d}",query=c["query"],answerable=True,
                    expected_chunk_selectors=selectors,expected_answer_claims=c["expected_answer_claims"],
                    tags=["holdout-source-first"]))
            print(json.dumps(dict(document=n,accepted=count)),flush=True)
    results=await asyncio.gather(*(one(n,s) for n,s in enumerate(snapshots)),return_exceptions=True)
    failures=[dict(document=snapshots[n][0],error_type=type(r).__name__,reason=str(r) if isinstance(r,ValueError) else "provider_error")
              for n,r in enumerate(results) if isinstance(r,Exception)]
    # 拒答题单独衡量，不把相关但没有答案的段落当正例。
    negatives=["本库资料是否规定所有线上系统统一的P99延迟服务保证？",
               "本库资料是否提供本项目测试账号的API密钥内容？",
               "本库资料是否给出2027年全部数据库产品的定价？",
               "本库资料是否规定本项目生产服务器的唯一部署密码？"]
    output["cases"] += [dict(case_id=f"holdout_negative_{i}",query=q,answerable=False,
        expected_answer_claims=["资料不足，不应编造具体值。"],tags=["holdout-source-first","no-answer"]) for i,q in enumerate(negatives)]
    output["generation_failures"]=failures
    output["cases"].sort(key=lambda c:c["case_id"])
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n")
    if failures:raise RuntimeError("部分新增题未通过原文/新颖性复核；报告缺口，不用检索分数筛选")

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--base",required=True)
    p.add_argument("--dev",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    asyncio.run(run(p.parse_args()))

"""真实 Chat 工具任务评测：逐层计数，保留失败，临时工作区测试结束后清理。"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select, delete

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.database import SessionLocal
from app.models import Message, ProjectFile, ToolRouteRun, ToolCallRun, User, Project, Conversation
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.tools.catalog import ToolCatalog
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import PlannedToolCall


FILE_QUERIES = [
    "读取 evaluation-notes.md，告诉我项目名称和保留天数，不要联网。",
    "找一下上传的备份保留笔记，项目代号和周期分别是什么？不要联网。",
    "Read evaluation-notes.md and report the project and retention period. Do not use the web.",
    "Search the uploaded notes for backup retention and state the project name and days. Don't use the web.",
    "工作区里的 evaluation-notes.md 有什么备份规定？请引用文件。不要联网。",
    "我忘了我们备份要留多久，帮我查当前项目的笔记，顺便报一下项目名，不要联网。",
    "查一下当前项目文件里 CedarLedger 的保留周期，不要用网上的信息。",
    "List the uploaded files, inspect the retention note, and give its project code and days. Don't search online.",
]
WEATHER_QUERIES = [
    "只用高德查深圳现在的天气和温度，不要网页搜索。",
    "查一下北京当前的天气和气温，使用高德，不要联网搜索。",
    "Use Amap to get current weather and temperature in 广州. Do not search the web.",
    "杭州现在气温多少、天气如何？请实际查高德，不要网页搜索。",
]
OTHER_CASES = [
    dict(group="multi", query="分别用高德查深圳和北京现在的天气与温度，不要网页搜索。",required=[["amap.maps.weather"]], min_weather=2),
    dict(group="multi", query="查深圳天气，同时读 evaluation-notes.md 给我项目名及保留天数，不要网页搜索。",required=[["amap.maps.weather"],["workspace.files.read","workspace.files.search"]],facts=["CedarLedger","17"]),
    dict(group="search",query="使用 Tavily 搜索 PostgreSQL 官方文档的 WAL 机制，给出来源链接和简短解释。不要访问本地文件。",required=[["web.tavily.search"]]),
    dict(group="search",query="使用 Tavily 搜索 FastAPI 官方关于 StreamingResponse 的资料，回答用途并给来源链接。不要读本地文件。",required=[["web.tavily.search"]]),
    dict(group="documentation",query="使用 Context7 查询 httpx 的 Timeout 配置方式，给出示例和来源。不要读本地文件。",required=[["docs.context7.resolve"],["docs.context7.query"]]),
    dict(group="documentation",query="通过 Context7 查 FastAPI 的 StreamingResponse 用法，简要说明，不读本地文件。",required=[["docs.context7.resolve"],["docs.context7.query"]]),
    dict(group="negative",query="不要使用任何工具，只回复：已收到。",required=[],facts=["已收到"],no_tools=True),
    dict(group="negative",query="Do not use any tools. Just reply received.",required=[],facts=["received"],no_tools=True),
]


def coverage(required, keys):
    return all(set(group) & set(keys) for group in required)


def task_cases():
    cases=[dict(group="file",query=q,required=[["workspace.files.read","workspace.files.search"]],facts=["CedarLedger","17"]) for q in FILE_QUERIES]
    return cases+[dict(group="weather",query=q,required=[["amap.maps.weather"]]) for q in WEATHER_QUERIES]+OTHER_CASES


def summarize(rows, denied):
    positive=[r for r in rows if r["group"]!="negative"]
    latencies=sorted(r["elapsed_ms"] for r in rows)
    all_calls=[c for r in rows for c in r.get("calls",[])]
    schema_total=sum(r.get("schema_total",0) for r in rows)
    return dict(cases=len(rows),task_passed=sum(r["passed"] for r in rows),task_success_rate=sum(r["passed"] for r in rows)/len(rows),
        candidate_task_coverage=sum(r.get("candidate_covered",False) for r in positive)/len(positive) if positive else None,
        planner_task_coverage=sum(r.get("planner_covered",False) for r in positive)/len(positive) if positive else None,
        online_planner_cases=sum(r.get("llm_planner",False) for r in rows),
        argument_schema_pass_rate=sum(r.get("schema_pass",0) for r in rows)/schema_total if schema_total else None,
        execution_success_rate=sum(c["status"]=="success" for c in all_calls)/len(all_calls) if all_calls else None,
        tool_calls=len(all_calls),no_tool_correct=sum(r.get("no_tool_correct") is True for r in rows),
        safety_rejected=sum(r["passed"] for r in denied),safety_cases=len(denied),
        mean_task_ms=statistics.mean(latencies),p95_task_ms=latencies[min(len(latencies)-1,int(len(latencies)*.95))])


def prepare_fixture():
    """独立 API 评测不依赖浏览器库，也不修改用户全局设置。"""
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=="1528713326@qq.com"))
        project=Project(user_id=user.id,name="tool-eval-"+uuid.uuid4().hex[:8],default_model="deepseek-ai/DeepSeek-V3.2")
        db.add(project);db.flush()
        f=ProjectFile(user_id=user.id,project_id=project.id,kind="text",file_name="evaluation-notes.md",
            mime_type="text/markdown",file_size=100,storage_key="eval/virtual-note.md",parsed_text="Evaluation note")
        db.add(f);db.commit()
        return AuthService(UserRepository(db)).create_access_token(user),user.id,project.id,f.id,project.name


def cleanup(*, user_id, project_id):
    with SessionLocal() as db:
        conversations=list(db.scalars(select(Conversation).where(Conversation.project_id==project_id,Conversation.user_id==user_id)))
        ids=[c.id for c in conversations]
        if ids:
            routes=list(db.scalars(select(ToolRouteRun.id).where(ToolRouteRun.conversation_id.in_(ids))))
            if routes:
                db.execute(delete(ToolCallRun).where(ToolCallRun.route_run_id.in_(routes)))
                db.execute(delete(ToolRouteRun).where(ToolRouteRun.id.in_(routes)))
            for c in conversations:db.delete(c)
        db.delete(db.get(Project,project_id));db.commit()


async def security(user_id, project_id):
    tests = [
        ("unknown_shell", "shell.exec", {"command":"echo forbidden"}),
        ("unknown_sql", "database.sql", {"query":"DELETE FROM files"}),
        ("missing_file_id", "workspace.files.read", {}),
        ("wrong_file_type", "workspace.files.read", {"file_id":42}),
        ("foreign_file", "workspace.files.read", {"file_id":"not-owned-evaluation-id"}),
        ("local_path", "workspace.files.read", {"file_id":"/etc/passwd"}),
    ]
    rows=[]
    with SessionLocal() as db:
        executor=ToolExecutor(db=db,user_id=user_id,project_id=project_id)
        for name,key,args in tests:
            definition=executor.catalog.get_or_none(key)
            call=PlannedToolCall(call_id=name,tool_key=key,provider=definition.provider if definition else "unknown",
                category=definition.category if definition else "unknown",display_name=name,confidence=1,reason="安全拒绝测试",arguments=args)
            result,events=await executor.execute(call)
            rows.append(dict(id=name,status=result.status,passed=result.status!="success" and not result.sources,
                evidence=[e.type for e in events]))
    return rows


def run(output: Path, backend: str):
    token,user_id,project_id,file_id,_=prepare_fixture()
    output.parent.mkdir(parents=True,exist_ok=True)
    rows=[]
    try:
        with SessionLocal() as db:
            f=db.get(ProjectFile,file_id)
            f.file_name="evaluation-notes.md"
            f.parsed_text="# CedarLedger backup retention\nProject code: CedarLedger.\nBackup retention: 17 days.\nOwner: research group.\n"
            db.commit()
        headers={"Authorization":"Bearer "+token}
        cases=task_cases()
        with httpx.Client(base_url=backend,headers=headers,timeout=240) as client:
            for index,case in enumerate(cases,1):
                started=time.perf_counter()
                row=dict(id=f"tool_task_{index:02}",group=case["group"],query=case["query"],passed=False)
                try:
                    response=client.post("/api/conversations",json=dict(title=f"tool-eval-{index}",project_id=project_id,model_name="deepseek-ai/DeepSeek-V3.2"))
                    response.raise_for_status(); cid=response.json()["id"]
                    with client.stream("POST","/api/chat/events-stream",json=dict(conversation_id=cid,content=case["query"],
                        model_name="deepseek-ai/DeepSeek-V3.2",web_search_enabled=True,tool_run_mode="workspace_review")) as stream:
                        stream.raise_for_status()
                        events=[json.loads(line) for line in stream.iter_lines() if line.strip()]
                    with SessionLocal() as db:
                        route=db.scalar(select(ToolRouteRun).where(ToolRouteRun.conversation_id==cid).order_by(ToolRouteRun.created_at.desc()))
                        if route is None:
                            raise ValueError("未找到工具轨迹")
                        answer=db.get(Message,route.assistant_message_id)
                        trace=json.loads(route.events_json or "[]")
                        calls=list(db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id==route.id)))
                        candidate_events=[e for e in trace if e.get("type")=="tool_candidate_selection"]
                        candidates=[c["tool_key"] for e in candidate_events for c in e.get("candidates",[])]
                        plans=[e for e in trace if e.get("type")=="tool_plan"]
                        planned=[c["tool_key"] for e in plans for c in e.get("calls",e.get("plan",{}).get("calls",[]))]
                        if not planned:
                            planned=[c["tool_key"] for c in json.loads(route.plan_json or "{}").get("calls",[])]
                        sources=[s for c in calls if c.status=="success" for s in json.loads(c.sources_json or "[]")]
                        facts_ok=all(f.lower() in answer.content.lower() for f in case.get("facts",[]))
                        source_ok=bool(sources) if case["required"] else not calls
                        if case["group"] in {"file","multi"} and case.get("facts"):
                            source_ok &= any(s.get("metadata",{}).get("file_id")==file_id for s in sources)
                        weather_calls=[c for c in calls if c.tool_key=="amap.maps.weather" and c.status=="success"]
                        if case["group"] in {"weather","multi"}:
                            source_ok &= len(weather_calls)>=case.get("min_weather",1)
                            for c in weather_calls:
                                temps=[str(s.get("metadata",{}).get("raw",{}).get("temperature","")) for s in json.loads(c.sources_json or "[]")]
                                numeric=[v for v in temps if re.fullmatch(r"-?\d+(\.\d+)?",v)]
                                facts_ok &= bool(numeric) and any(v in answer.content for v in numeric)
                        if case["group"] in {"search","documentation"}:
                            facts_ok &= bool(re.search(r"https?://",answer.content))
                        schema=[e for e in trace if e.get("type")=="tool_schema_validation"]
                        row.update(candidate_covered=coverage(case["required"],candidates),planner_covered=coverage(case["required"],planned),
                            llm_planner=any(e.get("strategy")=="llm_primary" for e in trace if e.get("type")=="tool_planner_end"),
                            no_tool_correct=(not calls) if case.get("no_tools") else None,
                            candidate_keys=list(dict.fromkeys(candidates)),planned_keys=list(dict.fromkeys(planned)),
                            calls=[dict(tool_key=c.tool_key,status=c.status) for c in calls],
                            schema_pass=sum(e.get("status")=="passed" for e in schema),schema_total=len(schema),
                            final_status=answer.status,answer=answer.content,source_checked=source_ok,facts_checked=facts_ok,
                            passed=answer.status=="done" and facts_ok and source_ok and coverage(case["required"],[c.tool_key for c in calls]))
                except Exception as exc:
                    row["error_type"]=type(exc).__name__
                    if isinstance(exc,httpx.HTTPStatusError):
                        row["http_status"]=exc.response.status_code
                row["elapsed_ms"]=round((time.perf_counter()-started)*1000,1)
                rows.append(row)
                output.write_text(json.dumps(dict(model="deepseek-ai/DeepSeek-V3.2",cases=rows),ensure_ascii=False,indent=2)+"\n")
                print(json.dumps({k:row.get(k) for k in ["id","group","passed","error_type","elapsed_ms"]}),flush=True)
        denied=asyncio.run(security(user_id,project_id))
        payload=dict(model="deepseek-ai/DeepSeek-V3.2",annotation="维护者预设任务与事实断言；同一小文件的问法变体，不是独立用户盲测；不包含写入和Durable成功率。",
            metrics=summarize(rows,denied),cases=rows,safety=denied)
        output.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
        print(json.dumps(payload["metrics"],ensure_ascii=False),flush=True)
    finally:
        cleanup(user_id=user_id,project_id=project_id)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--backend",default="http://127.0.0.1:32007")
    parser.add_argument("--rescore",action="store_true",help="仅从已存原始轨迹按当前标注重算，不重新调用模型")
    args=parser.parse_args()
    if args.rescore:
        payload=json.loads(args.output.read_text())
        for row,case in zip(payload["cases"],task_cases(),strict=True):
            if row["query"]!=case["query"]:raise ValueError("原始问题与当前标注不一致")
            row["candidate_covered"]=coverage(case["required"],row.get("candidate_keys",[]))
            row["planner_covered"]=coverage(case["required"],row.get("planned_keys",[]))
            row["passed"]=bool(row.get("final_status")=="done" and row.get("source_checked") and row.get("facts_checked")
                and coverage(case["required"],[c["tool_key"] for c in row.get("calls",[])]))
        payload["scoring_correction"]="仅修正两题预期工具标识为 Catalog 中的 web.tavily.search；未重跑或删除失败案例。"
        payload["metrics"]=summarize(payload["cases"],payload["safety"])
        args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
        print(json.dumps(payload["metrics"],ensure_ascii=False))
    else:
        run(args.output,args.backend)

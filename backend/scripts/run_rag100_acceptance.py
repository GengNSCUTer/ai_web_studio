"""先完成独立标注审核，再固定题集入库、检索矩阵和端到端评测；可从已保存阶段续跑。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT=Path(__file__).resolve().parents[2]


def run(args):
    state_path=ROOT/"backend/evals/results"/f"{args.corpus}_100_state_20261028.json"
    state=json.loads(state_path.read_text()) if state_path.exists() else {"base":args.base,"corpus":args.corpus}
    if state["base"]!=args.base:raise ValueError("不能混用知识库")
    fixture=ROOT/"backend/evals"/f"{args.corpus}_100_chinese_20261028.json"
    environment=dict(os.environ,PYTHONPATH=str(ROOT/"backend"))

    def call(script,arguments,stage):
        print(json.dumps(dict(stage=stage,status="starting")),flush=True)
        with (ROOT/"backend/evals/results"/f"{args.corpus}_100_{stage}_20261028.log").open("a") as log:
            outcome=subprocess.run([sys.executable,str(ROOT/script),*arguments],cwd=ROOT,env=environment,stdout=log,stderr=log)
        if outcome.returncode:raise RuntimeError(f"{stage}失败，详情保留在阶段日志")

    def persist():state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n")
    if not state.get("reviewed"):
        for attempt in range(3):
            try:
                call("backend/scripts/build_rag_100.py",["--base",args.base,"--corpus",args.corpus,"--output",str(fixture)],"build")
                break
            except RuntimeError:
                if attempt==2:raise
        data=json.loads(fixture.read_text())
        if data.get("ready_for_evaluation") is not True or len(data["cases"])!=100:raise ValueError("题集未审核完整")
        state["reviewed"]=True;persist()
    if not state.get("eval_set_id"):
        result=subprocess.run([sys.executable,str(ROOT/"backend/scripts/import_rag_gold_set.py"),"--knowledge-base-id",args.base,
            "--user-id",args.user,"--fixture",str(fixture)],cwd=ROOT,env=environment,text=True,capture_output=True)
        if result.returncode:raise RuntimeError("导入失败："+result.stderr[-1000:])
        state["eval_set_id"]=json.loads(result.stdout)["eval_set_id"];persist()
    eval_set=state["eval_set_id"]
    prefix=ROOT/"backend/evals/results"/f"{args.corpus}_100_20261028"
    if not state.get("matrix_complete"):
        call("backend/scripts/run_rag_eval_matrix.py",["--knowledge-base-id",args.base,"--user-id",args.user,
            "--eval-set-id",eval_set,"--top-k","20","--output",str(prefix)+"_matrix.json"],"matrix")
        state["matrix_complete"]=True;persist()
    call("backend/scripts/summarize_expanded_eval.py",["--matrix",str(prefix)+"_matrix.json","--output",str(prefix)+"_common6.json"],"summary")
    if not state.get("e2e_complete"):
        call("backend/scripts/run_rag_expanded_e2e.py",["--base",args.base,"--eval-set",eval_set,"--workers","2",
            "--output",str(prefix)+"_e2e.json"],"e2e")
        state["e2e_complete"]=True;persist()
    print(json.dumps(state,ensure_ascii=False),flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",required=True);p.add_argument("--user",required=True)
    p.add_argument("--corpus",choices=["paper","chinese"],required=True)
    run(p.parse_args())

"""同一固定工具候选集：旧规则、修复规则降级、真实在线语义混合对比。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import subprocess
import sys
import time
import types
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.database import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.tools.catalog import ToolCatalog  # noqa: E402
from app.services.tools.schemas import ToolDefinition  # noqa: E402
from app.services.tools.selector import ToolCandidateSelector  # noqa: E402
from app.services.tools.semantic_retrieval import ToolSemanticRetriever  # noqa: E402


def baseline_class(commit: str):
    code = subprocess.check_output(['git', 'show', f'{commit}:backend/app/services/tools/selector.py'], cwd=ROOT, text=True)
    module = types.ModuleType('candidate_baseline')
    sys.modules[module.__name__] = module
    exec(compile(code, 'baseline_selector.py', 'exec'), module.__dict__)
    return module.ToolCandidateSelector


def catalog_for(case: dict):
    catalog = ToolCatalog()
    if case.get('custom_tool'):
        tool = ToolDefinition(provider='test_mcp', source_type='mcp_server', **case['custom_tool'])
        catalog._definitions[tool.tool_key] = tool
    return catalog


def measure(case: dict, tools: list, trace: dict, elapsed: float) -> dict:
    keys = {tool.tool_key for tool in tools}
    required = case.get('required', [])
    hits = sum(bool(keys.intersection(group)) for group in required)
    forbidden = keys.intersection(case.get('forbidden', []))
    forbidden.update(tool.tool_key for tool in tools if tool.category in case.get('forbidden_categories', []))
    if 'allowed' in case:
        forbidden.update(keys-set(case['allowed']))
    return dict(id=case['id'], group=case['group'], query=case['query'], keys=[tool.tool_key for tool in tools],
                hits=hits, required_groups=len(required), complete=hits == len(required),
                forbidden=sorted(forbidden), empty_correct=not keys if case.get('empty') else None,
                elapsed_ms=round(elapsed, 2), retrieval=trace.get('retrieval'), candidates=trace.get('candidates'))


def summary(rows: list[dict]) -> dict:
    positive = [row for row in rows if row['required_groups']]
    negative = [row for row in rows if row['empty_correct'] is not None]
    elapsed = sorted(row['elapsed_ms'] for row in rows)
    return dict(cases=len(rows), positive_cases=len(positive), required_groups=sum(row['required_groups'] for row in positive),
        tool_group_recall_at_6=round(sum(row['hits'] for row in positive)/sum(row['required_groups'] for row in positive), 4),
        complete_coverage=round(sum(row['complete'] for row in positive)/len(positive), 4),
        empty_correct=sum(row['empty_correct'] for row in negative), empty_cases=len(negative),
        forbidden_leak_cases=sum(bool(row['forbidden']) for row in rows),
        mean_ms=round(statistics.mean(elapsed), 1), p95_ms=elapsed[min(len(elapsed)-1, int(len(elapsed)*.95))],
        semantic_successes=sum((row.get('retrieval') or {}).get('mode') == 'hybrid' for row in rows),
        semantic_errors=sum((row.get('retrieval') or {}).get('fallback_reason') in {'embedding_timeout','embedding_unavailable'} for row in rows))


async def evaluate(output: Path, gold_path: Path):
    gold = json.loads(gold_path.read_text())
    old_class = baseline_class(gold['baseline_commit'])
    with SessionLocal() as db:
        user_id = db.scalar(select(User.id).where(User.email == '1528713326@qq.com'))
    if not user_id:
        raise RuntimeError('固定测试账号不存在。')
    semantic = ToolSemanticRetriever(user_id=user_id)
    rows = {name: [] for name in ('baseline', 'rules_fallback', 'hybrid')}
    for case in gold['cases']:
        catalog = catalog_for(case)
        args = dict(query=case['query'], enabled=case.get('enabled', True),
            allowed_tool_keys=set(case['allowed']) if 'allowed' in case else None, recent_messages=case.get('history', []))
        for name, selector in [('baseline', old_class(catalog)), ('rules_fallback', ToolCandidateSelector(catalog)),
                               ('hybrid', ToolCandidateSelector(catalog, semantic_retriever=semantic))]:
            start = time.perf_counter()
            tools, trace = await selector.select_async(**args) if name == 'hybrid' else selector.select(**args)
            rows[name].append(measure(case, tools, trace, (time.perf_counter()-start)*1000))
        print(json.dumps({'completed':case['id'], 'old_hit':rows['baseline'][-1]['complete'],
                          'new_hit':rows['hybrid'][-1]['complete']}, ensure_ascii=False), flush=True)
    result = dict(version=gold['version'], gold_sha256=hashlib.sha256(gold_path.read_bytes()).hexdigest(),
                  baseline_commit=gold['baseline_commit'], annotation=gold['annotation'],
                  summaries={name: summary(values) for name, values in rows.items()},
                  groups={name: {group: summary([row for row in values if row['group']==group])
                           for group in sorted({row['group'] for row in values}) if any(row['required_groups'] for row in values if row['group']==group)}
                          for name,values in rows.items()}, cases=rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result['summaries'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT/'backend/evals/results/tool_candidate_v3_2026-10-11.json')
    parser.add_argument('--gold', type=Path, default=ROOT/'backend/evals/tool_candidate_gold_set.json')
    arguments = parser.parse_args()
    asyncio.run(evaluate(arguments.output, arguments.gold))

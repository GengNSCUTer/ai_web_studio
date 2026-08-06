"""Run model-backed end-to-end RAG evaluation for a persisted Gold Set.

The command deliberately keeps this run separate from the retrieval matrix:
retrieval metrics remain deterministic and cheap, while this command invokes
the configured chat provider for answer generation and LLM-as-a-Judge scoring.
No API key is written to the output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.core.database import SessionLocal
from app.core.startup import ensure_runtime_schema
from app.repositories.knowledge_repo import (
    KnowledgeBaseRepository,
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeEvalCaseRepository,
    KnowledgeEvalSetRepository,
)
from app.repositories.setting_repo import UserSettingRepository
from app.services.chat_provider_service import ChatProviderService, resolve_provider_base_url
from app.services.knowledge_e2e_evaluation_service import KnowledgeE2EEvaluationConfig, KnowledgeE2EEvaluationService
from app.services.knowledge_evaluation_service import KnowledgeEvaluationService
from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalPipeline
from app.services.setting_service import SettingService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--eval-set-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--model", help="Override the configured answer model")
    parser.add_argument("--judge-model", help="Override the configured judge model")
    parser.add_argument("--output", required=True, help="JSON result path")
    parser.add_argument("--markdown-output", help="Optional Markdown report path")
    return parser.parse_args()


def build_service(db: Any) -> tuple[KnowledgeE2EEvaluationService, SettingService]:
    setting_service = SettingService(UserSettingRepository(db))
    pipeline = KnowledgeRetrievalPipeline(
        chunk_repo=KnowledgeChunkRepository(db),
        setting_service=setting_service,
    )
    return KnowledgeE2EEvaluationService(
        retrieval_pipeline=pipeline,
        chat_provider=ChatProviderService(),
    ), setting_service


def _pct(value: Any) -> str:
    return "-" if value is None else f"{float(value) * 100:.2f}%"


def _ms(value: Any) -> str:
    return "-" if value is None else f"{float(value):.1f}"


def render_markdown(*, payload: dict[str, Any], knowledge_base_name: str, document_count: int, chunk_count: int) -> str:
    metrics = payload.get("metrics") or {}
    lines = [
        "# RAG 端到端答案评测报告",
        "",
        "> 本报告调用真实配置的 Chat Provider 生成答案，再使用独立 Judge 请求评估答案质量。结果与检索矩阵分开保存。",
        "",
        "## 评测范围",
        "",
        f"知识库 **{knowledge_base_name}** 当前包含 **{document_count} 份文档、{chunk_count} 个活动 Chunk**；固定 Gold Set 包含 **{payload.get('case_count', len(payload.get('cases') or []))} 个 Case**。检索阶段使用 `{payload.get('retrieval_mode')}` + Rerank={payload.get('rerank_enabled')}，候选 top-k={payload.get('top_k')}，Rerank 最终 top-n={(payload.get('config_snapshot') or {}).get('rerank_top_n', '-')}。",
        f"答案模型：`{payload.get('answer_model', '-')}`；独立 Judge：`{payload.get('judge_model', '-')}`。",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 数值 | 口径 |",
        "| --- | ---: | --- |",
        f"| Answer Faithfulness | {_pct(metrics.get('answer_faithfulness'))} | LLM Judge 判断答案事实是否被给定证据支持 |",
        f"| Answer Relevancy | {_pct(metrics.get('answer_relevancy'))} | LLM Judge 判断答案是否直接回答问题 |",
        f"| Citation Correctness | {_pct(metrics.get('citation_correctness'))} | LLM Judge 判断引用是否存在且支持对应结论 |",
        f"| Citation Validity | {_pct(metrics.get('citation_validity'))} | 确定性检查引用标签是否映射到本次返回的来源 |",
        f"| Ground-Truth Citation Precision | {_pct(metrics.get('ground_truth_citation_precision'))} | 被引用来源中属于人工目标 Chunk 的比例 |",
        f"| Ground-Truth Citation Recall | {_pct(metrics.get('ground_truth_citation_recall'))} | 人工目标 Chunk 被引用覆盖的比例 |",
        f"| 已完成 Judge 的 Case | {metrics.get('scored_case_count', 0)}/{metrics.get('case_count', 0)} | 缺少生成或 Judge 结果的 Case 不进入三个 Judge 均值 |",
        f"| 平均检索耗时 | {_ms(metrics.get('avg_retrieval_elapsed_ms'))} ms | 只含 RAG 检索 |",
        f"| 平均答案生成耗时 | {_ms(metrics.get('avg_generation_elapsed_ms'))} ms | Provider 非流式补全 |",
        f"| 平均 Judge 耗时 | {_ms(metrics.get('avg_judge_elapsed_ms'))} ms | Provider 非流式评审 |",
        f"| 检索/生成/Judge 失败 | {metrics.get('retrieval_failure_count', 0)}/{metrics.get('generation_failure_count', 0)}/{metrics.get('judge_failure_count', 0)} | 失败会保留在逐 Case 明细中 |",
        "",
        "## 逐 Case 明细",
        "",
        "| Case | 状态 | Faithfulness | Relevancy | Citation | 引用标签 | GT 引用 Precision | GT 引用 Recall |",
        "| --- | --- | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    for case in payload.get("cases") or []:
        judge = case.get("judge") or {}
        audit = case.get("citation_audit") or {}
        labels = ", ".join(case.get("citations") or []) or "-"
        lines.append(
            f"| {case.get('case_id', '-') } | {case.get('status', '-')} | {_pct(judge.get('answer_faithfulness'))} | {_pct(judge.get('answer_relevancy'))} | {_pct(judge.get('citation_correctness'))} | {labels} | {_pct(audit.get('ground_truth_citation_precision'))} | {_pct(audit.get('ground_truth_citation_recall'))} |"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- Faithfulness、Answer Relevancy 和 Citation Correctness 是 LLM-as-a-Judge 分数，依赖评审模型，不等于人工标注准确率。",
            "- Citation Validity、Ground-Truth Citation Precision/Recall 是确定性审计；Ground Truth 只代表评测者审阅的证据，不代表所有可接受证据。",
            "- 当前运行评估的是固定内部 Gold Set，仍需要更大、更多领域和多模型重复运行后，才能用于生产级结论。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    if args.top_k < 1 or args.top_k > 50:
        raise SystemExit("--top-k must be between 1 and 50")
    ensure_runtime_schema()
    with SessionLocal() as db:
        base_repo = KnowledgeBaseRepository(db)
        knowledge_base = base_repo.get_by_user(args.knowledge_base_id, args.user_id)
        eval_set = KnowledgeEvalSetRepository(db).get_by_user(args.eval_set_id, args.user_id)
        if not knowledge_base or not eval_set or eval_set.knowledge_base_id != knowledge_base.id:
            raise SystemExit("knowledge base or evaluation set not found")
        cases = KnowledgeEvalCaseRepository(db).list_by_eval_set(args.eval_set_id, args.user_id)
        if not cases:
            raise SystemExit("evaluation set has no cases")
        service, setting_service = build_service(db)
        user_settings = setting_service.get_or_create_user_settings(args.user_id)
        provider_type = str(user_settings.provider_type or "ollama")
        base_url = resolve_provider_base_url(
            provider_type=provider_type,
            configured_api_base_url=getattr(user_settings, "api_base_url", None),
            configured_ollama_base_url=getattr(user_settings, "ollama_base_url", None),
        )
        api_key = setting_service.resolve_provider_api_key(args.user_id)
        model_name = args.model or str(user_settings.default_model or "")
        if not model_name:
            raise SystemExit("chat model is not configured")
        retrieval_config = KnowledgeEvaluationService._build_retrieval_config(
            knowledge_base=knowledge_base,
            retrieval_mode="hybrid",
            rerank_enabled=True,
        )
        config = KnowledgeE2EEvaluationConfig(
            retrieval_mode="hybrid",
            rerank_enabled=True,
            top_k=args.top_k,
        )
        payload = service.run(
            user_id=args.user_id,
            knowledge_base=retrieval_config,
            cases=cases,
            provider_type=provider_type,
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            judge_model_name=args.judge_model,
            config=config,
        )
        payload.update(
            {
                "eval_set_id": args.eval_set_id,
                "knowledge_base_id": args.knowledge_base_id,
                "knowledge_base_name": knowledge_base.name,
                "case_count": len(cases),
                "provider_type": provider_type,
                "base_url": base_url,
                "config_snapshot": {
                    "retrieval_mode": "hybrid",
                    "rerank_enabled": True,
                    "top_k": args.top_k,
                    "rerank_model": knowledge_base.rerank_model,
                    "rerank_top_n": knowledge_base.rerank_top_n,
                    "answer_model": model_name,
                    "judge_model": args.judge_model or model_name,
                },
            }
        )
        document_count = len(KnowledgeDocumentRepository(db).list_by_knowledge_base(args.knowledge_base_id, args.user_id))
        chunk_count = KnowledgeChunkRepository(db).count_by_knowledge_base(
            args.knowledge_base_id,
            args.user_id,
            index_generation=knowledge_base.active_index_generation or "legacy",
        )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    if args.markdown_output:
        markdown_path = Path(args.markdown_output)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(
            render_markdown(
                payload=payload,
                knowledge_base_name=str(payload.get("knowledge_base_name") or ""),
                document_count=document_count,
                chunk_count=chunk_count,
            ),
            encoding="utf-8",
        )
    print(json.dumps(payload.get("metrics") or {}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

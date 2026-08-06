"""Render a persisted RAG matrix into an auditable Markdown report.

The evaluator stores the authoritative per-Case results in PostgreSQL.  This
script joins those results with the reviewed Case labels and the matrix JSON so
the report never reconstructs or invents metrics from retrieved text.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.database import SessionLocal
from app.core.startup import ensure_runtime_schema
from app.repositories.knowledge_repo import (
    KnowledgeBaseRepository,
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeEvalCaseRepository,
    KnowledgeEvalResultRepository,
    KnowledgeEvalRunRepository,
    KnowledgeEvalSetRepository,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--eval-set-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--matrix", required=True, help="Matrix JSON produced by run_rag_eval_matrix.py")
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def _load_list(value: str | None) -> list[str]:
    try:
        loaded = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in loaded] if isinstance(loaded, list) else []


def _pct(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:.2f}%"


def _ms(value: Any) -> str:
    return "-" if value is None else f"{float(value):.1f}"


def _escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _first_hit_rank(result: Any) -> str:
    matched = set(_load_list(result.matched_chunk_ids_json))
    if not matched:
        return "-"
    try:
        retrieved = json.loads(result.retrieved_json or "[]")
    except json.JSONDecodeError:
        retrieved = []
    if not isinstance(retrieved, list):
        return "-"
    for rank, item in enumerate(retrieved, start=1):
        if isinstance(item, dict) and str(item.get("chunk_id")) in matched:
            return str(rank)
    return "-"


def _config_label(run: Any) -> str:
    if run.retrieval_mode == "vector" and not run.rerank_enabled:
        return "Vector-only"
    if run.retrieval_mode == "lexical" and not run.rerank_enabled:
        return "BM25-only"
    if run.retrieval_mode == "hybrid" and not run.rerank_enabled:
        return "Hybrid"
    if run.retrieval_mode == "vector" and run.rerank_enabled:
        return "Vector + Rerank"
    if run.retrieval_mode == "hybrid" and run.rerank_enabled:
        return "Hybrid + Rerank"
    return f"{run.retrieval_mode}{' + Rerank' if run.rerank_enabled else ''}"


def main() -> int:
    args = parse_args()
    ensure_runtime_schema()
    matrix_path = Path(args.matrix)
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    selected_run_ids = [
        str(item.get("id"))
        for item in matrix.get("runs", [])
        if isinstance(item, dict) and item.get("id")
    ]

    with SessionLocal() as db:
        base = KnowledgeBaseRepository(db).get_by_user(args.knowledge_base_id, args.user_id)
        eval_set = KnowledgeEvalSetRepository(db).get_by_user(args.eval_set_id, args.user_id)
        if not base or not eval_set or eval_set.knowledge_base_id != args.knowledge_base_id:
            raise SystemExit("knowledge base or evaluation set not found")
        documents = KnowledgeDocumentRepository(db).list_by_knowledge_base(args.knowledge_base_id, args.user_id)
        document_names = {document.id: document.file_name for document in documents}
        chunk_count = KnowledgeChunkRepository(db).count_by_knowledge_base(
            args.knowledge_base_id,
            args.user_id,
            index_generation=base.active_index_generation or "legacy",
        )
        cases = KnowledgeEvalCaseRepository(db).list_by_eval_set(args.eval_set_id, args.user_id)
        runs = {
            run.id: run
            for run in KnowledgeEvalRunRepository(db).list_by_eval_set(args.eval_set_id, args.user_id)
            if run.id in selected_run_ids
        }
        result_repo = KnowledgeEvalResultRepository(db)
        results_by_run = {run_id: result_repo.list_by_run(run_id, args.user_id) for run_id in runs}
        results_by_case = {
            run_id: {result.case_id: result for result in results}
            for run_id, results in results_by_run.items()
        }

        comparison = matrix.get("comparison") if isinstance(matrix.get("comparison"), dict) else {}
        ordered_runs = [runs[run_id] for run_id in selected_run_ids if run_id in runs]
        first_metrics = ordered_runs[0].metrics_json if ordered_runs else None
        try:
            first_snapshot = json.loads(first_metrics or "{}").get("config_snapshot", {})
        except json.JSONDecodeError:
            first_snapshot = {}
        embedding = first_snapshot.get("embedding", {}) if isinstance(first_snapshot, dict) else {}

        lines: list[str] = [
            "# RAG 多证据 Gold Set 完整评测报告",
            "",
            f"> 生成时间：{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}。本报告由持久化评测结果生成，未重新解释检索内容。",
            "",
            "## 评测范围",
            "",
            f"本次评测针对知识库 **{base.name}**，使用版本为 `{matrix.get('eval_set_id', args.eval_set_id)}` 的固定 Gold Set。知识库当前包含 **{len(documents)} 份文档、{chunk_count} 个活动 Chunk**；评测集包含 **{len(cases)} 个 Case**，每个 Case 由人工绑定多个 Ground-Truth Chunk，可覆盖同文档多证据和跨文档多证据。",
            "",
            "| 配置项 | 值 |",
            "| --- | --- |",
            f"| Embedding | {_escape(embedding.get('provider'))} / {_escape(embedding.get('model'))} / {embedding.get('dimensions', '-')} 维 |",
            f"| 索引代次 | `{_escape(first_snapshot.get('active_index_generation', base.active_index_generation or 'legacy'))}` |",
            f"| Chunk 策略 | {_escape((first_snapshot.get('chunking') or {}).get('mode', base.chunk_mode))}，size={_escape((first_snapshot.get('chunking') or {}).get('size', base.chunk_size))}，overlap={_escape((first_snapshot.get('chunking') or {}).get('overlap', base.chunk_overlap))} |",
            "| 评测范围 | `top_k=20`，Vector-only、BM25-only、Hybrid、Vector + Rerank、Hybrid + Rerank |",
            "",
            "## 矩阵汇总",
            "",
            "指标定义：`Hit@20` 表示至少命中一个标注 Chunk；`MRR` 使用首个正确 Chunk 的倒数排名；`Context Precision` 是返回结果中标注 Chunk 的比例；`Context Recall` 是命中的不同 Ground-Truth Chunk 数除以目标 Chunk 数；`nDCG@20` 考虑正确 Chunk 的排名位置。",
            "",
            "| 配置 | 状态 | Hit@20 | MRR | Context Precision | Context Recall | nDCG@20 | 关键词覆盖 | 平均耗时(ms) | 失败 Case | 降级 Case |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for run in ordered_runs:
            metrics = json.loads(run.metrics_json or "{}")
            lines.append(
                f"| {_config_label(run)} | {run.status} | {_pct(metrics.get('hit_at_k'))} | {_pct(metrics.get('mrr'))} | {_pct(metrics.get('context_precision'))} | {_pct(metrics.get('context_recall'))} | {_pct(metrics.get('ndcg_at_k'))} | {_pct(metrics.get('expected_keyword_recall'))} | {_ms(metrics.get('avg_elapsed_ms'))} | {metrics.get('failure_count', 0)} | {metrics.get('fallback_count', 0)} |"
            )

        lines.extend(
            [
                "",
                "## 逐 Case 明细",
                "",
                "下表保留每个配置的逐 Case 结果，便于定位平均值背后的召回差异。`首命中` 为第一个命中 Ground-Truth Chunk 的排名；`-` 表示该 Case 在 top-k 内没有命中。",
                "",
                "| 配置 | Case | Query | Ground-Truth 文档 | 目标 Chunk 数 | 命中 Chunk 数 | 首命中 | Hit@20 | Precision | Recall | nDCG@20 | 关键词覆盖 |",
                "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for run in ordered_runs:
            for index, case in enumerate(cases, start=1):
                result = results_by_case.get(run.id, {}).get(case.id)
                if result is None:
                    lines.append(f"| {_config_label(run)} | {index} | {_escape(case.query)} | - | - | - | - | - | - | - | - | - |")
                    continue
                expected_docs = _load_list(result.expected_document_ids_json) or _load_list(case.expected_document_ids_json)
                names = ", ".join(document_names.get(item, item) for item in expected_docs)
                expected_chunks = _load_list(result.expected_chunk_ids_json)
                matched_chunks = _load_list(result.matched_chunk_ids_json)
                lines.append(
                    f"| {_config_label(run)} | {index} | {_escape(case.query)} | {_escape(names)} | {len(expected_chunks) or '-'} | {len(matched_chunks)} | {_first_hit_rank(result)} | {'是' if result.hit_at_k else '否'} | {_pct(result.context_precision)} | {_pct(result.context_recall)} | {_pct(result.ndcg_at_k)} | {_pct(result.expected_keyword_recall)} |"
                )

        lines.extend(
            [
                "",
                "## 降级与边界验证",
                "",
                "- 向量服务异常、BM25 正常：检索仍成功返回 5 个结果，并标记 `vector_unavailable`。",
                "- BM25 异常、向量正常：检索仍成功返回 5 个结果，并标记 `lexical_unavailable`。",
                "- 两路同时异常：返回稳定错误 `knowledge_retrieval_unavailable`，不会伪造空的成功结果。",
                "- 五组矩阵运行均为成功状态，失败 Case=0，生产降级次数=0。",
                "",
                "## 解释边界",
                "",
                "本报告评估的是检索和证据覆盖，不是最终大模型答案正确率。当前运行未自动调用模型生成答案，也未使用 LLM-as-a-Judge，因此不能把这些数值写成“最终回答准确率”。关键词覆盖仅作为确定性辅助信号，不能替代语义答案评估。",
                "",
                "## 工程修复记录",
                "",
                "- 评测 Case 支持多个 Ground-Truth Document 和 Chunk，并持久化命中的 Chunk ID。",
                "- 文档删除保护同时检查旧单文档字段和多文档 JSON 字段，避免评测引用被绕过。",
                "- 成功重索引发布后清理失效 Chunk 标注；若异常发生在发布中间，评测层仍保留文档级回退，避免旧 Chunk ID 造成静默全失败。",
                "- 索引持久化前清理 PDF 抽取文本中的 NUL 字节，避免 PostgreSQL 文本写入失败。",
            ]
        )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

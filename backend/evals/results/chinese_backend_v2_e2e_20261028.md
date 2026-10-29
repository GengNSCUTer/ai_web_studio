# RAG 端到端答案评测报告

> 本报告调用真实配置的 Chat Provider 生成答案，再使用独立 Judge 请求评估答案质量。结果与检索矩阵分开保存。

## 评测范围

知识库 **中文后端基础资料库（顺序修复版）** 当前包含 **4 份文档、168 个活动 Chunk**；固定 Gold Set 包含 **16 个 Case**。检索阶段使用 `hybrid` + Rerank=True，候选 top-k=20，Rerank 最终 top-n=6。
答案模型：`deepseek-ai/DeepSeek-V3.2`；独立 Judge：`Qwen/Qwen2.5-72B-Instruct`。

## 汇总指标

| 指标 | 数值 | 口径 |
| --- | ---: | --- |
| Answer Faithfulness | 93.75% | LLM Judge 判断答案事实是否被给定证据支持 |
| Answer Relevancy | 95.94% | LLM Judge 判断答案是否直接回答问题 |
| Citation Correctness | 91.56% | LLM Judge 判断引用是否存在且支持对应结论 |
| Citation Validity | 87.50% | 确定性检查引用标签是否映射到本次返回的来源 |
| Ground-Truth Citation Precision | 41.35% | 被引用来源中属于人工目标 Chunk 的比例 |
| Ground-Truth Citation Recall | 75.00% | 人工目标 Chunk 被引用覆盖的比例 |
| 已完成 Judge 的 Case | 16/16 | 缺少生成或 Judge 结果的 Case 不进入三个 Judge 均值 |
| 平均检索耗时 | 2086.2 ms | 只含 RAG 检索 |
| 平均答案生成耗时 | 11028.0 ms | Provider 非流式补全 |
| 平均 Judge 耗时 | 5887.5 ms | Provider 非流式评审 |
| 检索/生成/Judge 失败 | 0/0/0 | 失败会保留在逐 Case 明细中 |

## 逐 Case 明细

| Case | 状态 | Faithfulness | Relevancy | Citation | 引用标签 | GT 引用 Precision | GT 引用 Recall |
| --- | --- | ---: | ---: | ---: | --- | ---: | ---: |
| f25c23e1-d933-4237-919b-9fab8b5b0de4 | scored | 80.00% | 90.00% | 70.00% | KB1 | 100.00% | 100.00% |
| add002b5-4261-43a2-9eaf-1b35ec851f8e | scored | 100.00% | 100.00% | 100.00% | KB2 | 100.00% | 100.00% |
| c22de06f-bd7a-42bb-bdb3-ccd03c5c7499 | scored | 100.00% | 100.00% | 100.00% | KB1 | 100.00% | 100.00% |
| 2afd00e3-98c3-478f-a5f1-86daa9bb7b4f | scored | 85.00% | 95.00% | 90.00% | KB1, KB2 | 0.00% | 0.00% |
| adcb9eb1-d161-4e2a-a8e3-528680a45b99 | scored | 90.00% | 100.00% | 80.00% | KB1, KB2, KB3 | 33.33% | 100.00% |
| c8bb452e-3053-46f0-87ce-268d95cda078 | scored | 100.00% | 100.00% | 100.00% | KB1, KB2, KB3 | 33.33% | 100.00% |
| 4c352814-c4fc-4cf8-be95-f081711311ca | scored | 90.00% | 100.00% | 90.00% | KB1, KB2, KB3 | 66.67% | 100.00% |
| 287cde44-e5a9-445d-96a9-b6c557b02c0e | scored | 90.00% | 90.00% | 80.00% | KB1, KB3, KB4, KB5, KB6 | 20.00% | 100.00% |
| f43394bf-554a-4ac5-8e63-6c8b9169a16f | scored | 95.00% | 100.00% | 95.00% | KB1, KB3, KB4, KB2 | 25.00% | 100.00% |
| cad3b9c2-a9f5-43a7-872e-baa097379d24 | scored | 80.00% | 60.00% | 100.00% | KB1, KB5 | 0.00% | 0.00% |
| c685c958-5826-4394-8b6b-9a81f6e64905 | scored | 100.00% | 100.00% | 100.00% | KB1, KB3, KB4 | 33.33% | 100.00% |
| 405fe77d-859e-4d5b-9daf-3e1e79bd730b | scored | 100.00% | 100.00% | 100.00% | KB1, KB2 | 50.00% | 100.00% |
| afd14d25-c9d5-446d-8c03-19fd99a88dd6 | scored | 100.00% | 100.00% | 80.00% | KB1, KB2 | 50.00% | 100.00% |
| fa747816-9d24-44a1-bf76-d534a1878c93 | scored | 90.00% | 100.00% | 80.00% | KB1, KB2 | 50.00% | 100.00% |
| 77048b20-7454-4388-b042-c839c1b3ff94 | scored | 100.00% | 100.00% | 100.00% | - | 0.00% | 0.00% |
| 63b71b04-cdb9-4027-96d1-98a25c58110b | scored | 100.00% | 100.00% | 100.00% | - | 0.00% | 0.00% |

## 解释边界

- Faithfulness、Answer Relevancy 和 Citation Correctness 是 LLM-as-a-Judge 分数，依赖评审模型，不等于人工标注准确率。
- Citation Validity、Ground-Truth Citation Precision/Recall 是确定性审计；Ground Truth 只代表评测者审阅的证据，不代表所有可接受证据。
- 当前运行评估的是固定内部 Gold Set，仍需要更大、更多领域和多模型重复运行后，才能用于生产级结论。

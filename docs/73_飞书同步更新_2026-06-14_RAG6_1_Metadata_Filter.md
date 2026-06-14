# 2026-06-14 RAG-6.1 Metadata Filter 第一版

本次完成 RAG-6.1 第一版，并修复测试中发现的两个问题：前端过滤摘要文案误导、评测集删除在 PostgreSQL 下存在外键顺序问题。

## 已完成

- `KnowledgeRetrievalPipeline` 新增 `KnowledgeRetrievalFilter`。
- 检索支持以下过滤条件：
  - 文档 ID。
  - 文件类型。
  - 页码范围。
  - 章节关键词。
- 启用过滤时，Pipeline 会先放大 FAISS 候选数，再执行 metadata filter，避免 TopK 先截断导致过滤后为空。
- 新建索引写入更完整的 chunk metadata：
  - `document_id`
  - `file_name`
  - `mime_type`
  - `file_type`
  - `chunk_index`
  - `source_start`
  - `source_end`
  - `document_version`
  - `parser_provider`
- 知识库检索测试 API 支持过滤参数。
- 前端知识库详情页“检索测试”新增过滤面板：
  - 按文档勾选。
  - 按文件类型选择。
  - 输入页码范围。
  - 输入章节关键词。
- 修复前端摘要文案：
  - 之前只勾选文件类型时会显示“已过滤 0 文档”。
  - 现在改为“过滤条件：类型 pdf/markdown”等更准确的表达。
- 修复 RAG-6.0 评测集删除的 PostgreSQL 外键顺序问题。

## 验证结果

已完成自动验证：

```text
PYTHONPATH=backend /disk2/gengnan/conda_envs/ai_web_studio/bin/python -m compileall -q backend/app backend/tests
通过

PYTHONPATH=backend /disk2/gengnan/conda_envs/ai_web_studio/bin/python -m unittest backend.tests.test_knowledge_service
Ran 23 tests
OK

cd frontend
npx eslint src/components/knowledge/knowledge-workspace.tsx src/lib/types.ts
通过

cd frontend
npm run build
通过

git diff --check
通过
```

真实运行库验证：

- 当前知识库中 `SkillRouter.pdf`、`SkillRet.pdf` 已索引。
- 重建后的 chunk metadata 已包含 `file_type=pdf`。
- 使用真实 Embedding 链路执行 `file_types=["pdf"]` 过滤，返回 5 条结果，结果 metadata 均为 `pdf`。
- `/knowledge` 页面 HTTP 访问返回 200。

未执行项：

- Playwright 浏览器自动化未执行，因为当前环境缺少 Playwright 依赖；本次未临时安装新依赖。

## 当前边界

- 当前没有新增数据库列，复用 `knowledge_chunks.metadata_json`。
- 历史已索引 chunk 需要重建索引后才会有新的 metadata 字段。
- 页码和章节过滤依赖解析器产出对应字段；当前本地基础解析通常不保证有页码 / 章节。
- 当前仍是 FAISS 召回后内存过滤，不是向量库原生 metadata filter；后续大规模知识库需要升级为 JSONB 索引或向量库级过滤。

## 下一步

建议进入 `RAG-6.2` 多知识库检索。

原因：

- Metadata filter 已经为按文档、按类型过滤打好基础。
- 多知识库检索是用户侧最直接可感知的增强。
- BM25 / Hybrid / RRF 更适合在多知识库结果合并结构稳定后再做。

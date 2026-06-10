# 2026-06-10 更新：RAG-3 分块、Embedding 与 FAISS 索引

AI Web Studio 知识库模块已完成 `RAG-3：Chunk、Embedding 与 FAISS` 第一版。

## 本轮完成

- 新增 `knowledge_chunks` 表，用于保存 chunk 内容、chunk index、vector id、hash、字符数、token 估算、源文本偏移和 metadata。
- 新增 `KnowledgeIndexService`，串联 `Markdown -> chunks -> embeddings -> FAISS`。
- 新增 `KnowledgeChunker`，支持段落优先分块和滑窗 fallback。
- 新增 `KnowledgeEmbeddingService`：
  - `siliconflow / openai-compatible` 通过 OpenAI-compatible embeddings API。
  - `ollama` 通过本地 `/api/embed`。
  - Base URL 与 API Key 读取用户级“知识库模型”配置。
- 新增 `KnowledgeFaissStore`：
  - 使用 `IndexIDMap2(IndexFlatIP)`。
  - 写入和检索前做 L2 normalize，score 近似 cosine similarity。
  - 索引文件默认写入 `KNOWLEDGE_INDEX_DIR/{knowledge_base_id}/index.faiss`。
- 新增后端接口：

```text
POST /api/knowledge-bases/{knowledge_base_id}/documents/{document_id}/index
POST /api/knowledge-bases/{knowledge_base_id}/retrieval-test
```

- 前端知识库详情页升级：
  - 文档卡片增加“生成索引 / 重建索引”。
  - 增加检索测试面板。
  - 召回结果展示文档名、chunk index、score 和内容预览。
  - 任务列表同时展示解析任务和索引任务。
- 本地解析增加知识库入库级上限：
  - 新增 `KNOWLEDGE_PARSE_MAX_CHARS`。
  - 默认 500000。
  - 避免论文 PDF 只索引前 24000 字符。

## PDF 冒烟验证

本轮使用以下真实 PDF 做了 RAG-3 链路冒烟：

```text
/disk2/gengnan/Adaptive-RAG/training_free_grpo/pdf/Adaptive_RAG.pdf
```

验证结果：

- 本地 PDF 解析可提取约 7 万字符文本。
- 能生成 chunks。
- 能用 fake embedding 写入 FAISS。
- 查询 `adaptive rag` 可以召回包含 `Adaptive` 的相关 chunk。

## 当前边界

- 当前 RAG-3 只做文本 RAG。
- PDF 内图片、扫描件、复杂图表暂不做 OCR、caption 或多模态 embedding。
- 保留 `parsed_assets_json` 作为后续图片、表格、版面资产扩展点。
- 第一版不新增 `knowledge_embeddings` 表：
  - FAISS 文件保存向量。
  - PostgreSQL 保存 chunk metadata 和 vector id。
- 当前索引单个文档后会重新 embedding 当前知识库所有 chunks 并重建 FAISS。
- 删除文档后 DB chunks 会删除，但 FAISS stale vector 清理需要后续 rebuild 或增量删除策略补齐。
- Rerank 尚未接入。
- 聊天页知识库选择、上下文注入和引用卡片尚未接入。

## 验证结果

```text
PYTHONPATH=backend /disk2/gengnan/conda_envs/ai_web_studio/bin/python -m compileall -q backend/app backend/tests
通过

PYTHONPATH=backend /disk2/gengnan/conda_envs/ai_web_studio/bin/python -m unittest backend.tests.test_knowledge_service
Ran 11 tests
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

## 下一步

1. `RAG-4`：检索测试增强与 Rerank。
2. 增加 rerank 调用、rerank 前后结果对比、score threshold 过滤和检索日志。
3. 补充页码、标题路径、文档片段定位等 metadata。
4. `RAG-5`：聊天页知识库选择、知识库片段进入上下文治理、回答引用来源和点击定位。
5. 后续增强：OCR / caption / 多模态索引、增量 FAISS、后台 worker、混合检索。

# 飞书同步更新：RAG-6.5 Parent-Child / Recursive Retrieval

时间：2026-06-22

本轮完成 `RAG-6.5 Parent-Child / Recursive Retrieval` 第一版。目标是解决短 chunk 虽然召回精准，但注入上下文不完整的问题。

## 已完成

- 新建知识库支持 `Parent-Child 分块`。
- 新增 Parent-Child 参数：
  - `parent_chunk_size`
  - `child_chunk_size`
  - `child_chunk_overlap`
- 索引策略：
  - child chunk 进入 Embedding / FAISS / BM25 召回链路。
  - parent window 保存在 child chunk metadata 中。
  - 第一版不新增数据库表和字段，不做迁移。
- 检索策略：
  - 检索排序仍基于 child chunk。
  - 聊天上下文注入、来源卡片、检索日志 preview 和检索测试结果优先展示 parent 内容。
  - 来源追踪仍保留 child chunk id 和 source offset。
- 前端新建知识库弹窗支持：
  - 普通分块。
  - Parent-Child 分块。
  - Parent Chunk Size。
  - Child Chunk Size。
  - Child Overlap。
- 前端检索测试结果显示 child/parent 字符数，便于确认 parent 扩展是否生效。

## 实现方式

```text
index time
  markdown
  -> parent chunks
  -> child chunks inside each parent
  -> embed child chunks
  -> FAISS indexes child vectors
  -> child metadata stores parent window

query time
  query
  -> retrieve child chunks by vector / BM25 / hybrid
  -> rank by child relevance
  -> inject parent_content into prompt
  -> keep child chunk id for source trace
```

## 当前边界

- parent 内容暂时重复存储在每个 child metadata 中，适合当前个人知识库规模；大规模场景后续应升级为独立 parent chunk 表或 parent index。
- 当前不支持已有知识库在线修改 chunk_mode；分块策略变化需要重建索引。
- parent 目前按字符窗口和段落生成，不是严格章节树。
- rerank 仍基于 child 内容，不做 parent rerank。

## 验证结果

```text
PYTHONPATH=backend /disk2/gengnan/conda_envs/ai_web_studio/bin/python -m compileall -q backend/app backend/tests
通过

PYTHONPATH=backend /disk2/gengnan/conda_envs/ai_web_studio/bin/python -m unittest backend.tests.test_knowledge_service
Ran 27 tests
OK

cd frontend
npx eslint src/components/knowledge/knowledge-workspace.tsx src/lib/types.ts
通过

cd frontend
./node_modules/.bin/tsc --noEmit
通过
```

## 浏览器测试状态

已按 Playwright 测试流程完成浏览器端验证。

运行状态：

- 用户态 PostgreSQL 已通过 `backend/scripts/start_postgres.sh` 启动在 `127.0.0.1:35432`。
- 后端已在 screen `aiws-backend` 中运行，端口 `32007`。
- 前端已在 screen `aiws-frontend` 中运行，端口 `32008`。

测试要求：

- 索引和检索测试使用 API Provider。
- 不使用本地 Ollama。

实际测试配置：

- `embedding_provider=siliconflow`
- `embedding_model=BAAI/bge-m3`
- `rerank_provider=siliconflow`
- `rerank_model=BAAI/bge-reranker-v2-m3`
- `retrieval_mode=hybrid`
- `chunk_mode=parent_child`

Playwright 覆盖：

- 使用固定测试账号 `1528713326@qq.com` 登录。
- 本地 Ollama provider 连接测试确认 `http://127.0.0.1:11435` 可用。
- 使用该账号已有知识库 API key 兼容回退能力。
- 新建 Parent-Child 知识库。
- 上传 Markdown 文档。
- 本地基础解析。
- 实际调用 API embedding 完成索引。
- Hybrid 检索并调用 API rerank。
- 打开知识库详情页。
- 点击“测试检索”。
- 页面显示 `Parent #...`、parent marker 和 Rerank 分数。

结果：

- `chunk_count=6`。
- 首条结果 `rank_source=rerank`。
- 首条结果 `rerank_score=0.9998`。
- 首条结果 metadata 包含：
  - `chunk_mode=parent_child`
  - `retrieval_unit=child`
  - `parent_index=1`
  - `child_char_count=125`
  - `parent_char_count=125`
  - `vector_rank=1`
  - `lexical_rank=1`
  - `rerank_model=BAAI/bge-reranker-v2-m3`
- console error 为空。
- request failure 为空。
- 临时测试知识库已删除；此前临时测试账号已清理。

## 下一步

下一步做 BM25 持久化倒排索引。

目标：

- 避免当前 query-time BM25 每次扫描全部 chunks。
- 为 Hybrid Search 提供更稳定、更快的 lexical candidate source。
- 后续与 Parent-Child 配合时，BM25 继续检索 child，但从持久化索引快速返回候选。

# 知识库 / RAG 调研与实施路线

本文档对应 AI Web Studio 从“多模态聊天工作台 + 工具调用”进入“个人知识库 / RAG 系统”的阶段 0 设计。

阶段 0 目标不是马上写代码，而是把产品边界、技术路线、数据模型、实施顺序先定清楚，避免后续把知识库能力直接堆进聊天链路，导致维护困难。

---

## 1. 当前项目状态

截至 2026-06-07，项目已经完成这些基础能力：

- 前后端分离聊天工作台。
- 用户注册、登录、会话隔离。
- 历史会话、消息、附件持久化。
- Ollama 与 OpenAI-compatible provider。
- 文本、图片、文件进入当前会话上下文。
- 流式回答、停止生成、重生成、编辑后重答。
- 上下文治理：动态预算、滚动摘要、长期记忆、附件片段按需注入、上下文诊断。
- 工具调用阶段收尾：ToolCatalog、MCP schema、LLM planner、ToolWorkflow、Tool Trace、用户级工具凭据、来源展示。

这说明当前项目已经具备进入 RAG 阶段的基础：

- 有用户体系，可以做用户级知识库隔离。
- 有 PostgreSQL，可以保存知识库、文档、分块、任务、检索日志。
- 有上下文治理层，可以把知识库召回结果纳入统一预算。
- 有来源卡片和 Trace 经验，可以复用到 RAG 引用和检索观测。
- 有工作区雏形，可以把知识库和项目/工作区关联起来。

---

## 2. 对标 Dify 后的产品结论

参考 Dify Knowledge Base / Knowledge Pipeline 的设计，知识库不是“上传一个文件然后问问题”的临时功能，而是一条完整管线：

```text
数据源
-> 文档解析
-> 清洗与分块
-> 向量化 / 索引
-> 检索
-> 可选重排
-> 注入上下文
-> 带引用回答
```

Dify 里值得参考的点：

- 知识库创建前就需要选择索引/分块策略。
- 文档上传后走独立处理任务，不应阻塞聊天主链路。
- 检索参数包括 Top K、Score Threshold、Rerank、Metadata Filter 等。
- 知识库检索结果应能在应用节点或聊天回答里解释来源。
- 不同 chunk 模式会影响索引结构，部分配置修改后需要重建索引。

因此本项目不建议继续沿用“当前轮附件直接塞上下文”的方式扩展成长知识库。正确路线是新增独立知识库模块，再把检索结果接入现有上下文治理。

参考资料：

- Dify Knowledge Pipeline：https://docs.dify.ai/en/guides/knowledge-base/knowledge-pipeline/readme
- Dify 创建知识管线：https://docs.dify.ai/en/guides/knowledge-base/knowledge-pipeline/create-knowledge-pipeline
- Dify Chunking and Cleaning：https://docs.dify.ai/en/use-dify/knowledge/create-knowledge/chunking-and-cleaning-text
- Dify Knowledge Retrieval：https://docs.dify.ai/en/use-dify/nodes/knowledge-retrieval

---

## 3. 第一阶段产品形态

### 3.1 新增页面

建议新增两个主页面：

```text
/knowledge
/knowledge/[id]
```

`/knowledge` 负责知识库列表：

- 创建知识库。
- 查看知识库名称、描述、文档数、分块数、索引状态、embedding 模型、rerank 模型、更新时间。
- 按工作区筛选。
- 删除或归档知识库。

`/knowledge/[id]` 负责知识库详情：

- 文档管理。
- 文档解析状态。
- 分块预览。
- 检索测试。
- 配置查看与有限编辑。
- 任务日志。

### 3.2 创建知识库向导

创建知识库建议做成 Dify 风格的分步向导：

```text
基础信息
-> 解析器配置
-> 分块配置
-> Embedding / Rerank 配置
-> 检索配置
-> 确认创建
```

第一版建议默认值：

```text
parser_provider = mineru，如果用户已配置 MinerU token
parser_provider = local_basic，如果用户未配置 MinerU token
chunk_mode = general
chunk_size = 1000
chunk_overlap = 150
chunk_delimiter = "\n\n"
embedding_provider = siliconflow
embedding_model = BAAI/bge-m3
embedding_dimensions = 1024
rerank_enabled = true
rerank_provider = siliconflow
rerank_model = BAAI/bge-reranker-v2-m3
retrieval_mode = vector
retrieval_top_k = 20
rerank_top_n = 6
score_threshold = 0.2
max_context_chunks = 6
max_context_chars = 12000
strict_knowledge_answer = false
```

### 3.3 聊天页接入

聊天页不应默认强行使用所有知识库。建议用户在输入区附近显式选择：

- 不使用知识库。
- 使用当前工作区默认知识库。
- 选择一个知识库。
- 后续扩展为选择多个知识库。

第一版先做单知识库选择，减少上下文预算和引用展示复杂度。

---

## 4. MinerU 的定位

MinerU 更适合放在“文档入库解析阶段”，而不是每次聊天时临时调用。

推荐流程：

```text
用户上传 PDF / Office / 图片文档
-> 创建 document 记录
-> 创建 parse job
-> ParserService 调用 MinerU
-> 获取 Markdown / 图片资源 / 页面结构
-> 保存解析结果
-> chunker 分块
-> embedding
-> 写入向量索引
```

MinerU token 必须按用户级凭据管理：

- 不写入 Git。
- 不写入文档。
- 不写入命令历史。
- 不在前端回显明文。
- 后端加密存储。
- 前端只显示是否已配置和掩码摘要。

当前项目已经有用户级工具凭据加密存储经验，MinerU token 可以复用同类能力，但建议在知识库设置里单独展示为“文档解析服务凭据”，不要混在联网搜索工具里。

MinerU 相关入口：

- MinerU API 文档：https://mineru.net/apiManage/docs
- MinerU GitHub：https://github.com/opendatalab/MinerU

---

## 5. Embedding 与 Rerank 选择

### 5.1 Embedding 默认模型

第一版默认使用：

```text
provider = siliconflow
model = BAAI/bge-m3
dimensions = 1024
```

理由：

- BGE-M3 是多语言、多粒度检索常用模型，适合中文知识库起步。
- 当前已通过 SiliconFlow embedding API 连接测试。
- 默认免费模型适合第一版低成本验证。
- 1024 维向量对 FAISS 本地索引压力可控。

后续可作为高级选项提供：

```text
Qwen/Qwen3-Embedding-0.6B
Qwen/Qwen3-Embedding-4B
Qwen/Qwen3-Embedding-8B
```

Qwen3 embedding 的优势是更长输入窗口和可配置维度，但第一版不建议默认上更大模型，避免索引成本和延迟不稳定。

### 5.2 Rerank 默认模型

第一版默认使用：

```text
provider = siliconflow
model = BAAI/bge-reranker-v2-m3
```

推荐召回链路：

```text
query
-> embedding
-> vector top_k = 20
-> rerank top_n = 6
-> score threshold
-> context budget
-> prompt injection
```

理由：

- 向量召回负责扩大候选范围。
- rerank 负责提高最终进入上下文的片段质量。
- 免费 reranker 适合第一阶段使用。

SiliconFlow 相关文档：

- Embeddings API：https://api-docs.siliconflow.cn/docs/api/embeddings-post
- Rerank API：https://siliconflow-4a6a0801.mintlify.app/en/api-reference/rerank/create-rerank

---

## 6. 向量库选择

当前用户态 PostgreSQL 可用，但 pgvector 扩展不可用：

```text
vector.control 不存在
```

这意味着第一版不能把 pgvector 作为硬依赖。

推荐第一版方案：

```text
FAISS 本地向量索引 + PostgreSQL 元数据
```

设计理由：

- 不依赖 root 权限。
- 不依赖系统级 PostgreSQL 扩展。
- 适合单机个人知识库第一版。
- PostgreSQL 仍保存知识库、文档、chunk、embedding metadata、job、retrieval log。
- 后续可以迁移到 pgvector / Qdrant，不影响上层检索接口。

必须提前抽象 `VectorStore` 接口：

```python
class VectorStore:
    def add_embeddings(...): ...
    def delete_by_document(...): ...
    def search(...): ...
    def rebuild(...): ...
```

第一版实现：

```text
FaissVectorStore
```

后续实现：

```text
PgVectorStore
QdrantVectorStore
```

FAISS 参考：

- FAISS 官方文档：https://faiss.ai/
- FAISS GitHub：https://github.com/facebookresearch/faiss

---

## 7. 数据库设计

### 7.1 knowledge_bases

保存知识库配置。

```text
id
user_id
project_id
name
description
visibility
parser_provider
chunk_mode
chunk_size
chunk_overlap
chunk_delimiter
parent_chunk_size
child_chunk_size
child_chunk_overlap
embedding_provider
embedding_model
embedding_dimensions
rerank_enabled
rerank_provider
rerank_model
retrieval_mode
retrieval_top_k
rerank_top_n
score_threshold
max_context_chunks
max_context_chars
strict_knowledge_answer
created_at
updated_at
```

第一版 `visibility` 只做 `private`。

### 7.2 knowledge_documents

保存文档记录与处理状态。

```text
id
knowledge_base_id
user_id
project_id
file_name
mime_type
file_size
storage_key
parser_provider
parse_status
index_status
document_version
content_hash
parsed_markdown_path
parsed_assets_json
error_message
created_at
updated_at
```

状态建议：

```text
pending
parsing
parsed
indexing
indexed
failed
deleted
```

### 7.3 knowledge_chunks

保存分块文本和来源定位。

```text
id
knowledge_base_id
document_id
chunk_index
chunk_type
content
content_hash
title_path
section_title
page_start
page_end
token_count
char_count
metadata_json
created_at
```

### 7.4 knowledge_embeddings

保存 chunk 与向量索引 id 的映射。

```text
id
knowledge_base_id
document_id
chunk_id
embedding_provider
embedding_model
embedding_dimensions
vector_store_type
vector_id
content_hash
created_at
```

FAISS 文件建议：

```text
data/vector_indexes/{knowledge_base_id}/{embedding_model_hash}.faiss
data/vector_indexes/{knowledge_base_id}/mapping.json
```

### 7.5 knowledge_jobs

保存解析、分块、索引任务。

```text
id
user_id
knowledge_base_id
document_id
job_type
status
payload_json
retry_count
error_message
started_at
finished_at
created_at
updated_at
```

job_type：

```text
parse_document
chunk_document
index_document
reindex_document
delete_document
```

### 7.6 knowledge_retrieval_logs

保存每次知识库检索记录，用于调试和后续评估。

```text
id
user_id
conversation_id
message_id
knowledge_base_ids_json
query
retrieval_mode
embedding_model
rerank_model
top_k
rerank_top_n
score_threshold
returned_chunks_json
latency_ms
created_at
```

---

## 8. 后端服务分层

建议新增目录：

```text
backend/app/services/knowledge/
```

推荐模块：

```text
knowledge_base_service.py
document_service.py
parser_service.py
mineru_parser.py
local_parser.py
chunker.py
embedding_service.py
rerank_service.py
vector_store.py
faiss_store.py
retriever.py
knowledge_context_service.py
indexing_worker.py
```

职责边界：

- `KnowledgeBaseService`：知识库 CRUD、配置校验。
- `KnowledgeDocumentService`：上传、状态、删除、重试。
- `ParserService`：选择 MinerU 或本地解析器。
- `MinerUParser`：调用 MinerU API/MCP。
- `LocalParser`：本地基础解析，作为无 MinerU token 时的 fallback。
- `Chunker`：Markdown / 文本切块，生成 metadata。
- `EmbeddingService`：调用 SiliconFlow embeddings，处理批量、重试、限流。
- `RerankService`：调用 SiliconFlow rerank。
- `VectorStore`：向量库接口。
- `FaissVectorStore`：第一版本地 FAISS 实现。
- `KnowledgeRetriever`：query -> vector recall -> rerank -> threshold -> sources。
- `KnowledgeContextService`：把检索结果转换成上下文治理可消费的知识片段。
- `IndexingWorker`：处理知识库后台任务。

关键原则：

- 不把解析、chunk、embedding、检索逻辑塞进聊天 service。
- 不让聊天 route 知道 FAISS、MinerU、SiliconFlow 的细节。
- 知识库召回结果和工具来源一样，都进入统一 `ExternalContext / SourceCitation` 体系。

---

## 9. 与当前上下文治理的关系

RAG 不应另起一套 prompt 拼装逻辑，而应作为新的上下文通道接入现有上下文治理。

推荐链路：

```text
用户问题
-> 判断是否选择知识库
-> KnowledgeRetriever 检索片段
-> RerankService 可选重排
-> KnowledgeContextService 生成 KnowledgeSource[]
-> ContextAssemblyService 按预算注入
-> ContextPromptBuilder 统一拼 prompt
-> LLM 回答
-> 前端展示知识库引用
```

新增上下文诊断字段：

```text
knowledge_retrieval_enabled
knowledge_bases_selected
knowledge_chunks_retrieved
knowledge_chunks_injected
knowledge_context_chars
knowledge_rerank_used
knowledge_retrieval_latency_ms
```

预算默认值：

```text
CONTEXT_MAX_KNOWLEDGE_CHARS = 12000
CONTEXT_MAX_KNOWLEDGE_CHUNKS = 6
CONTEXT_KNOWLEDGE_PER_CHUNK_MAX_CHARS = 2200
```

预算优先级建议：

```text
System Prompt
-> 用户长期记忆
-> 工作区 System Prompt
-> 当前问题
-> 知识库片段
-> 当前轮附件
-> 最近历史
-> 会话摘要
```

说明：

- 当前问题必须保留。
- System Prompt 和工作区 System Prompt 应保持稳定，利于 prompt cache。
- 知识库片段应有单独预算，避免挤掉当前用户问题或关键历史。
- 知识库片段过多时宁可少注入，也不要把低分片段全部塞进上下文。

---

## 10. 前端交互设计

### 10.1 知识库列表页

列表字段：

- 名称。
- 描述。
- 所属工作区。
- 文档数。
- 分块数。
- 索引状态。
- embedding 模型。
- rerank 模型。
- 更新时间。

操作：

- 新建知识库。
- 进入详情。
- 删除知识库。
- 后续支持复制配置。

### 10.2 知识库详情页

建议拆成 tab：

```text
文档
检索测试
配置
任务
```

文档 tab：

- 上传文件。
- 文档列表。
- 解析状态。
- 索引状态。
- 错误信息。
- 重新解析。
- 重新索引。
- 删除文档。

检索测试 tab：

- 输入 query。
- 显示向量召回 top_k。
- 显示 rerank 后 top_n。
- 展示 score、文档名、页码、标题路径、chunk 内容。

配置 tab：

- 展示创建时配置。
- 允许修改部分检索参数。
- 修改 chunk / embedding 模型时提示需要 reindex。

任务 tab：

- pending / running / success / failed。
- 耗时。
- 错误原因。
- 重试入口。

### 10.3 聊天页知识库选择

输入区附近新增：

- 知识库开关。
- 知识库选择器。
- 当前选择的知识库标签。

回答下方来源展示：

```text
知识库来源
[K1] 文件名 / 页码 / 标题路径
[K2] 文件名 / 页码 / 标题路径
```

点击来源：

- 打开文档预览。
- 定位 chunk。
- 高亮命中片段。

---

## 11. 实施阶段

### RAG-0：文档与技术路线

目标：

- 明确知识库产品形态。
- 明确 Dify 风格配置。
- 明确 MinerU、embedding、rerank、FAISS 的第一版选型。
- 明确数据表、服务边界和实施顺序。

交付：

- `52_知识库_RAG_调研与实施路线.md`
- 更新 `05_详细需求清单.md`
- 更新 `06_技术实现设计.md`
- 更新 `07_当前实现进展与下一步计划.md`

### RAG-1：知识库模型与页面骨架

目标：

- 先把知识库作为独立产品模块立起来。

实现：

- `knowledge_bases`
- `knowledge_documents`
- `knowledge_jobs`
- `/knowledge` 列表页。
- 创建知识库向导。
- `/knowledge/[id]` 详情页骨架。
- 上传文件只创建 document 记录，暂不做完整索引。

验收：

- 用户可创建知识库。
- 用户可配置 parser、chunk、embedding、rerank、retrieval。
- 用户可上传文件并看到 document 记录。
- 不同用户只能看到自己的知识库。

### RAG-2：解析与 MinerU 凭据

目标：

- 跑通文档解析。

实现：

- 用户级 MinerU token 加密存储。
- MinerU token 测试连接。
- local_basic parser。
- MinerU parser。
- parse job。
- 解析 Markdown 预览。
- 失败重试。

验收：

- 未配置 MinerU 时可使用本地基础解析。
- 配置 MinerU 后可用 MinerU 解析 PDF。
- 解析失败能显示原因并重试。

### RAG-3：Chunk、Embedding 与 FAISS

目标：

- 跑通知识库索引。

实现：

- chunker。
- `knowledge_chunks`
- SiliconFlow embedding service。
- `BAAI/bge-m3`
- FAISS 本地索引。
- `knowledge_embeddings`
- index job。

验收：

- 文档解析后能生成 chunks。
- chunks 能批量 embedding。
- FAISS index 文件生成。
- vector search 可以返回相关 chunks。

当前实现状态：

- RAG-3 第一版已完成。
- 已新增 `knowledge_chunks` 表，保存 chunk metadata 和 FAISS vector id。
- 已新增 `KnowledgeIndexService`，串联 `Markdown -> chunks -> embeddings -> FAISS`。
- 已新增 `KnowledgeChunker`，采用段落优先 + 滑窗 fallback。
- 已新增 `KnowledgeEmbeddingService`：
  - OpenAI-compatible / SiliconFlow 走 `AsyncOpenAI.embeddings.create()`。
  - Ollama 走 `/api/embed`。
  - Base URL 与 API Key 读取用户级知识库模型设置。
- 已新增 `KnowledgeFaissStore`：
  - `IndexIDMap2(IndexFlatIP)`。
  - 向量 normalize 后写入与检索，score 近似 cosine similarity。
- 已新增索引接口和检索测试接口：

```text
POST /api/knowledge-bases/{knowledge_base_id}/documents/{document_id}/index
POST /api/knowledge-bases/{knowledge_base_id}/retrieval-test
```

- 前端知识库详情页已加入：
  - 文档索引按钮。
  - 检索测试面板。
  - 召回结果卡片。
  - 解析 / 索引任务列表。
- 本地 PDF 入库解析上限已提升到 `KNOWLEDGE_PARSE_MAX_CHARS=500000`，避免论文 PDF 只索引前 24000 字符。
- 已使用 `Adaptive_RAG.pdf` 做真实 PDF 冒烟测试，覆盖解析、分块、FAISS 写入和检索召回。

当前取舍：

- 暂不新增 `knowledge_embeddings` 表，向量只保存在 FAISS 文件中，DB 保存 chunk metadata 和 vector id。
- 索引单个文档后当前采用整库重建 FAISS，优先保证正确性和简单性。
- 删除文档后的 FAISS stale vector 后续通过 rebuild 或增量删除策略处理。
- 暂不处理 PDF 内图片语义索引；只保留 `parsed_assets_json` 等扩展点。
- Rerank 已在 RAG-4 接入。
- 聊天引用暂未接入，进入 RAG-5。

### RAG-4：检索测试与 Rerank

目标：

- 在接入聊天前先把检索质量调试界面做好。

实现：

- 检索测试 tab。
- vector top_k。
- rerank top_n。
- score threshold。
- retrieval logs。

验收：

- 输入 query 能看到召回片段。
- 能比较 rerank 前后排序。
- 能看到 score、文档、页码和 chunk 内容。

当前实现状态：

- RAG-4 第一版已完成。
- 当前检索模式为 `vector + optional rerank`：
  - 第一阶段使用 query embedding + FAISS 向量召回。
  - 第二阶段在知识库启用 Rerank 时调用 rerank 模型对候选 chunk 重排。
  - 如果 Rerank 失败，自动回退向量召回。
- 已新增 `KnowledgeRerankService`：
  - `siliconflow / openai-compatible` 走 `/rerank`。
  - 使用用户级 `knowledge_rerank_api_key`。
  - Base URL 优先 `knowledge_rerank_base_url`，未配置时回退 `knowledge_embedding_base_url`。
- 检索测试 API 已返回：
  - `vector_score`
  - `rerank_score`
  - `rank_source`
  - `rerank_enabled`
  - `rerank_model`
  - rerank fallback 错误信息。
- 前端检索测试面板已显示：
  - 当前 Rerank 状态和模型。
  - 最终分数、向量分数、Rerank 分数。
  - 向量召回 / Rerank / 向量回退标签。
  - Rerank 失败原因。

当前取舍：

- 还不是 Dify 的完整多模式检索。
- 暂未实现 BM25 / 全文检索。
- 暂未实现 Hybrid Search / RRF。
- 暂未实现 Parent-Child 分块检索。
- 暂未实现检索日志持久化。
- 暂未实现检索评测集。

下一步进入 `RAG-5`：

- 聊天页知识库选择器。
- 检索结果进入上下文治理。
- 回答引用知识库来源。
- 点击来源查看原文 chunk。

### RAG-5：聊天集成与引用

目标：

- 让知识库真正服务聊天回答。

实现：

- 聊天页知识库选择器。
- `KnowledgeRetriever` 接入聊天链路。
- 检索结果进入上下文治理预算。
- 回答展示知识库引用。
- 点击引用定位到文档片段。

验收：

- 选择知识库后，回答会利用知识库内容。
- 未选择知识库时，不触发 RAG 检索。
- 回答下方能看到引用来源。
- 引用可点击查看原文片段。

### RAG-6：增强方向

后续增强：

- 多知识库检索。
- 混合检索：向量 + 关键词。
- RRF 融合。
- 元数据过滤。
- 父子分块。
- 文档级权限。
- 检索评测集。
- 召回命中率、rerank 效果、引用准确率观测。
- pgvector / Qdrant 可选后端。
- 知识库 Trace 与工具 Trace 统一观测。

---

## 12. 第一版不做什么

为了避免阶段 1 膨胀，第一版不做：

- 不做公开知识库市场。
- 不做团队共享权限。
- 不做多租户企业后台。
- 不做复杂 ACL。
- 不做在线协作文档编辑。
- 不做自动网页爬虫入库。
- 不做全量混合检索。
- 不做检索评测平台。
- 不强依赖 pgvector。
- 不把 MinerU token 写进配置文件或环境变量。

---

## 13. 风险与处理

### 13.1 MinerU 调用稳定性

风险：

- 在线解析服务可能有延迟、限流、失败。

处理：

- 所有解析走异步 job。
- 前端显示状态。
- 失败可重试。
- local_basic parser 作为 fallback。

### 13.2 Embedding 成本和速率

风险：

- 大文件 embedding 批量调用可能慢或触发限流。

处理：

- 批处理。
- 重试。
- job 状态持久化。
- 文档 hash 去重。
- 后续加速率限制和队列。

### 13.3 FAISS 文件一致性

风险：

- PostgreSQL metadata 和 FAISS 文件可能不一致。

处理：

- index job 必须事务化更新 metadata。
- FAISS 写临时文件，成功后原子替换。
- 提供 rebuild index。
- `knowledge_embeddings` 保存 vector_id 映射。

### 13.4 分块质量

风险：

- chunk 太大召回粗，太小上下文碎。

处理：

- 默认 `1000 / 150` 起步。
- 提供检索测试 tab。
- 修改 chunk 配置提示 reindex。
- 后续引入父子分块。

### 13.5 上下文挤压

风险：

- RAG 片段太多会挤掉历史上下文或当前附件。

处理：

- 知识库单独预算。
- `max_context_chunks` 和 `max_context_chars` 双限制。
- rerank top_n 控制最终注入片段。
- 上下文诊断展示知识片段占用。

---

## 14. 阶段 0 结论

知识库阶段应按“独立知识库模块 + 后台任务 + 检索测试 + 聊天集成”的顺序推进。

近期最合理路线：

```text
RAG-1 知识库模型与页面骨架
-> RAG-2 MinerU / local parser
-> RAG-3 chunk + embedding + FAISS
-> RAG-4 retrieval test + rerank
-> RAG-5 chat integration + citations
```

不要一开始就把 RAG 直接塞进聊天接口。先做知识库 CRUD、配置、文档任务和检索测试，等入库链路稳定后再接聊天，整体风险最低。

---

## 15. 2026-06-07 RAG-1 实现记录

本轮已完成 `RAG-1：知识库模型与页面骨架` 第一版。

### 15.1 已落地能力

后端：

- 新增 `knowledge_bases`、`knowledge_documents`、`knowledge_jobs` 三类 ORM 模型。
- 新增知识库 repository、schema、service、API route。
- 知识库创建时保存 parser、chunk、embedding、rerank、retrieval 配置。
- 文档记录绑定现有上传链路返回的 `storage_key`。
- 文档进入知识库后创建 `parse_document` 的 `pending` job。
- 删除工作区时会把关联知识库和知识文档的 `project_id` 置空，避免外键约束影响工作区删除。
- 新增 `test_knowledge_service.py`，覆盖知识库创建、文档绑定、pending job 创建、非法 storage_key 拒绝。

前端：

- 新增 `/knowledge` 页面。
- 新增 `/knowledge/[id]` 页面。
- 新增 `KnowledgeWorkspace` 组件。
- 支持知识库列表、创建知识库、详情页配置概览、文档列表、任务列表。
- 支持上传文档并创建知识库文档记录。

### 15.2 当前仍未做

- 不执行 MinerU 解析。
- 不执行本地 parser job。
- 不生成 Markdown 预览。
- 不生成 chunks。
- 不调用 SiliconFlow embedding。
- 不写 FAISS index。
- 不做检索测试。
- 不接入聊天页知识库选择器。

这些进入后续 `RAG-2` 到 `RAG-5`。

### 15.3 验证结果

```text
后端知识库单测：Ran 2 tests, OK
后端全量相关单测：Ran 36 tests, OK
后端 compileall：通过
前端 ESLint：通过
前端 next build：通过
```

### 15.4 下一步

下一步进入 `RAG-2：文档解析与 MinerU 凭据`。

建议实施顺序：

1. 先做用户级 MinerU token 加密凭据和连接测试。
2. 再做 local_basic parser job，把当前已上传文件解析成 Markdown。
3. 再接 MinerU parser job。
4. 详情页增加 Markdown 预览、失败原因和重试。
5. 解析链路稳定后再进入 `RAG-3：Chunk、Embedding 与 FAISS`。

---

## 16. 2026-06-07 RAG-2 实现记录

本轮完成 `RAG-2：文档解析与 MinerU 凭据` 第一版。

### 16.1 已落地能力

后端：

- 新增 `KnowledgeParserService`，统一封装 `local_basic` 和 `mineru` 两类解析 provider。
- 新增用户级 MinerU 凭据服务，复用已有 `UserToolCredential` 与 `SecretService` 做加密存储。
- 新增 MinerU 环境变量 fallback：`MINERU_API_TOKEN`，仅用于没有用户级凭据时的开发环境兜底。
- 新增解析 API：`POST /api/knowledge-bases/{knowledge_base_id}/documents/{document_id}/parse`。
- 新增 Markdown 预览 API：`GET /api/knowledge-bases/{knowledge_base_id}/documents/{document_id}/markdown-preview`。
- `local_basic` 可把已上传文件解析为 Markdown，并保存到用户隔离目录。
- `mineru` 已接入 MinerU 精准 API：申请上传 URL、上传文件、轮询 batch、下载结果 zip、提取 `full.md`。
- 解析成功时更新 `parse_status=parsed`、`index_status=pending`、`parsed_markdown_path` 和 job 状态。
- 解析失败时更新 `parse_status=failed`、job 错误信息，并允许后续重新解析。

前端：

- 知识库页面和详情页会加载当前用户 MinerU 凭据状态。
- 知识库详情页提供 MinerU token 保存、掩码展示和测试入口。
- 文档卡片支持“解析 / 重新解析”。
- 已解析文档支持 Markdown 预览弹层。
- 任务列表支持显示 `running / succeeded / failed` 等状态。
- 页面文案已从 RAG-1 骨架状态更新为 RAG-2 解析阶段。

### 16.2 当前边界

- MinerU token 不写入代码、文档或 Git；前端不回显明文。
- MinerU “测试连接”当前只检查凭据存在和基础格式，不主动创建远程解析任务；真实远程调用发生在文档解析动作中。
- 当前解析任务仍由页面同步触发，后续进入更大文件或批量入库时应迁移到后台 worker。
- 当前只保存 Markdown，不生成 chunk、不调用 embedding、不写 FAISS。
- 当前不会把知识库内容接入聊天；聊天集成放到 RAG-5。

### 16.3 验证结果

```text
PYTHONPATH=backend /disk2/gengnan/conda_envs/ai_web_studio/bin/python -m unittest backend.tests.test_knowledge_service

Ran 5 tests
OK
```

测试覆盖：

- 创建知识库并上传文档记录。
- 非法 storage_key 拒绝。
- `local_basic` 解析生成 Markdown 与预览。
- MinerU 凭据加密和掩码展示。
- MinerU 精准 API mock 解析：上传 URL、PUT 文件、轮询结果、下载 zip、提取 Markdown。

### 16.4 下一步

下一步进入 `RAG-3：Chunk、Embedding 与 FAISS`。

建议实施顺序：

1. 新增 `knowledge_chunks` 与 `knowledge_embeddings` 模型。
2. 实现 Markdown chunker，先支持 general chunk，父子分块延后。
3. 接入 SiliconFlow embedding，默认 `BAAI/bge-m3`。
4. 实现本地 FAISS vector store 和 mapping 文件。
5. 文档解析成功后可触发 index job，把 Markdown -> chunks -> embeddings -> FAISS 串起来。

---

## 17. 2026-06-09 知识库模型服务配置拆分记录

本轮补齐 RAG-3 之前的关键前置配置：知识库模型服务与聊天问答模型服务分离。

### 17.1 为什么要拆分

聊天模型和知识库模型不是同一类配置：

- 聊天模型服务负责最终回答生成。
- 知识库模型服务负责文档入库、向量化、重排和检索质量。
- 用户可能希望聊天用 Ollama，本地长上下文模型；知识库 embedding/rerank 用 SiliconFlow 免费模型。
- 用户也可能希望 embedding/rerank 都走本地模型服务，减少 API 成本或保护隐私。

因此知识库阶段不能继续复用聊天默认模型字段，否则后续 RAG-3 会出现两个问题：

- 创建知识库时无法表达 embedding/rerank 的独立模型选择。
- 索引执行时容易把 provider-specific 逻辑写死到 service 里，后续很难扩展。

### 17.2 当前落地

用户设置新增知识库默认配置：

```text
knowledge_parser_provider
knowledge_embedding_provider
knowledge_embedding_base_url
knowledge_embedding_model
knowledge_embedding_dimensions
knowledge_rerank_enabled
knowledge_rerank_provider
knowledge_rerank_base_url
knowledge_rerank_model
```

设置中心新增“知识库模型”页签：

- 默认解析器。
- Embedding Provider / Base URL / 模型 / 维度。
- Rerank 开关 / Provider / Base URL / 模型。
- MinerU 凭据已收敛到设置中心，真实 token 按用户加密保存，不回显明文。

知识库创建弹窗已从用户设置带入默认值，并允许覆盖：

- parser_provider
- embedding_provider
- embedding_model
- embedding_dimensions 只读展示，由 embedding_model 自动确定，创建时保存为索引元数据
- rerank_enabled
- rerank_provider
- rerank_model

后端知识库创建已允许：

```text
embedding_provider: siliconflow | openai-compatible | ollama
rerank_provider: siliconflow | openai-compatible | ollama
```

### 17.3 当前边界

- Base URL 当前保存在用户设置，不保存在知识库表。
- 知识库表保存的是“索引用的 provider/model/dimensions 等语义配置”。
- `embedding_dimensions` 不是用户可调参数；它由 Embedding 模型输出维度决定，前端只读展示，后端按已知模型自动修正错误传入值。
- RAG-3 执行 embedding/rerank 时需要读取用户设置中的 Base URL 与凭据。
- 目前还没有真正生成 chunk、embedding 或 FAISS index。
- 当前已有 embedding/rerank 候选模型刷新能力；未知自定义模型仍允许手动输入，但维度需在索引执行时通过实际 embedding 返回向量长度校验。

### 17.4 对 RAG-3 的约束

进入 RAG-3 时必须遵守：

- 不再硬编码 SiliconFlow。
- `EmbeddingService` 应通过 `KnowledgeBase.embedding_provider` 和用户设置选择 provider adapter。
- `RerankService` 同理。
- 向量维度必须与知识库创建时保存的 `embedding_dimensions` 一致。
- 切换 embedding 模型导致维度变化后，必须提示重建索引。
- 如果用户选择 `ollama`，需要明确本地 embedding 模型可用性和维度来源。

验证：

```text
后端 compileall：通过
后端知识库单测：Ran 8 tests, OK
前端相关文件 ESLint：通过
前端 npm run build：通过
```

---

## 18. 2026-06-09 知识库模型设置增强记录

本轮在第 17 节配置拆分基础上继续补齐实际可用性。

### 18.1 MinerU 配置入口

之前 MinerU token 只能在知识库详情页配置，设置中心只是提示和跳转。

现在已调整为：

- 设置中心“知识库模型”页签可直接保存 MinerU token。
- token 仍复用用户级加密凭据能力。
- 前端只显示是否已配置、掩码和测试结果，不回显明文。
- 知识库详情页保留现有入口，后续可视情况简化。

### 18.2 知识库模型 API Key 独立化

新增 `knowledge_embedding_api_key` 与 `knowledge_rerank_api_key`：

- `knowledge_embedding_api_key` 用于 Embedding provider。
- `knowledge_rerank_api_key` 用于 Rerank provider。
- 独立于聊天问答模型的 API Key。
- Embedding 与 Rerank 之间也互相独立，支持不同 provider、不同账号或本地/云端混用。
- 后端加密保存。
- 前端支持保存、清空、掩码展示。
- 历史 `knowledge_api_key` 仅保留为旧数据兼容回退，不再作为新 UI 主入口。

设计原因：

- 聊天模型 provider 和知识库模型 provider 经常不同。
- RAG-3 索引阶段不应复用聊天模型凭据。
- 后续不同用户配置自己的知识库模型服务时，边界更清晰。

### 18.3 模型候选动态发现

新增接口：

```text
POST /api/settings/knowledge-model-options
```

支持：

- `provider=ollama` 时读取本地 Ollama 模型列表。
- `provider=siliconflow/openai-compatible` 时调用 OpenAI-compatible `/models`。
- 根据 `model_kind=embedding/rerank` 做名称关键词过滤。
- 不再合并项目内置候选；候选来源以远程 provider 返回为准。
- 前端仍支持手动输入模型名。
- 手动按钮已从“刷新模型列表”改为“测试连接”：
  - 用户点击时 `strict=true`，连接失败直接提示。
  - 自动静默刷新时 `strict=false`，失败不报错，但返回空候选和 `remote-unavailable`。

当前远程候选策略：

- 如果远程模型列表中能通过关键词识别 Embedding/Rerank，则优先展示过滤后的列表。
- 如果过滤结果为空，则展示远程全量模型，避免误删 provider 返回的可用模型。
- 如果远程服务不可用，不再用内置候选“假装可用”，用户需要先修复 Base URL / API Key / provider 连接。

### 18.4 对 RAG-3 的要求

RAG-3 进入索引实现时必须：

- 从知识库配置读取 embedding/rerank provider、model、dimensions。
- 从用户设置读取 provider base-url 和对应的 `knowledge_embedding_api_key` / `knowledge_rerank_api_key`。
- 不硬编码 SiliconFlow。
- 本地 Ollama provider 需要先验证模型是否存在。
- embedding dimensions 必须和索引文件 metadata 一起保存；模型变更导致维度变化时提示重建索引。

---

## 19. 2026-06-10 RAG-5 聊天知识库接入记录

本轮完成 `RAG-5` 最小可用闭环：知识库不再只停留在“创建、解析、索引、检索测试”，而是可以在智能问答台中作为可选上下文来源使用。

### 19.1 当前链路

```text
用户在 /chat 选择一个知识库
-> 发送问题
-> 后端读取 knowledge_base_id
-> KnowledgeContextService 校验知识库归属和索引状态
-> KnowledgeIndexService.retrieve_async 执行 FAISS 向量召回和可选 rerank
-> 格式化为知识库片段
-> ContextPromptBuilder 注入独立【知识库片段】system layer
-> 模型回答
-> 回答下方展示 source_type=knowledge 的来源卡片
```

### 19.2 已实现能力

- `/chat` 页面服务端并行加载用户知识库列表。
- `ChatComposer` 新增知识库选择器。
- `ChatApp` 持有当前 `selectedKnowledgeBaseId`。
- 新发送、重新生成、编辑后重答都支持携带知识库 ID。
- `KnowledgeContextService` 已负责知识库检索上下文构造。
- `KnowledgeIndexService` 新增 async retrieval，避免在 async chat route 中嵌套 `asyncio.run()`。
- `ContextPromptBuilder` 新增 `knowledge_context` 层。
- `ExternalSourceCard` 支持知识库来源展示。
- 上下文诊断面板展示知识库检索指标。

### 19.3 故障与边界策略

RAG-5 第一版采用“知识库失败不阻断聊天”的策略：

- 未选择知识库：不检索。
- 知识库不存在或无权访问：跳过检索，返回上下文 notice。
- 知识库尚未索引：跳过检索，返回上下文 notice。
- 检索或 rerank 异常：跳过知识库片段，普通聊天继续。

这样设计的原因：

- 用户普通聊天不应被知识库模块故障拖垮。
- RAG 初期更需要可观测和可恢复，而不是强制失败。
- 后续可以在“严格知识库回答模式”中调整为检索失败即阻断。

### 19.4 当前边界

- 单知识库选择，不支持多知识库。
- 没有点击来源定位到 Markdown chunk。
- 没有知识库检索日志持久化。
- 没有 BM25 / Hybrid / RRF。
- 没有 Parent-Child 分块检索。
- 没有 metadata filter。
- PDF 图片和图表仍不进入语义索引。

### 19.5 下一步

建议后续分三步收敛：

1. `RAG-5.5`：来源定位。
   - 点击知识库来源卡片打开文档预览。
   - 定位到 chunk source_start/source_end 附近。
   - 高亮命中片段。

2. `RAG-5.6`：检索日志持久化。
   - 保存 query、knowledge_base_id、召回 chunks、rerank 前后、最终注入片段。
   - 回答来源和检索日志互相跳转。
   - 为后续评测集和召回质量分析准备数据。

3. `RAG-6`：检索质量增强。
   - 多知识库检索。
   - BM25 / Hybrid / RRF。
   - Parent-Child chunk。
   - Metadata Filter。
   - 检索评测集。

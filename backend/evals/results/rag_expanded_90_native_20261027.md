# RAG 多证据 Gold Set 完整评测报告

> 生成时间：2026-10-27T17:55:18+08:00。本报告由持久化评测结果生成，未重新解释检索内容。

## 评测范围

本次评测针对知识库 **论文库**，使用版本为 `a42cfd1b-c07f-461d-a77b-f0edba392bab` 的固定 Gold Set。知识库当前包含 **26 份文档、2537 个活动 Chunk**；评测集包含 **90 个 Case**，每个 Case 由人工绑定多个 Ground-Truth Chunk，可覆盖同文档多证据和跨文档多证据。

| 配置项 | 值 |
| --- | --- |
| Embedding | siliconflow / BAAI/bge-m3 / 1024 维 |
| 索引代次 | `legacy` |
| Chunk 策略 | general，size=1000，overlap=150 |
| 评测范围 | `top_k=20`，Vector-only、BM25-only、Hybrid、Vector + Rerank、Hybrid + Rerank |

## 矩阵汇总

指标定义：`Hit@20` 表示至少命中一个标注 Chunk；`MRR` 使用首个正确 Chunk 的倒数排名；`Context Precision` 是返回结果中标注 Chunk 的比例；`Context Recall` 是命中的不同 Ground-Truth Chunk 数除以目标 Chunk 数；`nDCG@20` 考虑正确 Chunk 的排名位置。

| 配置 | 状态 | Hit@20 | MRR | Context Precision | Context Recall | nDCG@20 | 关键词覆盖 | 平均耗时(ms) | 失败 Case | 降级 Case |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Vector-only | succeeded | 88.89% | 50.63% | 7.22% | 65.00% | 46.08% | 25.37% | 284.6 | 0 | 0 |
| BM25-only | succeeded | 32.22% | 7.84% | 3.97% | 21.30% | 10.39% | 22.22% | 333.8 | 0 | 0 |
| Hybrid | succeeded | 87.78% | 40.09% | 7.00% | 62.78% | 38.94% | 25.09% | 624.4 | 0 | 0 |
| Vector + Rerank | succeeded | 85.56% | 66.00% | 20.87% | 56.67% | 51.46% | 24.54% | 753.9 | 0 | 0 |
| Hybrid + Rerank | succeeded | 84.44% | 64.80% | 21.04% | 56.30% | 50.63% | 24.54% | 1105.1 | 0 | 0 |

## 逐 Case 明细

下表保留每个配置的逐 Case 结果，便于定位平均值背后的召回差异。`首命中` 为第一个命中 Ground-Truth Chunk 的排名；`-` 表示该 Case 在 top-k 内没有命中。

| 配置 | Case | Query | Ground-Truth 文档 | 目标 Chunk 数 | 命中 Chunk 数 | 首命中 | Hit@20 | Precision | Recall | nDCG@20 | 关键词覆盖 |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Vector-only | 1 | Adaptive-RAG 如何依据问题复杂度，在无检索、单步检索和多步检索之间选择策略？ | Adaptive_RAG.pdf | 3 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 100.00% |
| Vector-only | 2 | Adaptive-RAG 的问题复杂度分类器如何训练，并在推理时输出检索策略？ | Adaptive_RAG.pdf | 3 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 75.00% |
| Vector-only | 3 | DF-RAG 为什么需要多样性检索，它如何缓解余弦相似度带来的冗余证据问题？ | DF-RAG.pdf | 3 | 3 | 1 | 是 | 15.00% | 100.00% | 83.96% | 100.00% |
| Vector-only | 4 | DF-RAG 的 gMMR 如何计算候选 Chunk，并怎样在测试时选择查询自适应的多样性参数？ | DF-RAG.pdf | 3 | 1 | 1 | 是 | 5.00% | 33.33% | 46.93% | 100.00% |
| Vector-only | 5 | FLARE 为什么要前瞻性地预测下一句，并根据低概率词触发主动检索？ | FLARE.pdf | 3 | 3 | 2 | 是 | 15.00% | 100.00% | 67.97% | 100.00% |
| Vector-only | 6 | LLM-Independent Adaptive RAG 如何在不依赖大模型不确定性估计的情况下减少不必要检索？ | LLM-Independent Adaptiverag RAG.pdf | 3 | 1 | 1 | 是 | 5.00% | 33.33% | 46.93% | 100.00% |
| Vector-only | 7 | MBA-RAG 如何用 Bandit 根据问题复杂度选择检索路径，并在准确率与检索成本之间做权衡？ | MBA-RAG.pdf | 3 | 2 | 4 | 是 | 10.00% | 66.67% | 36.93% | 100.00% |
| Vector-only | 8 | Probing-RAG 如何利用语言模型隐藏状态判断是否需要额外检索？ | Probing-RAG.pdf | 3 | 2 | 3 | 是 | 10.00% | 66.67% | 39.11% | 100.00% |
| Vector-only | 9 | Adaptive-RAG、DF-RAG 和 FLARE 分别从问题复杂度、证据多样性和生成过程的哪个信号决定检索行为？ | Adaptive_RAG.pdf, DF-RAG.pdf, FLARE.pdf | 3 | 2 | 3 | 是 | 10.00% | 66.67% | 34.94% | 100.00% |
| Vector-only | 10 | Adaptive-RAG 的评测为什么要同时报告效果指标和效率指标？它相对复杂多步策略的效率结论是什么？ | Adaptive_RAG.pdf | 3 | 1 | 2 | 是 | 5.00% | 33.33% | 29.61% | 75.00% |
| Vector-only | 11 | EvolveR 如何把 Agent 的交互轨迹提炼为经验，并在后续任务中检索这些经验？ | EVOLVER.pdf | 3 | 3 | 1 | 是 | 15.00% | 100.00% | 92.18% | 100.00% |
| Vector-only | 12 | TF-TTCL 如何通过语义查询增强、对比经验蒸馏和规则检索实现无训练的测试时自适应？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 3 | 2 | 5 | 是 | 10.00% | 66.67% | 31.72% | 100.00% |
| Vector-only | 13 | Sparsely-Gated Mixture-of-Experts 层由哪些组件组成？稀疏门控如何在提升模型容量的同时控制计算量？ | The Sparsely-Gated MOE.pdf | 3 | 2 | 1 | 是 | 10.00% | 66.67% | 65.08% | 100.00% |
| Vector-only | 14 | Mixture-of-Agents 如何通过 proposer 和 aggregator 的分层结构组合多个 LLM 的输出？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 3 | 3 | 5 | 是 | 15.00% | 100.00% | 44.84% | 100.00% |
| Vector-only | 15 | MoTE 如何将 reasoning chain 与 step-level expert routing 结合起来进行 self-alignment？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 3 | 3 | 3 | 是 | 15.00% | 100.00% | 53.75% | 100.00% |
| Vector-only | 16 | SkillDAG 为什么把技能库表示为 typed directed graph，而不是只使用 embedding 相似度检索？它如何处理依赖和冲突关系？ | SkillDAG.pdf | 3 | 2 | 1 | 是 | 10.00% | 66.67% | 65.08% | 100.00% |
| Vector-only | 17 | SKILLRET 如何构建大规模技能检索 benchmark？为什么多技能 query 是一个重要评测场景？ | SkillRet.pdf | 3 | 2 | 2 | 是 | 10.00% | 66.67% | 49.82% | 75.00% |
| Vector-only | 18 | SkillRouter 的实验为什么说明完整 skill body 对路由很重要？移除 body 会带来什么影响？ | SkillRouter.pdf | 3 | 2 | 1 | 是 | 10.00% | 66.67% | 65.08% | 100.00% |
| Vector-only | 19 | Dynamic Routing MoE 如何根据输入难度和专家选择置信度动态决定激活多少专家，并如何控制负载均衡？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 3 | 2 | 2 | 是 | 10.00% | 66.67% | 41.62% | 75.00% |
| Vector-only | 20 | Test-Time Learning for Large Language Models 如何利用无标签测试数据和输入困惑度实现适应，并通过 LoRA 减少灾难性遗忘？ | Test_Time_Learning_for_La.pdf | 3 | 3 | 1 | 是 | 15.00% | 100.00% | 79.29% | 100.00% |
| Vector-only | 21 | AT-RAG模型在数据预处理阶段如何为文档分配主题标签？ | AT-RAG.pdf | 2 | 1 | 9 | 是 | 5.00% | 50.00% | 18.46% | 16.67% |
| Vector-only | 22 | AT-RAG的主题过滤机制如何减少检索过程中的计算开销？ | AT-RAG.pdf | 2 | 2 | 3 | 是 | 10.00% | 100.00% | 54.38% | 0.00% |
| Vector-only | 23 | Adaptive-RAG框架中，用于预测查询复杂度的分类器是如何获得训练标签的？ | Adaptive_RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector-only | 24 | 为什么在现实场景中，为所有查询统一采用多步检索增强方法（如迭代访问LLM和检索器）是低效的？ | Adaptive_RAG.pdf | 2 | 1 | 8 | 是 | 5.00% | 50.00% | 19.34% | 0.00% |
| Vector-only | 25 | DF-RAG框架中的评估器（Evaluator）如何为候选文本块集打分以选择最优的多样性参数λ？ | DF-RAG.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 81.75% | 0.00% |
| Vector-only | 26 | DF-RAG的几何最大边际相关性（gMMR）评分函数在设计上，如何从几何角度衡量候选文本块与已选文本块集之间的多样性？ | DF-RAG.pdf | 2 | 1 | 6 | 是 | 5.00% | 50.00% | 21.84% | 0.00% |
| Vector-only | 27 | DH-HGCN模型如何利用超图卷积来学习用户表示？请描述其核心的两阶段特征变换过程。 | DH-MHCN.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 100.00% | 0.00% |
| Vector-only | 28 | DH-HGCN模型在构建用户-用户社交图时，对评论关系施加了何种限制性处理？ | DH-MHCN.pdf | 2 | 1 | 4 | 是 | 5.00% | 50.00% | 26.41% | 0.00% |
| Vector-only | 29 | 在DH-HGCN中，为何需要在用户嵌入学习过程中引入自门控单元（SGUs）？其设计动机是什么？ | DH-MHCN.pdf | 2 | 1 | 2 | 是 | 5.00% | 50.00% | 38.69% | 0.00% |
| Vector-only | 30 | DeepSeek-OCR 为什么把 OCR 当作验证长文本视觉压缩的试验任务，而不是直接在语言模型里压缩文本？ | DeepSeek_OCR_paper.pdf | 2 | 2 | 2 | 是 | 10.00% | 100.00% | 52.64% | 100.00% |
| Vector-only | 31 | DeepEncoder 如何串联局部注意力与全局注意力，在处理高分辨率文档时控制视觉 Token 数量和激活内存？ | DeepSeek_OCR_paper.pdf | 2 | 2 | 2 | 是 | 10.00% | 100.00% | 60.53% | 100.00% |
| Vector-only | 32 | DeepSeek-OCR 对视觉编码器有哪些要求，多分辨率模式如何帮助研究压缩比例与文本解码能力之间的关系？ | DeepSeek_OCR_paper.pdf | 2 | 2 | 12 | 是 | 10.00% | 100.00% | 31.57% | 100.00% |
| Vector-only | 33 | 在EvolveR框架中，离线自蒸馏阶段如何确保经验库中原则的质量并避免冗余？ | EVOLVER.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector-only | 34 | EvolveR框架主要旨在解决现有LLM智能体在哪种根本性能力上的不足？ | EVOLVER.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Vector-only | 35 | EvolveR框架中，策略进化机制是如何与整个经验生命周期闭环关联，从而实现智能体的自我演化的？ | EVOLVER.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 100.00% | 0.00% |
| Vector-only | 36 | FLARE方法中，如何利用对下一句的预测来指导检索过程？ | FLARE.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 85.03% | 0.00% |
| Vector-only | 37 | FLARE方法选择在生成过程中触发检索的核心机制依据是什么？ | FLARE.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Vector-only | 38 | 动态路由机制中，如何根据路由概率确定每个token最终激活的专家集合？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 2 | 13 | 是 | 10.00% | 100.00% | 30.29% | 0.00% |
| Vector-only | 39 | 动态路由方法在哪些类型的任务或场景下可能表现不佳或存在局限性？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 1 | 4 | 是 | 5.00% | 50.00% | 26.41% | 0.00% |
| Vector-only | 40 | 为什么动态路由机制需要引入额外的损失函数来约束路由概率分布？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 100.00% | 0.00% |
| Vector-only | 41 | KG2RAG框架中，图引导扩展过程如何利用知识图谱来增加检索块的多样性？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 2 | 2 | 是 | 10.00% | 100.00% | 69.34% | 0.00% |
| Vector-only | 42 | KG2RAG框架在文档离线处理阶段，对于知识图谱的可用性有哪些不同的处理方式？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector-only | 43 | KG2RAG框架中，基于知识图谱的上下文组织模块为什么要同时充当过滤器和整理器的双重角色？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 2 | 2 | 是 | 10.00% | 100.00% | 56.41% | 0.00% |
| Vector-only | 44 | 论文提出的LLM无关自适应检索方法中，如何利用知识图谱来构建特征？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 1 | 6 | 是 | 5.00% | 50.00% | 21.84% | 0.00% |
| Vector-only | 45 | 论文中评估的'Knowledgability'特征在应用时存在什么潜在的限制或前提条件？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Vector-only | 46 | 为什么论文认为基于外部信息的自适应检索方法，对于复杂问题的表现可能优于基于不确定性的方法？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 91.97% | 0.00% |
| Vector-only | 47 | MBA-RAG框架中，如何平衡对不同检索策略的探索与利用？ | MBA-RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector-only | 48 | MBA-RAG的动态奖励函数是如何设计以同时优化答案准确性和计算效率的？ | MBA-RAG.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Vector-only | 49 | MHCN模型如何通过超图卷积操作来编码不同类型的高阶用户关系？ | MHCN.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Vector-only | 50 | MHCN模型在利用超图建模高阶关系时，其多通道聚合操作存在什么潜在缺陷？ | MHCN.pdf | 2 | 1 | 7 | 是 | 5.00% | 50.00% | 20.44% | 0.00% |
| Vector-only | 51 | MHCN引入自监督学习任务的主要动机是什么？ | MHCN.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector-only | 52 | MoTE框架中，用于处理推理链不同步骤的LoRA专家是如何具体配置和工作的？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 2 | 2 | 是 | 10.00% | 100.00% | 65.09% | 0.00% |
| Vector-only | 53 | MoTE方法主要适用于哪些类型的语言模型？它在模型能力上有何特定的前提或限制？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 2 | 3 | 是 | 10.00% | 100.00% | 45.09% | 0.00% |
| Vector-only | 54 | 在MoTE框架中，引入中间推理步骤（如问题分析、答案指导）是如何从机制上增强模型安全性的？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 1 | 4 | 是 | 5.00% | 50.00% | 26.41% | 0.00% |
| Vector-only | 55 | 在Mixture-of-Agents框架中，如何为每一层选择合适的大语言模型？选择过程主要依据哪两个标准？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 1 | 11 | 是 | 5.00% | 50.00% | 17.10% | 0.00% |
| Vector-only | 56 | Mixture-of-Agents方法在应用上存在哪些固有的限制或前提条件？它是否需要对底层大语言模型进行微调？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector-only | 57 | 根据论文中的发现，即使辅助模型生成的回答质量较低，为什么聚合器大语言模型仍然能够产生更高质量的最终输出？这种现象被称为什么？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 1 | 11 | 是 | 5.00% | 50.00% | 17.10% | 0.00% |
| Vector-only | 58 | PSO-DGNN方法中，如何通过解耦模式设计搜索空间以构建深层图神经网络？ | PSO-DGNN.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 85.03% | 0.00% |
| Vector-only | 59 | 在深层图神经网络中，传统的耦合模式为何会导致性能下降或遇到瓶颈？ | PSO-DGNN.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 85.03% | 0.00% |
| Vector-only | 60 | PathwiseRAG框架中，查询意图分析的双流架构具体如何运作？ | PathwiseRAG.pdf | 2 | 1 | 3 | 是 | 5.00% | 50.00% | 30.66% | 0.00% |
| Vector-only | 61 | PathwiseRAG在并行路径探索中，如何处理来自不同检索策略的冲突信息？ | PathwiseRAG.pdf | 2 | 1 | 16 | 是 | 5.00% | 50.00% | 15.00% | 0.00% |
| Vector-only | 62 | PathwiseRAG的动态推理网络在迭代过程中如何根据新发现的信息进行自我调整？ | PathwiseRAG.pdf | 2 | 1 | 2 | 是 | 5.00% | 50.00% | 38.69% | 0.00% |
| Vector-only | 63 | Probing-RAG方法中，探针器（prober）被放置在语言模型的哪个相对位置，以及这样放置的设计依据是什么？ | Probing-RAG.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 100.00% | 0.00% |
| Vector-only | 64 | Probing-RAG方法在决定是否需要检索时，主要依赖于什么信息？这种方法与基于外部分类器的方法（如Adaptive-RAG）相比，存在什么根本区别？ | Probing-RAG.pdf | 2 | 2 | 10 | 是 | 10.00% | 100.00% | 34.83% | 0.00% |
| Vector-only | 65 | 在Probing-RAG中，训练探针器（prober）所使用的数据标签（y）是如何生成的？具体依据什么标准来判定一个样本需要标记为“需要检索”（y=0）？ | Probing-RAG.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Vector-only | 66 | RankRAG在指令微调的第二阶段，其训练数据混合体具体包含哪几个组成部分？ | RankRAG.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Vector-only | 67 | 在标准的RAG流程中，仅依赖检索模型来确保高召回率存在哪些固有挑战？ | RankRAG.pdf | 2 | 2 | 4 | 是 | 10.00% | 100.00% | 42.51% | 0.00% |
| Vector-only | 68 | RankRAG框架的设计基于一个关于LLM能力的核心假设是什么？这个假设如何指导了方法的统一？ | RankRAG.pdf | 2 | 2 | 2 | 是 | 10.00% | 100.00% | 60.53% | 0.00% |
| Vector-only | 69 | SkillDAG的在线编辑协议在提交图更新时需要强制执行哪些结构性不变量，以确保图不会变得不一致？ | SkillDAG.pdf | 2 | 1 | 3 | 是 | 5.00% | 50.00% | 30.66% | 0.00% |
| Vector-only | 70 | SkillDAG的检索接口在处理冲突关系时，与处理其他正向关系类型（如依赖、组合）有何根本不同？这种设计选择旨在避免什么风险？ | SkillDAG.pdf | 2 | 1 | 3 | 是 | 5.00% | 50.00% | 30.66% | 0.00% |
| Vector-only | 71 | 与仅依赖技能自描述相似度的扁平检索基线相比，SkillDAG引入显式关系类型（如依赖、特化）旨在解决哪种核心的语义混淆问题？ | SkillDAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector-only | 72 | SKILLRET基准在构建训练集和评估集时，如何确保技能库的划分不会导致数据泄露？ | SkillRet.pdf | 2 | 2 | 2 | 是 | 10.00% | 100.00% | 59.12% | 0.00% |
| Vector-only | 73 | SKILLRET基准在评估技能检索模型时，主要揭示了现有现成检索模型存在哪些局限性？ | SkillRet.pdf | 2 | 2 | 7 | 是 | 10.00% | 100.00% | 39.78% | 0.00% |
| Vector-only | 74 | 根据论文分析，在SKILLRET上进行任务特定微调后，模型性能提升的主要原因是什么？ | SkillRet.pdf | 2 | 2 | 2 | 是 | 10.00% | 100.00% | 69.34% | 0.00% |
| Vector-only | 75 | SKILLROUTER模型在训练过程中采用了哪两种特定的技术调整来应对同质化技能池带来的挑战？ | SkillRouter.pdf | 2 | 1 | 6 | 是 | 5.00% | 50.00% | 21.84% | 0.00% |
| Vector-only | 76 | SKILLROUTER的基准测试在评估路由性能时，主要关注哪两种特定的失败模式？这两种模式是如何通过实验设计来隔离的？ | SkillRouter.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector-only | 77 | 根据论文中的分析，为什么仅依赖技能名称和描述（元数据）进行路由选择在大型重叠技能库中会效果不佳？其根本原因是什么？ | SkillRouter.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 80.66% | 0.00% |
| Vector-only | 78 | TARG方法中，如何利用模型的短前缀输出来计算不确定性分数以决定是否进行检索？请具体说明其计算过程。 | TARG.pdf | 2 | 1 | 4 | 是 | 5.00% | 50.00% | 26.41% | 0.00% |
| Vector-only | 79 | TARG方法在哪些情况下可能不适用或效果有限？请基于其设计原理和假设进行说明。 | TARG.pdf | 2 | 1 | 6 | 是 | 5.00% | 50.00% | 21.84% | 0.00% |
| Vector-only | 80 | 为什么TARG方法中，基于top-1/top-2 logit间隙的边际信号在现代指令调优LLMs下能成为鲁棒的默认选择？其背后的机制原因是什么？ | TARG.pdf | 2 | 2 | 9 | 是 | 10.00% | 100.00% | 36.18% | 0.00% |
| Vector-only | 81 | TLM方法在测试时学习过程中，采用了何种具体策略来高效利用计算资源并选择对模型优化更重要的样本？ | Test_Time_Learning_for_La.pdf | 2 | 1 | 2 | 是 | 5.00% | 50.00% | 38.69% | 0.00% |
| Vector-only | 82 | 与传统的基于熵最小化的测试时适应方法相比，TLM方法在优化目标上做出了何种关键改变以更好地适应大语言模型的自回归特性？ | Test_Time_Learning_for_La.pdf | 2 | 2 | 2 | 是 | 10.00% | 100.00% | 52.64% | 0.00% |
| Vector-only | 83 | TLM方法在参数更新时采用LoRA而非全参数优化，主要是为了解决哪两个关键问题？ | Test_Time_Learning_for_La.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 91.97% | 0.00% |
| Vector-only | 84 | 在稀疏门控混合专家层中，门控网络如何实现稀疏性以节省计算？ | The Sparsely-Gated MOE.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 16.67% |
| Vector-only | 85 | 稀疏门控混合专家层在分布式训练中面临哪些主要性能挑战？ | The Sparsely-Gated MOE.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Vector-only | 86 | 为什么在训练稀疏门控混合专家层时，门控网络容易收敛到总是选择少数相同专家的状态？ | The Sparsely-Gated MOE.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Vector-only | 87 | TF-TTCL框架中的多智能体角色扮演具体涉及哪些角色，它们各自在查询增强阶段承担什么功能？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 2 | 1 | 2 | 是 | 5.00% | 50.00% | 38.69% | 50.00% |
| Vector-only | 88 | TF-TTCL框架为何能够在不更新模型参数的情况下实现自我改进？其核心机制是什么？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 2 | 1 | 5 | 是 | 5.00% | 50.00% | 23.72% | 0.00% |
| Vector-only | 89 | SELF-RAG框架在推理阶段如何通过特殊令牌实现生成过程的自适应控制？ | self-rag.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Vector-only | 90 | SELF-RAG为何要在每个生成片段后插入批判令牌来评估输出？ | self-rag.pdf | 2 | 2 | 2 | 是 | 10.00% | 100.00% | 65.09% | 0.00% |
| BM25-only | 1 | Adaptive-RAG 如何依据问题复杂度，在无检索、单步检索和多步检索之间选择策略？ | Adaptive_RAG.pdf | 3 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 100.00% |
| BM25-only | 2 | Adaptive-RAG 的问题复杂度分类器如何训练，并在推理时输出检索策略？ | Adaptive_RAG.pdf | 3 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 50.00% |
| BM25-only | 3 | DF-RAG 为什么需要多样性检索，它如何缓解余弦相似度带来的冗余证据问题？ | DF-RAG.pdf | 3 | 2 | 7 | 是 | 10.00% | 66.67% | 28.73% | 100.00% |
| BM25-only | 4 | DF-RAG 的 gMMR 如何计算候选 Chunk，并怎样在测试时选择查询自适应的多样性参数？ | DF-RAG.pdf | 3 | 1 | 2 | 是 | 5.00% | 33.33% | 29.61% | 100.00% |
| BM25-only | 5 | FLARE 为什么要前瞻性地预测下一句，并根据低概率词触发主动检索？ | FLARE.pdf | 3 | 2 | 12 | 是 | 10.00% | 66.67% | 24.41% | 100.00% |
| BM25-only | 6 | LLM-Independent Adaptive RAG 如何在不依赖大模型不确定性估计的情况下减少不必要检索？ | LLM-Independent Adaptiverag RAG.pdf | 3 | 2 | 5 | 是 | 28.57% | 66.67% | 34.87% | 100.00% |
| BM25-only | 7 | MBA-RAG 如何用 Bandit 根据问题复杂度选择检索路径，并在准确率与检索成本之间做权衡？ | MBA-RAG.pdf | 3 | 2 | 18 | 是 | 10.00% | 66.67% | 21.91% | 100.00% |
| BM25-only | 8 | Probing-RAG 如何利用语言模型隐藏状态判断是否需要额外检索？ | Probing-RAG.pdf | 3 | 1 | 5 | 是 | 5.00% | 33.33% | 18.15% | 100.00% |
| BM25-only | 9 | Adaptive-RAG、DF-RAG 和 FLARE 分别从问题复杂度、证据多样性和生成过程的哪个信号决定检索行为？ | Adaptive_RAG.pdf, DF-RAG.pdf, FLARE.pdf | 3 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 25.00% |
| BM25-only | 10 | Adaptive-RAG 的评测为什么要同时报告效果指标和效率指标？它相对复杂多步策略的效率结论是什么？ | Adaptive_RAG.pdf | 3 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 11 | EvolveR 如何把 Agent 的交互轨迹提炼为经验，并在后续任务中检索这些经验？ | EVOLVER.pdf | 3 | 3 | 2 | 是 | 15.00% | 100.00% | 59.24% | 75.00% |
| BM25-only | 12 | TF-TTCL 如何通过语义查询增强、对比经验蒸馏和规则检索实现无训练的测试时自适应？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 3 | 2 | 5 | 是 | 12.50% | 66.67% | 29.64% | 100.00% |
| BM25-only | 13 | Sparsely-Gated Mixture-of-Experts 层由哪些组件组成？稀疏门控如何在提升模型容量的同时控制计算量？ | The Sparsely-Gated MOE.pdf | 3 | 2 | 2 | 是 | 22.22% | 66.67% | 49.82% | 100.00% |
| BM25-only | 14 | Mixture-of-Agents 如何通过 proposer 和 aggregator 的分层结构组合多个 LLM 的输出？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 3 | 2 | 5 | 是 | 12.50% | 66.67% | 34.87% | 100.00% |
| BM25-only | 15 | MoTE 如何将 reasoning chain 与 step-level expert routing 结合起来进行 self-alignment？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 3 | 3 | 1 | 是 | 15.00% | 100.00% | 85.29% | 100.00% |
| BM25-only | 16 | SkillDAG 为什么把技能库表示为 typed directed graph，而不是只使用 embedding 相似度检索？它如何处理依赖和冲突关系？ | SkillDAG.pdf | 3 | 2 | 3 | 是 | 10.00% | 66.67% | 38.27% | 100.00% |
| BM25-only | 17 | SKILLRET 如何构建大规模技能检索 benchmark？为什么多技能 query 是一个重要评测场景？ | SkillRet.pdf | 3 | 2 | 6 | 是 | 14.29% | 66.67% | 29.04% | 75.00% |
| BM25-only | 18 | SkillRouter 的实验为什么说明完整 skill body 对路由很重要？移除 body 会带来什么影响？ | SkillRouter.pdf | 3 | 2 | 15 | 是 | 10.00% | 66.67% | 22.78% | 100.00% |
| BM25-only | 19 | Dynamic Routing MoE 如何根据输入难度和专家选择置信度动态决定激活多少专家，并如何控制负载均衡？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 3 | 1 | 8 | 是 | 5.00% | 33.33% | 14.80% | 75.00% |
| BM25-only | 20 | Test-Time Learning for Large Language Models 如何利用无标签测试数据和输入困惑度实现适应，并通过 LoRA 减少灾难性遗忘？ | Test_Time_Learning_for_La.pdf | 3 | 3 | 4 | 是 | 15.00% | 100.00% | 43.58% | 100.00% |
| BM25-only | 21 | AT-RAG模型在数据预处理阶段如何为文档分配主题标签？ | AT-RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 16.67% |
| BM25-only | 22 | AT-RAG的主题过滤机制如何减少检索过程中的计算开销？ | AT-RAG.pdf | 2 | 2 | 5 | 是 | 13.33% | 100.00% | 44.16% | 0.00% |
| BM25-only | 23 | Adaptive-RAG框架中，用于预测查询复杂度的分类器是如何获得训练标签的？ | Adaptive_RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 24 | 为什么在现实场景中，为所有查询统一采用多步检索增强方法（如迭代访问LLM和检索器）是低效的？ | Adaptive_RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 25 | DF-RAG框架中的评估器（Evaluator）如何为候选文本块集打分以选择最优的多样性参数λ？ | DF-RAG.pdf | 2 | 1 | 12 | 是 | 5.56% | 50.00% | 16.57% | 0.00% |
| BM25-only | 26 | DF-RAG的几何最大边际相关性（gMMR）评分函数在设计上，如何从几何角度衡量候选文本块与已选文本块集之间的多样性？ | DF-RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 27 | DH-HGCN模型如何利用超图卷积来学习用户表示？请描述其核心的两阶段特征变换过程。 | DH-MHCN.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 28 | DH-HGCN模型在构建用户-用户社交图时，对评论关系施加了何种限制性处理？ | DH-MHCN.pdf | 2 | 1 | 11 | 是 | 8.33% | 50.00% | 17.10% | 0.00% |
| BM25-only | 29 | 在DH-HGCN中，为何需要在用户嵌入学习过程中引入自门控单元（SGUs）？其设计动机是什么？ | DH-MHCN.pdf | 2 | 1 | 4 | 是 | 12.50% | 50.00% | 26.41% | 0.00% |
| BM25-only | 30 | DeepSeek-OCR 为什么把 OCR 当作验证长文本视觉压缩的试验任务，而不是直接在语言模型里压缩文本？ | DeepSeek_OCR_paper.pdf | 2 | 1 | 20 | 是 | 5.00% | 50.00% | 13.96% | 100.00% |
| BM25-only | 31 | DeepEncoder 如何串联局部注意力与全局注意力，在处理高分辨率文档时控制视觉 Token 数量和激活内存？ | DeepSeek_OCR_paper.pdf | 2 | 2 | 5 | 是 | 16.67% | 100.00% | 40.82% | 100.00% |
| BM25-only | 32 | DeepSeek-OCR 对视觉编码器有哪些要求，多分辨率模式如何帮助研究压缩比例与文本解码能力之间的关系？ | DeepSeek_OCR_paper.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 66.67% |
| BM25-only | 33 | 在EvolveR框架中，离线自蒸馏阶段如何确保经验库中原则的质量并避免冗余？ | EVOLVER.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 34 | EvolveR框架主要旨在解决现有LLM智能体在哪种根本性能力上的不足？ | EVOLVER.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 35 | EvolveR框架中，策略进化机制是如何与整个经验生命周期闭环关联，从而实现智能体的自我演化的？ | EVOLVER.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 36 | FLARE方法中，如何利用对下一句的预测来指导检索过程？ | FLARE.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 37 | FLARE方法选择在生成过程中触发检索的核心机制依据是什么？ | FLARE.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 38 | 动态路由机制中，如何根据路由概率确定每个token最终激活的专家集合？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 39 | 动态路由方法在哪些类型的任务或场景下可能表现不佳或存在局限性？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 40 | 为什么动态路由机制需要引入额外的损失函数来约束路由概率分布？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 41 | KG2RAG框架中，图引导扩展过程如何利用知识图谱来增加检索块的多样性？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 1 | 13 | 是 | 6.67% | 50.00% | 16.10% | 0.00% |
| BM25-only | 42 | KG2RAG框架在文档离线处理阶段，对于知识图谱的可用性有哪些不同的处理方式？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 43 | KG2RAG框架中，基于知识图谱的上下文组织模块为什么要同时充当过滤器和整理器的双重角色？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 44 | 论文提出的LLM无关自适应检索方法中，如何利用知识图谱来构建特征？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 45 | 论文中评估的'Knowledgability'特征在应用时存在什么潜在的限制或前提条件？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 46 | 为什么论文认为基于外部信息的自适应检索方法，对于复杂问题的表现可能优于基于不确定性的方法？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 47 | MBA-RAG框架中，如何平衡对不同检索策略的探索与利用？ | MBA-RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 48 | MBA-RAG的动态奖励函数是如何设计以同时优化答案准确性和计算效率的？ | MBA-RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 49 | MHCN模型如何通过超图卷积操作来编码不同类型的高阶用户关系？ | MHCN.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 50 | MHCN模型在利用超图建模高阶关系时，其多通道聚合操作存在什么潜在缺陷？ | MHCN.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 51 | MHCN引入自监督学习任务的主要动机是什么？ | MHCN.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 52 | MoTE框架中，用于处理推理链不同步骤的LoRA专家是如何具体配置和工作的？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 1 | 3 | 是 | 25.00% | 50.00% | 30.66% | 0.00% |
| BM25-only | 53 | MoTE方法主要适用于哪些类型的语言模型？它在模型能力上有何特定的前提或限制？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 54 | 在MoTE框架中，引入中间推理步骤（如问题分析、答案指导）是如何从机制上增强模型安全性的？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 55 | 在Mixture-of-Agents框架中，如何为每一层选择合适的大语言模型？选择过程主要依据哪两个标准？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 56 | Mixture-of-Agents方法在应用上存在哪些固有的限制或前提条件？它是否需要对底层大语言模型进行微调？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 57 | 根据论文中的发现，即使辅助模型生成的回答质量较低，为什么聚合器大语言模型仍然能够产生更高质量的最终输出？这种现象被称为什么？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 58 | PSO-DGNN方法中，如何通过解耦模式设计搜索空间以构建深层图神经网络？ | PSO-DGNN.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 59 | 在深层图神经网络中，传统的耦合模式为何会导致性能下降或遇到瓶颈？ | PSO-DGNN.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 60 | PathwiseRAG框架中，查询意图分析的双流架构具体如何运作？ | PathwiseRAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 61 | PathwiseRAG在并行路径探索中，如何处理来自不同检索策略的冲突信息？ | PathwiseRAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 62 | PathwiseRAG的动态推理网络在迭代过程中如何根据新发现的信息进行自我调整？ | PathwiseRAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 63 | Probing-RAG方法中，探针器（prober）被放置在语言模型的哪个相对位置，以及这样放置的设计依据是什么？ | Probing-RAG.pdf | 2 | 1 | 5 | 是 | 9.09% | 50.00% | 23.72% | 0.00% |
| BM25-only | 64 | Probing-RAG方法在决定是否需要检索时，主要依赖于什么信息？这种方法与基于外部分类器的方法（如Adaptive-RAG）相比，存在什么根本区别？ | Probing-RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 65 | 在Probing-RAG中，训练探针器（prober）所使用的数据标签（y）是如何生成的？具体依据什么标准来判定一个样本需要标记为“需要检索”（y=0）？ | Probing-RAG.pdf | 2 | 1 | 4 | 是 | 25.00% | 50.00% | 26.41% | 0.00% |
| BM25-only | 66 | RankRAG在指令微调的第二阶段，其训练数据混合体具体包含哪几个组成部分？ | RankRAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 67 | 在标准的RAG流程中，仅依赖检索模型来确保高召回率存在哪些固有挑战？ | RankRAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 68 | RankRAG框架的设计基于一个关于LLM能力的核心假设是什么？这个假设如何指导了方法的统一？ | RankRAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 69 | SkillDAG的在线编辑协议在提交图更新时需要强制执行哪些结构性不变量，以确保图不会变得不一致？ | SkillDAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 70 | SkillDAG的检索接口在处理冲突关系时，与处理其他正向关系类型（如依赖、组合）有何根本不同？这种设计选择旨在避免什么风险？ | SkillDAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 71 | 与仅依赖技能自描述相似度的扁平检索基线相比，SkillDAG引入显式关系类型（如依赖、特化）旨在解决哪种核心的语义混淆问题？ | SkillDAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 72 | SKILLRET基准在构建训练集和评估集时，如何确保技能库的划分不会导致数据泄露？ | SkillRet.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 73 | SKILLRET基准在评估技能检索模型时，主要揭示了现有现成检索模型存在哪些局限性？ | SkillRet.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 74 | 根据论文分析，在SKILLRET上进行任务特定微调后，模型性能提升的主要原因是什么？ | SkillRet.pdf | 2 | 1 | 20 | 是 | 5.00% | 50.00% | 13.96% | 0.00% |
| BM25-only | 75 | SKILLROUTER模型在训练过程中采用了哪两种特定的技术调整来应对同质化技能池带来的挑战？ | SkillRouter.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 76 | SKILLROUTER的基准测试在评估路由性能时，主要关注哪两种特定的失败模式？这两种模式是如何通过实验设计来隔离的？ | SkillRouter.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 77 | 根据论文中的分析，为什么仅依赖技能名称和描述（元数据）进行路由选择在大型重叠技能库中会效果不佳？其根本原因是什么？ | SkillRouter.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 78 | TARG方法中，如何利用模型的短前缀输出来计算不确定性分数以决定是否进行检索？请具体说明其计算过程。 | TARG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 79 | TARG方法在哪些情况下可能不适用或效果有限？请基于其设计原理和假设进行说明。 | TARG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 80 | 为什么TARG方法中，基于top-1/top-2 logit间隙的边际信号在现代指令调优LLMs下能成为鲁棒的默认选择？其背后的机制原因是什么？ | TARG.pdf | 2 | 2 | 2 | 是 | 15.38% | 100.00% | 56.41% | 0.00% |
| BM25-only | 81 | TLM方法在测试时学习过程中，采用了何种具体策略来高效利用计算资源并选择对模型优化更重要的样本？ | Test_Time_Learning_for_La.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 82 | 与传统的基于熵最小化的测试时适应方法相比，TLM方法在优化目标上做出了何种关键改变以更好地适应大语言模型的自回归特性？ | Test_Time_Learning_for_La.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 83 | TLM方法在参数更新时采用LoRA而非全参数优化，主要是为了解决哪两个关键问题？ | Test_Time_Learning_for_La.pdf | 2 | 2 | 4 | 是 | 10.00% | 100.00% | 43.51% | 0.00% |
| BM25-only | 84 | 在稀疏门控混合专家层中，门控网络如何实现稀疏性以节省计算？ | The Sparsely-Gated MOE.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 85 | 稀疏门控混合专家层在分布式训练中面临哪些主要性能挑战？ | The Sparsely-Gated MOE.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 86 | 为什么在训练稀疏门控混合专家层时，门控网络容易收敛到总是选择少数相同专家的状态？ | The Sparsely-Gated MOE.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 87 | TF-TTCL框架中的多智能体角色扮演具体涉及哪些角色，它们各自在查询增强阶段承担什么功能？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 16.67% |
| BM25-only | 88 | TF-TTCL框架为何能够在不更新模型参数的情况下实现自我改进？其核心机制是什么？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 89 | SELF-RAG框架在推理阶段如何通过特殊令牌实现生成过程的自适应控制？ | self-rag.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| BM25-only | 90 | SELF-RAG为何要在每个生成片段后插入批判令牌来评估输出？ | self-rag.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid | 1 | Adaptive-RAG 如何依据问题复杂度，在无检索、单步检索和多步检索之间选择策略？ | Adaptive_RAG.pdf | 3 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 100.00% |
| Hybrid | 2 | Adaptive-RAG 的问题复杂度分类器如何训练，并在推理时输出检索策略？ | Adaptive_RAG.pdf | 3 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 50.00% |
| Hybrid | 3 | DF-RAG 为什么需要多样性检索，它如何缓解余弦相似度带来的冗余证据问题？ | DF-RAG.pdf | 3 | 2 | 1 | 是 | 10.00% | 66.67% | 76.54% | 100.00% |
| Hybrid | 4 | DF-RAG 的 gMMR 如何计算候选 Chunk，并怎样在测试时选择查询自适应的多样性参数？ | DF-RAG.pdf | 3 | 1 | 1 | 是 | 5.00% | 33.33% | 46.93% | 100.00% |
| Hybrid | 5 | FLARE 为什么要前瞻性地预测下一句，并根据低概率词触发主动检索？ | FLARE.pdf | 3 | 3 | 3 | 是 | 15.00% | 100.00% | 51.79% | 100.00% |
| Hybrid | 6 | LLM-Independent Adaptive RAG 如何在不依赖大模型不确定性估计的情况下减少不必要检索？ | LLM-Independent Adaptiverag RAG.pdf | 3 | 2 | 1 | 是 | 10.00% | 66.67% | 60.02% | 100.00% |
| Hybrid | 7 | MBA-RAG 如何用 Bandit 根据问题复杂度选择检索路径，并在准确率与检索成本之间做权衡？ | MBA-RAG.pdf | 3 | 2 | 8 | 是 | 10.00% | 66.67% | 28.93% | 100.00% |
| Hybrid | 8 | Probing-RAG 如何利用语言模型隐藏状态判断是否需要额外检索？ | Probing-RAG.pdf | 3 | 2 | 2 | 是 | 10.00% | 66.67% | 41.09% | 100.00% |
| Hybrid | 9 | Adaptive-RAG、DF-RAG 和 FLARE 分别从问题复杂度、证据多样性和生成过程的哪个信号决定检索行为？ | Adaptive_RAG.pdf, DF-RAG.pdf, FLARE.pdf | 3 | 2 | 5 | 是 | 10.00% | 66.67% | 29.20% | 100.00% |
| Hybrid | 10 | Adaptive-RAG 的评测为什么要同时报告效果指标和效率指标？它相对复杂多步策略的效率结论是什么？ | Adaptive_RAG.pdf | 3 | 1 | 3 | 是 | 5.00% | 33.33% | 23.46% | 75.00% |
| Hybrid | 11 | EvolveR 如何把 Agent 的交互轨迹提炼为经验，并在后续任务中检索这些经验？ | EVOLVER.pdf | 3 | 3 | 1 | 是 | 15.00% | 100.00% | 88.86% | 100.00% |
| Hybrid | 12 | TF-TTCL 如何通过语义查询增强、对比经验蒸馏和规则检索实现无训练的测试时自适应？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 3 | 2 | 3 | 是 | 10.00% | 66.67% | 40.18% | 100.00% |
| Hybrid | 13 | Sparsely-Gated Mixture-of-Experts 层由哪些组件组成？稀疏门控如何在提升模型容量的同时控制计算量？ | The Sparsely-Gated MOE.pdf | 3 | 3 | 1 | 是 | 15.00% | 100.00% | 77.37% | 100.00% |
| Hybrid | 14 | Mixture-of-Agents 如何通过 proposer 和 aggregator 的分层结构组合多个 LLM 的输出？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 3 | 3 | 3 | 是 | 15.00% | 100.00% | 50.59% | 100.00% |
| Hybrid | 15 | MoTE 如何将 reasoning chain 与 step-level expert routing 结合起来进行 self-alignment？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 3 | 3 | 2 | 是 | 15.00% | 100.00% | 64.62% | 100.00% |
| Hybrid | 16 | SkillDAG 为什么把技能库表示为 typed directed graph，而不是只使用 embedding 相似度检索？它如何处理依赖和冲突关系？ | SkillDAG.pdf | 3 | 2 | 1 | 是 | 10.00% | 66.67% | 67.14% | 100.00% |
| Hybrid | 17 | SKILLRET 如何构建大规模技能检索 benchmark？为什么多技能 query 是一个重要评测场景？ | SkillRet.pdf | 3 | 2 | 3 | 是 | 10.00% | 66.67% | 41.62% | 75.00% |
| Hybrid | 18 | SkillRouter 的实验为什么说明完整 skill body 对路由很重要？移除 body 会带来什么影响？ | SkillRouter.pdf | 3 | 2 | 2 | 是 | 10.00% | 66.67% | 49.82% | 100.00% |
| Hybrid | 19 | Dynamic Routing MoE 如何根据输入难度和专家选择置信度动态决定激活多少专家，并如何控制负载均衡？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 3 | 1 | 3 | 是 | 5.00% | 33.33% | 23.46% | 75.00% |
| Hybrid | 20 | Test-Time Learning for Large Language Models 如何利用无标签测试数据和输入困惑度实现适应，并通过 LoRA 减少灾难性遗忘？ | Test_Time_Learning_for_La.pdf | 3 | 3 | 2 | 是 | 15.00% | 100.00% | 61.97% | 100.00% |
| Hybrid | 21 | AT-RAG模型在数据预处理阶段如何为文档分配主题标签？ | AT-RAG.pdf | 2 | 1 | 18 | 是 | 5.00% | 50.00% | 14.43% | 16.67% |
| Hybrid | 22 | AT-RAG的主题过滤机制如何减少检索过程中的计算开销？ | AT-RAG.pdf | 2 | 2 | 3 | 是 | 10.00% | 100.00% | 57.06% | 0.00% |
| Hybrid | 23 | Adaptive-RAG框架中，用于预测查询复杂度的分类器是如何获得训练标签的？ | Adaptive_RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid | 24 | 为什么在现实场景中，为所有查询统一采用多步检索增强方法（如迭代访问LLM和检索器）是低效的？ | Adaptive_RAG.pdf | 2 | 1 | 10 | 是 | 5.00% | 50.00% | 17.72% | 0.00% |
| Hybrid | 25 | DF-RAG框架中的评估器（Evaluator）如何为候选文本块集打分以选择最优的多样性参数λ？ | DF-RAG.pdf | 2 | 2 | 4 | 是 | 10.00% | 100.00% | 41.74% | 0.00% |
| Hybrid | 26 | DF-RAG的几何最大边际相关性（gMMR）评分函数在设计上，如何从几何角度衡量候选文本块与已选文本块集之间的多样性？ | DF-RAG.pdf | 2 | 1 | 13 | 是 | 5.00% | 50.00% | 16.10% | 0.00% |
| Hybrid | 27 | DH-HGCN模型如何利用超图卷积来学习用户表示？请描述其核心的两阶段特征变换过程。 | DH-MHCN.pdf | 2 | 2 | 4 | 是 | 10.00% | 100.00% | 48.25% | 0.00% |
| Hybrid | 28 | DH-HGCN模型在构建用户-用户社交图时，对评论关系施加了何种限制性处理？ | DH-MHCN.pdf | 2 | 1 | 6 | 是 | 5.00% | 50.00% | 21.84% | 0.00% |
| Hybrid | 29 | 在DH-HGCN中，为何需要在用户嵌入学习过程中引入自门控单元（SGUs）？其设计动机是什么？ | DH-MHCN.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Hybrid | 30 | DeepSeek-OCR 为什么把 OCR 当作验证长文本视觉压缩的试验任务，而不是直接在语言模型里压缩文本？ | DeepSeek_OCR_paper.pdf | 2 | 1 | 8 | 是 | 5.00% | 50.00% | 19.34% | 100.00% |
| Hybrid | 31 | DeepEncoder 如何串联局部注意力与全局注意力，在处理高分辨率文档时控制视觉 Token 数量和激活内存？ | DeepSeek_OCR_paper.pdf | 2 | 2 | 3 | 是 | 10.00% | 100.00% | 57.06% | 100.00% |
| Hybrid | 32 | DeepSeek-OCR 对视觉编码器有哪些要求，多分辨率模式如何帮助研究压缩比例与文本解码能力之间的关系？ | DeepSeek_OCR_paper.pdf | 2 | 2 | 15 | 是 | 10.00% | 100.00% | 29.76% | 100.00% |
| Hybrid | 33 | 在EvolveR框架中，离线自蒸馏阶段如何确保经验库中原则的质量并避免冗余？ | EVOLVER.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid | 34 | EvolveR框架主要旨在解决现有LLM智能体在哪种根本性能力上的不足？ | EVOLVER.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Hybrid | 35 | EvolveR框架中，策略进化机制是如何与整个经验生命周期闭环关联，从而实现智能体的自我演化的？ | EVOLVER.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 91.97% | 0.00% |
| Hybrid | 36 | FLARE方法中，如何利用对下一句的预测来指导检索过程？ | FLARE.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 81.75% | 0.00% |
| Hybrid | 37 | FLARE方法选择在生成过程中触发检索的核心机制依据是什么？ | FLARE.pdf | 2 | 1 | 10 | 是 | 5.00% | 50.00% | 17.72% | 0.00% |
| Hybrid | 38 | 动态路由机制中，如何根据路由概率确定每个token最终激活的专家集合？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid | 39 | 动态路由方法在哪些类型的任务或场景下可能表现不佳或存在局限性？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 1 | 5 | 是 | 5.00% | 50.00% | 23.72% | 0.00% |
| Hybrid | 40 | 为什么动态路由机制需要引入额外的损失函数来约束路由概率分布？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 91.97% | 0.00% |
| Hybrid | 41 | KG2RAG框架中，图引导扩展过程如何利用知识图谱来增加检索块的多样性？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 2 | 4 | 是 | 10.00% | 100.00% | 43.51% | 0.00% |
| Hybrid | 42 | KG2RAG框架在文档离线处理阶段，对于知识图谱的可用性有哪些不同的处理方式？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid | 43 | KG2RAG框架中，基于知识图谱的上下文组织模块为什么要同时充当过滤器和整理器的双重角色？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 2 | 3 | 是 | 10.00% | 100.00% | 47.23% | 0.00% |
| Hybrid | 44 | 论文提出的LLM无关自适应检索方法中，如何利用知识图谱来构建特征？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 1 | 8 | 是 | 5.00% | 50.00% | 19.34% | 0.00% |
| Hybrid | 45 | 论文中评估的'Knowledgability'特征在应用时存在什么潜在的限制或前提条件？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 1 | 2 | 是 | 5.00% | 50.00% | 38.69% | 0.00% |
| Hybrid | 46 | 为什么论文认为基于外部信息的自适应检索方法，对于复杂问题的表现可能优于基于不确定性的方法？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 85.03% | 0.00% |
| Hybrid | 47 | MBA-RAG框架中，如何平衡对不同检索策略的探索与利用？ | MBA-RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid | 48 | MBA-RAG的动态奖励函数是如何设计以同时优化答案准确性和计算效率的？ | MBA-RAG.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Hybrid | 49 | MHCN模型如何通过超图卷积操作来编码不同类型的高阶用户关系？ | MHCN.pdf | 2 | 1 | 4 | 是 | 5.00% | 50.00% | 26.41% | 0.00% |
| Hybrid | 50 | MHCN模型在利用超图建模高阶关系时，其多通道聚合操作存在什么潜在缺陷？ | MHCN.pdf | 2 | 1 | 13 | 是 | 5.00% | 50.00% | 16.10% | 0.00% |
| Hybrid | 51 | MHCN引入自监督学习任务的主要动机是什么？ | MHCN.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid | 52 | MoTE框架中，用于处理推理链不同步骤的LoRA专家是如何具体配置和工作的？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 81.75% | 0.00% |
| Hybrid | 53 | MoTE方法主要适用于哪些类型的语言模型？它在模型能力上有何特定的前提或限制？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 2 | 5 | 是 | 10.00% | 100.00% | 37.68% | 0.00% |
| Hybrid | 54 | 在MoTE框架中，引入中间推理步骤（如问题分析、答案指导）是如何从机制上增强模型安全性的？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 1 | 7 | 是 | 5.00% | 50.00% | 20.44% | 0.00% |
| Hybrid | 55 | 在Mixture-of-Agents框架中，如何为每一层选择合适的大语言模型？选择过程主要依据哪两个标准？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 1 | 13 | 是 | 5.00% | 50.00% | 16.10% | 0.00% |
| Hybrid | 56 | Mixture-of-Agents方法在应用上存在哪些固有的限制或前提条件？它是否需要对底层大语言模型进行微调？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid | 57 | 根据论文中的发现，即使辅助模型生成的回答质量较低，为什么聚合器大语言模型仍然能够产生更高质量的最终输出？这种现象被称为什么？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 1 | 14 | 是 | 5.00% | 50.00% | 15.69% | 0.00% |
| Hybrid | 58 | PSO-DGNN方法中，如何通过解耦模式设计搜索空间以构建深层图神经网络？ | PSO-DGNN.pdf | 2 | 2 | 2 | 是 | 10.00% | 100.00% | 58.03% | 0.00% |
| Hybrid | 59 | 在深层图神经网络中，传统的耦合模式为何会导致性能下降或遇到瓶颈？ | PSO-DGNN.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 81.75% | 0.00% |
| Hybrid | 60 | PathwiseRAG框架中，查询意图分析的双流架构具体如何运作？ | PathwiseRAG.pdf | 2 | 1 | 15 | 是 | 5.00% | 50.00% | 15.33% | 0.00% |
| Hybrid | 61 | PathwiseRAG在并行路径探索中，如何处理来自不同检索策略的冲突信息？ | PathwiseRAG.pdf | 2 | 1 | 18 | 是 | 5.00% | 50.00% | 14.43% | 0.00% |
| Hybrid | 62 | PathwiseRAG的动态推理网络在迭代过程中如何根据新发现的信息进行自我调整？ | PathwiseRAG.pdf | 2 | 1 | 3 | 是 | 5.00% | 50.00% | 30.66% | 0.00% |
| Hybrid | 63 | Probing-RAG方法中，探针器（prober）被放置在语言模型的哪个相对位置，以及这样放置的设计依据是什么？ | Probing-RAG.pdf | 2 | 2 | 2 | 是 | 10.00% | 100.00% | 55.79% | 0.00% |
| Hybrid | 64 | Probing-RAG方法在决定是否需要检索时，主要依赖于什么信息？这种方法与基于外部分类器的方法（如Adaptive-RAG）相比，存在什么根本区别？ | Probing-RAG.pdf | 2 | 2 | 14 | 是 | 10.00% | 100.00% | 30.69% | 0.00% |
| Hybrid | 65 | 在Probing-RAG中，训练探针器（prober）所使用的数据标签（y）是如何生成的？具体依据什么标准来判定一个样本需要标记为“需要检索”（y=0）？ | Probing-RAG.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Hybrid | 66 | RankRAG在指令微调的第二阶段，其训练数据混合体具体包含哪几个组成部分？ | RankRAG.pdf | 2 | 1 | 2 | 是 | 5.00% | 50.00% | 38.69% | 0.00% |
| Hybrid | 67 | 在标准的RAG流程中，仅依赖检索模型来确保高召回率存在哪些固有挑战？ | RankRAG.pdf | 2 | 2 | 6 | 是 | 10.00% | 100.00% | 37.17% | 0.00% |
| Hybrid | 68 | RankRAG框架的设计基于一个关于LLM能力的核心假设是什么？这个假设如何指导了方法的统一？ | RankRAG.pdf | 2 | 2 | 3 | 是 | 10.00% | 100.00% | 51.10% | 0.00% |
| Hybrid | 69 | SkillDAG的在线编辑协议在提交图更新时需要强制执行哪些结构性不变量，以确保图不会变得不一致？ | SkillDAG.pdf | 2 | 1 | 5 | 是 | 5.00% | 50.00% | 23.72% | 0.00% |
| Hybrid | 70 | SkillDAG的检索接口在处理冲突关系时，与处理其他正向关系类型（如依赖、组合）有何根本不同？这种设计选择旨在避免什么风险？ | SkillDAG.pdf | 2 | 1 | 5 | 是 | 5.00% | 50.00% | 23.72% | 0.00% |
| Hybrid | 71 | 与仅依赖技能自描述相似度的扁平检索基线相比，SkillDAG引入显式关系类型（如依赖、特化）旨在解决哪种核心的语义混淆问题？ | SkillDAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid | 72 | SKILLRET基准在构建训练集和评估集时，如何确保技能库的划分不会导致数据泄露？ | SkillRet.pdf | 2 | 2 | 8 | 是 | 10.00% | 100.00% | 35.91% | 0.00% |
| Hybrid | 73 | SKILLRET基准在评估技能检索模型时，主要揭示了现有现成检索模型存在哪些局限性？ | SkillRet.pdf | 2 | 2 | 16 | 是 | 10.00% | 100.00% | 29.70% | 0.00% |
| Hybrid | 74 | 根据论文分析，在SKILLRET上进行任务特定微调后，模型性能提升的主要原因是什么？ | SkillRet.pdf | 2 | 2 | 4 | 是 | 10.00% | 100.00% | 42.51% | 0.00% |
| Hybrid | 75 | SKILLROUTER模型在训练过程中采用了哪两种特定的技术调整来应对同质化技能池带来的挑战？ | SkillRouter.pdf | 2 | 1 | 8 | 是 | 5.00% | 50.00% | 19.34% | 0.00% |
| Hybrid | 76 | SKILLROUTER的基准测试在评估路由性能时，主要关注哪两种特定的失败模式？这两种模式是如何通过实验设计来隔离的？ | SkillRouter.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid | 77 | 根据论文中的分析，为什么仅依赖技能名称和描述（元数据）进行路由选择在大型重叠技能库中会效果不佳？其根本原因是什么？ | SkillRouter.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 79.04% | 0.00% |
| Hybrid | 78 | TARG方法中，如何利用模型的短前缀输出来计算不确定性分数以决定是否进行检索？请具体说明其计算过程。 | TARG.pdf | 2 | 1 | 6 | 是 | 5.00% | 50.00% | 21.84% | 0.00% |
| Hybrid | 79 | TARG方法在哪些情况下可能不适用或效果有限？请基于其设计原理和假设进行说明。 | TARG.pdf | 2 | 1 | 8 | 是 | 5.00% | 50.00% | 19.34% | 0.00% |
| Hybrid | 80 | 为什么TARG方法中，基于top-1/top-2 logit间隙的边际信号在现代指令调优LLMs下能成为鲁棒的默认选择？其背后的机制原因是什么？ | TARG.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 83.16% | 0.00% |
| Hybrid | 81 | TLM方法在测试时学习过程中，采用了何种具体策略来高效利用计算资源并选择对模型优化更重要的样本？ | Test_Time_Learning_for_La.pdf | 2 | 1 | 3 | 是 | 5.00% | 50.00% | 30.66% | 0.00% |
| Hybrid | 82 | 与传统的基于熵最小化的测试时适应方法相比，TLM方法在优化目标上做出了何种关键改变以更好地适应大语言模型的自回归特性？ | Test_Time_Learning_for_La.pdf | 2 | 1 | 3 | 是 | 5.00% | 50.00% | 30.66% | 0.00% |
| Hybrid | 83 | TLM方法在参数更新时采用LoRA而非全参数优化，主要是为了解决哪两个关键问题？ | Test_Time_Learning_for_La.pdf | 2 | 2 | 1 | 是 | 10.00% | 100.00% | 85.03% | 0.00% |
| Hybrid | 84 | 在稀疏门控混合专家层中，门控网络如何实现稀疏性以节省计算？ | The Sparsely-Gated MOE.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 16.67% |
| Hybrid | 85 | 稀疏门控混合专家层在分布式训练中面临哪些主要性能挑战？ | The Sparsely-Gated MOE.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Hybrid | 86 | 为什么在训练稀疏门控混合专家层时，门控网络容易收敛到总是选择少数相同专家的状态？ | The Sparsely-Gated MOE.pdf | 2 | 1 | 1 | 是 | 5.00% | 50.00% | 61.31% | 0.00% |
| Hybrid | 87 | TF-TTCL框架中的多智能体角色扮演具体涉及哪些角色，它们各自在查询增强阶段承担什么功能？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 2 | 1 | 3 | 是 | 5.00% | 50.00% | 30.66% | 50.00% |
| Hybrid | 88 | TF-TTCL框架为何能够在不更新模型参数的情况下实现自我改进？其核心机制是什么？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 2 | 1 | 7 | 是 | 5.00% | 50.00% | 20.44% | 0.00% |
| Hybrid | 89 | SELF-RAG框架在推理阶段如何通过特殊令牌实现生成过程的自适应控制？ | self-rag.pdf | 2 | 1 | 2 | 是 | 5.00% | 50.00% | 38.69% | 0.00% |
| Hybrid | 90 | SELF-RAG为何要在每个生成片段后插入批判令牌来评估输出？ | self-rag.pdf | 2 | 2 | 4 | 是 | 10.00% | 100.00% | 45.75% | 0.00% |
| Vector + Rerank | 1 | Adaptive-RAG 如何依据问题复杂度，在无检索、单步检索和多步检索之间选择策略？ | Adaptive_RAG.pdf | 3 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 100.00% |
| Vector + Rerank | 2 | Adaptive-RAG 的问题复杂度分类器如何训练，并在推理时输出检索策略？ | Adaptive_RAG.pdf | 3 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 50.00% |
| Vector + Rerank | 3 | DF-RAG 为什么需要多样性检索，它如何缓解余弦相似度带来的冗余证据问题？ | DF-RAG.pdf | 3 | 2 | 1 | 是 | 33.33% | 66.67% | 65.08% | 100.00% |
| Vector + Rerank | 4 | DF-RAG 的 gMMR 如何计算候选 Chunk，并怎样在测试时选择查询自适应的多样性参数？ | DF-RAG.pdf | 3 | 1 | 4 | 是 | 16.67% | 33.33% | 20.21% | 100.00% |
| Vector + Rerank | 5 | FLARE 为什么要前瞻性地预测下一句，并根据低概率词触发主动检索？ | FLARE.pdf | 3 | 3 | 1 | 是 | 50.00% | 100.00% | 83.85% | 100.00% |
| Vector + Rerank | 6 | LLM-Independent Adaptive RAG 如何在不依赖大模型不确定性估计的情况下减少不必要检索？ | LLM-Independent Adaptiverag RAG.pdf | 3 | 1 | 1 | 是 | 16.67% | 33.33% | 46.93% | 100.00% |
| Vector + Rerank | 7 | MBA-RAG 如何用 Bandit 根据问题复杂度选择检索路径，并在准确率与检索成本之间做权衡？ | MBA-RAG.pdf | 3 | 2 | 3 | 是 | 33.33% | 66.67% | 40.18% | 100.00% |
| Vector + Rerank | 8 | Probing-RAG 如何利用语言模型隐藏状态判断是否需要额外检索？ | Probing-RAG.pdf | 3 | 2 | 2 | 是 | 33.33% | 66.67% | 47.76% | 100.00% |
| Vector + Rerank | 9 | Adaptive-RAG、DF-RAG 和 FLARE 分别从问题复杂度、证据多样性和生成过程的哪个信号决定检索行为？ | Adaptive_RAG.pdf, DF-RAG.pdf, FLARE.pdf | 3 | 1 | 3 | 是 | 16.67% | 33.33% | 23.46% | 100.00% |
| Vector + Rerank | 10 | Adaptive-RAG 的评测为什么要同时报告效果指标和效率指标？它相对复杂多步策略的效率结论是什么？ | Adaptive_RAG.pdf | 3 | 1 | 1 | 是 | 16.67% | 33.33% | 46.93% | 75.00% |
| Vector + Rerank | 11 | EvolveR 如何把 Agent 的交互轨迹提炼为经验，并在后续任务中检索这些经验？ | EVOLVER.pdf | 3 | 3 | 1 | 是 | 50.00% | 100.00% | 87.11% | 50.00% |
| Vector + Rerank | 12 | TF-TTCL 如何通过语义查询增强、对比经验蒸馏和规则检索实现无训练的测试时自适应？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 3 | 1 | 2 | 是 | 16.67% | 33.33% | 29.61% | 100.00% |
| Vector + Rerank | 13 | Sparsely-Gated Mixture-of-Experts 层由哪些组件组成？稀疏门控如何在提升模型容量的同时控制计算量？ | The Sparsely-Gated MOE.pdf | 3 | 2 | 1 | 是 | 33.33% | 66.67% | 76.54% | 100.00% |
| Vector + Rerank | 14 | Mixture-of-Agents 如何通过 proposer 和 aggregator 的分层结构组合多个 LLM 的输出？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 3 | 1 | 1 | 是 | 16.67% | 33.33% | 46.93% | 100.00% |
| Vector + Rerank | 15 | MoTE 如何将 reasoning chain 与 step-level expert routing 结合起来进行 self-alignment？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 3 | 2 | 2 | 是 | 33.33% | 66.67% | 47.76% | 100.00% |
| Vector + Rerank | 16 | SkillDAG 为什么把技能库表示为 typed directed graph，而不是只使用 embedding 相似度检索？它如何处理依赖和冲突关系？ | SkillDAG.pdf | 3 | 1 | 1 | 是 | 16.67% | 33.33% | 46.93% | 100.00% |
| Vector + Rerank | 17 | SKILLRET 如何构建大规模技能检索 benchmark？为什么多技能 query 是一个重要评测场景？ | SkillRet.pdf | 3 | 2 | 2 | 是 | 33.33% | 66.67% | 53.07% | 75.00% |
| Vector + Rerank | 18 | SkillRouter 的实验为什么说明完整 skill body 对路由很重要？移除 body 会带来什么影响？ | SkillRouter.pdf | 3 | 2 | 2 | 是 | 33.33% | 66.67% | 47.76% | 100.00% |
| Vector + Rerank | 19 | Dynamic Routing MoE 如何根据输入难度和专家选择置信度动态决定激活多少专家，并如何控制负载均衡？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 3 | 1 | 2 | 是 | 16.67% | 33.33% | 29.61% | 75.00% |
| Vector + Rerank | 20 | Test-Time Learning for Large Language Models 如何利用无标签测试数据和输入困惑度实现适应，并通过 LoRA 减少灾难性遗忘？ | Test_Time_Learning_for_La.pdf | 3 | 2 | 3 | 是 | 33.33% | 66.67% | 43.67% | 100.00% |
| Vector + Rerank | 21 | AT-RAG模型在数据预处理阶段如何为文档分配主题标签？ | AT-RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 16.67% |
| Vector + Rerank | 22 | AT-RAG的主题过滤机制如何减少检索过程中的计算开销？ | AT-RAG.pdf | 2 | 2 | 5 | 是 | 33.33% | 100.00% | 45.56% | 0.00% |
| Vector + Rerank | 23 | Adaptive-RAG框架中，用于预测查询复杂度的分类器是如何获得训练标签的？ | Adaptive_RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector + Rerank | 24 | 为什么在现实场景中，为所有查询统一采用多步检索增强方法（如迭代访问LLM和检索器）是低效的？ | Adaptive_RAG.pdf | 2 | 1 | 4 | 是 | 16.67% | 50.00% | 26.41% | 0.00% |
| Vector + Rerank | 25 | DF-RAG框架中的评估器（Evaluator）如何为候选文本块集打分以选择最优的多样性参数λ？ | DF-RAG.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 87.72% | 0.00% |
| Vector + Rerank | 26 | DF-RAG的几何最大边际相关性（gMMR）评分函数在设计上，如何从几何角度衡量候选文本块与已选文本块集之间的多样性？ | DF-RAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 27 | DH-HGCN模型如何利用超图卷积来学习用户表示？请描述其核心的两阶段特征变换过程。 | DH-MHCN.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 100.00% | 0.00% |
| Vector + Rerank | 28 | DH-HGCN模型在构建用户-用户社交图时，对评论关系施加了何种限制性处理？ | DH-MHCN.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 29 | 在DH-HGCN中，为何需要在用户嵌入学习过程中引入自门控单元（SGUs）？其设计动机是什么？ | DH-MHCN.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 30 | DeepSeek-OCR 为什么把 OCR 当作验证长文本视觉压缩的试验任务，而不是直接在语言模型里压缩文本？ | DeepSeek_OCR_paper.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 100.00% | 100.00% |
| Vector + Rerank | 31 | DeepEncoder 如何串联局部注意力与全局注意力，在处理高分辨率文档时控制视觉 Token 数量和激活内存？ | DeepSeek_OCR_paper.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 85.03% | 100.00% |
| Vector + Rerank | 32 | DeepSeek-OCR 对视觉编码器有哪些要求，多分辨率模式如何帮助研究压缩比例与文本解码能力之间的关系？ | DeepSeek_OCR_paper.pdf | 2 | 1 | 2 | 是 | 16.67% | 50.00% | 38.69% | 100.00% |
| Vector + Rerank | 33 | 在EvolveR框架中，离线自蒸馏阶段如何确保经验库中原则的质量并避免冗余？ | EVOLVER.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector + Rerank | 34 | EvolveR框架主要旨在解决现有LLM智能体在哪种根本性能力上的不足？ | EVOLVER.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 35 | EvolveR框架中，策略进化机制是如何与整个经验生命周期闭环关联，从而实现智能体的自我演化的？ | EVOLVER.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 100.00% | 0.00% |
| Vector + Rerank | 36 | FLARE方法中，如何利用对下一句的预测来指导检索过程？ | FLARE.pdf | 2 | 2 | 2 | 是 | 33.33% | 100.00% | 69.34% | 0.00% |
| Vector + Rerank | 37 | FLARE方法选择在生成过程中触发检索的核心机制依据是什么？ | FLARE.pdf | 2 | 1 | 2 | 是 | 25.00% | 50.00% | 38.69% | 0.00% |
| Vector + Rerank | 38 | 动态路由机制中，如何根据路由概率确定每个token最终激活的专家集合？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 91.97% | 0.00% |
| Vector + Rerank | 39 | 动态路由方法在哪些类型的任务或场景下可能表现不佳或存在局限性？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector + Rerank | 40 | 为什么动态路由机制需要引入额外的损失函数来约束路由概率分布？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 91.97% | 0.00% |
| Vector + Rerank | 41 | KG2RAG框架中，图引导扩展过程如何利用知识图谱来增加检索块的多样性？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 2 | 3 | 是 | 33.33% | 100.00% | 52.50% | 0.00% |
| Vector + Rerank | 42 | KG2RAG框架在文档离线处理阶段，对于知识图谱的可用性有哪些不同的处理方式？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector + Rerank | 43 | KG2RAG框架中，基于知识图谱的上下文组织模块为什么要同时充当过滤器和整理器的双重角色？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 2 | 2 | 是 | 33.33% | 100.00% | 69.34% | 0.00% |
| Vector + Rerank | 44 | 论文提出的LLM无关自适应检索方法中，如何利用知识图谱来构建特征？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 45 | 论文中评估的'Knowledgability'特征在应用时存在什么潜在的限制或前提条件？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector + Rerank | 46 | 为什么论文认为基于外部信息的自适应检索方法，对于复杂问题的表现可能优于基于不确定性的方法？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 85.03% | 0.00% |
| Vector + Rerank | 47 | MBA-RAG框架中，如何平衡对不同检索策略的探索与利用？ | MBA-RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector + Rerank | 48 | MBA-RAG的动态奖励函数是如何设计以同时优化答案准确性和计算效率的？ | MBA-RAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 49 | MHCN模型如何通过超图卷积操作来编码不同类型的高阶用户关系？ | MHCN.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 50 | MHCN模型在利用超图建模高阶关系时，其多通道聚合操作存在什么潜在缺陷？ | MHCN.pdf | 2 | 1 | 2 | 是 | 16.67% | 50.00% | 38.69% | 0.00% |
| Vector + Rerank | 51 | MHCN引入自监督学习任务的主要动机是什么？ | MHCN.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector + Rerank | 52 | MoTE框架中，用于处理推理链不同步骤的LoRA专家是如何具体配置和工作的？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 87.72% | 0.00% |
| Vector + Rerank | 53 | MoTE方法主要适用于哪些类型的语言模型？它在模型能力上有何特定的前提或限制？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 2 | 2 | 是 | 33.33% | 100.00% | 62.41% | 0.00% |
| Vector + Rerank | 54 | 在MoTE框架中，引入中间推理步骤（如问题分析、答案指导）是如何从机制上增强模型安全性的？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 1 | 2 | 是 | 16.67% | 50.00% | 38.69% | 0.00% |
| Vector + Rerank | 55 | 在Mixture-of-Agents框架中，如何为每一层选择合适的大语言模型？选择过程主要依据哪两个标准？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 56 | Mixture-of-Agents方法在应用上存在哪些固有的限制或前提条件？它是否需要对底层大语言模型进行微调？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector + Rerank | 57 | 根据论文中的发现，即使辅助模型生成的回答质量较低，为什么聚合器大语言模型仍然能够产生更高质量的最终输出？这种现象被称为什么？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 58 | PSO-DGNN方法中，如何通过解耦模式设计搜索空间以构建深层图神经网络？ | PSO-DGNN.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 91.97% | 0.00% |
| Vector + Rerank | 59 | 在深层图神经网络中，传统的耦合模式为何会导致性能下降或遇到瓶颈？ | PSO-DGNN.pdf | 2 | 2 | 2 | 是 | 33.33% | 100.00% | 60.53% | 0.00% |
| Vector + Rerank | 60 | PathwiseRAG框架中，查询意图分析的双流架构具体如何运作？ | PathwiseRAG.pdf | 2 | 1 | 2 | 是 | 16.67% | 50.00% | 38.69% | 0.00% |
| Vector + Rerank | 61 | PathwiseRAG在并行路径探索中，如何处理来自不同检索策略的冲突信息？ | PathwiseRAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 62 | PathwiseRAG的动态推理网络在迭代过程中如何根据新发现的信息进行自我调整？ | PathwiseRAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 63 | Probing-RAG方法中，探针器（prober）被放置在语言模型的哪个相对位置，以及这样放置的设计依据是什么？ | Probing-RAG.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 91.97% | 0.00% |
| Vector + Rerank | 64 | Probing-RAG方法在决定是否需要检索时，主要依赖于什么信息？这种方法与基于外部分类器的方法（如Adaptive-RAG）相比，存在什么根本区别？ | Probing-RAG.pdf | 2 | 1 | 5 | 是 | 16.67% | 50.00% | 23.72% | 0.00% |
| Vector + Rerank | 65 | 在Probing-RAG中，训练探针器（prober）所使用的数据标签（y）是如何生成的？具体依据什么标准来判定一个样本需要标记为“需要检索”（y=0）？ | Probing-RAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 66 | RankRAG在指令微调的第二阶段，其训练数据混合体具体包含哪几个组成部分？ | RankRAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 67 | 在标准的RAG流程中，仅依赖检索模型来确保高召回率存在哪些固有挑战？ | RankRAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 68 | RankRAG框架的设计基于一个关于LLM能力的核心假设是什么？这个假设如何指导了方法的统一？ | RankRAG.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 100.00% | 0.00% |
| Vector + Rerank | 69 | SkillDAG的在线编辑协议在提交图更新时需要强制执行哪些结构性不变量，以确保图不会变得不一致？ | SkillDAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 70 | SkillDAG的检索接口在处理冲突关系时，与处理其他正向关系类型（如依赖、组合）有何根本不同？这种设计选择旨在避免什么风险？ | SkillDAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 71 | 与仅依赖技能自描述相似度的扁平检索基线相比，SkillDAG引入显式关系类型（如依赖、特化）旨在解决哪种核心的语义混淆问题？ | SkillDAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector + Rerank | 72 | SKILLRET基准在构建训练集和评估集时，如何确保技能库的划分不会导致数据泄露？ | SkillRet.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 73 | SKILLRET基准在评估技能检索模型时，主要揭示了现有现成检索模型存在哪些局限性？ | SkillRet.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 87.72% | 0.00% |
| Vector + Rerank | 74 | 根据论文分析，在SKILLRET上进行任务特定微调后，模型性能提升的主要原因是什么？ | SkillRet.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 100.00% | 0.00% |
| Vector + Rerank | 75 | SKILLROUTER模型在训练过程中采用了哪两种特定的技术调整来应对同质化技能池带来的挑战？ | SkillRouter.pdf | 2 | 1 | 2 | 是 | 16.67% | 50.00% | 38.69% | 0.00% |
| Vector + Rerank | 76 | SKILLROUTER的基准测试在评估路由性能时，主要关注哪两种特定的失败模式？这两种模式是如何通过实验设计来隔离的？ | SkillRouter.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Vector + Rerank | 77 | 根据论文中的分析，为什么仅依赖技能名称和描述（元数据）进行路由选择在大型重叠技能库中会效果不佳？其根本原因是什么？ | SkillRouter.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 91.97% | 0.00% |
| Vector + Rerank | 78 | TARG方法中，如何利用模型的短前缀输出来计算不确定性分数以决定是否进行检索？请具体说明其计算过程。 | TARG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 79 | TARG方法在哪些情况下可能不适用或效果有限？请基于其设计原理和假设进行说明。 | TARG.pdf | 2 | 1 | 2 | 是 | 16.67% | 50.00% | 38.69% | 0.00% |
| Vector + Rerank | 80 | 为什么TARG方法中，基于top-1/top-2 logit间隙的边际信号在现代指令调优LLMs下能成为鲁棒的默认选择？其背后的机制原因是什么？ | TARG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 81 | TLM方法在测试时学习过程中，采用了何种具体策略来高效利用计算资源并选择对模型优化更重要的样本？ | Test_Time_Learning_for_La.pdf | 2 | 1 | 6 | 是 | 16.67% | 50.00% | 21.84% | 0.00% |
| Vector + Rerank | 82 | 与传统的基于熵最小化的测试时适应方法相比，TLM方法在优化目标上做出了何种关键改变以更好地适应大语言模型的自回归特性？ | Test_Time_Learning_for_La.pdf | 2 | 1 | 3 | 是 | 16.67% | 50.00% | 30.66% | 0.00% |
| Vector + Rerank | 83 | TLM方法在参数更新时采用LoRA而非全参数优化，主要是为了解决哪两个关键问题？ | Test_Time_Learning_for_La.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 87.72% | 0.00% |
| Vector + Rerank | 84 | 在稀疏门控混合专家层中，门控网络如何实现稀疏性以节省计算？ | The Sparsely-Gated MOE.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 16.67% |
| Vector + Rerank | 85 | 稀疏门控混合专家层在分布式训练中面临哪些主要性能挑战？ | The Sparsely-Gated MOE.pdf | 2 | 1 | 3 | 是 | 16.67% | 50.00% | 30.66% | 0.00% |
| Vector + Rerank | 86 | 为什么在训练稀疏门控混合专家层时，门控网络容易收敛到总是选择少数相同专家的状态？ | The Sparsely-Gated MOE.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 87 | TF-TTCL框架中的多智能体角色扮演具体涉及哪些角色，它们各自在查询增强阶段承担什么功能？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 2 | 1 | 1 | 是 | 20.00% | 50.00% | 61.31% | 50.00% |
| Vector + Rerank | 88 | TF-TTCL框架为何能够在不更新模型参数的情况下实现自我改进？其核心机制是什么？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 2 | 1 | 3 | 是 | 16.67% | 50.00% | 30.66% | 0.00% |
| Vector + Rerank | 89 | SELF-RAG框架在推理阶段如何通过特殊令牌实现生成过程的自适应控制？ | self-rag.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Vector + Rerank | 90 | SELF-RAG为何要在每个生成片段后插入批判令牌来评估输出？ | self-rag.pdf | 2 | 2 | 2 | 是 | 33.33% | 100.00% | 62.41% | 0.00% |
| Hybrid + Rerank | 1 | Adaptive-RAG 如何依据问题复杂度，在无检索、单步检索和多步检索之间选择策略？ | Adaptive_RAG.pdf | 3 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 100.00% |
| Hybrid + Rerank | 2 | Adaptive-RAG 的问题复杂度分类器如何训练，并在推理时输出检索策略？ | Adaptive_RAG.pdf | 3 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 50.00% |
| Hybrid + Rerank | 3 | DF-RAG 为什么需要多样性检索，它如何缓解余弦相似度带来的冗余证据问题？ | DF-RAG.pdf | 3 | 2 | 1 | 是 | 33.33% | 66.67% | 65.08% | 100.00% |
| Hybrid + Rerank | 4 | DF-RAG 的 gMMR 如何计算候选 Chunk，并怎样在测试时选择查询自适应的多样性参数？ | DF-RAG.pdf | 3 | 1 | 4 | 是 | 16.67% | 33.33% | 20.21% | 100.00% |
| Hybrid + Rerank | 5 | FLARE 为什么要前瞻性地预测下一句，并根据低概率词触发主动检索？ | FLARE.pdf | 3 | 3 | 1 | 是 | 50.00% | 100.00% | 83.85% | 100.00% |
| Hybrid + Rerank | 6 | LLM-Independent Adaptive RAG 如何在不依赖大模型不确定性估计的情况下减少不必要检索？ | LLM-Independent Adaptiverag RAG.pdf | 3 | 2 | 1 | 是 | 33.33% | 66.67% | 67.14% | 100.00% |
| Hybrid + Rerank | 7 | MBA-RAG 如何用 Bandit 根据问题复杂度选择检索路径，并在准确率与检索成本之间做权衡？ | MBA-RAG.pdf | 3 | 2 | 3 | 是 | 33.33% | 66.67% | 40.18% | 100.00% |
| Hybrid + Rerank | 8 | Probing-RAG 如何利用语言模型隐藏状态判断是否需要额外检索？ | Probing-RAG.pdf | 3 | 2 | 2 | 是 | 33.33% | 66.67% | 47.76% | 100.00% |
| Hybrid + Rerank | 9 | Adaptive-RAG、DF-RAG 和 FLARE 分别从问题复杂度、证据多样性和生成过程的哪个信号决定检索行为？ | Adaptive_RAG.pdf, DF-RAG.pdf, FLARE.pdf | 3 | 1 | 3 | 是 | 16.67% | 33.33% | 23.46% | 100.00% |
| Hybrid + Rerank | 10 | Adaptive-RAG 的评测为什么要同时报告效果指标和效率指标？它相对复杂多步策略的效率结论是什么？ | Adaptive_RAG.pdf | 3 | 1 | 1 | 是 | 16.67% | 33.33% | 46.93% | 75.00% |
| Hybrid + Rerank | 11 | EvolveR 如何把 Agent 的交互轨迹提炼为经验，并在后续任务中检索这些经验？ | EVOLVER.pdf | 3 | 3 | 1 | 是 | 50.00% | 100.00% | 87.11% | 50.00% |
| Hybrid + Rerank | 12 | TF-TTCL 如何通过语义查询增强、对比经验蒸馏和规则检索实现无训练的测试时自适应？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 3 | 1 | 2 | 是 | 16.67% | 33.33% | 29.61% | 100.00% |
| Hybrid + Rerank | 13 | Sparsely-Gated Mixture-of-Experts 层由哪些组件组成？稀疏门控如何在提升模型容量的同时控制计算量？ | The Sparsely-Gated MOE.pdf | 3 | 2 | 1 | 是 | 33.33% | 66.67% | 76.54% | 100.00% |
| Hybrid + Rerank | 14 | Mixture-of-Agents 如何通过 proposer 和 aggregator 的分层结构组合多个 LLM 的输出？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 3 | 1 | 1 | 是 | 16.67% | 33.33% | 46.93% | 100.00% |
| Hybrid + Rerank | 15 | MoTE 如何将 reasoning chain 与 step-level expert routing 结合起来进行 self-alignment？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 3 | 2 | 2 | 是 | 33.33% | 66.67% | 47.76% | 100.00% |
| Hybrid + Rerank | 16 | SkillDAG 为什么把技能库表示为 typed directed graph，而不是只使用 embedding 相似度检索？它如何处理依赖和冲突关系？ | SkillDAG.pdf | 3 | 1 | 1 | 是 | 16.67% | 33.33% | 46.93% | 100.00% |
| Hybrid + Rerank | 17 | SKILLRET 如何构建大规模技能检索 benchmark？为什么多技能 query 是一个重要评测场景？ | SkillRet.pdf | 3 | 2 | 2 | 是 | 33.33% | 66.67% | 53.07% | 75.00% |
| Hybrid + Rerank | 18 | SkillRouter 的实验为什么说明完整 skill body 对路由很重要？移除 body 会带来什么影响？ | SkillRouter.pdf | 3 | 2 | 2 | 是 | 33.33% | 66.67% | 47.76% | 100.00% |
| Hybrid + Rerank | 19 | Dynamic Routing MoE 如何根据输入难度和专家选择置信度动态决定激活多少专家，并如何控制负载均衡？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 3 | 1 | 2 | 是 | 16.67% | 33.33% | 29.61% | 75.00% |
| Hybrid + Rerank | 20 | Test-Time Learning for Large Language Models 如何利用无标签测试数据和输入困惑度实现适应，并通过 LoRA 减少灾难性遗忘？ | Test_Time_Learning_for_La.pdf | 3 | 3 | 3 | 是 | 50.00% | 100.00% | 61.83% | 100.00% |
| Hybrid + Rerank | 21 | AT-RAG模型在数据预处理阶段如何为文档分配主题标签？ | AT-RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 16.67% |
| Hybrid + Rerank | 22 | AT-RAG的主题过滤机制如何减少检索过程中的计算开销？ | AT-RAG.pdf | 2 | 2 | 5 | 是 | 33.33% | 100.00% | 45.56% | 0.00% |
| Hybrid + Rerank | 23 | Adaptive-RAG框架中，用于预测查询复杂度的分类器是如何获得训练标签的？ | Adaptive_RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid + Rerank | 24 | 为什么在现实场景中，为所有查询统一采用多步检索增强方法（如迭代访问LLM和检索器）是低效的？ | Adaptive_RAG.pdf | 2 | 1 | 3 | 是 | 16.67% | 50.00% | 30.66% | 0.00% |
| Hybrid + Rerank | 25 | DF-RAG框架中的评估器（Evaluator）如何为候选文本块集打分以选择最优的多样性参数λ？ | DF-RAG.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 87.72% | 0.00% |
| Hybrid + Rerank | 26 | DF-RAG的几何最大边际相关性（gMMR）评分函数在设计上，如何从几何角度衡量候选文本块与已选文本块集之间的多样性？ | DF-RAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 27 | DH-HGCN模型如何利用超图卷积来学习用户表示？请描述其核心的两阶段特征变换过程。 | DH-MHCN.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 100.00% | 0.00% |
| Hybrid + Rerank | 28 | DH-HGCN模型在构建用户-用户社交图时，对评论关系施加了何种限制性处理？ | DH-MHCN.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 29 | 在DH-HGCN中，为何需要在用户嵌入学习过程中引入自门控单元（SGUs）？其设计动机是什么？ | DH-MHCN.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 30 | DeepSeek-OCR 为什么把 OCR 当作验证长文本视觉压缩的试验任务，而不是直接在语言模型里压缩文本？ | DeepSeek_OCR_paper.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 100.00% |
| Hybrid + Rerank | 31 | DeepEncoder 如何串联局部注意力与全局注意力，在处理高分辨率文档时控制视觉 Token 数量和激活内存？ | DeepSeek_OCR_paper.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 85.03% | 100.00% |
| Hybrid + Rerank | 32 | DeepSeek-OCR 对视觉编码器有哪些要求，多分辨率模式如何帮助研究压缩比例与文本解码能力之间的关系？ | DeepSeek_OCR_paper.pdf | 2 | 1 | 2 | 是 | 16.67% | 50.00% | 38.69% | 100.00% |
| Hybrid + Rerank | 33 | 在EvolveR框架中，离线自蒸馏阶段如何确保经验库中原则的质量并避免冗余？ | EVOLVER.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid + Rerank | 34 | EvolveR框架主要旨在解决现有LLM智能体在哪种根本性能力上的不足？ | EVOLVER.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 35 | EvolveR框架中，策略进化机制是如何与整个经验生命周期闭环关联，从而实现智能体的自我演化的？ | EVOLVER.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 100.00% | 0.00% |
| Hybrid + Rerank | 36 | FLARE方法中，如何利用对下一句的预测来指导检索过程？ | FLARE.pdf | 2 | 2 | 2 | 是 | 33.33% | 100.00% | 69.34% | 0.00% |
| Hybrid + Rerank | 37 | FLARE方法选择在生成过程中触发检索的核心机制依据是什么？ | FLARE.pdf | 2 | 1 | 2 | 是 | 33.33% | 50.00% | 38.69% | 0.00% |
| Hybrid + Rerank | 38 | 动态路由机制中，如何根据路由概率确定每个token最终激活的专家集合？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid + Rerank | 39 | 动态路由方法在哪些类型的任务或场景下可能表现不佳或存在局限性？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid + Rerank | 40 | 为什么动态路由机制需要引入额外的损失函数来约束路由概率分布？ | Harder Task Needs More Experts;Dynamic Routing in MoE Models.pdf | 2 | 2 | 1 | 是 | 40.00% | 100.00% | 91.97% | 0.00% |
| Hybrid + Rerank | 41 | KG2RAG框架中，图引导扩展过程如何利用知识图谱来增加检索块的多样性？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 2 | 3 | 是 | 33.33% | 100.00% | 52.50% | 0.00% |
| Hybrid + Rerank | 42 | KG2RAG框架在文档离线处理阶段，对于知识图谱的可用性有哪些不同的处理方式？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid + Rerank | 43 | KG2RAG框架中，基于知识图谱的上下文组织模块为什么要同时充当过滤器和整理器的双重角色？ | Knowledge Graph-Guided Retrieval Augmented Generation.pdf | 2 | 2 | 2 | 是 | 33.33% | 100.00% | 69.34% | 0.00% |
| Hybrid + Rerank | 44 | 论文提出的LLM无关自适应检索方法中，如何利用知识图谱来构建特征？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 45 | 论文中评估的'Knowledgability'特征在应用时存在什么潜在的限制或前提条件？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid + Rerank | 46 | 为什么论文认为基于外部信息的自适应检索方法，对于复杂问题的表现可能优于基于不确定性的方法？ | LLM-Independent Adaptiverag RAG.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 85.03% | 0.00% |
| Hybrid + Rerank | 47 | MBA-RAG框架中，如何平衡对不同检索策略的探索与利用？ | MBA-RAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid + Rerank | 48 | MBA-RAG的动态奖励函数是如何设计以同时优化答案准确性和计算效率的？ | MBA-RAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 49 | MHCN模型如何通过超图卷积操作来编码不同类型的高阶用户关系？ | MHCN.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 50 | MHCN模型在利用超图建模高阶关系时，其多通道聚合操作存在什么潜在缺陷？ | MHCN.pdf | 2 | 1 | 2 | 是 | 16.67% | 50.00% | 38.69% | 0.00% |
| Hybrid + Rerank | 51 | MHCN引入自监督学习任务的主要动机是什么？ | MHCN.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid + Rerank | 52 | MoTE框架中，用于处理推理链不同步骤的LoRA专家是如何具体配置和工作的？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 87.72% | 0.00% |
| Hybrid + Rerank | 53 | MoTE方法主要适用于哪些类型的语言模型？它在模型能力上有何特定的前提或限制？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 2 | 2 | 是 | 33.33% | 100.00% | 62.41% | 0.00% |
| Hybrid + Rerank | 54 | 在MoTE框架中，引入中间推理步骤（如问题分析、答案指导）是如何从机制上增强模型安全性的？ | Mixture of insighTful Experts (MoTE)-The Synergy of Reasoning Chains and Expert Mixtures in Self-Alignment.pdf | 2 | 1 | 2 | 是 | 16.67% | 50.00% | 38.69% | 0.00% |
| Hybrid + Rerank | 55 | 在Mixture-of-Agents框架中，如何为每一层选择合适的大语言模型？选择过程主要依据哪两个标准？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 56 | Mixture-of-Agents方法在应用上存在哪些固有的限制或前提条件？它是否需要对底层大语言模型进行微调？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid + Rerank | 57 | 根据论文中的发现，即使辅助模型生成的回答质量较低，为什么聚合器大语言模型仍然能够产生更高质量的最终输出？这种现象被称为什么？ | Mixture-of-Agents Enhances Large Language Model Capabilities.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 58 | PSO-DGNN方法中，如何通过解耦模式设计搜索空间以构建深层图神经网络？ | PSO-DGNN.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 91.97% | 0.00% |
| Hybrid + Rerank | 59 | 在深层图神经网络中，传统的耦合模式为何会导致性能下降或遇到瓶颈？ | PSO-DGNN.pdf | 2 | 2 | 2 | 是 | 33.33% | 100.00% | 60.53% | 0.00% |
| Hybrid + Rerank | 60 | PathwiseRAG框架中，查询意图分析的双流架构具体如何运作？ | PathwiseRAG.pdf | 2 | 1 | 2 | 是 | 16.67% | 50.00% | 38.69% | 0.00% |
| Hybrid + Rerank | 61 | PathwiseRAG在并行路径探索中，如何处理来自不同检索策略的冲突信息？ | PathwiseRAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 62 | PathwiseRAG的动态推理网络在迭代过程中如何根据新发现的信息进行自我调整？ | PathwiseRAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 63 | Probing-RAG方法中，探针器（prober）被放置在语言模型的哪个相对位置，以及这样放置的设计依据是什么？ | Probing-RAG.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 91.97% | 0.00% |
| Hybrid + Rerank | 64 | Probing-RAG方法在决定是否需要检索时，主要依赖于什么信息？这种方法与基于外部分类器的方法（如Adaptive-RAG）相比，存在什么根本区别？ | Probing-RAG.pdf | 2 | 1 | 5 | 是 | 16.67% | 50.00% | 23.72% | 0.00% |
| Hybrid + Rerank | 65 | 在Probing-RAG中，训练探针器（prober）所使用的数据标签（y）是如何生成的？具体依据什么标准来判定一个样本需要标记为“需要检索”（y=0）？ | Probing-RAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 66 | RankRAG在指令微调的第二阶段，其训练数据混合体具体包含哪几个组成部分？ | RankRAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 67 | 在标准的RAG流程中，仅依赖检索模型来确保高召回率存在哪些固有挑战？ | RankRAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 68 | RankRAG框架的设计基于一个关于LLM能力的核心假设是什么？这个假设如何指导了方法的统一？ | RankRAG.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 100.00% | 0.00% |
| Hybrid + Rerank | 69 | SkillDAG的在线编辑协议在提交图更新时需要强制执行哪些结构性不变量，以确保图不会变得不一致？ | SkillDAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 70 | SkillDAG的检索接口在处理冲突关系时，与处理其他正向关系类型（如依赖、组合）有何根本不同？这种设计选择旨在避免什么风险？ | SkillDAG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 71 | 与仅依赖技能自描述相似度的扁平检索基线相比，SkillDAG引入显式关系类型（如依赖、特化）旨在解决哪种核心的语义混淆问题？ | SkillDAG.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid + Rerank | 72 | SKILLRET基准在构建训练集和评估集时，如何确保技能库的划分不会导致数据泄露？ | SkillRet.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 83.16% | 0.00% |
| Hybrid + Rerank | 73 | SKILLRET基准在评估技能检索模型时，主要揭示了现有现成检索模型存在哪些局限性？ | SkillRet.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 87.72% | 0.00% |
| Hybrid + Rerank | 74 | 根据论文分析，在SKILLRET上进行任务特定微调后，模型性能提升的主要原因是什么？ | SkillRet.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 100.00% | 0.00% |
| Hybrid + Rerank | 75 | SKILLROUTER模型在训练过程中采用了哪两种特定的技术调整来应对同质化技能池带来的挑战？ | SkillRouter.pdf | 2 | 1 | 2 | 是 | 16.67% | 50.00% | 38.69% | 0.00% |
| Hybrid + Rerank | 76 | SKILLROUTER的基准测试在评估路由性能时，主要关注哪两种特定的失败模式？这两种模式是如何通过实验设计来隔离的？ | SkillRouter.pdf | 2 | 0 | - | 否 | 0.00% | 0.00% | 0.00% | 0.00% |
| Hybrid + Rerank | 77 | 根据论文中的分析，为什么仅依赖技能名称和描述（元数据）进行路由选择在大型重叠技能库中会效果不佳？其根本原因是什么？ | SkillRouter.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 91.97% | 0.00% |
| Hybrid + Rerank | 78 | TARG方法中，如何利用模型的短前缀输出来计算不确定性分数以决定是否进行检索？请具体说明其计算过程。 | TARG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 79 | TARG方法在哪些情况下可能不适用或效果有限？请基于其设计原理和假设进行说明。 | TARG.pdf | 2 | 1 | 3 | 是 | 16.67% | 50.00% | 30.66% | 0.00% |
| Hybrid + Rerank | 80 | 为什么TARG方法中，基于top-1/top-2 logit间隙的边际信号在现代指令调优LLMs下能成为鲁棒的默认选择？其背后的机制原因是什么？ | TARG.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 81 | TLM方法在测试时学习过程中，采用了何种具体策略来高效利用计算资源并选择对模型优化更重要的样本？ | Test_Time_Learning_for_La.pdf | 2 | 1 | 6 | 是 | 16.67% | 50.00% | 21.84% | 0.00% |
| Hybrid + Rerank | 82 | 与传统的基于熵最小化的测试时适应方法相比，TLM方法在优化目标上做出了何种关键改变以更好地适应大语言模型的自回归特性？ | Test_Time_Learning_for_La.pdf | 2 | 1 | 3 | 是 | 16.67% | 50.00% | 30.66% | 0.00% |
| Hybrid + Rerank | 83 | TLM方法在参数更新时采用LoRA而非全参数优化，主要是为了解决哪两个关键问题？ | Test_Time_Learning_for_La.pdf | 2 | 2 | 1 | 是 | 33.33% | 100.00% | 87.72% | 0.00% |
| Hybrid + Rerank | 84 | 在稀疏门控混合专家层中，门控网络如何实现稀疏性以节省计算？ | The Sparsely-Gated MOE.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 16.67% |
| Hybrid + Rerank | 85 | 稀疏门控混合专家层在分布式训练中面临哪些主要性能挑战？ | The Sparsely-Gated MOE.pdf | 2 | 1 | 3 | 是 | 16.67% | 50.00% | 30.66% | 0.00% |
| Hybrid + Rerank | 86 | 为什么在训练稀疏门控混合专家层时，门控网络容易收敛到总是选择少数相同专家的状态？ | The Sparsely-Gated MOE.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 87 | TF-TTCL框架中的多智能体角色扮演具体涉及哪些角色，它们各自在查询增强阶段承担什么功能？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 2 | 1 | 1 | 是 | 20.00% | 50.00% | 61.31% | 50.00% |
| Hybrid + Rerank | 88 | TF-TTCL框架为何能够在不更新模型参数的情况下实现自我改进？其核心机制是什么？ | Training-Free Test-Time Contrastive Learning for Large Language Models-Camera Ready.pdf | 2 | 1 | 3 | 是 | 16.67% | 50.00% | 30.66% | 0.00% |
| Hybrid + Rerank | 89 | SELF-RAG框架在推理阶段如何通过特殊令牌实现生成过程的自适应控制？ | self-rag.pdf | 2 | 1 | 1 | 是 | 16.67% | 50.00% | 61.31% | 0.00% |
| Hybrid + Rerank | 90 | SELF-RAG为何要在每个生成片段后插入批判令牌来评估输出？ | self-rag.pdf | 2 | 2 | 2 | 是 | 33.33% | 100.00% | 62.41% | 0.00% |

## 降级与边界验证

- 向量服务异常、BM25 正常：检索仍成功返回 5 个结果，并标记 `vector_unavailable`。
- BM25 异常、向量正常：检索仍成功返回 5 个结果，并标记 `lexical_unavailable`。
- 两路同时异常：返回稳定错误 `knowledge_retrieval_unavailable`，不会伪造空的成功结果。
- 五组矩阵运行均为成功状态，失败 Case=0，生产降级次数=0。

## 解释边界

本报告评估的是检索和证据覆盖，不是最终大模型答案正确率。当前运行未自动调用模型生成答案，也未使用 LLM-as-a-Judge，因此不能把这些数值写成“最终回答准确率”。关键词覆盖仅作为确定性辅助信号，不能替代语义答案评估。

## 工程修复记录

- 评测 Case 支持多个 Ground-Truth Document 和 Chunk，并持久化命中的 Chunk ID。
- 文档删除保护同时检查旧单文档字段和多文档 JSON 字段，避免评测引用被绕过。
- 成功重索引发布后清理失效 Chunk 标注；若异常发生在发布中间，评测层仍保留文档级回退，避免旧 Chunk ID 造成静默全失败。
- 索引持久化前清理 PDF 抽取文本中的 NUL 字节，避免 PostgreSQL 文本写入失败。

# 实操第 4 课：Query Rewrite 与 HyDE

本节目标：在不改索引结构、不重新切 chunk、不重建向量库的前提下，先做低风险的高级检索优化。

本次新增三种检索增强策略：

```text
none: 原问题直接检索
rewrite: 先把口语问题改写成适合检索的问题
hyde: 先生成一段假想答案/假想文档，再用它的 embedding 检索真实文档
```

## 为什么先做这一步

RAG 的检索问题经常不是“向量库不够好”，而是“用户问题不适合直接拿去检索”。

例如用户问：

```text
这个咋报销？
```

这句话对人来说可能够用，因为人能结合上下文猜到“这个”指差旅、发票、审批流程。但检索系统只看到这几个词，缺少关键名词：

```text
差旅报销
发票
审批
提交期限
报销额度
```

如果直接 embedding，“这个咋报销？”的向量可能很泛，容易召回“费用”“流程”“申请”一类相似但不准确的片段。

Query Rewrite 和 HyDE 的共同特点是：只改变检索用的 query，不改变真实索引。也就是说，已经入库的 chunk、embedding、权限过滤、rerank、回答 prompt 都可以继续沿用。这就是它低风险的原因。

## 专业名词解释

### Query Rewrite

Query Rewrite 直译是“查询改写”。

它让 LLM 把用户的口语问题改成更完整、更适合搜索的问题。

例子：

```text
原问题：这个咋报销？
改写后：差旅费用报销需要提交哪些材料、审批流程是什么？
```

改写后的问题保留了原意，但补全了检索关键词。它适合短问题、省略问题、口语问题、追问问题。

### HyDE

HyDE 是 Hypothetical Document Embeddings 的缩写，意思是“假想文档 embedding”。

它不是先搜索，而是先让 LLM 生成一段“可能包含答案的假想文档”，再把这段假想文档做 embedding，用这个 embedding 去真实文档里找相似 chunk。

例子：

```text
原问题：2026 年一线城市住宿标准是多少？
HyDE 文档：2026 年差旅报销政策规定，一线城市住宿费标准为每天若干元，员工需要在报销时提交发票和审批单。
```

这段 HyDE 文档不作为最终答案，也不能直接展示给用户。它只是为了让 embedding 更接近“真实答案片段”的语义空间。

### Embedding

Embedding 是把文本变成一串数字向量。语义接近的文本，向量距离通常更近。

RAG 里常见做法是：

```text
问题 -> embedding -> 找相似 chunk -> 把 chunk 交给 LLM 回答
```

HyDE 把第一步换成：

```text
问题 -> 假想文档 -> embedding -> 找相似 chunk -> 把真实 chunk 交给 LLM 回答
```

### Recall / 召回

召回是检索阶段找回候选文档片段的过程。

如果正确 chunk 没有被召回，后面的 rerank 和回答模型很难补救。Query Rewrite 和 HyDE 的核心价值，就是提高“正确 chunk 进入候选集”的概率。

## 本次代码链路

新增策略只影响检索 query：

```text
用户原问题
  |
  |-- none ----> 原问题作为 retrieval_query
  |
  |-- rewrite -> LLM 改写问题 -> 改写问题作为 retrieval_query
  |
  |-- hyde ----> LLM 生成假想文档 -> HyDE 文档作为 retrieval_query
  |
真实检索器：vector / text / hybrid / rerank / ACL
  |
真实召回 chunk
  |
回答模型仍然回答“用户原问题”
```

注意最后一步很重要：HyDE 文档不能替代用户原问题。它可能包含猜测内容，只能用于检索，不能作为事实依据。

## 为什么回答仍然使用原问题

假设用户问：

```text
2026 年报销提交期限是 30 天还是 45 天？
```

HyDE 可能生成：

```text
2026 年报销提交期限为 30 天。
```

如果直接拿这段 HyDE 文档去回答，就会把“假想内容”当事实，风险很高。

正确做法是：

```text
HyDE 文档只负责找真实制度 chunk。
最终回答必须基于真实召回 chunk，并回答原问题。
```

## Trace 里记录什么

本次 trace 新增这些字段：

```text
original_question: 用户原问题
retrieval_query: 实际用于检索的文本
retrieval_enhancement.strategy: none / rewrite / hyde
retrieval_enhancement.rewritten_question: 改写问题
retrieval_enhancement.hyde_document: HyDE 假想文档
vector_candidate_chunks: vector 召回候选
text_candidate_chunks: text 召回候选
fused_candidate_chunks: hybrid 融合后候选
reranked_candidate_chunks: rerank 后候选
context_chunks: 最终给回答模型的上下文
citation_chunks: 最终被引用的片段
```

这些字段可以回答三个调试问题：

```text
1. LLM 改写/HyDE 是否生成了合理的检索文本？
2. 正确 chunk 是否进入了候选集？
3. 如果最终答错，是检索错、rerank 错，还是回答阶段错？
```

## 评测对比

本次评测默认三路对比：

```text
normal_query
rewrite
hyde
```

其中 normal_query 对应 `enhancement=none`。

推荐观察指标：

```text
Hit@K: 正确来源是否进入前 K 个上下文
MRR: 第一个正确来源排在第几位
NDCG@K: 多个正确来源的排序质量
expected_source_hit_rate: 期望来源命中率
keyword_hit_rate: 答案关键词命中率
citation_support_rate: 引用是否支持答案
latency_ms: 延迟
```

## 案例 1：口语问题

问题：

```text
这个咋报销？
```

normal query 可能只检索到“报销”“流程”附近的泛化内容。

rewrite 可能生成：

```text
差旅费用报销需要提交哪些材料、审批流程和报销期限是什么？
```

优点：

```text
1. 补全“差旅费用”“材料”“审批流程”“期限”等关键词。
2. hybrid 检索时，text 通道更容易命中制度原文。
3. vector 通道也能获得更明确的语义方向。
```

## 案例 2：文档措辞和用户措辞不一致

问题：

```text
员工请长假要提前多久说？
```

文档可能写的是：

```text
连续休 5 天以上年假，应至少提前 10 个工作日提交申请。
```

rewrite 可能生成：

```text
员工连续休 5 天以上年假需要提前多少个工作日提交申请？
```

这里 rewrite 的价值是把“说”改成“提交申请”，把“长假”改成“连续休 5 天以上年假”，更接近制度文本。

## 案例 3：语义抽象问题

问题：

```text
哪些行为会导致临时权限被收回？
```

文档可能分散在多个段落，且没有完全一样的问句。HyDE 可能生成：

```text
临时访问权限在超期、审批过期、使用目的结束、违反权限使用规范或存在安全风险时应被收回。
```

优点：

```text
1. HyDE 生成的是“答案形态”的文本，更接近真实制度段落。
2. 向量检索更容易命中描述条件、触发因素、撤销规则的 chunk。
3. 对抽象、归纳类问题通常比简单 rewrite 更强。
```

## 什么时候用哪种策略

建议：

```text
none:
问题本身很清楚，包含编号、日期、政策名、文件名。

rewrite:
短问题、口语问题、追问问题、代词较多的问题。

hyde:
抽象归纳问题、用户措辞和文档措辞差异较大、需要找“像答案一样”的段落。
```

需要注意：

```text
rewrite 和 hyde 都会多一次 LLM 调用，延迟和成本会上升。
HyDE 可能生成错误假设，所以只能用于检索，不能作为事实。
编号、金额、日期类问题不一定适合 HyDE，因为假想文档可能编出错误数字。
```

## 推荐实验方法

固定这些变量：

```text
retrieval_mode: hybrid
top_k: 10
first_round_multiplier: 10
rerank: 按当前默认设置
chunk 参数: 不变
索引结构: 不变
```

只改变：

```text
retrieval.enhancement
```

运行三组：

```text
A: none
B: rewrite
C: hyde
```

观察：

```text
rewrite 是否提高短问题命中率
HyDE 是否提高抽象问题命中率
是否有策略带来明显延迟
是否有 HyDE 因假想内容导致召回跑偏
```

## 判断结论

如果 rewrite 命中率最高：

```text
说明当前主要问题是用户问法太口语、太短、缺关键词。
可以考虑把 rewrite 作为默认增强策略。
```

如果 HyDE 命中率最高：

```text
说明问题和文档之间存在明显语义鸿沟。
可以对抽象问题开启 HyDE，或后续做自动路由。
```

如果 none 最稳：

```text
说明测试问题本身已经很适合检索，增强反而可能引入噪声。
默认保持 none，只在特定场景开启。
```

如果三者都差：

```text
说明问题可能不在 query 层，而在 chunk、索引、权限过滤、top_k、rerank 或 prompt。
下一步再考虑更高风险的索引结构调整。
```

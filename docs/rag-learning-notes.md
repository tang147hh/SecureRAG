# RAG Learning Notes

这份文档整理前 10 节 RAG 入门课，基于当前 SecureRAG/Kotaemon 项目理解 RAG 的完整链路。

核心主线：

```text
文档解析
-> Chunking
-> Embedding
-> 向量数据库
-> 检索策略
-> Rerank
-> Prompt
-> 评估
-> 生产优化
```

## 第 1 课：RAG 是什么

RAG 是 Retrieval-Augmented Generation，即检索增强生成。

它的核心思想是：

```text
先从真实资料中检索相关片段
再把这些片段放入提示词
最后让大模型基于资料回答
```

RAG 比直接问大模型更适合公司内部文档，因为公司制度、合同、知识库、项目文档通常不在大模型训练数据里。RAG 会调用真实文档，将适合的片段加入 prompt，让大模型基于资料回答，从而减少幻觉。

最小 RAG 流程：

```text
用户问题
-> 检索相关文档片段
-> 把片段放入 prompt
-> LLM 基于片段生成答案
```

在本项目里的主要对应关系：

```text
DocumentIngestor
-> TokenSplitter
-> VectorIndexing
-> VectorRetrieval
-> AnswerWithContextPipeline
```

## 第 2 课：文档解析

文档解析的目标，是把各种格式的资料统一变成 `Document` 对象。

RAG 不直接处理 PDF、Word、Excel、网页等原始格式。后续的 chunking、embedding、检索，本质上处理的是文本和结构化 metadata。

统一流程：

```text
PDF / Word / HTML / Markdown / Excel / 网页
-> Loader 解析
-> Document(text, metadata)
```

项目入口：

- `libs/kotaemon/kotaemon/indices/ingests/files.py`
- `libs/kotaemon/kotaemon/loaders/`
- `libs/kotaemon/kotaemon/base/schema.py`

`Document` 里通常包含两类重要信息：

```text
text: 真正参与切块、embedding、检索的正文
metadata: 文件名、页码、章节、URL、权限、来源等辅助信息
```

PDF 比较复杂，因为它可能是：

```text
原生文本 PDF
扫描版 PDF
表格很多的 PDF
论文、合同、报告等复杂排版 PDF
```

扫描版 PDF 需要 OCR，因为页面本质上是图片，没有可复制的文本层。

文档解析质量决定 RAG 的地基质量。如果解析出来的文本混乱，后面的 embedding、rerank、prompt 都很难补救。

## 第 3 课：Chunking 切块

Chunking 是把长文档切成适合检索的小片段。

RAG 通常不把整篇文档直接放进向量库，因为切块可以让：

```text
prompt 更短
成本更低
回答更精准
引用更清楚
```

项目默认配置在 `DocumentIngestor` 中：

```python
TokenSplitter.withx(
    chunk_size=1024,
    chunk_overlap=256,
    separator="\n\n",
    backup_separators=["\n", ".", " ", "\u200B"],
)
```

参数含义：

```text
chunk_size: 每块大约多少 token
chunk_overlap: 相邻 chunk 重叠多少 token
separator: 优先切分边界
backup_separators: 备用切分边界
```

`chunk_overlap` 用来减少关键信息被切断的问题。

chunk 太大：

```text
一个 chunk 里混了太多主题
检索结果不精准
prompt 成本高
模型容易被无关内容干扰
```

chunk 太小：

```text
语义不完整
答案缺上下文
关键信息可能被切开
```

常见切块方法：

```text
固定长度切块
按标题切块
递归切块
父子块
```

父子块的思想是：

```text
检索时用小块
回答时给大块
```

当检索到子块时，可以带上对应父块，让上下文更完整。

### 中文切块注意点

当前默认配置没有中文标点：

```python
backup_separators=["\n", ".", " ", "\u200B"]
```

对于中文文档不是最优。中文常见边界包括：

```text
。 ！ ？ ； ： ，
```

如果不加入中文标点，中文长段落里又没有空格，切块器可能更容易按 token 数硬切，导致语义边界不自然。

建议中文增强配置：

```python
TokenSplitter.withx(
    chunk_size=1024,
    chunk_overlap=256,
    separator="\n\n",
    backup_separators=[
        "\n",
        "。",
        "！",
        "？",
        "；",
        ".",
        "!",
        "?",
        ";",
        " ",
        "\u200B",
    ],
)
```

对于合同、法规等长句很多的文档，可以再考虑加入 `：` 和 `，`，但要观察 chunk 是否被切得过碎。

## 第 4 课：Embedding

Embedding 是把文本变成一串数字，让机器可以计算语义相似度。

例如：

```text
正式员工每年有 10 天年假
员工一年能休几天带薪假？
```

这两句话字面不同，但语义相近。Embedding 模型会把它们映射到相近的向量位置。

RAG 中 embedding 用在两个地方：

```text
文档入库：
chunk text -> embedding model -> chunk vector -> vector database

用户提问：
question -> embedding model -> question vector -> 相似度搜索
```

项目入口：

- `libs/kotaemon/kotaemon/embeddings/base.py`
- `libs/kotaemon/kotaemon/embeddings/openai.py`
- `libs/kotaemon/kotaemon/embeddings/fastembed.py`
- `libs/kotaemon/kotaemon/indices/vectorindex.py`

入库时：

```python
embeddings = self.embedding(docs)
self.vector_store.add(
    embeddings=embeddings,
    ids=[t.doc_id for t in docs],
)
```

检索时：

```python
emb = self.embedding(text)[0].embedding
_, scores, ids = self.vector_store.query(
    embedding=emb,
    top_k=top_k_first_round,
    doc_ids=scope,
    **kwargs
)
```

Embedding 适合语义搜索，但对编号、姓名、代码、日期等精确匹配信息，不一定比关键词搜索可靠。

重要原则：

```text
文档入库和用户查询最好使用同一个 embedding 模型。
```

不同 embedding 模型的向量空间可能不兼容。如果换模型，通常需要重新给所有文档做 embedding 并重建索引。

## 第 5 课：向量数据库

向量数据库负责存储 embedding，并根据相似度快速找回相关 chunk。

普通 SQL 数据库擅长精确匹配：

```sql
SELECT * FROM docs WHERE file_name = '员工手册.pdf';
```

向量数据库擅长语义相似度搜索：

```text
找出和“正式员工年假多少天”语义最接近的 5 个 chunk
```

常见向量数据库：

```text
FAISS
Chroma
Milvus
Qdrant
pgvector
```

入门建议先学 Chroma，因为它简单、本地可用，适合教学。

项目支持的向量库位于：

```text
libs/kotaemon/kotaemon/storages/vectorstores/
```

其中 Chroma 入口：

```text
libs/kotaemon/kotaemon/storages/vectorstores/chroma.py
```

本项目中：

```text
vectorstore: 存向量
docstore: 存原始 Document / chunk text
```

检索时：

```text
vectorstore 根据问题向量查到 ids / scores
docstore 根据 ids 取回正文
```

可以理解为：

```text
vectorstore 管“找谁像”
docstore 管“内容是什么”
```

`top_k=5` 表示返回相似度最高的前 5 个 chunk。

## 第 6 课：检索策略

检索策略决定给 LLM 看哪些资料。

核心问题包括：

```text
查多少个？
分数太低的要不要？
只查某些文件吗？
要不要同时用关键词搜索？
要不要先多召回再重排？
```

项目入口：

```text
libs/kotaemon/kotaemon/indices/vectorindex.py
```

`VectorRetrieval` 的关键参数：

```python
top_k: int = 5
first_round_top_k_mult: int = 10
retrieval_mode: str = "hybrid"  # vector, text, hybrid
```

### top-k

`top_k` 太小：

```text
可能漏掉答案
```

`top_k` 太大：

```text
无关内容变多
prompt 变长
成本变高
```

### 相似度阈值

相似度阈值用于过滤低相关 chunk，避免无关资料进入 prompt。

阈值太高：

```text
可能把有用 chunk 筛出去
```

阈值太低：

```text
可能引入噪声
```

### Metadata Filter

metadata filter 可用于：

```text
权限管理
只查指定文件
只查指定部门
只查某个时间范围
只查用户有权访问的资料
```

项目中有类似 `scope` 的机制，用于限定检索范围：

```python
scope = kwargs.pop("scope", None)
```

### Vector Search

适合语义匹配。例如：

```text
年假多少天
员工一年能休几天带薪假
```

### Text Search

适合精确匹配。例如：

```text
合同编号
姓名
代码
日期
发票号
```

### Hybrid Search

Hybrid search 是：

```text
vector search + text search
```

它比单独 vector search 更稳，因为同时覆盖：

```text
语义相似
字面精确
```

本项目默认：

```python
retrieval_mode: str = "hybrid"
```

## 第 7 课：Rerank 重排

Rerank 是对第一轮检索结果重新排序，把最相关的 chunk 放到前面。

可以理解为：

```text
Retrieval = 粗找
Rerank = 精排
```

典型流程：

```text
全库
-> retrieval 召回 30-100 个候选
-> rerank 精排
-> top 3-10 给 LLM
```

为什么不直接对全量文档 rerank：

```text
reranker 通常需要逐个比较 query 和 chunk
全量 rerank 成本高、延迟大
```

项目入口：

```text
libs/kotaemon/kotaemon/indices/vectorindex.py
libs/kotaemon/kotaemon/indices/rankings/
libs/kotaemon/kotaemon/rerankings/
```

`VectorRetrieval` 中：

```python
rerankers: Sequence[BaseReranking] = []
```

如果配置了 reranker，会在检索结果出来后重新排序。

Rerank 特别适合：

```text
文档很多
chunk 很相似
用户问题复杂
第一轮检索经常把正确答案排在后面
hybrid search 合并后结果较乱
需要提升引用准确性
```

项目中：

```python
first_round_top_k_mult: int = 10
```

当：

```text
top_k=5
first_round_top_k_mult=10
do_extend=True
```

第一轮召回：

```text
50 个 chunk
```

最终返回：

```text
5 个 chunk
```

## 第 8 课：Prompt 设计

RAG prompt 不是普通聊天 prompt，而是带资料约束的回答指令。

核心要求：

```text
只基于资料
没有资料就拒答
答案带引用
```

项目入口：

```text
libs/kotaemon/kotaemon/indices/qa/citation_qa.py
libs/kotaemon/kotaemon/indices/qa/citation.py
```

项目默认 prompt 包含：

```text
Use the following pieces of context to answer the question...
If you don't know the answer, just say that you don't know...
```

也就是：

```text
使用上下文回答问题
不知道就说不知道
不要编造
```

适合中文企业知识库的 prompt 示例：

```text
你是一个严谨的资料问答助手。

请只根据【资料】回答【问题】。
不要使用资料之外的信息。
如果资料不足以回答，请回答：“资料中没有提供相关信息。”
回答时请给出引用编号，例如 [1]、[2]。
如果多个资料支持同一结论，请引用最相关的资料。

【资料】
{context}

【问题】
{question}

【回答】
```

引用来源的作用：

```text
让用户知道答案来自哪里
方便人工核查
降低幻觉风险
提升可信度
```

拒答规则用于解决大模型幻觉问题。当资料不足时，模型应该明确说明无法回答，而不是猜测。

Prompt 是 RAG 的最后一道约束：

```text
检索负责找资料
prompt 负责规定怎么用资料
LLM 负责组织语言
```

## 第 9 课：评估

RAG 评估不是只看答案像不像，而是同时评估：

```text
检索有没有找对
回答有没有基于资料
```

因此评估要拆成两层：

```text
Retrieval Evaluation
Answer Evaluation
```

### Accuracy

最终答案是否正确。

```text
正确答案数 / 总问题数
```

### Recall@k

评估检索阶段。

`Recall@5` 表示：

```text
top-5 检索结果里是否包含正确证据
```

如果正确证据在 top-5 里，Recall@5 命中。

### Hallucination Rate

幻觉率评估模型有没有说资料里没有的内容。

例如资料只写：

```text
正式员工每年享有 10 天年假。
```

模型回答：

```text
正式员工每年享有 10 天年假，并且可以跨年度累计。
```

如果资料里没有“跨年度累计”，这就是幻觉。

### Citation Coverage

检查答案是否带引用。

```text
带引用答案数 / 总答案数
```

### Citation Correctness

检查引用是否真的支持答案。

```text
引用正确答案数 / 带引用答案数
```

### Refusal Accuracy

评估资料没有答案时，系统是否正确拒答；资料有答案时，系统是否没有误拒答。

RAG 错误归因：

```text
检索没找对
-> 查解析、切块、embedding、top-k、filter、rerank

检索找对但回答错
-> 查 prompt、上下文组织、LLM、引用逻辑
```

评估数据集可以是一张表：

```text
question
expected_answer
expected_source
should_refuse
```

核心心智模型：

```text
1. 资料找对了吗？
2. 找对资料后，答案说对了吗？
```

## 第 10 课：生产优化

RAG 从 demo 变成生产系统，关键不只是能回答，而是：

```text
稳定
便宜
快速
安全
可追踪
```

### 成本

主要成本来源：

```text
Embedding 成本
LLM 生成成本
Rerank 成本
向量数据库存储和查询成本
OCR / multimodal 文档解析成本
```

降低成本的方法：

```text
文档只在新增或更新时 embedding
控制 top_k
对常见问题做缓存
简单任务用便宜模型
rerank 只处理候选结果
```

文档 embedding 不应该在每次用户提问时重新做，因为这样会增加成本并显著拉高响应时间。

### 延迟

一次问答可能经过：

```text
问题 embedding
向量检索
全文检索
rerank
拼 prompt
LLM 生成
引用处理
```

常见慢点：

```text
LLM 生成
Rerank
OCR / 文档解析
远程 embedding API
```

排查顺序可以是：

```text
1. LLM 生成是否太慢
2. rerank 是否太慢或候选太多
3. 检索是否慢，尤其 hybrid search / metadata filter
4. embedding API 是否慢
5. prompt 是否太长
6. 引用和后处理是否阻塞
```

### 缓存

RAG 系统可以缓存：

```text
文档解析结果
Embedding 结果
检索结果
最终答案
```

答案缓存要注意：

```text
文档更新后缓存要失效
不同权限用户不能共用不该看的答案缓存
时间敏感问题不适合长期缓存
```

### 并发

生产系统需要考虑：

```text
embedding API 并发限制
LLM API rate limit
向量库查询并发
文档上传和索引任务排队
长文档解析不能阻塞聊天
```

常见做法：

```text
文档索引走后台任务
聊天请求设置超时
失败重试
批量 embedding
限制单用户并发
```

### 权限控制

权限控制最好放在检索阶段，而不是回答阶段。

正确思路：

```text
用户身份
-> 得到可访问 doc_ids / metadata 条件
-> 检索时只在授权范围内查
-> LLM 只看到用户有权限看的 chunk
```

不要先全库检索，再让 LLM 判断能不能说。因为资料一旦进入 prompt，就已经存在泄露风险。

### 生产指标

上线后应持续观察：

```text
平均响应时间
P95 / P99 延迟
每次问答 token 成本
检索 Recall@k
拒答率
用户反馈满意度
幻觉率
引用正确率
无权限访问拦截次数
```

生产版 RAG 关心：

```text
答得准不准
快不快
贵不贵
稳不稳
有没有泄露权限
能不能追踪和评估
```

## 总结

前 10 节课建立了 RAG 的完整心智模型：

```text
文档解析解决“资料如何进入系统”
Chunking 解决“长文档如何变成可检索片段”
Embedding 解决“文本如何变成可计算相似度的向量”
向量数据库解决“向量如何存储和快速查询”
检索策略解决“给 LLM 看哪些资料”
Rerank 解决“候选资料如何精排”
Prompt 解决“模型如何基于资料回答”
评估解决“如何知道系统好不好”
生产优化解决“如何稳定、低成本、安全上线”
```

下一阶段建议进入实践：

```text
准备一个中文 Markdown / TXT 测试文档
用 DocumentIngestor 切块
用 embedding 入 Chroma
提一个问题
查看 retrieval 返回的 chunk
再接 LLM 回答
```

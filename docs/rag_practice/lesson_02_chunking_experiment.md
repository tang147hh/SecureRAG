# 实操第 2 课：Chunk Size 和 Chunk Overlap 实验

本节目标：

```text
理解 chunk_size 和 chunk_overlap 如何影响检索结果、引用质量和回答质量。
```

第 1 课已经证明默认配置能回答基础问题。第 2 课开始只改一个变量组：切块参数。

## 参数含义

`chunk_size` 控制每个 chunk 的最大长度。

`chunk_overlap` 控制相邻 chunk 之间保留多少重叠内容。

可以先这样理解：

```text
chunk_size 越小：检索更聚焦，但上下文可能不足。
chunk_size 越大：上下文更完整，但容易混入多个主题。
chunk_overlap 越大：越不容易切断关键信息，但会增加重复内容和索引成本。
```

## 实验前准备

使用第 1 课的 3 个文档：

```text
employee_handbook.md
expense_policy.md
access_control_policy.md
```

每一组参数都需要重新索引文档。

为了避免旧索引干扰，建议每组实验使用一个新的文件集合、目录或索引空间。如果前端不方便新建索引空间，就删除旧测试文档后重新上传。

## 实验参数组

本节测试 4 组：

```text
A: chunk_size=300, chunk_overlap=50
B: chunk_size=600, chunk_overlap=100
C: chunk_size=1024, chunk_overlap=256
D: chunk_size=1500, chunk_overlap=300
```

其他参数保持不变：

```text
embedding: 当前默认模型
retrieval_mode: hybrid
top_k: 10
rerank: 关闭
prompt: 默认 RAG
```

## 测试问题

优先测试这 6 个问题：

```text
Q001 正式员工每年有多少天带薪年假？
Q002 员工申请年假需要提前多久？
Q005 报销单需要在费用发生后多久内提交？
Q006 BX-1024 是否通过审批？
Q008 私有文档可以被所有用户查看吗？
Q011 公司明年是否计划上市？
```

## 每组观察内容

### 1. Chunk 可读性

在文件详情或 chunk 面板里观察：

```text
chunk 是否语义完整？
标题和正文是否在同一个 chunk？
一句话是否被切断？
一个 chunk 是否混入太多主题？
```

### 2. 检索质量

每个问题观察：

```text
top-k 里是否有正确证据？
正确证据排第几？
是否出现很多无关 chunk？
```

### 3. 回答质量

每个问题观察：

```text
答案是否正确？
引用是否指向正确文件？
无答案问题是否拒答？
```

## 记录表

可以复制下面表格记录结果：

```text
实验组:
chunk_size:
chunk_overlap:

问题:
最终答案是否正确:
引用是否正确:
top-k 是否命中正确证据:
正确证据排名:
chunk 可读性:
主要问题:
```

## 判断规则

如果 chunk 太小，常见现象：

```text
答案需要上下文时容易缺信息
标题和正文分离
引用片段过短
相邻 chunk 重复较多
```

如果 chunk 太大，常见现象：

```text
一个 chunk 混入多个制度
检索结果看起来相关但不够聚焦
LLM 被无关内容干扰
引用不够精确
```

如果 overlap 太小，常见现象：

```text
关键句被切断
前后条件分散在两个 chunk
```

如果 overlap 太大，常见现象：

```text
重复 chunk 变多
索引成本增加
检索结果多样性下降
```

## 本节结论模板

完成后写下：

```text
在当前中文短文档测试集上，我观察到：

最稳定参数组：
原因：

最差参数组：
原因：

下一步是否需要中文标点增强切块：
```


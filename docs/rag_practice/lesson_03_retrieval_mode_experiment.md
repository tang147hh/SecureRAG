# 实操第 3 课：Retrieval Mode 实验

第 2 课结论：

```text
chunk_size=1024
chunk_overlap=256
```

在当前测试中回答更准确。

因此第 3 课固定这组 chunk 参数，只测试召回方式：

```text
vector
text
hybrid
```

## 本节目标

理解不同 retrieval mode 适合什么问题：

```text
vector: 语义相似检索
text: 关键词 / 编号 / 精确匹配
hybrid: 语义检索 + 文本检索
```

## 固定参数

本节不要再改 chunk 参数。

```text
chunk_size: 1024
chunk_overlap: 256
top_k: 10
rerank: 关闭
prompt: 默认 RAG
```

只改：

```text
retrieval_mode
```

## 实验组

```text
A: retrieval_mode=vector
B: retrieval_mode=text
C: retrieval_mode=hybrid
```

## 测试问题类型

### 1. 语义型问题

这些问题字面和文档不完全一样，但语义相同。

```text
员工一年能休几天带薪假？
如果员工连续休 5 天以上年假，要提前多久申请？
出差去一线城市住酒店每天最多能报多少？
```

预期：

```text
vector 和 hybrid 应该表现较好。
text 可能因为关键词不完全一致而漏掉。
```

### 2. 精确编号型问题

这些问题依赖编号、代码、日期。

```text
BX-1024 是否通过审批？
BX-1048 当前是什么状态？
BX-1066 为什么被退回？
HT-2026-009 的第二笔付款节点是什么？
HT-2026-021 什么时候终止？
```

预期：

```text
text 和 hybrid 应该表现较好。
vector 可能会召回相似但编号不对的片段。
```

### 3. 权限型问题

这些问题容易被相似制度干扰。

```text
财务系统临时访问权限最长多久？
生产环境临时访问权限最长多久？
付款审批模块谁可以访问？
哪些文档不得设为公共文档？
```

预期：

```text
hybrid 通常更稳。
vector 可能混淆相似权限。
text 可能依赖关键词是否命中。
```

### 4. 旧制度 vs 新制度问题

这些问题测试系统能否区分适用时间。

```text
2026 年发生的报销事项，提交期限是 30 天还是 45 天？
2026 年一线城市住宿标准是多少？
2026 年生产环境临时权限最长是 7 天还是 14 天？
```

预期：

```text
hybrid 更可能同时找回旧制度和新制度，再由 prompt 判断适用范围。
```

### 5. 无答案拒答问题

```text
公司明年是否计划上市？
CEO 的个人手机号是多少？
员工年终奖发放比例是多少？
```

预期：

```text
三个模式都应该拒答。
如果某个模式总是召回无关内容并强答，需要优化 prompt 或阈值。
```

## 观察指标

每个问题记录：

```text
答案是否正确
引用是否正确
是否找到了正确 chunk
是否混入无关章节
是否出现编号混淆
是否正确拒答
```

## 推荐记录格式

```text
retrieval_mode=vector:
正确数：
编号题错误：
权限题错误：
拒答是否正常：
明显问题：

retrieval_mode=text:
正确数：
语义题错误：
编号题表现：
拒答是否正常：
明显问题：

retrieval_mode=hybrid:
正确数：
是否整体最稳：
明显问题：
```

## 判断规则

如果 vector 错编号题：

```text
说明编号、日期、代码更适合 text 或 hybrid。
```

如果 text 错语义题：

```text
说明用户问法和文档措辞不一致，需要 vector。
```

如果 hybrid 最稳：

```text
后续默认使用 hybrid。
下一课测试 top_k 和召回倍数。
```

如果 hybrid 也混入大量无关 chunk：

```text
下一步需要测试 rerank 或相似度阈值。
```


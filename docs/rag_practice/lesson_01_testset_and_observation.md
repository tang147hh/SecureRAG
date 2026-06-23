# 实操第 1 课：测试集和观察面板

本节目标不是调参，而是先建立一个稳定测试环境：

```text
固定文档
固定问题
固定标准答案
固定观察指标
```

只有这样，后面调整 chunk、embedding、top_k、rerank、prompt 时，才能判断变化到底有没有变好。

## 练习材料

本目录提供了 3 份中文测试文档：

```text
employee_handbook.md
expense_policy.md
access_control_policy.md
```

以及 1 份问题集：

```text
rag_test_questions.csv
```

问题集包含：

```text
事实型问题
精确编号问题
权限问题
资料中无答案的问题
```

## 上传文档

在前端文件页面上传这 3 个 Markdown 文件：

```text
docs/rag_practice/employee_handbook.md
docs/rag_practice/expense_policy.md
docs/rag_practice/access_control_policy.md
```

本节先使用默认索引参数，不要急着调整。

建议默认值：

```text
chunk_size: 1024
chunk_overlap: 256
embedding: 当前默认模型
private: 先关闭
```

## 提问

从 `rag_test_questions.csv` 里选择前 5 个问题先测试：

```text
正式员工每年有多少天带薪年假？
员工申请年假需要提前多久？
新员工试用期多久？
一线城市出差住宿标准是多少？
报销单需要在费用发生后多久内提交？
```

再测试 2 个特殊问题：

```text
BX-1024 是否通过审批？
公司明年是否计划上市？
```

## 观察内容

每个问题都观察 4 件事：

```text
1. 最终答案是否正确
2. 引用来源是否正确
3. 检索结果是否包含正确 chunk
4. 没有答案的问题是否拒答
```

如果前端能看到引用卡片或参考文档，重点检查：

```text
文件名是否正确
片段内容是否支持答案
页码或来源 metadata 是否合理
引用是否和答案对应
```

## 判断方法

如果答案错，并且引用或检索片段里没有正确证据：

```text
优先怀疑检索链路：
文档解析、chunking、embedding、top_k、retrieval_mode、rerank
```

如果答案错，但引用或检索片段里已经有正确证据：

```text
优先怀疑生成链路：
prompt、上下文组织、LLM、引用格式
```

如果资料中没有答案但模型强行回答：

```text
优先怀疑 prompt 的拒答约束不够强
```

## 本节验收

完成本节后，你应该能回答：

```text
1. 当前系统对事实型问题是否能找对资料？
2. 当前系统对编号型问题是否能命中精确编号？
3. 当前系统对无答案问题是否会拒答？
4. 当答案错误时，问题更像出在检索还是生成？
```


# RAG 正式评测集说明

本目录下的正式评测集沿用评测中心已有的 `RagEvalExample.tags` 字段，不新增 schema。

## 标签分类

| tag | 用途 | 典型问题 |
| --- | --- | --- |
| `semantic` | 语义型问题，问法和文档措辞不完全一致 | 员工一年能休几天带薪假？ |
| `exact_id` | 编号、日期、合同号、报销单号等精确匹配 | BX-1024 是否通过审批？ |
| `permission` | 权限边界、访问范围、公开/私有文档 | 付款审批模块谁可以访问？ |
| `temporal_policy` | 新旧制度、时间适用性判断 | 2026 年发生的报销事项，提交期限是 30 天还是 45 天？ |
| `no_answer` | 资料中无答案，期望模型拒答 | 公司明年是否计划上市？ |

## 从现有 CSV 生成正式评测 CSV

运行：

```bash
python docs/rag_practice/build_rag_eval_dataset.py
```

默认读取：

```text
docs/rag_practice/rag_test_questions.csv
docs/rag_practice/long_policy_questions.csv
```

并输出：

```text
docs/rag_practice/formal_rag_eval_examples.csv
```

输出字段与评测中心样例字段保持接近：

```text
question
expected_answer
expected_source_ids
expected_keywords
tags
```

其中 `expected_source_ids` 取 `expected_source` 的第一个空格前字段，例如 `long_policy_mixed.md`。如果实际导入后文件 ID 是数据库中的 UUID，需要在导入时把文件名映射为真实 source id。

## 当前映射规则

- `should_refuse=true` 或 `question_type=no_answer` -> `no_answer`
- `question_type=exact` -> `exact_id`
- `question_type=permission` -> `permission`
- `question_type=temporal_policy` -> `temporal_policy`
- 问题包含 `权限`、`访问`、`公共文档`、`查看` -> `permission`
- 问题包含 `2026 年`、`旧制度`、`新制度` -> `temporal_policy`
- 问题包含 `BX-`、`HT-`、`编号` -> `exact_id`
- 其他事实型、条件型问题默认归为 `semantic`

原始两个 CSV 中尚未包含时间适用性问题，脚本会从 `lesson_03_retrieval_mode_experiment.md` 补充 3 条 `temporal_policy` 样例。

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path


DOC_NAME = "星河智造科技有限公司综合运营制度手册_无问题版.md"
DEFAULT_DB = Path("ktem_app_data/user_data/sql.db")
DEFAULT_OUTPUT = Path("docs/rag_practice/xinghe_formal_eval_examples.csv")


EXAMPLES = [
    {
        "id": "XH-S001",
        "question": "正式员工每年享有多少天带薪年假？",
        "expected_answer": "正式员工每年享有 10 天带薪年假。",
        "expected_keywords": "10 天带薪年假, 正式员工",
        "tags": "semantic",
    },
    {
        "id": "XH-S002",
        "question": "一次申请连续 5 天以上年假需要提前多久提交？",
        "expected_answer": "需要至少提前 10 个工作日提交申请。",
        "expected_keywords": "连续 5 天以上年假, 10 个工作日",
        "tags": "semantic",
    },
    {
        "id": "XH-S003",
        "question": "大型行业会议住宿标准最多可以上浮多少？",
        "expected_answer": "公司指定大型行业会议住宿标准可以上浮 20%。",
        "expected_keywords": "大型行业会议, 上浮 20%",
        "tags": "semantic",
    },
    {
        "id": "XH-S004",
        "question": "超过 90 天未提交的报销单是否还会受理？",
        "expected_answer": "超过 90 天未提交报销原则上不再受理。",
        "expected_keywords": "超过 90 天, 原则上不再受理",
        "tags": "semantic",
    },
    {
        "id": "XH-E001",
        "question": "BX-1024 是否已经通过审批？",
        "expected_answer": "BX-1024 已经通过审批。",
        "expected_keywords": "BX-1024, 已经通过审批",
        "tags": "exact_id",
    },
    {
        "id": "XH-E002",
        "question": "BX-1024 的审批日期是哪一天？",
        "expected_answer": "BX-1024 的审批日期为 2026-06-15。",
        "expected_keywords": "BX-1024, 2026-06-15",
        "tags": "exact_id",
    },
    {
        "id": "XH-E003",
        "question": "BX-1048 当前是什么状态？",
        "expected_answer": "BX-1048 当前状态为待部门负责人审批。",
        "expected_keywords": "BX-1048, 待部门负责人审批",
        "tags": "exact_id",
    },
    {
        "id": "XH-E004",
        "question": "BX-1066 为什么被退回？",
        "expected_answer": "BX-1066 因缺少有效发票被退回。",
        "expected_keywords": "BX-1066, 缺少有效发票",
        "tags": "exact_id",
    },
    {
        "id": "XH-E005",
        "question": "HT-2026-009 的第二笔付款节点是什么？",
        "expected_answer": "HT-2026-009 的第二笔付款节点为验收通过后 15 个工作日内。",
        "expected_keywords": "HT-2026-009, 验收通过后 15 个工作日内",
        "tags": "exact_id",
    },
    {
        "id": "XH-E006",
        "question": "HT-2026-021 的终止日期是什么时候？",
        "expected_answer": "HT-2026-021 终止日期为 2026-05-20。",
        "expected_keywords": "HT-2026-021, 2026-05-20",
        "tags": "exact_id",
    },
    {
        "id": "XH-P001",
        "question": "财务系统付款审批模块允许哪些角色访问？",
        "expected_answer": "财务系统付款审批模块仅允许财务主管和财务经理访问。",
        "expected_keywords": "付款审批模块, 财务主管, 财务经理",
        "tags": "permission",
    },
    {
        "id": "XH-P002",
        "question": "普通财务专员能否修改付款审批结果？",
        "expected_answer": "普通财务专员不能修改付款审批结果。",
        "expected_keywords": "普通财务专员, 不能修改付款审批结果",
        "tags": "permission",
    },
    {
        "id": "XH-P003",
        "question": "生产环境访问权限授予哪些人？",
        "expected_answer": "生产环境访问权限只授予运维负责人和经过审批的研发负责人。",
        "expected_keywords": "生产环境访问权限, 运维负责人, 审批的研发负责人",
        "tags": "permission",
    },
    {
        "id": "XH-P004",
        "question": "哪些文档不得设为公共文档？",
        "expected_answer": "包含薪酬、绩效、劳动合同、客户商业秘密的文档不得设为公共文档。",
        "expected_keywords": "薪酬, 绩效, 劳动合同, 客户商业秘密, 不得设为公共文档",
        "tags": "permission",
    },
    {
        "id": "XH-P005",
        "question": "私有文档默认谁可以查看？",
        "expected_answer": "私有文档只能由上传者本人查看。",
        "expected_keywords": "私有文档, 上传者本人",
        "tags": "permission",
    },
    {
        "id": "XH-P006",
        "question": "离职员工的私有文档默认转交给谁？",
        "expected_answer": "离职员工的私有文档默认转交给直属主管。",
        "expected_keywords": "离职员工, 私有文档, 直属主管",
        "tags": "permission",
    },
    {
        "id": "XH-T001",
        "question": "2026 年发生的报销事项，提交期限是 30 天还是 45 天？",
        "expected_answer": "2026 年事项适用新制度，报销提交期限为 30 天。",
        "expected_keywords": "2026 年, 新制度, 30 天",
        "tags": "temporal_policy",
    },
    {
        "id": "XH-T002",
        "question": "2025 年发生的报销事项，提交期限是多少天？",
        "expected_answer": "2025 年事项优先查找旧制度，旧制度报销提交期限为 45 天。",
        "expected_keywords": "2025 年, 旧制度, 45 天",
        "tags": "temporal_policy",
    },
    {
        "id": "XH-T003",
        "question": "2026 年一线城市住宿标准是多少？",
        "expected_answer": "2026 年适用新制度，一线城市住宿标准为每天 500 元。",
        "expected_keywords": "2026 年, 新制度, 500 元",
        "tags": "temporal_policy",
    },
    {
        "id": "XH-T004",
        "question": "2025 年生产环境临时权限最长是 7 天还是 14 天？",
        "expected_answer": "2025 年事项适用旧制度，生产环境临时权限最长 14 天。",
        "expected_keywords": "2025 年, 旧制度, 14 天",
        "tags": "temporal_policy",
    },
    {
        "id": "XH-T005",
        "question": "事项缺少发生日期时，如何判断适用旧制度还是新制度？",
        "expected_answer": "事项缺少发生日期时，应提示需要发生日期才能判断旧制度或新制度。",
        "expected_keywords": "缺少发生日期, 需要发生日期, 判断旧制度或新制度",
        "tags": "temporal_policy",
    },
    {
        "id": "XH-N001",
        "question": "公司明年是否计划上市？",
        "expected_answer": "资料中没有提供相关信息，应拒答。",
        "expected_keywords": "",
        "tags": "no_answer",
    },
    {
        "id": "XH-N002",
        "question": "CEO 的个人手机号是多少？",
        "expected_answer": "资料中没有提供相关信息，应拒答。",
        "expected_keywords": "",
        "tags": "no_answer",
    },
    {
        "id": "XH-N003",
        "question": "员工年终奖发放比例是多少？",
        "expected_answer": "资料中没有提供相关信息，应拒答。",
        "expected_keywords": "",
        "tags": "no_answer",
    },
]


def current_source_id(db_path: Path, source_name: str) -> str:
    if not db_path.exists():
        return source_name
    conn = sqlite3.connect(db_path)
    tables = conn.execute(
        "select name from sqlite_master where type='table' and name like 'index__%__source'"
    ).fetchall()
    for (table,) in tables:
        row = conn.execute(
            f'select id from "{table}" where name=? order by date_created desc limit 1',
            (source_name,),
        ).fetchone()
        if row:
            return str(row[0])
    return source_name


def main() -> None:
    parser = argparse.ArgumentParser(description="Build eval examples for Xinghe handbook.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    source_id = current_source_id(args.db, DOC_NAME)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "id",
                "question",
                "expected_answer",
                "expected_source_ids",
                "expected_keywords",
                "tags",
                "source_file",
                "source_note",
            ],
        )
        writer.writeheader()
        for example in EXAMPLES:
            writer.writerow(
                {
                    **example,
                    "expected_source_ids": "" if example["tags"] == "no_answer" else source_id,
                    "source_file": DOC_NAME,
                    "source_note": "星河智造制度手册 FACT/附录章节",
                }
            )
    print(f"Wrote {len(EXAMPLES)} examples to {args.output}")
    print(f"expected_source_ids={source_id}")


if __name__ == "__main__":
    main()

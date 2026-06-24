from __future__ import annotations

import argparse
import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_INPUTS = (
    ROOT / "rag_test_questions.csv",
    ROOT / "long_policy_questions.csv",
)
DEFAULT_OUTPUT = ROOT / "formal_rag_eval_examples.csv"


TEMPORAL_POLICY_EXAMPLES = [
    {
        "id": "TP001",
        "question": "2026 年发生的报销事项，提交期限是 30 天还是 45 天？",
        "expected_answer": "应按 2026 年适用制度判断提交期限。",
        "expected_source": "long_policy_mixed.md 新旧制度适用范围",
        "question_type": "temporal_policy",
        "should_refuse": "false",
        "source_file": "lesson_03_retrieval_mode_experiment.md",
    },
    {
        "id": "TP002",
        "question": "2026 年一线城市住宿标准是多少？",
        "expected_answer": "应按 2026 年适用制度判断一线城市住宿标准。",
        "expected_source": "long_policy_mixed.md 新旧制度适用范围",
        "question_type": "temporal_policy",
        "should_refuse": "false",
        "source_file": "lesson_03_retrieval_mode_experiment.md",
    },
    {
        "id": "TP003",
        "question": "2026 年生产环境临时权限最长是 7 天还是 14 天？",
        "expected_answer": "应按 2026 年适用制度判断生产环境临时权限有效期。",
        "expected_source": "long_policy_mixed.md 新旧制度适用范围",
        "question_type": "temporal_policy",
        "should_refuse": "false",
        "source_file": "lesson_03_retrieval_mode_experiment.md",
    },
]


def tags_for(row: dict[str, str]) -> list[str]:
    question_type = (row.get("question_type") or "").strip().lower()
    question = row.get("question") or ""
    should_refuse = (row.get("should_refuse") or "").strip().lower() == "true"

    if should_refuse or question_type == "no_answer":
        return ["no_answer"]
    if question_type == "exact":
        return ["exact_id"]
    if question_type == "permission":
        return ["permission"]
    if question_type == "temporal_policy":
        return ["temporal_policy"]
    if any(token in question for token in ("权限", "访问", "公共文档", "查看")):
        return ["permission"]
    if any(token in question for token in ("2026 年", "旧制度", "新制度")):
        return ["temporal_policy"]
    if any(token in question for token in ("BX-", "HT-", "编号")):
        return ["exact_id"]
    return ["semantic"]


def keywords_for(row: dict[str, str], tags: list[str]) -> list[str]:
    expected_answer = row.get("expected_answer") or ""
    expected_source = row.get("expected_source") or ""
    keywords: list[str] = []
    if expected_answer and "资料中没有提供" not in expected_answer:
        keywords.append(expected_answer)
    if "exact_id" in tags:
        for token in (row.get("question") or "").replace("？", " ").split():
            if "-" in token:
                keywords.append(token.strip("，。?？"))
    if expected_source:
        parts = expected_source.split()
        if len(parts) > 1:
            keywords.append(parts[-1])
    output: list[str] = []
    for keyword in keywords:
        if keyword and keyword not in output:
            output.append(keyword)
    return output


def source_id_for(row: dict[str, str]) -> str:
    expected_source = row.get("expected_source") or ""
    return expected_source.split()[0] if expected_source else ""


def read_rows(paths: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open(newline="", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                row["source_file"] = path.name
                rows.append(row)
    rows.extend(TEMPORAL_POLICY_EXAMPLES)
    return rows


def build_rows(paths: list[Path]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for row in read_rows(paths):
        tags = tags_for(row)
        output.append(
            {
                "id": row.get("id") or "",
                "question": row.get("question") or "",
                "expected_answer": row.get("expected_answer") or "",
                "expected_source_ids": source_id_for(row),
                "expected_keywords": ", ".join(keywords_for(row, tags)),
                "tags": ", ".join(tags),
                "should_refuse": row.get("should_refuse") or "false",
                "source_file": row.get("source_file") or "",
                "source_note": row.get("expected_source") or "",
            }
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a formal RAG eval CSV with tags from rag_practice questions."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output CSV path. Defaults to {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        default=list(DEFAULT_INPUTS),
        help="Input question CSV files.",
    )
    args = parser.parse_args()

    rows = build_rows(args.inputs)
    fieldnames = [
        "id",
        "question",
        "expected_answer",
        "expected_source_ids",
        "expected_keywords",
        "tags",
        "should_refuse",
        "source_file",
        "source_note",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} examples to {args.output}")


if __name__ == "__main__":
    main()

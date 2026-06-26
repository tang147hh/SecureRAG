from __future__ import annotations

import json
import re

from ktem.llms.manager import llms

from kotaemon.base import BaseComponent, Document, HumanMessage, Node, SystemMessage
from kotaemon.llms import ChatLLM, PromptTemplate

DEFAULT_RAG_FUSION_PROMPT = (
    "Generate {num_queries} complementary search queries for RAG-Fusion. "
    "Return a JSON array of strings only. The first query must be the original "
    "question unchanged. The remaining queries must use different retrieval "
    "angles, not simple paraphrases.\n\n"
    "Mandatory retrieval angles to cover when relevant:\n"
    "1. Time applicability: effective date, old policy/new policy boundary, "
    "event date versus hire date/submission date/approval date, and whether the "
    "date in the question is enough to decide which policy applies.\n"
    "2. Person and role applicability: employee type, probation status, department, "
    "role permissions, eligibility, and whether a rule for regular employees can "
    "be applied to another identity.\n"
    "3. Amount and standard lookup: exact fee names, city tier, lodging/meal/"
    "transport standards, numeric caps, and exceptions.\n"
    "4. Approval and process: pre-approval, approver, required materials, change/"
    "cancellation flow, reimbursement deadline, and extra approval conditions.\n"
    "5. Exception/conflict evidence: conference meals, client banquets, upgraded "
    "tickets, old/new standard conflicts, missing facts, and non-duplicated benefits.\n\n"
    "Rules:\n"
    "- Preserve every hard constraint from the original question, including named "
    "entities, dates, time windows, departments, roles, cities, numeric conditions, "
    "policy names, and comparison criteria.\n"
    "- If a question includes any date, at least one query must explicitly search "
    "for policy effective date and old/new policy boundary.\n"
    "- If a question includes an employee identity or department, at least one query "
    "must explicitly search for applicability and permission/eligibility boundary.\n"
    "- If a question asks multiple fee/process items, split them across amount/"
    "standard and approval/process queries.\n"
    "- Do not answer the question. Do not include explanations outside the JSON.\n"
    "Give queries in {lang}.\n\n"
    "Original question: {question}\n"
    "JSON array:"
)


class RagFusionQueryPipeline(BaseComponent):
    """Generate complementary retrieval queries for RAG-Fusion."""

    llm: ChatLLM = Node(default_callback=lambda _: llms.get_default())
    fusion_template: str = DEFAULT_RAG_FUSION_PROMPT
    lang: str = "English"
    num_queries: int = 4
    min_queries: int = 3
    max_queries: int = 5

    @staticmethod
    def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
        return any(term.lower() in text.lower() for term in terms)

    @classmethod
    def _angle_queries(cls, question: str) -> list[tuple[str, str, tuple[str, ...]]]:
        return [
            (
                "time",
                (
                    f"{question} 生效日期 旧制度 新制度 政策边界 "
                    "事项发生日期 入职日期 提交日期 审批日期 适用时间"
                ),
                (
                    "生效",
                    "旧制度",
                    "新制度",
                    "政策边界",
                    "事项发生日期",
                    "入职日期",
                    "提交日期",
                    "审批日期",
                    "effective date",
                    "old policy",
                    "new policy",
                ),
            ),
            (
                "identity",
                (
                    f"{question} 试用期员工 正式员工 上海销售团队 "
                    "身份适用 部门适用 权限边界 报销资格"
                ),
                (
                    "身份适用",
                    "部门适用",
                    "权限边界",
                    "报销资格",
                    "eligibility",
                    "applicability",
                ),
            ),
            (
                "amount",
                (
                    f"{question} 费用标准 住宿费 餐补 交通费 市内交通 "
                    "城市等级 深圳 一线城市 金额上限"
                ),
                (
                    "费用标准",
                    "城市等级",
                    "金额",
                    "上限",
                    "standard",
                    "allowance",
                    "cap",
                    "limit",
                ),
            ),
            (
                "approval",
                (
                    f"{question} 出差申请 提前审批 审批人 所需材料 "
                    "报销流程 例外条件 不重复报销"
                ),
                (
                    "提前审批",
                    "审批人",
                    "所需材料",
                    "报销流程",
                    "例外",
                    "不重复",
                    "approval",
                    "approver",
                    "exception",
                ),
            ),
        ]

    @classmethod
    def _ensure_angle_coverage(
        cls,
        question: str,
        queries: list[str],
        max_queries: int,
    ) -> list[str]:
        unique_queries: list[str] = []
        for query in [question, *queries]:
            normalized = " ".join(str(query).split())
            if normalized and normalized not in unique_queries:
                unique_queries.append(normalized)

        selected = unique_queries[:1] or [question]
        remaining = unique_queries[1:]

        for _, fallback_query, markers in cls._angle_queries(question):
            angle_pool = selected[1:] + remaining
            if not any(cls._contains_any(query, markers) for query in angle_pool):
                selected.append(" ".join(fallback_query.split()))
            else:
                for query in list(remaining):
                    if cls._contains_any(query, markers):
                        selected.append(query)
                        remaining.remove(query)
                        break
            if len(selected) >= max_queries:
                return selected[:max_queries]

        for query in remaining:
            if query not in selected:
                selected.append(query)
            if len(selected) >= max_queries:
                break

        return selected[:max_queries]

    @staticmethod
    def _parse_queries(text: str) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\[[\s\S]*\]", text)
            parsed = json.loads(match.group(0)) if match else None

        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return []

    def run(self, question: str) -> Document:  # type: ignore
        prompt_template = PromptTemplate(self.fusion_template)
        target_count = max(self.min_queries, min(self.max_queries, self.num_queries))
        prompt = prompt_template.populate(
            question=question,
            lang=self.lang,
            num_queries=target_count,
        )
        messages = [
            SystemMessage(content="You are a careful retrieval query planner."),
            HumanMessage(content=prompt),
        ]
        response = self.llm(messages)
        queries = self._parse_queries(response.text)
        unique_queries = self._ensure_angle_coverage(
            question=question,
            queries=queries,
            max_queries=self.max_queries,
        )

        if len(unique_queries) < self.min_queries:
            for _, fallback, _ in self._angle_queries(question):
                normalized = " ".join(fallback.split())
                if normalized not in unique_queries:
                    unique_queries.append(normalized)
                if len(unique_queries) >= self.min_queries:
                    break

        return Document(
            text=json.dumps(unique_queries[: self.max_queries], ensure_ascii=False),
            metadata={
                "queries": unique_queries[: self.max_queries],
                "raw_response": response.text,
            },
        )

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

STATUS_SUPPORTED = "supported"
STATUS_UNSUPPORTED = "unsupported"
STATUS_INSUFFICIENT = "insufficient"

DEFAULT_MIN_COVERAGE = 0.75
DEFAULT_MIN_SUPPORTED = 1

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])\s*|\n+")
_CLAUSE_SPLIT_RE = re.compile(r"(?:，|,)?(?:但|但是|不过|然而|因此|所以|故)(?:，|,)?")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")
_YEAR_MONTH_RE = re.compile(r"(?P<year>20\d{2})\s*(?:年|-|/)\s*(?P<month>0?[1-9]|1[0-2])\s*(?:月)?")
_YEAR_RE = re.compile(r"(?P<year>20\d{2})\s*年?")

_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "there",
    "their",
    "answer",
    "context",
    "根据",
    "当前",
    "信息",
    "资料",
    "问题",
    "回答",
    "可以",
    "需要",
    "不能",
    "无法",
    "因此",
    "如果",
    "但是",
    "以及",
    "或者",
    "因为",
    "所以",
}

_STRONG_FACT_TERMS = {
    "住宿标准",
    "餐补",
    "交通费",
    "市内交通",
    "出差",
    "报销",
    "审批",
    "申请",
    "材料",
    "发票",
    "一线城市",
    "其他城市",
    "上海",
    "深圳",
    "北京",
    "广州",
    "客户",
    "会议",
    "试用期",
    "正式员工",
    "销售",
    "旧制度",
    "新制度",
    "生效日期",
    "适用时间",
    "发生日期",
    "事项发生",
    "提交期限",
    "费用发生",
}

_REFUSAL_MARKERS = (
    "无法回答",
    "无法确定",
    "不能确定",
    "不知道",
    "未找到",
    "没有相关信息",
    "根据当前信息不足",
    "资料中没有提供",
    "cannot answer",
    "not enough information",
    "insufficient information",
    "do not know",
)

_INSUFFICIENT_MARKERS = (
    "未提及",
    "没有提及",
    "未说明",
    "没有说明",
    "未提供",
    "没有提供",
    "无法确认",
    "不能确认",
    "无法判断",
    "不能判断",
    "无法确定",
    "不能确定",
    "资料不足",
    "证据不足",
    "insufficient",
    "not enough",
    "not mention",
)


@dataclass(frozen=True)
class EvidenceDecision:
    status: str
    evidence_coverage: float
    unsupported_count: int
    insufficient_count: int
    should_refuse: bool
    should_retry: bool
    reason: str


def is_refusal_answer(answer: str) -> bool:
    normalized = (answer or "").lower()
    return any(marker.lower() in normalized for marker in _REFUSAL_MARKERS)


def is_insufficient_claim(text: str) -> bool:
    normalized = (text or "").lower()
    return any(marker.lower() in normalized for marker in _INSUFFICIENT_MARKERS)


def split_answer_sentences(answer: str) -> list[str]:
    normalized = re.sub(r"<[^>]+>", " ", answer or "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return []

    parts = _SENTENCE_SPLIT_RE.split(normalized)
    sentences: list[str] = []
    for part in parts:
        item = part.strip()
        if not item:
            continue
        clauses = [piece.strip() for piece in _CLAUSE_SPLIT_RE.split(item) if piece.strip()]
        if len(clauses) > 1:
            sentences.extend(clauses)
        elif len(item) > 220 and "，" in item:
            sentences.extend(
                piece.strip() for piece in item.split("，") if piece.strip()
            )
        else:
            sentences.append(item)
    return sentences


def _tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in _TOKEN_RE.findall(text or ""):
        normalized = token.lower()
        if len(normalized) <= 1 or normalized in _STOPWORDS:
            continue
        tokens.add(normalized)
        if re.fullmatch(r"[\u4e00-\u9fff]{2,}", normalized):
            for size in (2, 3, 4):
                for index in range(0, max(0, len(normalized) - size + 1)):
                    gram = normalized[index : index + size]
                    if gram not in _STOPWORDS:
                        tokens.add(gram)
    return tokens


def _numbers(text: str) -> set[str]:
    return {
        number
        for number in _NUMBER_RE.findall(text or "")
        if not re.fullmatch(r"20\d{2}", number)
    }


def _date_tokens(text: str) -> set[str]:
    value = text or ""
    tokens = set()
    covered_years: set[str] = set()
    for match in _YEAR_MONTH_RE.finditer(value):
        year = match.group("year")
        month = int(match.group("month"))
        tokens.add(f"{year}-{month:02d}")
        covered_years.add(year)
    for match in _YEAR_RE.finditer(value):
        year = match.group("year")
        if year not in covered_years:
            tokens.add(year)
    return tokens


def _strong_fact_terms(tokens: set[str]) -> set[str]:
    return tokens & _STRONG_FACT_TERMS


def _chunk_id(chunk: Any, index: int) -> str:
    if isinstance(chunk, dict):
        return str(chunk.get("chunk_id") or chunk.get("id") or f"chunk-{index}")
    return str(getattr(chunk, "doc_id", "") or getattr(chunk, "id_", "") or f"chunk-{index}")


def _chunk_text(chunk: Any) -> str:
    if isinstance(chunk, dict):
        metadata = chunk.get("metadata") or {}
        return str(
            chunk.get("text")
            or chunk.get("content")
            or chunk.get("excerpt")
            or metadata.get("page_content")
            or metadata.get("content")
            or ""
        )
    return str(getattr(chunk, "text", "") or getattr(chunk, "content", "") or "")


def _chunk_source(chunk: Any) -> tuple[str, str]:
    if isinstance(chunk, dict):
        metadata = chunk.get("metadata") or {}
        return (
            str(chunk.get("source_id") or metadata.get("file_id") or metadata.get("source_id") or ""),
            str(chunk.get("source_name") or metadata.get("file_name") or metadata.get("source") or ""),
        )
    metadata = getattr(chunk, "metadata", {}) or {}
    return (
        str(metadata.get("file_id") or metadata.get("source_id") or ""),
        str(metadata.get("file_name") or metadata.get("source") or ""),
    )


def _chunk_page(chunk: Any) -> str | None:
    metadata = chunk.get("metadata") if isinstance(chunk, dict) else getattr(chunk, "metadata", {})
    metadata = metadata or {}
    value = metadata.get("page_label")
    if isinstance(chunk, dict) and chunk.get("page_label") is not None:
        value = chunk.get("page_label")
    return str(value) if value is not None else None


def _evidence_snapshot(chunk: Any, index: int, overlap: set[str]) -> dict[str, Any]:
    text = " ".join(_chunk_text(chunk).split())
    source_id, source_name = _chunk_source(chunk)
    return {
        "chunk_id": _chunk_id(chunk, index),
        "source_id": source_id,
        "source_name": source_name,
        "page_label": _chunk_page(chunk),
        "overlap_terms": sorted(overlap)[:12],
        "excerpt": text[:360],
    }


def assess_answer_support(
    answer: str,
    evidence_chunks: Iterable[Any],
    *,
    min_overlap_ratio: float = 0.35,
    min_overlap_terms: int = 2,
    top_evidence: int = 3,
) -> dict[str, Any]:
    sentences = split_answer_sentences(answer)
    chunks = list(evidence_chunks or [])
    chunk_tokens = [(_tokens(_chunk_text(chunk)), chunk) for chunk in chunks]

    checks: list[dict[str, Any]] = []
    for sentence_index, sentence in enumerate(sentences, start=1):
        sentence_tokens = _tokens(sentence)
        sentence_numbers = _numbers(sentence)
        sentence_dates = _date_tokens(sentence)

        if is_refusal_answer(sentence) or is_insufficient_claim(sentence):
            checks.append(
                {
                    "index": sentence_index,
                    "sentence": sentence,
                    "status": STATUS_INSUFFICIENT,
                    "score": 0.0,
                    "reason": "refusal_or_missing_information_sentence",
                    "evidence": [],
                }
            )
            continue

        if not sentence_tokens:
            checks.append(
                {
                    "index": sentence_index,
                    "sentence": sentence,
                    "status": STATUS_INSUFFICIENT,
                    "score": 0.0,
                    "reason": "no_checkable_claim_terms",
                    "evidence": [],
                }
            )
            continue

        ranked: list[tuple[float, int, set[str], Any]] = []
        for chunk_index, (tokens, chunk) in enumerate(chunk_tokens, start=1):
            overlap = sentence_tokens & tokens
            if not overlap:
                continue
            ratio = len(overlap) / max(1, len(sentence_tokens))
            ranked.append((ratio, chunk_index, overlap, chunk))
        ranked.sort(key=lambda item: (item[0], len(item[2])), reverse=True)

        best_ratio = ranked[0][0] if ranked else 0.0
        best_overlap = ranked[0][2] if ranked else set()
        strong_overlap = _strong_fact_terms(best_overlap)
        matched_evidence = [
            _evidence_snapshot(chunk, chunk_index, overlap)
            for _, chunk_index, overlap, chunk in ranked[:top_evidence]
        ]
        numbers_supported = True
        if sentence_numbers and ranked:
            evidence_number_text = " ".join(_chunk_text(item[3]) for item in ranked[:top_evidence])
            evidence_numbers = _numbers(evidence_number_text)
            numbers_supported = sentence_numbers.issubset(evidence_numbers)
        dates_supported = True
        if sentence_dates and ranked:
            evidence_date_text = " ".join(
                _chunk_text(item[3]) for item in ranked[:top_evidence]
            )
            evidence_dates = _date_tokens(evidence_date_text)
            dates_supported = sentence_dates.issubset(evidence_dates)

        if (
            best_ratio >= min_overlap_ratio
            and len(best_overlap) >= min_overlap_terms
            and numbers_supported
            and dates_supported
        ):
            status = STATUS_SUPPORTED
            reason = "claim_terms_and_numbers_found_in_evidence"
        elif (
            (sentence_numbers or sentence_dates)
            and numbers_supported
            and dates_supported
            and len(strong_overlap) >= 1
            and len(best_overlap) >= min_overlap_terms
        ):
            status = STATUS_SUPPORTED
            reason = "strong_fact_terms_and_numbers_found_in_evidence"
        elif (
            not sentence_numbers
            and not sentence_dates
            and len(strong_overlap) >= 2
            and len(best_overlap) >= max(4, min_overlap_terms)
        ):
            status = STATUS_SUPPORTED
            reason = "strong_process_terms_found_in_evidence"
        elif matched_evidence:
            status = STATUS_UNSUPPORTED
            reason = (
                "numbers_not_found_in_evidence"
                if not numbers_supported
                else "dates_not_found_in_evidence"
                if not dates_supported
                else "weak_evidence_overlap"
            )
        else:
            status = STATUS_UNSUPPORTED if chunks else STATUS_INSUFFICIENT
            reason = (
                "retrieved_evidence_does_not_support_claim"
                if chunks
                else "no_retrieved_evidence_available"
            )

        checks.append(
            {
                "index": sentence_index,
                "sentence": sentence,
                "status": status,
                "score": round(best_ratio, 4),
                "reason": reason,
                "evidence": matched_evidence,
            }
        )

    checkable = [
        item for item in checks if item["status"] != STATUS_INSUFFICIENT or item["evidence"]
    ]
    denominator = len(checkable) or len(checks)
    supported_count = sum(1 for item in checks if item["status"] == STATUS_SUPPORTED)
    unsupported_count = sum(1 for item in checks if item["status"] == STATUS_UNSUPPORTED)
    insufficient_count = sum(1 for item in checks if item["status"] == STATUS_INSUFFICIENT)
    coverage = supported_count / denominator if denominator else 0.0

    return {
        "sentence_count": len(checks),
        "supported_count": supported_count,
        "unsupported_count": unsupported_count,
        "insufficient_count": insufficient_count,
        "evidence_coverage": coverage,
        "checks": checks,
    }


def decide_evidence_gate(
    assessment: dict[str, Any],
    *,
    retry_used: bool = False,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    min_supported: int = DEFAULT_MIN_SUPPORTED,
) -> EvidenceDecision:
    coverage = float(assessment.get("evidence_coverage") or 0.0)
    supported = int(assessment.get("supported_count") or 0)
    unsupported = int(assessment.get("unsupported_count") or 0)
    insufficient = int(assessment.get("insufficient_count") or 0)
    sentence_count = int(assessment.get("sentence_count") or 0)
    risky = unsupported > 0 or insufficient > 0 or coverage < min_coverage
    enough_support = supported >= min_supported and coverage >= min_coverage
    should_retry = risky and not retry_used and sentence_count > 0
    should_refuse = risky and retry_used and not enough_support
    if not sentence_count:
        reason = "empty_answer"
        should_retry = not retry_used
        should_refuse = retry_used
    elif should_refuse:
        reason = "evidence_coverage_below_threshold_after_retry"
    elif should_retry:
        reason = "evidence_coverage_below_threshold"
    else:
        reason = "evidence_support_sufficient"
    return EvidenceDecision(
        status=STATUS_SUPPORTED if enough_support else STATUS_INSUFFICIENT,
        evidence_coverage=coverage,
        unsupported_count=unsupported,
        insufficient_count=insufficient,
        should_refuse=should_refuse,
        should_retry=should_retry,
        reason=reason,
    )


def build_refusal_answer(assessment: dict[str, Any]) -> str:
    unsupported = [
        item.get("sentence")
        for item in assessment.get("checks", [])
        if item.get("status") in {STATUS_UNSUPPORTED, STATUS_INSUFFICIENT}
    ]
    if unsupported:
        first = str(unsupported[0])
        return (
            "根据当前检索到的证据，无法可靠回答该问题。"
            f"证据不足的关键表述是：{first}"
        )
    return "根据当前检索到的证据，无法可靠回答该问题。"

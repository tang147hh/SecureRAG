from __future__ import annotations

from dataclasses import dataclass
from math import log2
from typing import Any

from sqlmodel import Session, select

from ktem.db.engine import engine
from ktem.evidence import is_refusal_answer
from ktem.permissions import can_read_source

from .models import RagEvalDataset, RagEvalExample, RagEvalRun, _now


@dataclass(frozen=True)
class EvalMetricInputs:
    answer: str
    references: list[dict[str, Any]]
    trace_data: dict[str, Any]
    expected_source_ids: list[str]
    expected_keywords: list[str]
    error: str | None = None
    tags: list[str] | None = None


def _normalize_list(values: list[Any] | None) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            output.append(normalized)
            seen.add(normalized)
    return output


def _chunk_source_id(chunk: dict[str, Any] | None) -> str:
    if not chunk or not isinstance(chunk, dict):
        return ""
    metadata = chunk.get("metadata") or {}
    return str(
        chunk.get("source_id")
        or metadata.get("file_id")
        or metadata.get("source_id")
        or ""
    ).strip()


def _chunk_source_aliases(chunk: dict[str, Any] | None) -> set[str]:
    if not chunk or not isinstance(chunk, dict):
        return set()
    metadata = chunk.get("metadata") or {}
    aliases = {
        chunk.get("source_id"),
        chunk.get("source_name"),
        metadata.get("file_id"),
        metadata.get("source_id"),
        metadata.get("file_name"),
    }
    return {str(alias).strip() for alias in aliases if str(alias or "").strip()}


def _trace_source_ids(trace_data: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("citation_chunks", "context_chunks", "candidate_chunks_after_rerank"):
        for chunk in trace_data.get(key) or []:
            ids.update(_chunk_source_aliases(chunk))
    return ids


def _trace_acl_source_ids(trace_data: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("citation_chunks", "context_chunks", "candidate_chunks_after_rerank"):
        for chunk in trace_data.get(key) or []:
            source_id = _chunk_source_id(chunk)
            if source_id:
                ids.add(source_id)
    return ids


def _ranked_chunks(trace_data: dict[str, Any]) -> list[dict[str, Any]]:
    chunks = (
        trace_data.get("context_chunks")
        or trace_data.get("candidate_chunks_after_rerank")
        or []
    )
    return [chunk for chunk in chunks if isinstance(chunk, dict)]


def _layer_counts(chunks: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"summary_layer": 0, "detail_layer": 0, "graph_layer": 0}
    for chunk in chunks:
        layer = str(chunk.get("retrieval_layer") or "")
        if not layer:
            if chunk.get("type") == "summary":
                layer = "summary_layer"
            elif chunk.get("type") == "graph":
                layer = "graph_layer"
            else:
                layer = "detail_layer"
        if layer in counts:
            counts[layer] += 1
    return counts


def _metric_k(trace_data: dict[str, Any], ranked_chunks: list[dict[str, Any]]) -> int:
    params = trace_data.get("retrieval_params") or {}
    for key in ("topK", "top_k", "topk"):
        try:
            value = int(params.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return len(ranked_chunks)


def _ranked_sources(trace_data: dict[str, Any]) -> tuple[list[set[str]], int]:
    chunks = _ranked_chunks(trace_data)
    k = _metric_k(trace_data, chunks)
    return [_chunk_source_aliases(chunk) for chunk in chunks[:k]], k


def _first_hit_rank(
    ranked_sources: list[set[str]], expected_source_ids: list[str]
) -> int | None:
    expected = set(expected_source_ids)
    for index, aliases in enumerate(ranked_sources, start=1):
        if aliases & expected:
            return index
    return None


def _ndcg_at_k(ranked_sources: list[set[str]], expected_source_ids: list[str]) -> float:
    if not expected_source_ids or not ranked_sources:
        return 0.0
    expected = set(expected_source_ids)
    seen: set[str] = set()
    dcg = 0.0
    for rank, aliases in enumerate(ranked_sources, start=1):
        matched = sorted((aliases & expected) - seen)
        if matched:
            dcg += 1.0 / log2(rank + 1)
            seen.add(matched[0])
    ideal_hits = min(len(expected), len(ranked_sources))
    idcg = sum(1.0 / log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def _is_refusal_answer(answer: str) -> bool:
    return is_refusal_answer(answer)


def calculate_metrics(
    inputs: EvalMetricInputs,
    *,
    acl_leak_detected: bool = False,
) -> dict[str, Any]:
    answer = inputs.answer or ""
    trace_data = inputs.trace_data or {}
    expected_source_ids = _normalize_list(inputs.expected_source_ids)
    expected_keywords = _normalize_list(inputs.expected_keywords)
    tags = {tag.lower() for tag in _normalize_list(inputs.tags)}
    observed_source_ids = _trace_source_ids(trace_data)
    hit_source_ids = [
        source_id
        for source_id in expected_source_ids
        if source_id in observed_source_ids
    ]
    answer_lower = answer.lower()
    hit_keywords = [
        keyword for keyword in expected_keywords if keyword.lower() in answer_lower
    ]
    citation_chunks = trace_data.get("citation_chunks") or []
    ranked_chunks = _ranked_chunks(trace_data)
    retrieval_layer_counts = _layer_counts(ranked_chunks)
    ranked_sources, metric_k = _ranked_sources(trace_data)
    first_hit_rank = _first_hit_rank(ranked_sources, expected_source_ids)
    citation_hit_count = sum(
        1
        for chunk in citation_chunks
        if _chunk_source_aliases(chunk) & set(expected_source_ids)
    )
    durations = trace_data.get("durations_ms") or {}
    tokens = trace_data.get("tokens") or {}
    errors = trace_data.get("errors") or []
    verification = trace_data.get("answer_verification") or {}
    verification_retry = verification.get("retry") or {}
    graph_rag = trace_data.get("graph_rag") or {}
    error = inputs.error
    if not error and errors:
        error = str((errors[-1] or {}).get("message") or "")
    refusal_detected = _is_refusal_answer(answer)
    expected_refusal = "no_answer" in tags

    return {
        "answer_present": bool(answer.strip()),
        "citation_present": bool(citation_chunks or inputs.references),
        "expected_source_hit_rate": (
            len(hit_source_ids) / len(expected_source_ids)
            if expected_source_ids
            else None
        ),
        "expected_source_hit_count": len(hit_source_ids),
        "expected_source_total": len(expected_source_ids),
        "matched_source_ids": hit_source_ids,
        "hit_at_k": (first_hit_rank is not None if expected_source_ids else None),
        "hit_k": metric_k,
        "mrr": (1 / first_hit_rank if first_hit_rank else 0),
        "ndcg_at_k": (
            _ndcg_at_k(ranked_sources, expected_source_ids)
            if expected_source_ids
            else None
        ),
        "keyword_hit_rate": (
            len(hit_keywords) / len(expected_keywords) if expected_keywords else None
        ),
        "keyword_hit_count": len(hit_keywords),
        "keyword_total": len(expected_keywords),
        "matched_keywords": hit_keywords,
        "refusal_detected": refusal_detected,
        "expected_refusal": expected_refusal,
        "refusal_accuracy": refusal_detected if expected_refusal else None,
        "citation_support_rate": (
            citation_hit_count / len(citation_chunks) if citation_chunks else 0
        ),
        "summary_layer_context_count": retrieval_layer_counts["summary_layer"],
        "detail_layer_context_count": retrieval_layer_counts["detail_layer"],
        "graph_layer_context_count": retrieval_layer_counts["graph_layer"],
        "used_summary_layer": retrieval_layer_counts["summary_layer"] > 0,
        "graph_rag_enabled": bool(graph_rag.get("enabled")),
        "graph_entity_count": len(graph_rag.get("entities") or []),
        "graph_relationship_count": len(graph_rag.get("relationships") or []),
        "graph_path_count": len(graph_rag.get("paths") or []),
        "graph_answer_fragment_count": len(graph_rag.get("answer_fragments") or []),
        "multi_hop_graph_ready": (
            bool(graph_rag.get("enabled"))
            and len(graph_rag.get("paths") or []) > 0
            and len(graph_rag.get("relationships") or []) > 0
        ),
        "evidence_coverage": verification.get("evidence_coverage"),
        "evidence_supported_count": verification.get("supported_count"),
        "evidence_unsupported_count": verification.get("unsupported_count"),
        "evidence_insufficient_count": verification.get("insufficient_count"),
        "hallucination_risk_count": int(verification.get("unsupported_count") or 0)
        + int(verification.get("insufficient_count") or 0),
        "answer_verification_action": verification.get("final_action"),
        "answer_verification_retry": bool(verification_retry.get("triggered")),
        "acl_leak_detected": bool(acl_leak_detected),
        "latency_ms": int(durations.get("total") or 0),
        "prompt_tokens": int(tokens.get("prompt_tokens") or 0),
        "completion_tokens": int(tokens.get("completion_tokens") or 0),
        "total_tokens": int(tokens.get("total_tokens") or 0),
        "error": error or None,
    }


class RagEvaluationStore:
    def list_datasets(self, owner_user_id: str) -> list[RagEvalDataset]:
        with Session(engine) as session:
            statement = (
                select(RagEvalDataset)
                .where(RagEvalDataset.owner_user_id == owner_user_id)
                .order_by(RagEvalDataset.date_updated.desc())  # type: ignore[attr-defined]
            )
            return session.exec(statement).all()

    def get_dataset(self, dataset_id: str, owner_user_id: str) -> RagEvalDataset | None:
        with Session(engine) as session:
            return session.exec(
                select(RagEvalDataset).where(
                    RagEvalDataset.id == dataset_id,
                    RagEvalDataset.owner_user_id == owner_user_id,
                )
            ).one_or_none()

    def require_dataset(self, dataset_id: str, owner_user_id: str) -> RagEvalDataset:
        dataset = self.get_dataset(dataset_id, owner_user_id)
        if dataset is None:
            raise KeyError("dataset_not_found")
        return dataset

    def create_dataset(
        self,
        *,
        owner_user_id: str,
        name: str,
        description: str = "",
        tags: list[str] | None = None,
    ) -> RagEvalDataset:
        now = _now()
        row = RagEvalDataset(
            owner_user_id=owner_user_id,
            name=name.strip(),
            description=description or "",
            tags=_normalize_list(tags),
            date_created=now,
            date_updated=now,
        )
        with Session(engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def update_dataset(
        self,
        dataset_id: str,
        owner_user_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> RagEvalDataset:
        with Session(engine) as session:
            row = session.exec(
                select(RagEvalDataset).where(
                    RagEvalDataset.id == dataset_id,
                    RagEvalDataset.owner_user_id == owner_user_id,
                )
            ).one_or_none()
            if row is None:
                raise KeyError("dataset_not_found")
            if name is not None:
                row.name = name.strip()
            if description is not None:
                row.description = description
            if tags is not None:
                row.tags = _normalize_list(tags)
            row.date_updated = _now()
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def delete_dataset(self, dataset_id: str, owner_user_id: str) -> None:
        with Session(engine) as session:
            dataset = session.exec(
                select(RagEvalDataset).where(
                    RagEvalDataset.id == dataset_id,
                    RagEvalDataset.owner_user_id == owner_user_id,
                )
            ).one_or_none()
            if dataset is None:
                return
            for run in session.exec(
                select(RagEvalRun).where(RagEvalRun.dataset_id == dataset_id)
            ).all():
                session.delete(run)
            for example in session.exec(
                select(RagEvalExample).where(RagEvalExample.dataset_id == dataset_id)
            ).all():
                session.delete(example)
            session.delete(dataset)
            session.commit()

    def list_examples(
        self, dataset_id: str, owner_user_id: str
    ) -> list[RagEvalExample]:
        self.require_dataset(dataset_id, owner_user_id)
        with Session(engine) as session:
            statement = (
                select(RagEvalExample)
                .where(RagEvalExample.dataset_id == dataset_id)
                .order_by(RagEvalExample.date_created.asc())  # type: ignore[attr-defined]
            )
            return session.exec(statement).all()

    def get_example(self, example_id: str, owner_user_id: str) -> RagEvalExample | None:
        with Session(engine) as session:
            row = session.exec(
                select(RagEvalExample).where(RagEvalExample.id == example_id)
            ).one_or_none()
            if row is None:
                return None
            dataset = session.exec(
                select(RagEvalDataset).where(
                    RagEvalDataset.id == row.dataset_id,
                    RagEvalDataset.owner_user_id == owner_user_id,
                )
            ).one_or_none()
            return row if dataset is not None else None

    def require_example(self, example_id: str, owner_user_id: str) -> RagEvalExample:
        row = self.get_example(example_id, owner_user_id)
        if row is None:
            raise KeyError("example_not_found")
        return row

    def create_example(
        self,
        *,
        dataset_id: str,
        owner_user_id: str,
        question: str,
        evaluator_user_id: str,
        expected_answer: str | None = None,
        expected_source_ids: list[str] | None = None,
        expected_keywords: list[str] | None = None,
        selected_file_ids: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> RagEvalExample:
        self.require_dataset(dataset_id, owner_user_id)
        now = _now()
        row = RagEvalExample(
            dataset_id=dataset_id,
            question=question.strip(),
            expected_answer=expected_answer,
            expected_source_ids=_normalize_list(expected_source_ids),
            expected_keywords=_normalize_list(expected_keywords),
            evaluator_user_id=evaluator_user_id.strip() or owner_user_id,
            selected_file_ids=_normalize_list(selected_file_ids),
            tags=_normalize_list(tags),
            date_created=now,
            date_updated=now,
        )
        with Session(engine) as session:
            session.add(row)
            dataset = session.exec(
                select(RagEvalDataset).where(RagEvalDataset.id == dataset_id)
            ).one()
            dataset.date_updated = now
            session.add(dataset)
            session.commit()
            session.refresh(row)
            return row

    def update_example(
        self,
        example_id: str,
        owner_user_id: str,
        **values: Any,
    ) -> RagEvalExample:
        with Session(engine) as session:
            row = session.exec(
                select(RagEvalExample).where(RagEvalExample.id == example_id)
            ).one_or_none()
            if row is None:
                raise KeyError("example_not_found")
            dataset = session.exec(
                select(RagEvalDataset).where(
                    RagEvalDataset.id == row.dataset_id,
                    RagEvalDataset.owner_user_id == owner_user_id,
                )
            ).one_or_none()
            if dataset is None:
                raise KeyError("example_not_found")
            for field in (
                "question",
                "expected_answer",
                "evaluator_user_id",
            ):
                if field in values and values[field] is not None:
                    setattr(row, field, values[field])
            for field in (
                "expected_source_ids",
                "expected_keywords",
                "selected_file_ids",
                "tags",
            ):
                if field in values and values[field] is not None:
                    setattr(row, field, _normalize_list(values[field]))
            row.date_updated = _now()
            dataset.date_updated = row.date_updated
            session.add(row)
            session.add(dataset)
            session.commit()
            session.refresh(row)
            return row

    def delete_example(self, example_id: str, owner_user_id: str) -> None:
        with Session(engine) as session:
            row = session.exec(
                select(RagEvalExample).where(RagEvalExample.id == example_id)
            ).one_or_none()
            if row is None:
                return
            dataset = session.exec(
                select(RagEvalDataset).where(
                    RagEvalDataset.id == row.dataset_id,
                    RagEvalDataset.owner_user_id == owner_user_id,
                )
            ).one_or_none()
            if dataset is None:
                return
            session.delete(row)
            dataset.date_updated = _now()
            session.add(dataset)
            session.commit()

    def create_run(
        self,
        *,
        dataset_id: str,
        example_id: str | None,
        owner_user_id: str,
        evaluator_user_id: str,
        question: str,
        settings_snapshot: dict[str, Any] | None = None,
    ) -> RagEvalRun:
        self.require_dataset(dataset_id, owner_user_id)
        now = _now()
        row = RagEvalRun(
            dataset_id=dataset_id,
            example_id=example_id,
            owner_user_id=owner_user_id,
            evaluator_user_id=evaluator_user_id,
            question=question,
            settings_snapshot=settings_snapshot or {},
            date_created=now,
            date_updated=now,
        )
        with Session(engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        answer: str,
        references: list[dict[str, Any]],
        metrics: dict[str, Any],
        trace_id: str | None = None,
        error: str | None = None,
    ) -> RagEvalRun:
        with Session(engine) as session:
            row = session.exec(select(RagEvalRun).where(RagEvalRun.id == run_id)).one()
            row.status = status
            row.answer = answer
            row.references = references
            row.metrics = metrics
            row.trace_id = trace_id
            row.error = error
            row.date_updated = _now()
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def list_runs(
        self,
        *,
        owner_user_id: str,
        dataset_id: str | None = None,
        example_id: str | None = None,
        limit: int = 50,
    ) -> list[RagEvalRun]:
        with Session(engine) as session:
            statement = select(RagEvalRun).where(
                RagEvalRun.owner_user_id == owner_user_id
            )
            if dataset_id:
                statement = statement.where(RagEvalRun.dataset_id == dataset_id)
            if example_id:
                statement = statement.where(RagEvalRun.example_id == example_id)
            statement = statement.order_by(RagEvalRun.date_created.desc()).limit(limit)  # type: ignore[attr-defined]
            return session.exec(statement).all()

    def get_run(self, run_id: str, owner_user_id: str) -> RagEvalRun | None:
        with Session(engine) as session:
            return session.exec(
                select(RagEvalRun).where(
                    RagEvalRun.id == run_id,
                    RagEvalRun.owner_user_id == owner_user_id,
                )
            ).one_or_none()

    def detect_acl_leak(
        self,
        *,
        index: Any | None,
        trace_data: dict[str, Any],
        evaluator_user_id: str,
    ) -> bool:
        if index is None:
            return False
        source_ids = _trace_acl_source_ids(trace_data)
        if not source_ids:
            return False
        Source = (
            index._resources.get("Source") if hasattr(index, "_resources") else None
        )
        if Source is None:
            return False
        with Session(engine) as session:
            rows = session.execute(
                select(Source).where(Source.id.in_(source_ids))
            ).all()
        source_by_id = {str(source.id): source for (source,) in rows}
        for source_id in source_ids:
            source = source_by_id.get(source_id)
            if source is not None and not can_read_source(index, source, evaluator_user_id):
                return True
        return False


store = RagEvaluationStore()

from __future__ import annotations

import contextvars
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime
from typing import Any, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from ktem.db.engine import engine
from ktem.permissions import permission_service

from .models import RagTraceRun

_active_recorder: contextvars.ContextVar["RagTraceRecorder | None"] = (
    contextvars.ContextVar("rag_trace_recorder", default=None)
)

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _now() -> datetime:
    return datetime.now(CHINA_TZ)


def _elapsed_ms(start: float) -> int:
    return max(0, int((time.perf_counter() - start) * 1000))


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if key in {"window", "table_origin", "image_origin"}:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            output[key] = value
    return output


def _doc_source_id(doc: Any) -> str:
    metadata = getattr(doc, "metadata", {}) or {}
    return str(
        metadata.get("file_id")
        or metadata.get("source_id")
        or metadata.get("file_name")
        or ""
    )


def chunk_snapshot(
    doc: Any,
    include_text: bool = True,
    *,
    retrieval_channel: str | None = None,
    rank_before_fusion: int | None = None,
    rank_after_fusion: int | None = None,
    rank_after_rerank: int | None = None,
) -> dict[str, Any]:
    metadata = getattr(doc, "metadata", {}) or {}
    retrieval_metadata = getattr(doc, "retrieval_metadata", {}) or {}
    text = str(getattr(doc, "text", "") or "")
    item = {
        "chunk_id": str(getattr(doc, "doc_id", "") or getattr(doc, "id_", "") or ""),
        "source_id": _doc_source_id(doc),
        "source_name": str(metadata.get("file_name") or metadata.get("source") or ""),
        "page_label": (
            str(metadata.get("page_label"))
            if metadata.get("page_label") is not None
            else None
        ),
        "type": str(metadata.get("type") or "text"),
        "retrieval_layer": retrieval_metadata.get("retrieval_layer")
        or metadata.get("retrieval_layer")
        or (
            "summary_layer"
            if metadata.get("type") == "summary"
            else "detail_layer"
        ),
        "summary_layer": metadata.get("summary_layer"),
        "summary_scope": metadata.get("summary_scope"),
        "score": getattr(doc, "score", None),
        "reranking_score": metadata.get("reranking_score"),
        "llm_reranking_score": metadata.get("llm_trulens_score")
        or metadata.get("llm_reranking_score"),
        "retrieval_channel": retrieval_channel
        or retrieval_metadata.get("retrieval_channel"),
        "retrieval_channels": retrieval_metadata.get("retrieval_channels"),
        "vector_rank": retrieval_metadata.get("vector_rank"),
        "text_rank": retrieval_metadata.get("text_rank"),
        "rrf_score": retrieval_metadata.get("rrf_score"),
        "fusion_query": retrieval_metadata.get("fusion_query"),
        "fusion_query_index": retrieval_metadata.get("fusion_query_index"),
        "fusion_channel": retrieval_metadata.get("fusion_channel"),
        "fusion_query_hits": retrieval_metadata.get("fusion_query_hits"),
        "fusion_rank_contributions": retrieval_metadata.get(
            "fusion_rank_contributions"
        ),
        "final_rank": retrieval_metadata.get("final_rank"),
        "rank_before_fusion": (
            rank_before_fusion
            if rank_before_fusion is not None
            else retrieval_metadata.get("rank_before_fusion")
        ),
        "rank_after_fusion": (
            rank_after_fusion
            if rank_after_fusion is not None
            else retrieval_metadata.get("rank_after_fusion")
        ),
        "rank_after_rerank": (
            rank_after_rerank
            if rank_after_rerank is not None
            else retrieval_metadata.get("rank_after_rerank")
        ),
        "metadata": _safe_metadata(metadata),
    }
    if include_text:
        item["text"] = text
        item["excerpt"] = " ".join(text.split())[:500]
    return item


class StageTimer:
    def __init__(self, recorder: "RagTraceRecorder", stage: str):
        self.recorder = recorder
        self.stage = stage
        self.started = 0.0

    def __enter__(self):
        self.started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.recorder.add_duration(self.stage, _elapsed_ms(self.started))
        if exc is not None:
            self.recorder.record_error(self.stage, exc)
        return False


class RagTraceRecorder:
    def __init__(
        self,
        *,
        conversation_id: str,
        user_id: str,
        question: str,
        selected_file_ids: list[str],
        retrieval_params: dict[str, Any],
        effective_principal: dict[str, Any],
        turn_index: int | None = None,
    ) -> None:
        self.trace_id = uuid4().hex
        self.started = time.perf_counter()
        self._filtered_source_ids: set[str] = set()
        self.data: dict[str, Any] = {
            "trace_id": self.trace_id,
            "conversation_id": conversation_id,
            "message_id": None,
            "turn_index": turn_index,
            "user_id": user_id,
            "question": question,
            "selected_file_ids": selected_file_ids,
            "effective_principal": effective_principal,
            "retrieval_params": retrieval_params,
            "acl": {
                "pre_filter_source_count": 0,
                "post_filter_source_count": 0,
                "pre_filter_chunk_count": 0,
                "post_filter_chunk_count": 0,
                "filtered_source_count": 0,
                "filtered_reason_summary": {},
            },
            "original_question": question,
            "retrieval_query": question,
            "retrieval_enhancement": {
                "strategy": "none",
                "original_question": question,
                "rewritten_question": None,
                "hyde_document": None,
                "retrieval_query": question,
            },
            "query_rewrite": {"enabled": False, "rewritten_question": None},
            "hyde": {"enabled": False, "document": None},
            "rag_fusion": {
                "enabled": False,
                "queries": [],
                "raw_response": None,
            },
            "graph_rag": {
                "enabled": False,
                "provider": None,
                "search_type": None,
                "graph_ids": [],
                "entities": [],
                "relationships": [],
                "paths": [],
                "sources": [],
                "answer_fragments": [],
            },
            "rerank_enabled": False,
            "vector_candidate_chunks": [],
            "text_candidate_chunks": [],
            "fusion_query_candidates": [],
            "fused_candidate_chunks": [],
            "reranked_candidate_chunks": [],
            "candidate_chunks_before_rerank": [],
            "candidate_chunks_after_rerank": [],
            "context_chunks": [],
            "citation_chunks": [],
            "answer_verification": {
                "sentence_count": 0,
                "supported_count": 0,
                "unsupported_count": 0,
                "insufficient_count": 0,
                "evidence_coverage": 0.0,
                "checks": [],
                "gate": {
                    "status": "insufficient",
                    "should_retry": False,
                    "should_refuse": False,
                    "reason": "not_run",
                },
                "retry": {
                    "triggered": False,
                    "query": None,
                    "added_context_count": 0,
                },
                "final_action": "not_run",
            },
            "tokens": {
                "prompt_tokens": -1,
                "completion_tokens": -1,
                "total_tokens": -1,
            },
            "durations_ms": {
                "query_rewrite": 0,
                "hyde": 0,
                "rag_fusion_query_generation": 0,
                "retrieval": 0,
                "acl_filter": 0,
                "rerank": 0,
                "llm_generation": 0,
                "total": 0,
            },
            "errors": [],
            "status": "running",
            "created_at": _now().isoformat(),
        }

    def timer(self, stage: str) -> StageTimer:
        return StageTimer(self, stage)

    def add_duration(self, stage: str, elapsed_ms: int) -> None:
        durations = self.data.setdefault("durations_ms", {})
        durations[stage] = int(durations.get(stage, 0) or 0) + elapsed_ms

    def record_error(self, stage: str, exc: BaseException | str) -> None:
        message = str(exc)
        self.data.setdefault("errors", []).append(
            {
                "stage": stage,
                "type": (
                    type(exc).__name__ if isinstance(exc, BaseException) else "Error"
                ),
                "message": message,
            }
        )
        self.data["status"] = "failed"

    def record_acl_filter(
        self,
        *,
        index: Any,
        requested_source_ids: list[str],
        allowed_source_ids: list[str],
        user_id: str,
        pre_chunk_count: int,
        post_chunk_count: int,
    ) -> None:
        requested = [str(source_id) for source_id in requested_source_ids if source_id]
        allowed = [str(source_id) for source_id in allowed_source_ids if source_id]
        filtered_ids = [
            source_id for source_id in requested if source_id not in set(allowed)
        ]
        self._filtered_source_ids = set(filtered_ids)
        reasons = {
            source_id: permission_service.source_filter_reason(
                index, source_id, user_id
            )
            for source_id in filtered_ids
        }
        summary = Counter(reasons.values())
        self.data["selected_file_ids"] = allowed
        self.data["acl"] = {
            "pre_filter_source_count": len(requested),
            "post_filter_source_count": len(allowed),
            "pre_filter_chunk_count": pre_chunk_count,
            "post_filter_chunk_count": post_chunk_count,
            "filtered_source_count": len(filtered_ids),
            "filtered_reasons": [
                {"reason": reason, "count": count}
                for reason, count in sorted(summary.items())
            ],
            "filtered_reason_summary": dict(summary),
        }

    def _visible_docs(self, docs: Iterable[Any]) -> list[Any]:
        if not self._filtered_source_ids:
            return list(docs)
        return [
            doc for doc in docs if _doc_source_id(doc) not in self._filtered_source_ids
        ]

    def _chunk_snapshots(
        self,
        docs: Iterable[Any],
        *,
        retrieval_channel: str | None = None,
        rank_field: str | None = None,
    ) -> list[dict[str, Any]]:
        output = []
        for idx, doc in enumerate(self._visible_docs(docs), start=1):
            ranks = {
                "rank_before_fusion": None,
                "rank_after_fusion": None,
                "rank_after_rerank": None,
            }
            if rank_field:
                ranks[rank_field] = idx
            output.append(
                chunk_snapshot(
                    doc,
                    include_text=True,
                    retrieval_channel=retrieval_channel,
                    **ranks,
                )
            )
        return output

    def record_retrieval_candidates(
        self,
        *,
        vector_docs: Iterable[Any] | None = None,
        text_docs: Iterable[Any] | None = None,
        fused_docs: Iterable[Any] | None = None,
        fusion_query_candidates: Iterable[dict[str, Any]] | None = None,
    ) -> None:
        if vector_docs is not None:
            self.data["vector_candidate_chunks"] = self._chunk_snapshots(
                vector_docs,
                retrieval_channel="vector",
                rank_field="rank_before_fusion",
            )
        if text_docs is not None:
            self.data["text_candidate_chunks"] = self._chunk_snapshots(
                text_docs,
                retrieval_channel="text",
                rank_field="rank_before_fusion",
            )
        if fused_docs is not None:
            fused = self._chunk_snapshots(
                fused_docs,
                retrieval_channel=None,
                rank_field="rank_after_fusion",
            )
            self.data["fused_candidate_chunks"] = fused
            self.data["candidate_chunks_before_rerank"] = fused
        if fusion_query_candidates is not None:
            self.data["fusion_query_candidates"] = [
                {
                    "query": item.get("query"),
                    "query_index": item.get("query_index"),
                    "vector_candidate_chunks": self._chunk_snapshots(
                        item.get("vector_docs") or [],
                        retrieval_channel="vector",
                        rank_field="rank_before_fusion",
                    ),
                    "text_candidate_chunks": self._chunk_snapshots(
                        item.get("text_docs") or [],
                        retrieval_channel="text",
                        rank_field="rank_before_fusion",
                    ),
                    "fused_candidate_chunks": self._chunk_snapshots(
                        item.get("fused_docs") or [],
                        retrieval_channel=None,
                        rank_field="rank_after_fusion",
                    ),
                }
                for item in fusion_query_candidates
            ]

    def record_retrieval_enhancement(
        self,
        *,
        strategy: str,
        original_question: str,
        retrieval_query: str,
        rewritten_question: str | None = None,
        hyde_document: str | None = None,
        fusion_queries: list[str] | None = None,
        fusion_raw_response: str | None = None,
    ) -> None:
        normalized_strategy = (
            strategy if strategy in {"none", "rewrite", "hyde", "fusion"} else "none"
        )
        self.data["original_question"] = original_question
        self.data["retrieval_query"] = retrieval_query
        self.data["retrieval_enhancement"] = {
            "strategy": normalized_strategy,
            "original_question": original_question,
            "rewritten_question": rewritten_question,
            "hyde_document": hyde_document,
            "fusion_queries": fusion_queries or [],
            "retrieval_query": retrieval_query,
        }
        self.data["query_rewrite"] = {
            "enabled": normalized_strategy == "rewrite",
            "rewritten_question": rewritten_question,
        }
        self.data["hyde"] = {
            "enabled": normalized_strategy == "hyde",
            "document": hyde_document,
        }
        self.data["rag_fusion"] = {
            "enabled": normalized_strategy == "fusion",
            "queries": fusion_queries or [],
            "raw_response": fusion_raw_response,
        }

    def record_graph_retrieval(self, payload: dict[str, Any]) -> None:
        graph = self.data.setdefault(
            "graph_rag",
            {
                "enabled": False,
                "provider": None,
                "search_type": None,
                "graph_ids": [],
                "entities": [],
                "relationships": [],
                "paths": [],
                "sources": [],
                "answer_fragments": [],
            },
        )
        graph["enabled"] = bool(payload.get("enabled", True))
        graph["provider"] = payload.get("provider") or graph.get("provider")
        graph["search_type"] = payload.get("search_type") or graph.get("search_type")
        graph_id = payload.get("graph_id")
        if graph_id and graph_id not in graph.setdefault("graph_ids", []):
            graph["graph_ids"].append(graph_id)
        for key in ("entities", "relationships", "paths", "sources", "answer_fragments"):
            existing = graph.setdefault(key, [])
            for item in payload.get(key) or []:
                if item not in existing:
                    existing.append(item)

    def record_rerank(
        self,
        before_docs: Iterable[Any],
        after_docs: Iterable[Any],
        *,
        rerank_enabled: bool | None = None,
    ) -> None:
        if rerank_enabled is not None:
            self.data["rerank_enabled"] = bool(rerank_enabled)
        self.data["candidate_chunks_before_rerank"] = [
            chunk_snapshot(
                doc,
                include_text=True,
                retrieval_channel=None,
                rank_after_fusion=idx,
            )
            for idx, doc in enumerate(self._visible_docs(before_docs), start=1)
        ]
        self.data["fused_candidate_chunks"] = self.data[
            "candidate_chunks_before_rerank"
        ]
        self.data["reranked_candidate_chunks"] = [
            chunk_snapshot(
                doc,
                include_text=True,
                retrieval_channel="reranked",
                rank_after_rerank=idx,
            )
            for idx, doc in enumerate(self._visible_docs(after_docs), start=1)
        ]
        self.data["candidate_chunks_after_rerank"] = self.data[
            "reranked_candidate_chunks"
        ]

    def record_context(self, docs: Iterable[Any]) -> None:
        self.data["context_chunks"] = [
            chunk_snapshot(doc, include_text=True) for doc in self._visible_docs(docs)
        ]

    def record_answer(
        self,
        answer: Any,
        docs: Iterable[Any],
        cited_doc_ids: set[str] | None = None,
    ) -> None:
        metadata = getattr(answer, "metadata", {}) or {}
        self.data["tokens"] = {
            "prompt_tokens": metadata.get("prompt_tokens", -1),
            "completion_tokens": metadata.get("completion_tokens", -1),
            "total_tokens": metadata.get("total_tokens", -1),
        }
        cited_ids = cited_doc_ids or set()
        self.data["citation_chunks"] = [
            chunk_snapshot(doc, include_text=True)
            for doc in docs
            if str(getattr(doc, "doc_id", "")) in cited_ids
        ]

    def record_answer_verification(
        self,
        assessment: dict[str, Any],
        *,
        gate: Any | None = None,
        retry_triggered: bool = False,
        retry_query: str | None = None,
        retry_docs: Iterable[Any] | None = None,
        final_action: str = "accepted",
    ) -> None:
        retry_docs_list = list(retry_docs or [])
        gate_data = {
            "status": getattr(gate, "status", None),
            "should_retry": bool(getattr(gate, "should_retry", False)),
            "should_refuse": bool(getattr(gate, "should_refuse", False)),
            "reason": getattr(gate, "reason", None),
        }
        self.data["answer_verification"] = {
            "sentence_count": int(assessment.get("sentence_count") or 0),
            "supported_count": int(assessment.get("supported_count") or 0),
            "unsupported_count": int(assessment.get("unsupported_count") or 0),
            "insufficient_count": int(assessment.get("insufficient_count") or 0),
            "evidence_coverage": float(assessment.get("evidence_coverage") or 0.0),
            "checks": list(assessment.get("checks") or []),
            "gate": gate_data,
            "retry": {
                "triggered": bool(retry_triggered),
                "query": retry_query,
                "added_context_count": len(retry_docs_list),
                "added_context_chunks": self._chunk_snapshots(retry_docs_list),
            },
            "final_action": final_action,
        }

    def set_message(self, message_id: str) -> None:
        self.data["message_id"] = message_id

    def finish(self, status: str = "completed") -> dict[str, Any]:
        self.data["status"] = (
            status if self.data.get("status") != "failed" else "failed"
        )
        self.data["durations_ms"]["total"] = _elapsed_ms(self.started)
        self.data["updated_at"] = _now().isoformat()
        return self.data


def set_active_recorder(recorder: RagTraceRecorder | None):
    return _active_recorder.set(recorder)


def get_active_recorder() -> RagTraceRecorder | None:
    return _active_recorder.get()


def save_trace(data: dict[str, Any]) -> RagTraceRun:
    row = RagTraceRun(
        trace_id=str(data["trace_id"]),
        conversation_id=str(data["conversation_id"]),
        message_id=data.get("message_id"),
        turn_index=data.get("turn_index"),
        user_id=str(data["user_id"]),
        question=str(data.get("question") or ""),
        status=str(data.get("status") or "completed"),
        data=deepcopy(data),
        error=(
            (data.get("errors") or [{}])[-1].get("message")
            if data.get("errors")
            else None
        ),
        date_updated=_now(),
    )
    with Session(engine) as session:
        existing = session.exec(
            select(RagTraceRun).where(RagTraceRun.trace_id == row.trace_id)
        ).one_or_none()
        if existing:
            for field in (
                "conversation_id",
                "message_id",
                "turn_index",
                "user_id",
                "question",
                "status",
                "data",
                "error",
                "date_updated",
            ):
                setattr(existing, field, getattr(row, field))
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def list_conversation_traces(
    conversation_id: str, user_id: str, limit: int = 20
) -> list[RagTraceRun]:
    with Session(engine) as session:
        statement = (
            select(RagTraceRun)
            .where(
                RagTraceRun.conversation_id == conversation_id,
                RagTraceRun.user_id == user_id,
            )
            .order_by(RagTraceRun.date_created.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return session.exec(statement).all()


def get_trace(trace_id: str, user_id: str) -> RagTraceRun | None:
    with Session(engine) as session:
        statement = select(RagTraceRun).where(
            RagTraceRun.trace_id == trace_id,
            RagTraceRun.user_id == user_id,
        )
        return session.exec(statement).one_or_none()


def get_trace_by_message(message_id: str, user_id: str) -> RagTraceRun | None:
    with Session(engine) as session:
        statement = select(RagTraceRun).where(
            RagTraceRun.message_id == message_id,
            RagTraceRun.user_id == user_id,
        )
        return session.exec(statement).one_or_none()

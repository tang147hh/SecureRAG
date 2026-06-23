from __future__ import annotations

import contextvars
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime
from typing import Any, Iterable
from uuid import uuid4

from sqlmodel import Session, select
from tzlocal import get_localzone

from ktem.db.engine import engine
from ktem.permissions import permission_service

from .models import RagTraceRun

_active_recorder: contextvars.ContextVar["RagTraceRecorder | None"] = (
    contextvars.ContextVar("rag_trace_recorder", default=None)
)


def _now() -> datetime:
    return datetime.now(get_localzone())


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


def chunk_snapshot(doc: Any, include_text: bool = True) -> dict[str, Any]:
    metadata = getattr(doc, "metadata", {}) or {}
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
        "score": getattr(doc, "score", None),
        "reranking_score": metadata.get("reranking_score"),
        "llm_reranking_score": metadata.get("llm_trulens_score")
        or metadata.get("llm_reranking_score"),
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
                "filtered_source_ids": [],
                "filtered_reason_summary": {},
            },
            "query_rewrite": {"enabled": False, "rewritten_question": None},
            "candidate_chunks_before_rerank": [],
            "candidate_chunks_after_rerank": [],
            "context_chunks": [],
            "citation_chunks": [],
            "tokens": {
                "prompt_tokens": -1,
                "completion_tokens": -1,
                "total_tokens": -1,
            },
            "durations_ms": {
                "query_rewrite": 0,
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
                "type": type(exc).__name__ if isinstance(exc, BaseException) else "Error",
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
        filtered_ids = [source_id for source_id in requested if source_id not in set(allowed)]
        reasons = {
            source_id: permission_service.source_filter_reason(index, source_id, user_id)
            for source_id in filtered_ids
        }
        summary = Counter(reasons.values())
        self.data["acl"] = {
            "pre_filter_source_count": len(requested),
            "post_filter_source_count": len(allowed),
            "pre_filter_chunk_count": pre_chunk_count,
            "post_filter_chunk_count": post_chunk_count,
            "filtered_source_count": len(filtered_ids),
            "filtered_source_ids": filtered_ids,
            "filtered_reasons": [
                {"source_id": source_id, "reason": reason}
                for source_id, reason in reasons.items()
            ],
            "filtered_reason_summary": dict(summary),
        }

    def record_rerank(self, before_docs: Iterable[Any], after_docs: Iterable[Any]) -> None:
        self.data["candidate_chunks_before_rerank"] = [
            chunk_snapshot(doc, include_text=True) for doc in before_docs
        ]
        self.data["candidate_chunks_after_rerank"] = [
            chunk_snapshot(doc, include_text=True) for doc in after_docs
        ]

    def record_context(self, docs: Iterable[Any]) -> None:
        self.data["context_chunks"] = [
            chunk_snapshot(doc, include_text=True) for doc in docs
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

    def set_message(self, message_id: str) -> None:
        self.data["message_id"] = message_id

    def finish(self, status: str = "completed") -> dict[str, Any]:
        self.data["status"] = status if self.data.get("status") != "failed" else "failed"
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
        error=(data.get("errors") or [{}])[-1].get("message") if data.get("errors") else None,
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


def list_conversation_traces(conversation_id: str, user_id: str, limit: int = 20) -> list[RagTraceRun]:
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

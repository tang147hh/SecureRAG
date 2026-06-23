from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import Column, DateTime, Integer, JSON, String, create_engine
from sqlalchemy.orm import declarative_base
from sqlmodel import SQLModel

from ktem import react_api
from ktem.evaluation import EvalMetricInputs, calculate_metrics
from ktem.evaluation.models import RagEvalDataset, RagEvalExample, RagEvalRun
from ktem.permissions import permission_service
from ktem.permissions.models import SourcePermission
from ktem.trace import RagTraceRecorder, save_trace
from ktem.trace.models import RagTraceRun


def _engine(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr("ktem.evaluation.service.engine", test_engine)
    monkeypatch.setattr("ktem.trace.trace_service.engine", test_engine)
    monkeypatch.setattr(permission_service, "engine", test_engine)
    monkeypatch.setattr(react_api, "engine", test_engine)
    SQLModel.metadata.create_all(
        test_engine,
        tables=[
            RagEvalDataset.__table__,
            RagEvalExample.__table__,
            RagEvalRun.__table__,
            RagTraceRun.__table__,
            SourcePermission.__table__,
        ],
    )
    return test_engine


def test_create_dataset_and_example(monkeypatch):
    _engine(monkeypatch)
    service = react_api.ReactApiService()

    dataset = service.create_eval_dataset(
        react_api.RagEvalDatasetPayload(name="回归集", tags=["smoke"]),
        "alice",
    )
    example = service.create_eval_example(
        dataset.id,
        react_api.RagEvalExamplePayload(
            question="制度是什么？",
            expectedSourceIds=["doc-1"],
            expectedKeywords=["制度"],
            evaluatorUserId="bob",
            selectedFileIds=["doc-1"],
        ),
        "alice",
    )

    assert dataset.name == "回归集"
    assert example.evaluatorUserId == "bob"
    assert service.list_eval_examples(dataset.id, "alice")[0].expectedSourceIds == ["doc-1"]


def test_metric_hit_rates_are_calculated():
    metrics = calculate_metrics(
        EvalMetricInputs(
            answer="本制度要求加密存储。",
            references=[],
            trace_data={
                "context_chunks": [
                    {"source_id": "doc-1"},
                    {"source_id": "doc-3"},
                ],
                "citation_chunks": [{"source_id": "doc-1"}],
                "durations_ms": {"total": 123},
                "tokens": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            },
            expected_source_ids=["doc-1", "doc-2"],
            expected_keywords=["制度", "权限"],
        )
    )

    assert metrics["expected_source_hit_rate"] == 0.5
    assert metrics["keyword_hit_rate"] == 0.5
    assert metrics["citation_present"] is True
    assert metrics["latency_ms"] == 123


def test_run_example_generates_result_and_trace(monkeypatch):
    _engine(monkeypatch)
    service = react_api.ReactApiService()
    monkeypatch.setattr(service, "get_chat_settings", lambda user_id: react_api.ChatSettings())
    monkeypatch.setattr(service, "_file_index", lambda: None)

    def fake_run(payload, user_id, emit_token, **kwargs):
        recorder = RagTraceRecorder(
            conversation_id=payload.conversationId,
            user_id=user_id,
            question=payload.content,
            selected_file_ids=payload.selectedFileIds,
            retrieval_params={"topK": 10},
            effective_principal={"principal": {"type": "user", "id": user_id}},
        )
        recorder.set_message(kwargs["message_id"])
        recorder.record_context(
            [
                SimpleNamespace(
                    doc_id="chunk-1",
                    text="allowed text",
                    metadata={"file_id": "doc-1", "file_name": "policy.pdf"},
                )
            ]
        )
        trace_row = save_trace(recorder.finish("completed"))
        return react_api.RagPipelineRunResult(
            answerText="allowed text",
            formattedAnswer="allowed text",
            retrievalContent="",
            references=[],
            trace=service._trace_summary_to_api(trace_row),
            traceData=trace_row.data,
            messageId=kwargs["message_id"],
        )

    monkeypatch.setattr(service, "run_rag_once", fake_run)
    dataset = service.create_eval_dataset(
        react_api.RagEvalDatasetPayload(name="回归集"),
        "alice",
    )
    example = service.create_eval_example(
        dataset.id,
        react_api.RagEvalExamplePayload(
            question="制度是什么？",
            expectedSourceIds=["doc-1"],
            expectedKeywords=["allowed"],
            evaluatorUserId="bob",
        ),
        "alice",
    )

    run = service.run_eval_example(example.id, "alice")

    assert run.status == "completed"
    assert run.traceId is not None
    assert run.trace is not None
    assert run.metrics["expected_source_hit_rate"] == 1
    assert run.metrics["keyword_hit_rate"] == 1


def test_evaluator_acl_detects_hidden_source_and_result_omits_hidden_text(monkeypatch):
    test_engine = _engine(monkeypatch)
    Base = declarative_base()

    class Source(Base):
        __tablename__ = "eval_source"

        id = Column(String, primary_key=True)
        name = Column(String)
        path = Column(String)
        size = Column(Integer, default=0)
        date_created = Column(DateTime, default=datetime.utcnow)
        user = Column(String, default="")
        note = Column(JSON, default={})

    Base.metadata.create_all(test_engine)
    index = SimpleNamespace(
        id=1,
        config={"private": True},
        _resources={"Source": Source},
        Source=Source,
    )
    with permission_service.Session(test_engine) as session:
        session.add_all(
            [
                Source(id="owned", name="owned.pdf", user="bob", note={}),
                Source(id="hidden", name="hidden.pdf", user="alice", note={}),
            ]
        )
        session.commit()
    permission_service.ensure_default_acl(index, Source(id="owned", name="owned.pdf", user="bob"))
    permission_service.ensure_default_acl(index, Source(id="hidden", name="hidden.pdf", user="alice"))

    leak = react_api.eval_store.detect_acl_leak(
        index=index,
        trace_data={"context_chunks": [{"source_id": "hidden", "text": "secret hidden text"}]},
        evaluator_user_id="bob",
    )
    metrics = calculate_metrics(
        EvalMetricInputs(
            answer="allowed summary",
            references=[],
            trace_data={"context_chunks": [{"source_id": "owned", "text": "allowed text"}]},
            expected_source_ids=["hidden"],
            expected_keywords=["allowed"],
        ),
        acl_leak_detected=leak,
    )

    assert leak is True
    assert metrics["acl_leak_detected"] is True
    assert "secret hidden text" not in str({"answer": "allowed summary", "metrics": metrics})

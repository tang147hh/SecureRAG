from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import Column, DateTime, Integer, JSON, String, create_engine
from sqlalchemy.orm import declarative_base
from sqlmodel import SQLModel

from ktem.index.file.pipelines import DocumentRetrievalPipeline
from ktem.permissions.models import SourcePermission
from ktem.permissions import permission_service
from ktem.trace import RagTraceRecorder, get_trace_by_message, save_trace, set_active_recorder
from ktem.trace.models import RagTraceRun
from ktem import react_api


class DummyRetriever:
    def __call__(self, text, top_k, **kwargs):
        return [SimpleNamespace(doc_id="chunk-owned", text="allowed text", metadata={"file_id": "owned"})]


class TraceableTestRetrievalPipeline(DocumentRetrievalPipeline):
    @property
    def vector_retrieval(self):
        return DummyRetriever()


def _tables(test_engine):
    Base = declarative_base()

    class Source(Base):
        __tablename__ = "trace_source"

        id = Column(String, primary_key=True)
        name = Column(String)
        path = Column(String)
        size = Column(Integer, default=0)
        date_created = Column(DateTime, default=datetime.utcnow)
        user = Column(String, default="")
        note = Column(JSON, default={})

    class IndexTable(Base):
        __tablename__ = "trace_index"

        id = Column(Integer, primary_key=True)
        source_id = Column(String)
        target_id = Column(String)
        relation_type = Column(String)
        user = Column(String, default="")

    Base.metadata.create_all(test_engine)
    return Source, IndexTable


def test_trace_records_retrieval_params_and_durations(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr("ktem.trace.trace_service.engine", test_engine)
    SQLModel.metadata.create_all(test_engine, tables=[RagTraceRun.__table__])

    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="question",
        selected_file_ids=["owned"],
        retrieval_params={"topK": 3, "retrievalMode": "hybrid", "rerank": True},
        effective_principal={"principal": {"type": "user", "id": "alice"}},
        turn_index=0,
    )
    with recorder.timer("retrieval"):
        pass
    recorder.set_message("conv-0-assistant")
    row = save_trace(recorder.finish("completed"))

    assert row.data["retrieval_params"]["topK"] == 3
    assert row.data["durations_ms"]["retrieval"] >= 0
    assert row.data["durations_ms"]["total"] >= 0
    assert get_trace_by_message("conv-0-assistant", "alice").trace_id == row.trace_id


def test_trace_records_acl_filter_counts(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(permission_service, "engine", test_engine)
    monkeypatch.setattr("ktem.index.file.pipelines.engine", test_engine)
    SQLModel.metadata.create_all(test_engine, tables=[SourcePermission.__table__])
    Source, IndexTable = _tables(test_engine)

    with permission_service.Session(test_engine) as session:
        session.add_all(
            [
                Source(id="owned", name="owned.pdf", user="alice"),
                Source(id="hidden", name="hidden.pdf", user="bob"),
                IndexTable(source_id="owned", target_id="chunk-owned", relation_type="document"),
                IndexTable(source_id="hidden", target_id="chunk-hidden", relation_type="document"),
            ]
        )
        session.commit()

    index = SimpleNamespace(index_id=1, private=True, Source=Source, config={"private": True})
    permission_service.ensure_default_acl(index, Source(id="owned", name="owned.pdf", user="alice"))
    permission_service.ensure_default_acl(index, Source(id="hidden", name="hidden.pdf", user="bob"))

    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="question",
        selected_file_ids=["hidden", "owned"],
        retrieval_params={},
        effective_principal={},
    )
    set_active_recorder(recorder)
    try:
        pipeline = TraceableTestRetrievalPipeline()
        pipeline.Index = IndexTable
        pipeline.Source = Source
        pipeline.PermissionService = permission_service
        pipeline.index_id = 1
        pipeline.private = True
        pipeline.user_id = "alice"
        pipeline.top_k = 5
        pipeline.get_extra_table = False
        pipeline.mmr = False

        pipeline.run("query", doc_ids=["hidden", "owned"])
    finally:
        set_active_recorder(None)

    acl = recorder.data["acl"]
    assert acl["pre_filter_source_count"] == 2
    assert acl["post_filter_source_count"] == 1
    assert acl["pre_filter_chunk_count"] == 2
    assert acl["post_filter_chunk_count"] == 1
    assert acl["filtered_source_ids"] == ["hidden"]


def test_filtered_chunk_text_never_appears_in_trace_api(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(permission_service, "engine", test_engine)
    monkeypatch.setattr("ktem.trace.trace_service.engine", test_engine)
    SQLModel.metadata.create_all(test_engine, tables=[RagTraceRun.__table__, SourcePermission.__table__])
    Source, _ = _tables(test_engine)
    with permission_service.Session(test_engine) as session:
        session.add_all(
            [
                Source(id="owned", name="owned.pdf", user="alice"),
                Source(id="hidden", name="hidden.pdf", user="bob"),
            ]
        )
        session.commit()

    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="alice",
        question="question",
        selected_file_ids=["hidden", "owned"],
        retrieval_params={},
        effective_principal={},
    )
    recorder.record_acl_filter(
        index=SimpleNamespace(index_id=1, Source=Source, config={"private": True}),
        requested_source_ids=["hidden", "owned"],
        allowed_source_ids=["owned"],
        user_id="alice",
        pre_chunk_count=2,
        post_chunk_count=1,
    )
    recorder.record_context(
        [SimpleNamespace(doc_id="chunk-owned", text="allowed text", metadata={"file_id": "owned"})]
    )
    recorder.set_message("conv-0-assistant")
    row = save_trace(recorder.finish("completed"))
    serialized = str(row.data)

    assert "allowed text" in serialized
    assert "secret hidden text" not in serialized
    assert row.data["acl"]["filtered_source_ids"] == ["hidden"]


def test_missing_message_trace_returns_none(monkeypatch):
    monkeypatch.setattr(react_api, "get_trace_by_message", lambda message_id, user_id: None)

    service = react_api.ReactApiService()

    assert service.get_message_trace("legacy-message", "alice") is None

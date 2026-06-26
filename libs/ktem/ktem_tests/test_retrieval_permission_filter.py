from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import Column, DateTime, Integer, JSON, String, create_engine
from sqlalchemy.orm import declarative_base
from sqlmodel import SQLModel

from ktem.index.file.pipelines import DocumentRetrievalPipeline
from ktem.permissions.models import SourcePermission
from ktem.permissions import permission_service
from ktem.trace import RagTraceRecorder, set_active_recorder
from kotaemon.base import Document
from kotaemon.indices import VectorRetrieval


class DummyRetriever:
    def __call__(self, text, top_k, **kwargs):
        return [SimpleNamespace(text=text, top_k=top_k, kwargs=kwargs)]


class DummyRetrievalPipeline(DocumentRetrievalPipeline):
    @property
    def vector_retrieval(self):
        return DummyRetriever()


class FakeEmbedding:
    def __call__(self, text):
        return [SimpleNamespace(embedding=[0.1, 0.2])]


class ScopedVectorStore:
    def __init__(self, chunk_ids):
        self.chunk_ids = chunk_ids
        self.seen_doc_ids = None

    def query(self, embedding, top_k=1, doc_ids=None, **kwargs):
        self.seen_doc_ids = list(doc_ids or [])
        ids = [
            chunk_id for chunk_id in self.chunk_ids if chunk_id in set(doc_ids or [])
        ]
        ids = ids[:top_k]
        return [], [0.9 - idx * 0.1 for idx, _ in enumerate(ids)], ids


class ScopedDocStore:
    def __init__(self, docs):
        self.docs = docs
        self.seen_query_doc_ids = None

    def get(self, ids):
        return [self.docs[doc_id] for doc_id in ids if doc_id in self.docs]

    def query(self, query, top_k=10, doc_ids=None):
        self.seen_query_doc_ids = list(doc_ids or [])
        return self.get(list(doc_ids or [])[:top_k])


class EchoReranker:
    def run(self, documents, query):
        return list(documents)


class EndToEndAclRetrievalPipeline(DocumentRetrievalPipeline):
    @property
    def vector_retrieval(self):
        return self._vector_retrieval


def test_document_retrieval_filters_doc_ids_before_vector_search(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(permission_service, "engine", test_engine)
    monkeypatch.setattr("ktem.index.file.pipelines.engine", test_engine)
    SQLModel.metadata.create_all(test_engine, tables=[SourcePermission.__table__])

    Base = declarative_base()

    class Source(Base):
        __tablename__ = "test_source"

        id = Column(String, primary_key=True)
        name = Column(String)
        path = Column(String)
        size = Column(Integer, default=0)
        date_created = Column(DateTime, default=datetime.utcnow)
        user = Column(String, default="")
        note = Column(JSON, default={})

    class IndexTable(Base):
        __tablename__ = "test_index"

        id = Column(Integer, primary_key=True)
        source_id = Column(String)
        target_id = Column(String)
        relation_type = Column(String)
        user = Column(String, default="")

    Base.metadata.create_all(test_engine)
    with permission_service.Session(test_engine) as session:
        session.add_all(
            [
                Source(id="owned", name="owned.pdf", user="alice"),
                Source(id="hidden", name="hidden.pdf", user="bob"),
            ]
        )
        session.add_all(
            [
                IndexTable(
                    source_id="owned", target_id="chunk-owned", relation_type="document"
                ),
                IndexTable(
                    source_id="hidden",
                    target_id="chunk-hidden",
                    relation_type="document",
                ),
            ]
        )
        session.commit()

    index = SimpleNamespace(
        index_id=1,
        private=True,
        Source=Source,
        Index=IndexTable,
        PermissionService=permission_service,
        user_id="alice",
        top_k=5,
        get_extra_table=False,
        mmr=False,
    )
    permission_service.ensure_default_acl(
        index, Source(id="owned", name="owned.pdf", user="alice")
    )
    permission_service.ensure_default_acl(
        index, Source(id="hidden", name="hidden.pdf", user="bob")
    )

    pipeline = DummyRetrievalPipeline()
    pipeline.Index = IndexTable
    pipeline.Source = Source
    pipeline.PermissionService = permission_service
    pipeline.index_id = 1
    pipeline.private = True
    pipeline.user_id = "alice"
    pipeline.top_k = 5
    pipeline.get_extra_table = False
    pipeline.mmr = False

    docs = pipeline.run("query", doc_ids=["hidden", "owned"])

    assert docs[0].kwargs["scope"] == ["chunk-owned"]
    metadata_filter = docs[0].kwargs["filters"].filters[0]
    assert metadata_filter.value == ["owned"]


def test_two_user_acl_e2e_keeps_hidden_doc_out_of_all_rag_stages(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(permission_service, "engine", test_engine)
    monkeypatch.setattr("ktem.index.file.pipelines.engine", test_engine)
    SQLModel.metadata.create_all(test_engine, tables=[SourcePermission.__table__])

    Base = declarative_base()

    class Source(Base):
        __tablename__ = "acl_e2e_source"

        id = Column(String, primary_key=True)
        name = Column(String)
        path = Column(String)
        size = Column(Integer, default=0)
        date_created = Column(DateTime, default=datetime.utcnow)
        user = Column(String, default="")
        note = Column(JSON, default={})

    class IndexTable(Base):
        __tablename__ = "acl_e2e_index"

        id = Column(Integer, primary_key=True)
        source_id = Column(String)
        target_id = Column(String)
        relation_type = Column(String)
        user = Column(String, default="")

    Base.metadata.create_all(test_engine)
    with permission_service.Session(test_engine) as session:
        session.add_all(
            [
                Source(id="source-a", name="alice-private.pdf", user="alice"),
                Source(id="source-b", name="bob-visible.pdf", user="bob"),
            ]
        )
        session.add_all(
            [
                IndexTable(
                    source_id="source-a",
                    target_id="chunk-a",
                    relation_type="document",
                ),
                IndexTable(
                    source_id="source-b",
                    target_id="chunk-b",
                    relation_type="document",
                ),
            ]
        )
        session.commit()

    index = SimpleNamespace(index_id=1, private=True, Source=Source)
    permission_service.ensure_default_acl(
        index, Source(id="source-a", name="alice-private.pdf", user="alice")
    )
    permission_service.ensure_default_acl(
        index, Source(id="source-b", name="bob-visible.pdf", user="bob")
    )

    hidden_text = "ALICE_SECRET_PAYROLL_TOKEN"
    allowed_text = "Bob visible handbook paragraph"
    docs = {
        "chunk-a": Document(
            text=hidden_text,
            id_="chunk-a",
            metadata={"file_id": "source-a", "file_name": "alice-private.pdf"},
        ),
        "chunk-b": Document(
            text=allowed_text,
            id_="chunk-b",
            metadata={"file_id": "source-b", "file_name": "bob-visible.pdf"},
        ),
    }
    vector_store = ScopedVectorStore(["chunk-a", "chunk-b"])
    doc_store = ScopedDocStore(docs)
    vector_retrieval = VectorRetrieval(
        embedding=FakeEmbedding(),
        vector_store=vector_store,
        doc_store=doc_store,
        retrieval_mode="hybrid",
        rerankers=[EchoReranker()],
    )

    pipeline = EndToEndAclRetrievalPipeline()
    pipeline._vector_retrieval = vector_retrieval
    pipeline.Index = IndexTable
    pipeline.Source = Source
    pipeline.PermissionService = permission_service
    pipeline.index_id = 1
    pipeline.private = True
    pipeline.user_id = "bob"
    pipeline.top_k = 5
    pipeline.get_extra_table = False
    pipeline.mmr = False

    recorder = RagTraceRecorder(
        conversation_id="conv",
        user_id="bob",
        question="payroll policy?",
        selected_file_ids=["source-a", "source-b"],
        retrieval_params={
            "topK": 5,
            "retrievalMode": "hybrid",
            "rerank": True,
            "promptTemplateText": "Use only visible context.",
        },
        effective_principal={"principal": {"type": "user", "id": "bob"}},
    )
    set_active_recorder(recorder)
    try:
        retrieved_docs = pipeline.run(
            "payroll policy?", doc_ids=["source-a", "source-b"]
        )
    finally:
        set_active_recorder(None)

    recorder.record_context(retrieved_docs)
    recorder.record_answer(
        SimpleNamespace(
            metadata={
                "prompt_tokens": 3,
                "completion_tokens": 4,
                "total_tokens": 7,
            }
        ),
        retrieved_docs,
        cited_doc_ids={str(doc.doc_id) for doc in retrieved_docs},
    )

    assert vector_store.seen_doc_ids == ["chunk-b"]
    assert doc_store.seen_query_doc_ids == ["chunk-b"]
    assert [doc.metadata["file_id"] for doc in retrieved_docs] == [
        "source-b",
        "source-b",
    ]
    assert recorder.data["acl"]["filtered_source_count"] == 1
    assert recorder.data["selected_file_ids"] == ["source-b"]

    stage_payloads = {
        "vector": recorder.data["vector_candidate_chunks"],
        "text": recorder.data["text_candidate_chunks"],
        "fused": recorder.data["fused_candidate_chunks"],
        "rerank": recorder.data["reranked_candidate_chunks"],
        "context": recorder.data["context_chunks"],
        "citation": recorder.data["citation_chunks"],
        "prompt": recorder.data["retrieval_params"],
        "log": recorder.data,
    }
    for stage, payload in stage_payloads.items():
        serialized = str(payload)
        assert hidden_text not in serialized, stage
        assert "source-a" not in serialized, stage

    visible_source_ids = {
        chunk["source_id"]
        for key in (
            "vector_candidate_chunks",
            "text_candidate_chunks",
            "fused_candidate_chunks",
            "reranked_candidate_chunks",
            "context_chunks",
            "citation_chunks",
        )
        for chunk in recorder.data[key]
    }
    assert visible_source_ids == {"source-b"}

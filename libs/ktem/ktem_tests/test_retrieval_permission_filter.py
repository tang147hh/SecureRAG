from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import Column, DateTime, Integer, JSON, String, create_engine
from sqlalchemy.orm import declarative_base
from sqlmodel import SQLModel

from ktem.index.file.pipelines import DocumentRetrievalPipeline
from ktem.permissions.models import SourcePermission
from ktem.permissions import permission_service


class DummyRetriever:
    def __call__(self, text, top_k, **kwargs):
        return [SimpleNamespace(text=text, top_k=top_k, kwargs=kwargs)]


class DummyRetrievalPipeline(DocumentRetrievalPipeline):
    @property
    def vector_retrieval(self):
        return DummyRetriever()


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
                IndexTable(source_id="owned", target_id="chunk-owned", relation_type="document"),
                IndexTable(source_id="hidden", target_id="chunk-hidden", relation_type="document"),
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

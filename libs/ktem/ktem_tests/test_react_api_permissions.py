from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import Column, DateTime, Integer, JSON, String, create_engine
from sqlalchemy.orm import declarative_base
from sqlmodel import SQLModel

from ktem.permissions.models import SourcePermission
from ktem.permissions import permission_service
from ktem import react_api


class DummyDocStore:
    def get(self, ids):
        return []


def _service(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(permission_service, "engine", test_engine)
    monkeypatch.setattr(react_api, "engine", test_engine)
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

    class FileGroup(Base):
        __tablename__ = "test_group"

        id = Column(String, primary_key=True)
        name = Column(String)
        user = Column(String, default="")
        date_created = Column(DateTime, default=datetime.utcnow)
        data = Column(JSON, default={"files": []})

    Base.metadata.create_all(test_engine)
    index = SimpleNamespace(
        id=1,
        name="Files",
        config={"private": True},
        _resources={"Source": Source, "Index": IndexTable, "FileGroup": FileGroup},
        _docstore=DummyDocStore(),
    )
    service = react_api.ReactApiService()
    service.app_runtime = SimpleNamespace(index_manager=SimpleNamespace(indices=[index]))
    return service, index, Source, test_engine


def test_list_workspace_filters_by_acl(monkeypatch):
    service, index, Source, test_engine = _service(monkeypatch)
    with react_api.Session(test_engine) as session:
        session.add_all(
            [
                Source(id="owned", name="owned.pdf", user="alice", note={}),
                Source(id="hidden", name="hidden.pdf", user="bob", note={}),
            ]
        )
        session.commit()

    owned = Source(id="owned", name="owned.pdf", user="alice", note={})
    hidden = Source(id="hidden", name="hidden.pdf", user="bob", note={})
    permission_service.ensure_default_acl(index, owned)
    permission_service.ensure_default_acl(index, hidden)

    workspace = service.list_file_workspace("alice")
    assert [file.id for file in workspace.files] == ["owned"]
    assert workspace.files[0].permission == "owner"


def test_owner_can_grant_public_read(monkeypatch):
    service, index, Source, test_engine = _service(monkeypatch)
    with react_api.Session(test_engine) as session:
        session.add(Source(id="doc", name="doc.pdf", user="alice", note={}))
        session.commit()

    source = Source(id="doc", name="doc.pdf", user="alice", note={})
    permission_service.ensure_default_acl(index, source)
    payload = react_api.UpdateFilePermissionsPayload(
        permissions=[
            react_api.SourcePermissionItem(
                principalType="public", principalId="*", permission="read"
            )
        ]
    )
    detail = service.update_file_permissions("doc", payload, "alice")

    assert detail.file.permission == "owner"
    assert permission_service.can_read_source(index, source, "bob")

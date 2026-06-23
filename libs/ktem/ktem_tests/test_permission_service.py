from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import Column, DateTime, Integer, JSON, String, create_engine
from sqlalchemy.orm import declarative_base
from sqlmodel import SQLModel

from ktem.permissions.models import SourcePermission
from ktem.permissions import permission_service


def _index(monkeypatch, private: bool = True):
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(permission_service, "engine", test_engine)
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

    Base.metadata.create_all(test_engine)
    return SimpleNamespace(
        id=1,
        config={"private": private},
        _resources={"Source": Source},
    ), Source, test_engine


def test_resolve_principal_defaults_to_user_default():
    principal = permission_service.resolve_principal(None)
    assert principal.type == "user"
    assert principal.id == "default"


def test_default_acl_owner_can_read_but_other_user_cannot(monkeypatch):
    index, Source, _ = _index(monkeypatch, private=True)
    source = Source(id="src-1", name="a.pdf", user="alice")

    permission_service.ensure_default_acl(index, source)

    assert permission_service.can_read_source(index, source, "alice")
    assert not permission_service.can_read_source(index, source, "bob")


def test_filter_source_ids_keeps_order_and_removes_unreadable(monkeypatch):
    index, Source, test_engine = _index(monkeypatch, private=True)
    with permission_service.Session(test_engine) as session:
        session.add_all(
            [
                Source(id="owned", name="owned.pdf", user="alice"),
                Source(id="hidden", name="hidden.pdf", user="bob"),
            ]
        )
        session.commit()

    owned = Source(id="owned", name="owned.pdf", user="alice")
    hidden = Source(id="hidden", name="hidden.pdf", user="bob")
    permission_service.ensure_default_acl(index, owned)
    permission_service.ensure_default_acl(index, hidden)

    assert permission_service.filter_source_ids(
        index, ["hidden", "owned", "missing"], "alice"
    ) == ["owned"]

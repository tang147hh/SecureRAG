from __future__ import annotations

from typing import Any

from sqlalchemy import inspect
from sqlmodel import Session, select

from ktem.db.engine import engine
from ktem.index.models import Index
from ktem.permissions.permission_service import ensure_default_acl


def iter_file_indices() -> list[Any]:
    from ktem.index.file import FileIndex

    indices = []
    with Session(engine) as session:
        for index_def in session.exec(select(Index)).all():
            index_type = str(index_def.index_type)
            if "FileIndex" not in index_type and "GraphRAGIndex" not in index_type:
                continue
            index = FileIndex(
                app=None,
                id=index_def.id,
                name=index_def.name,
                config=index_def.config or {},
            )
            index.on_start()
            indices.append(index)
    return indices


def backfill_source_permissions() -> int:
    inspector = inspect(engine)
    count = 0
    for index in iter_file_indices():
        Source = index._resources.get("Source")
        if Source is None or not inspector.has_table(Source.__tablename__):
            continue
        with Session(engine) as session:
            sources = session.execute(select(Source)).all()
        for (source,) in sources:
            ensure_default_acl(index, source)
            count += 1
    return count


if __name__ == "__main__":
    total = backfill_source_permissions()
    print(f"Backfilled permissions for {total} indexed sources.")

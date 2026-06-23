from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel
from tzlocal import get_localzone


class SourcePermission(SQLModel, table=True):
    __tablename__ = "ktem__source_permission"  # type: ignore
    __table_args__ = (
        UniqueConstraint(
            "index_id",
            "source_id",
            "principal_type",
            "principal_id",
            name="uq_source_permission_principal",
        ),
        {"extend_existing": True},
    )

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    index_id: int = Field(index=True)
    source_id: str = Field(index=True)
    principal_type: str = Field(index=True)
    principal_id: str = Field(index=True)
    permission: str = Field(index=True)
    created_by: Optional[str] = Field(default=None)
    date_created: datetime = Field(
        default_factory=lambda: datetime.now(get_localzone())
    )
    date_updated: datetime = Field(
        default_factory=lambda: datetime.now(get_localzone())
    )

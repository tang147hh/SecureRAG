from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _now() -> datetime:
    return datetime.now(CHINA_TZ)


class RagTraceRun(SQLModel, table=True):
    __tablename__ = "ktem__rag_trace"  # type: ignore
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_rag_trace_message_id"),
        {"extend_existing": True},
    )

    trace_id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    conversation_id: str = Field(index=True)
    message_id: Optional[str] = Field(default=None, index=True)
    turn_index: Optional[int] = Field(default=None, index=True)
    user_id: str = Field(index=True)
    question: str
    status: str = Field(default="running", index=True)
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: Optional[str] = Field(default=None)
    date_created: datetime = Field(default_factory=_now, index=True)
    date_updated: datetime = Field(default_factory=_now)

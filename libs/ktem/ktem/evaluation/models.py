from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _now() -> datetime:
    return datetime.now(CHINA_TZ)


class RagEvalDataset(SQLModel, table=True):
    __tablename__ = "ktem__rag_eval_dataset"  # type: ignore
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    owner_user_id: str = Field(index=True)
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    date_created: datetime = Field(default_factory=_now, index=True)
    date_updated: datetime = Field(default_factory=_now)


class RagEvalExample(SQLModel, table=True):
    __tablename__ = "ktem__rag_eval_example"  # type: ignore
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    dataset_id: str = Field(index=True)
    question: str
    expected_answer: Optional[str] = None
    expected_source_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    expected_keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    evaluator_user_id: str = Field(index=True)
    selected_file_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    date_created: datetime = Field(default_factory=_now, index=True)
    date_updated: datetime = Field(default_factory=_now)


class RagEvalRun(SQLModel, table=True):
    __tablename__ = "ktem__rag_eval_run"  # type: ignore
    __table_args__ = {"extend_existing": True}

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    dataset_id: str = Field(index=True)
    example_id: Optional[str] = Field(default=None, index=True)
    owner_user_id: str = Field(index=True)
    evaluator_user_id: str = Field(index=True)
    status: str = Field(default="running", index=True)
    question: str
    answer: str = ""
    references: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    metrics: dict = Field(default_factory=dict, sa_column=Column(JSON))
    settings_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    trace_id: Optional[str] = Field(default=None, index=True)
    error: Optional[str] = None
    date_created: datetime = Field(default_factory=_now, index=True)
    date_updated: datetime = Field(default_factory=_now)

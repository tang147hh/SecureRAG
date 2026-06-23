"""add rag trace runs

Revision ID: 20260623_0002
Revises: 20260623_0001
Create Date: 2026-06-23
"""

from typing import Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "20260623_0002"
down_revision: Union[str, None] = "20260623_0001"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "ktem__rag_trace",
        sa.Column("trace_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("conversation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("message_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("turn_index", sa.Integer(), nullable=True),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("question", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("date_created", sa.DateTime(), nullable=False),
        sa.Column("date_updated", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("trace_id"),
        sa.UniqueConstraint("message_id", name="uq_rag_trace_message_id"),
    )
    op.create_index(
        "ix_rag_trace_conversation_created",
        "ktem__rag_trace",
        ["conversation_id", "date_created"],
        unique=False,
    )
    op.create_index(
        "ix_rag_trace_message",
        "ktem__rag_trace",
        ["message_id"],
        unique=False,
    )
    op.create_index(
        "ix_rag_trace_user",
        "ktem__rag_trace",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rag_trace_user", table_name="ktem__rag_trace")
    op.drop_index("ix_rag_trace_message", table_name="ktem__rag_trace")
    op.drop_index("ix_rag_trace_conversation_created", table_name="ktem__rag_trace")
    op.drop_table("ktem__rag_trace")

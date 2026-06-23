"""add rag evaluation center tables

Revision ID: 20260623_0003
Revises: 20260623_0002
Create Date: 2026-06-23
"""

from typing import Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "20260623_0003"
down_revision: Union[str, None] = "20260623_0002"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "ktem__rag_eval_dataset",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("owner_user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("date_created", sa.DateTime(), nullable=False),
        sa.Column("date_updated", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rag_eval_dataset_owner",
        "ktem__rag_eval_dataset",
        ["owner_user_id", "date_updated"],
        unique=False,
    )

    op.create_table(
        "ktem__rag_eval_example",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("dataset_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("question", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expected_answer", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("expected_source_ids", sa.JSON(), nullable=True),
        sa.Column("expected_keywords", sa.JSON(), nullable=True),
        sa.Column("evaluator_user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("selected_file_ids", sa.JSON(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("date_created", sa.DateTime(), nullable=False),
        sa.Column("date_updated", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rag_eval_example_dataset",
        "ktem__rag_eval_example",
        ["dataset_id", "date_created"],
        unique=False,
    )
    op.create_index(
        "ix_rag_eval_example_evaluator",
        "ktem__rag_eval_example",
        ["evaluator_user_id"],
        unique=False,
    )

    op.create_table(
        "ktem__rag_eval_run",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("dataset_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("example_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("owner_user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("evaluator_user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("question", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("answer", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("references", sa.JSON(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("settings_snapshot", sa.JSON(), nullable=True),
        sa.Column("trace_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("date_created", sa.DateTime(), nullable=False),
        sa.Column("date_updated", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rag_eval_run_owner_dataset",
        "ktem__rag_eval_run",
        ["owner_user_id", "dataset_id", "date_created"],
        unique=False,
    )
    op.create_index(
        "ix_rag_eval_run_example",
        "ktem__rag_eval_run",
        ["example_id"],
        unique=False,
    )
    op.create_index(
        "ix_rag_eval_run_trace",
        "ktem__rag_eval_run",
        ["trace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rag_eval_run_trace", table_name="ktem__rag_eval_run")
    op.drop_index("ix_rag_eval_run_example", table_name="ktem__rag_eval_run")
    op.drop_index("ix_rag_eval_run_owner_dataset", table_name="ktem__rag_eval_run")
    op.drop_table("ktem__rag_eval_run")
    op.drop_index("ix_rag_eval_example_evaluator", table_name="ktem__rag_eval_example")
    op.drop_index("ix_rag_eval_example_dataset", table_name="ktem__rag_eval_example")
    op.drop_table("ktem__rag_eval_example")
    op.drop_index("ix_rag_eval_dataset_owner", table_name="ktem__rag_eval_dataset")
    op.drop_table("ktem__rag_eval_dataset")

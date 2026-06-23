"""add source permissions

Revision ID: 20260623_0001
Revises:
Create Date: 2026-06-23
"""

from typing import Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "20260623_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "ktem__source_permission",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("index_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("principal_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("principal_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("permission", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("date_created", sa.DateTime(), nullable=False),
        sa.Column("date_updated", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "index_id",
            "source_id",
            "principal_type",
            "principal_id",
            name="uq_source_permission_principal",
        ),
    )
    op.create_index(
        "ix_source_permission_lookup",
        "ktem__source_permission",
        ["index_id", "source_id", "principal_type", "principal_id"],
        unique=False,
    )
    op.create_index(
        "ix_source_permission_read",
        "ktem__source_permission",
        ["index_id", "permission"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_source_permission_read", table_name="ktem__source_permission")
    op.drop_index("ix_source_permission_lookup", table_name="ktem__source_permission")
    op.drop_table("ktem__source_permission")

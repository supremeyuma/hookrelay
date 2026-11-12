"""delivery traceparent

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "deliveries",
        sa.Column("traceparent", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deliveries", "traceparent")

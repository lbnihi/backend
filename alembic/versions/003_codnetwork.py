"""COD Network lead id on orders

Revision ID: 003
Revises: 002
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("orders")}
    if "codnetwork_order_id" not in columns:
        op.add_column("orders", sa.Column("codnetwork_order_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "codnetwork_order_id")

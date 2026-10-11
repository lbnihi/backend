"""Index orders.event_id (duplicate-submit check on order creation)

Revision ID: 005
Revises: 004
Create Date: 2026-10-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("orders")}
    if "ix_orders_event_id" not in indexes:
        op.create_index("ix_orders_event_id", "orders", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_orders_event_id", table_name="orders")

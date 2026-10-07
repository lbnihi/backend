"""Initial schema: orders + order_events

Revision ID: 001
Revises:
Create Date: 2026-10-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_number", sa.String(20), nullable=False),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("country_code", sa.String(2)),
        sa.Column("city", sa.String(100)),
        sa.Column("is_vpn", sa.Boolean(), server_default=sa.false()),
        sa.Column("items", JsonType, nullable=False),
        sa.Column("subtotal", sa.Numeric(10, 2), nullable=False),
        sa.Column("total", sa.Numeric(10, 2), nullable=False),
        sa.Column("upsell_product_slug", sa.String(100)),
        sa.Column("upsell_accepted", sa.Boolean(), server_default=sa.false()),
        sa.Column("upsell_amount", sa.Numeric(10, 2), server_default="0"),
        sa.Column("upsell_decided", sa.Boolean(), server_default=sa.false()),
        sa.Column("utm_source", sa.String(100)),
        sa.Column("utm_medium", sa.String(100)),
        sa.Column("utm_campaign", sa.String(255)),
        sa.Column("utm_content", sa.String(255)),
        sa.Column("utm_term", sa.String(255)),
        sa.Column("fbclid", sa.String(255)),
        sa.Column("ttclid", sa.String(255)),
        sa.Column("sclid", sa.String(255)),
        sa.Column("fbc", sa.String(255)),
        sa.Column("fbp", sa.String(255)),
        sa.Column("ttp", sa.String(255)),
        sa.Column("user_agent", sa.Text()),
        sa.Column("page_url", sa.Text()),
        sa.Column("event_id", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_orders_order_number", "orders", ["order_number"], unique=True)
    op.create_index("ix_orders_phone", "orders", ["phone"])
    op.create_index("ix_orders_created_at", "orders", ["created_at"])

    op.create_table(
        "order_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE")),
        sa.Column("event_name", sa.String(50), nullable=False),
        sa.Column("event_id", sa.String(100), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_body", sa.Text()),
    )
    op.create_index("ix_order_events_order_id", "order_events", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_order_events_order_id", table_name="order_events")
    op.drop_table("order_events")
    op.drop_index("ix_orders_created_at", table_name="orders")
    op.drop_index("ix_orders_phone", table_name="orders")
    op.drop_index("ix_orders_order_number", table_name="orders")
    op.drop_table("orders")

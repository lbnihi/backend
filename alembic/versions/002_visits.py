"""Visits table for click tracking

Revision ID: 002
Revises: 001
Create Date: 2026-10-07

Replaces migrations/002_add_visits_table.sql so it runs automatically on deploy.
Safe if the table was already created by hand from that SQL file.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("visits"):
        return
    op.create_table(
        "visits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ip_address", sa.String(45), nullable=False),
        sa.Column("country_code", sa.String(2)),
        sa.Column("city", sa.String(100)),
        sa.Column("is_vpn", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("page_url", sa.Text()),
        sa.Column("referrer", sa.Text()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("utm_source", sa.String(100)),
        sa.Column("utm_medium", sa.String(100)),
        sa.Column("utm_campaign", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_visits_created_at", "visits", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_visits_created_at", table_name="visits")
    op.drop_table("visits")

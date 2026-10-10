"""Visit kind: page_view or checkout

Revision ID: 004
Revises: 003
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("visits")}
    if "kind" not in columns:
        op.add_column("visits", sa.Column("kind", sa.String(20), nullable=False, server_default="page_view"))


def downgrade() -> None:
    op.drop_column("visits", "kind")

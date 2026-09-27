"""Chart-of-accounts management (roadmap 2026-09 §4.5): accounts can be
deactivated. Idempotent on fresh and migrated databases alike.

Revision ID: 050
Revises: 049
Create Date: 2026-09-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "050"
down_revision: Union[str, None] = "049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "is_active" not in cols:
        op.add_column("accounts", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "is_active" in cols:
        op.drop_column("accounts", "is_active")

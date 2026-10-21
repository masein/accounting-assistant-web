"""A budget's currency.

A person keeps rials at a bank and Omani rials in a wallet, each as it is (docs/PLAN_CURRENCIES.md
phase 4): a budget for dining in Omani rials counts only what was spent in Omani rials, in their own
amounts. Empty is the books' own currency — every budget set before this one.

Revision ID: 084
Revises: 083
Create Date: 2026-10-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "084"
down_revision: Union[str, None] = "083"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("budget_limits")}
    if "currency" not in columns:
        op.add_column("budget_limits", sa.Column("currency", sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column("budget_limits", "currency")

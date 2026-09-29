"""Project budgets (roadmap 2026-09 §4.7): hours and fees per project. Idempotent.

Revision ID: 059
Revises: 058
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "059"
down_revision: Union[str, None] = "058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ("budget_hours", lambda: sa.Column("budget_hours", sa.Numeric(10, 2), nullable=True)),
    ("budget_amount", lambda: sa.Column("budget_amount", sa.BigInteger(), nullable=True)),
)


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("projects")}
    for name, make in COLUMNS:
        if name not in have:
            op.add_column("projects", make())


def downgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("projects")}
    for name, _make in reversed(COLUMNS):
        if name in have:
            op.drop_column("projects", name)

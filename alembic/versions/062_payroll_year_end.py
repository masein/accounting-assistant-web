"""Year-end pay runs — عیدی و پاداش and حق سنوات (roadmap 2026-09 §3.3). Idempotent.

Revision ID: 062
Revises: 061
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "062"
down_revision: Union[str, None] = "061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = {
    "employee_pay_profiles": (
        ("hired_on", lambda: sa.Column("hired_on", sa.Date(), nullable=True)),
    ),
    "pay_runs": (
        ("kind", lambda: sa.Column("kind", sa.String(16), nullable=False, server_default="regular")),
        ("year_key", lambda: sa.Column("year_key", sa.String(8), nullable=True)),
    ),
    "pay_run_lines": (
        ("eidi", lambda: sa.Column("eidi", sa.BigInteger(), nullable=False, server_default="0")),
        ("sanavat", lambda: sa.Column("sanavat", sa.BigInteger(), nullable=False, server_default="0")),
        ("days_worked", lambda: sa.Column("days_worked", sa.Integer(), nullable=False, server_default="0")),
    ),
}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for table, cols in COLUMNS.items():
        have = {c["name"] for c in insp.get_columns(table)}
        for name, make in cols:
            if name not in have:
                op.add_column(table, make())
    idx = {i["name"] for i in insp.get_indexes("pay_runs")}
    if "ix_pay_runs_kind" not in idx:
        op.create_index("ix_pay_runs_kind", "pay_runs", ["kind"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "ix_pay_runs_kind" in {i["name"] for i in insp.get_indexes("pay_runs")}:
        op.drop_index("ix_pay_runs_kind", table_name="pay_runs")
    for table, cols in COLUMNS.items():
        have = {c["name"] for c in insp.get_columns(table)}
        for name, _make in reversed(cols):
            if name in have:
                op.drop_column(table, name)

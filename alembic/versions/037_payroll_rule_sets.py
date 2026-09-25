"""Statutory payroll rules as data + employer insurance on pay runs.

* ``payroll_rule_sets``: platform-wide parameters per locale and year
  (Iran 1405 decree figures, UK 2026/27 bands), edited by the super-admin.
* ``employee_pay_profiles``: ``tax_mode`` (flat | statutory), ``children``
  (حق اولاد) and ``seniority_eligible`` (پایه سنوات).
* ``pay_run_lines``: ``allowances``, ``insurable_wage`` and the employer's
  insurance share; ``pay_runs.total_employer_social``.

Revision ID: 037
Revises: 036
Create Date: 2026-09-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "037"
down_revision: Union[str, None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(conn).get_columns(table)}


def _table_exists(conn, table: str) -> bool:
    return table in sa.inspect(conn).get_table_names()


_NEW_COLUMNS = (
    ("employee_pay_profiles", sa.Column("tax_mode", sa.String(16), nullable=False, server_default="flat")),
    ("employee_pay_profiles", sa.Column("children", sa.Integer(), nullable=False, server_default="0")),
    ("employee_pay_profiles", sa.Column("seniority_eligible", sa.Boolean(), nullable=False, server_default=sa.false())),
    ("pay_run_lines", sa.Column("allowances", sa.BigInteger(), nullable=False, server_default="0")),
    ("pay_run_lines", sa.Column("insurable_wage", sa.BigInteger(), nullable=False, server_default="0")),
    ("pay_run_lines", sa.Column("employer_social", sa.BigInteger(), nullable=False, server_default="0")),
    ("pay_runs", sa.Column("total_employer_social", sa.BigInteger(), nullable=False, server_default="0")),
)


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "payroll_rule_sets"):
        op.create_table(
            "payroll_rule_sets",
            sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("locale", sa.String(16), nullable=False, index=True),
            sa.Column("year", sa.String(16), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("effective_from", sa.Date(), nullable=False, index=True),
            sa.Column("effective_to", sa.Date(), nullable=True),
            sa.Column("params", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("locale", "year", name="uq_payroll_rule_sets_locale_year"),
        )
    for table, col in _NEW_COLUMNS:
        if _table_exists(conn, table) and not _column_exists(conn, table, col.name):
            op.add_column(table, col)


def downgrade() -> None:
    conn = op.get_bind()
    for table, col in reversed(_NEW_COLUMNS):
        if _table_exists(conn, table) and _column_exists(conn, table, col.name):
            op.drop_column(table, col.name)
    if _table_exists(conn, "payroll_rule_sets"):
        op.drop_table("payroll_rule_sets")

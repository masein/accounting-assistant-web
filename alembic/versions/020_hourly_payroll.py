"""Hourly payroll from tracked hours: the time entry gains a payroll dimension.

- time_entries: entry_type (work|leave|travel|unpaid), payable (counts toward
  employee pay — independent of billable), payroll_status (unpaid|paid) +
  payroll_run_id (settlement link), source/external_id (integration pushes),
  and client_id becomes NULLABLE (payroll-only entries have no client).
- employee_pay_profiles: monthly_standard_hours (required monthly hours for
  hours-derived pay; null → falls back to standard_hours).
- pay_run_lines: leave_hours (paid-leave hours shown on the payslip).

Revision ID: 020
Revises: 019
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


def _constraint_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.table_constraints WHERE constraint_name = :n"),
        {"n": name},
    ).first())


TIME_ENTRY_COLUMNS = [
    ("entry_type", sa.Column("entry_type", sa.String(16), nullable=False, server_default="work")),
    ("payable", sa.Column("payable", sa.Boolean, nullable=False, server_default="true")),
    ("payroll_status", sa.Column("payroll_status", sa.String(16), nullable=False, server_default="unpaid")),
    ("source", sa.Column("source", sa.String(32), nullable=True)),
    ("external_id", sa.Column("external_id", sa.String(128), nullable=True)),
]


def upgrade() -> None:
    conn = op.get_bind()
    UUID = sa.dialects.postgresql.UUID

    for name, col in TIME_ENTRY_COLUMNS:
        if not _column_exists(conn, "time_entries", name):
            op.add_column("time_entries", col)

    if not _column_exists(conn, "time_entries", "payroll_run_id"):
        op.add_column("time_entries", sa.Column("payroll_run_id", UUID(as_uuid=True), nullable=True))
    if not _constraint_exists(conn, "fk_time_entries_payroll_run"):
        op.create_foreign_key(
            "fk_time_entries_payroll_run", "time_entries", "pay_runs",
            ["payroll_run_id"], ["id"], ondelete="SET NULL",
        )
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_time_entries_payroll_run_id ON time_entries (payroll_run_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_time_entries_source_external ON time_entries (source, external_id)"
    ))

    # Payroll-only entries (leave, internal work) have no client.
    conn.execute(sa.text("ALTER TABLE time_entries ALTER COLUMN client_id DROP NOT NULL"))

    if not _column_exists(conn, "employee_pay_profiles", "monthly_standard_hours"):
        op.add_column(
            "employee_pay_profiles",
            sa.Column("monthly_standard_hours", sa.Numeric(8, 2), nullable=True),
        )

    if not _column_exists(conn, "pay_run_lines", "leave_hours"):
        op.add_column(
            "pay_run_lines",
            sa.Column("leave_hours", sa.Numeric(8, 2), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "pay_run_lines", "leave_hours"):
        op.drop_column("pay_run_lines", "leave_hours")
    if _column_exists(conn, "employee_pay_profiles", "monthly_standard_hours"):
        op.drop_column("employee_pay_profiles", "monthly_standard_hours")
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_time_entries_source_external"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_time_entries_payroll_run_id"))
    if _constraint_exists(conn, "fk_time_entries_payroll_run"):
        op.drop_constraint("fk_time_entries_payroll_run", "time_entries", type_="foreignkey")
    if _column_exists(conn, "time_entries", "payroll_run_id"):
        op.drop_column("time_entries", "payroll_run_id")
    for name, _ in TIME_ENTRY_COLUMNS:
        if _column_exists(conn, "time_entries", name):
            op.drop_column("time_entries", name)

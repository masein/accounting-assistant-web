"""Party bank accounts + payroll disbursement snapshot.

- entities: bank_name / account_holder / account_number / iban / sort_code —
  the counterparty's or employee's OWN bank account (payroll pays employees
  here; the company's own bank stays on company_profiles).
- pay_run_lines: paid_to — snapshot of the disbursement destination taken at
  pay time, so the payslip stays correct if the employee's bank changes later.

Revision ID: 024
Revises: 023
Create Date: 2026-07-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BANK_COLS = [
    ("bank_name", sa.String(128)),
    ("account_holder", sa.String(256)),
    ("account_number", sa.String(64)),
    ("iban", sa.String(34)),
    ("sort_code", sa.String(16)),
]


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    for name, coltype in _BANK_COLS:
        if not _column_exists(conn, "entities", name):
            op.add_column("entities", sa.Column(name, coltype, nullable=True))
    if not _column_exists(conn, "pay_run_lines", "paid_to"):
        op.add_column("pay_run_lines", sa.Column("paid_to", sa.String(256), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "pay_run_lines", "paid_to"):
        op.drop_column("pay_run_lines", "paid_to")
    for name, _ in _BANK_COLS:
        if _column_exists(conn, "entities", name):
            op.drop_column("entities", name)

"""Official-invoice identity fields (فاکتور رسمی).

The Iranian tax invoice requires structured identity for BOTH parties — economic
code (شماره اقتصادی), national ID (شناسه ملی), province/county, city, 10-digit
postal code — plus structured payment info (account no + شبا/IBAN). Adds those
columns to entities (payer card) and company_profiles (issuer card).

Revision ID: 023
Revises: 022
Create Date: 2026-07-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IDENTITY = [
    ("economic_code", sa.String(32)),
    ("national_id", sa.String(32)),
    ("province", sa.String(128)),
    ("city", sa.String(128)),
    ("postal_code", sa.String(16)),
]
_PROFILE_EXTRA = [
    ("bank_account_no", sa.String(64)),
    ("iban", sa.String(34)),
]


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    for table, cols in (("entities", _IDENTITY), ("company_profiles", _IDENTITY + _PROFILE_EXTRA)):
        for name, coltype in cols:
            if not _column_exists(conn, table, name):
                op.add_column(table, sa.Column(name, coltype, nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    for table, cols in (("entities", _IDENTITY), ("company_profiles", _IDENTITY + _PROFILE_EXTRA)):
        for name, _ in cols:
            if _column_exists(conn, table, name):
                op.drop_column(table, name)

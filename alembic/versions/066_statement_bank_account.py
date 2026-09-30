"""The bank account a statement belongs to, when the user chose it (roadmap
2026-09 §4.1). NULL = decided from the statement (account number, name).
Idempotent.

Revision ID: 066
Revises: 065
Create Date: 2026-09-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "066"
down_revision: Union[str, None] = "065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "bank_account_code" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("bank_statements")}:
        op.add_column("bank_statements", sa.Column("bank_account_code", sa.String(64), nullable=True))


def downgrade() -> None:
    if "bank_account_code" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("bank_statements")}:
        op.drop_column("bank_statements", "bank_account_code")

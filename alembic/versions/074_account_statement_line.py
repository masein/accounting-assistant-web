"""The balance-sheet line a migrated chart names for an account.

A chart migrated from Sepidar numbers its ledgers otherwise than the seeded
chart the Iranian statements' code prefixes were written for; a migration now
names each ledger's line from its title, and the accounts under it inherit it
(app/services/reporting/statement_lines.py). Nullable: every existing account
keeps reading its line from its code. Safe to run twice.

Revision ID: 074
Revises: 073
Create Date: 2026-10-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "074"
down_revision: Union[str, None] = "073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "statement_line" not in columns:
        op.add_column("accounts", sa.Column("statement_line", sa.String(32), nullable=True))


def downgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "statement_line" in columns:
        op.drop_column("accounts", "statement_line")

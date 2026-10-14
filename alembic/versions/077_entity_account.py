"""A party's own account (scenarios X1–X6).

``entities.account_id``: the ledger account a party's statement reads, when a
party is made from one of the chart's debtor or creditor accounts (a school's
hand-kept journal keeps «دبیرستان منادی حق» as an account under بدهکاران).
Nullable: every existing party keeps reading the control account its type
names. Idempotent on fresh and migrated databases alike.

Revision ID: 077
Revises: 076
Create Date: 2026-10-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "077"
down_revision: Union[str, None] = "076"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# money columns this migration adds (tests/test_money.py reads it): none
MONEY: dict[str, list[str]] = {}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    columns = {c["name"] for c in insp.get_columns("entities")}
    if "account_id" not in columns:
        op.add_column("entities", sa.Column("account_id", UUID(as_uuid=True),
                                            sa.ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True))
    indexes = {i["name"] for i in insp.get_indexes("entities")}
    if "ix_entities_account_id" not in indexes:
        op.create_index("ix_entities_account_id", "entities", ["account_id"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    indexes = {i["name"] for i in insp.get_indexes("entities")}
    if "ix_entities_account_id" in indexes:
        op.drop_index("ix_entities_account_id", table_name="entities")
    columns = {c["name"] for c in insp.get_columns("entities")}
    if "account_id" in columns:
        op.drop_column("entities", "account_id")

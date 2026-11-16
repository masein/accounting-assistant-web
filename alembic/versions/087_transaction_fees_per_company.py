"""Bank fee rules per company (review 2026-11-16).

``transaction_fees`` had no company: every company listed every other
company's fee rules (GET /transactions/fees), with their banks' ids. A rule is
for one bank account, and a bank account is a company's entity, so each rule
takes its bank's company. ``payment_methods`` stays one shared list of method
names (card to card, PAYA, SATNA …).

The NOT NULL-free ``company_id`` and its foreign key follow every other tenant
table added after 015 (``app/db/guards.install_tenant_guards``). Safe to run
twice; a fresh database gets the column from ``create_all``.

Revision ID: 087
Revises: 086
Create Date: 2026-11-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "087"
down_revision: Union[str, None] = "086"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "transaction_fees"
INDEX = "ix_transaction_fees_company_id"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if TABLE not in insp.get_table_names():
        return
    if "company_id" not in {c["name"] for c in insp.get_columns(TABLE)}:
        op.add_column(TABLE, sa.Column("company_id", UUID(as_uuid=True), nullable=True))
    bind.execute(sa.text(
        "UPDATE transaction_fees SET company_id = ("
        "  SELECT e.company_id FROM entities e WHERE e.id = transaction_fees.bank_id"
        ") WHERE company_id IS NULL"))
    if INDEX not in {i["name"] for i in insp.get_indexes(TABLE)}:
        op.create_index(INDEX, TABLE, ["company_id"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return
    if INDEX in {i["name"] for i in insp.get_indexes(TABLE)}:
        op.drop_index(INDEX, table_name=TABLE)
    if "fk_transaction_fees_company" in {fk["name"] for fk in insp.get_foreign_keys(TABLE)}:
        op.drop_constraint("fk_transaction_fees_company", TABLE, type_="foreignkey")
    if "company_id" in {c["name"] for c in insp.get_columns(TABLE)}:
        op.drop_column(TABLE, "company_id")

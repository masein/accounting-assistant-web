"""Exchange rates per company (roadmap 2026-09 §4.6): rates were one table
for the whole installation, so any company's accountant could change or
delete the rates every other company reports in. Rows now carry an optional
company: none = shared (the daily feeds, and every rate entered so far), a
company = that company's own. Codes widen to 16 characters so holding units
such as GOLD_GRAM can be priced. Idempotent on fresh and migrated databases.

Revision ID: 052
Revises: 051
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "052"
down_revision: Union[str, None] = "051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "exchange_rates"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"]: c for c in insp.get_columns(TABLE)}
    for name in ("from_currency", "to_currency"):
        length = getattr(cols[name]["type"], "length", None)
        if length is not None and length < 16:
            op.alter_column(TABLE, name, type_=sa.String(16), existing_type=sa.String(length),
                            existing_nullable=False)
    if "company_id" not in cols:
        op.add_column(TABLE, sa.Column("company_id", UUID(as_uuid=True), nullable=True))
    fks = {fk["name"] for fk in insp.get_foreign_keys(TABLE)}
    if "fk_exchange_rates_company" not in fks:
        op.create_foreign_key("fk_exchange_rates_company", TABLE, "companies", ["company_id"], ["id"],
                              ondelete="CASCADE")
    uniques = {u["name"] for u in insp.get_unique_constraints(TABLE)}
    if "uq_exchange_rates_from_to_date" in uniques:
        op.drop_constraint("uq_exchange_rates_from_to_date", TABLE, type_="unique")
    indexes = {i["name"] for i in insp.get_indexes(TABLE)}
    if "ix_exchange_rates_company_id" not in indexes:
        op.create_index("ix_exchange_rates_company_id", TABLE, ["company_id"])
    if "uq_exchange_rates_shared" not in indexes:
        op.create_index("uq_exchange_rates_shared", TABLE, ["from_currency", "to_currency", "effective_date"],
                        unique=True, postgresql_where=sa.text("company_id IS NULL"))
    if "uq_exchange_rates_company" not in indexes:
        op.create_index("uq_exchange_rates_company", TABLE,
                        ["company_id", "from_currency", "to_currency", "effective_date"],
                        unique=True, postgresql_where=sa.text("company_id IS NOT NULL"))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    indexes = {i["name"] for i in insp.get_indexes(TABLE)}
    for name in ("uq_exchange_rates_company", "uq_exchange_rates_shared", "ix_exchange_rates_company_id"):
        if name in indexes:
            op.drop_index(name, table_name=TABLE)
    if "company_id" in {c["name"] for c in insp.get_columns(TABLE)}:
        # A company's own rates have no shared equivalent to fall back to.
        op.execute(f"DELETE FROM {TABLE} WHERE company_id IS NOT NULL")
        if "fk_exchange_rates_company" in {fk["name"] for fk in insp.get_foreign_keys(TABLE)}:
            op.drop_constraint("fk_exchange_rates_company", TABLE, type_="foreignkey")
        op.drop_column(TABLE, "company_id")
    op.create_unique_constraint("uq_exchange_rates_from_to_date", TABLE,
                                ["from_currency", "to_currency", "effective_date"])

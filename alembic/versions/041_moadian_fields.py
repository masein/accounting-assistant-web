"""سامانه مودیان export fields (roadmap §3.1): goods/service id and unit on
invoice and quote lines; serial, unique tax number and status on invoices.

Revision ID: 041
Revises: 040
Create Date: 2026-09-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "041"
down_revision: Union[str, None] = "040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("invoice_items", sa.Column("sstid", sa.String(13), nullable=True)),
    ("invoice_items", sa.Column("mu", sa.String(8), nullable=True)),
    ("quote_items", sa.Column("sstid", sa.String(13), nullable=True)),
    ("quote_items", sa.Column("mu", sa.String(8), nullable=True)),
    ("invoices", sa.Column("moadian_serial", sa.BigInteger(), nullable=True)),
    ("invoices", sa.Column("moadian_taxid", sa.String(22), nullable=True)),
    ("invoices", sa.Column("moadian_status", sa.String(16), nullable=True)),
    ("invoices", sa.Column("moadian_exported_at", sa.DateTime(timezone=True), nullable=True)),
    ("invoices", sa.Column("moadian_reference", sa.String(64), nullable=True)),
    ("invoices", sa.Column("moadian_error", sa.Text(), nullable=True)),
)


def upgrade() -> None:
    conn = op.get_bind()
    for table, col in _COLUMNS:
        if col.name not in {c["name"] for c in sa.inspect(conn).get_columns(table)}:
            op.add_column(table, col)
    idx = {i["name"] for i in sa.inspect(conn).get_indexes("invoices")}
    if "ix_invoices_moadian_status" not in idx:
        op.create_index("ix_invoices_moadian_status", "invoices", ["moadian_status"])


def downgrade() -> None:
    conn = op.get_bind()
    if "ix_invoices_moadian_status" in {i["name"] for i in sa.inspect(conn).get_indexes("invoices")}:
        op.drop_index("ix_invoices_moadian_status", table_name="invoices")
    for table, col in reversed(_COLUMNS):
        if col.name in {c["name"] for c in sa.inspect(conn).get_columns(table)}:
            op.drop_column(table, col.name)

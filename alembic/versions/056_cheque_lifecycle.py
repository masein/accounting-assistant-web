"""Cheque lifecycle (roadmap 2026-09 §3.4): Sayad id and registration, ledger
mode and where the cheque sits, deposit, the invoice it pays, endorsement,
and each cheque's history. Idempotent.

Revision ID: 056
Revises: 055
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "056"
down_revision: Union[str, None] = "055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ("sayad_id", lambda: sa.Column("sayad_id", sa.String(16), nullable=True)),
    ("sayad_registered_on", lambda: sa.Column("sayad_registered_on", sa.Date(), nullable=True)),
    ("ledger_mode", lambda: sa.Column("ledger_mode", sa.String(8), nullable=False, server_default="direct")),
    ("holding_account_code", lambda: sa.Column("holding_account_code", sa.String(64), nullable=True)),
    ("deposited_on", lambda: sa.Column("deposited_on", sa.Date(), nullable=True)),
    ("deposit_account_code", lambda: sa.Column("deposit_account_code", sa.String(64), nullable=True)),
    ("invoice_id", lambda: sa.Column("invoice_id", UUID(as_uuid=True),
                                     sa.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)),
    ("payment_id", lambda: sa.Column("payment_id", UUID(as_uuid=True),
                                     sa.ForeignKey("payments.id", ondelete="SET NULL"), nullable=True)),
    ("endorsed_to", lambda: sa.Column("endorsed_to", sa.String(256), nullable=True)),
)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns("commitments")}
    for name, make in COLUMNS:
        if name not in have:
            op.add_column("commitments", make())
    indexes = {i["name"] for i in insp.get_indexes("commitments")}
    for col in ("sayad_id", "invoice_id", "payment_id"):
        if f"ix_commitments_{col}" not in indexes:
            op.create_index(f"ix_commitments_{col}", "commitments", [col])

    if not insp.has_table("commitment_events"):
        op.create_table(
            "commitment_events",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("commitment_id", UUID(as_uuid=True),
                      sa.ForeignKey("commitments.id", ondelete="CASCADE"), nullable=False),
            sa.Column("action", sa.String(24), nullable=False),
            sa.Column("happened_on", sa.Date(), nullable=False),
            sa.Column("transaction_id", UUID(as_uuid=True),
                      sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_commitment_events_company_id", "commitment_events", ["company_id"])
        op.create_index("ix_commitment_events_commitment_id", "commitment_events", ["commitment_id"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table("commitment_events"):
        op.drop_table("commitment_events")
    have = {c["name"] for c in insp.get_columns("commitments")}
    indexes = {i["name"] for i in insp.get_indexes("commitments")}
    for col in ("sayad_id", "invoice_id", "payment_id"):
        if f"ix_commitments_{col}" in indexes:
            op.drop_index(f"ix_commitments_{col}", table_name="commitments")
    for name, _make in reversed(COLUMNS):
        if name in have:
            op.drop_column("commitments", name)

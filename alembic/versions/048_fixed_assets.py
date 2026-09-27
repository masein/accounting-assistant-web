"""Fixed-asset register (roadmap 2026-09 §4.3).

``fixed_assets``: one card per asset. ``fixed_asset_depreciation``: one row per
posted month, unique per asset and month. Idempotent on fresh and migrated
databases alike (a fresh install already has both from create_all).

Revision ID: 048
Revises: 047
Create Date: 2026-09-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "048"
down_revision: Union[str, None] = "047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("fixed_assets"):
        op.create_table(
            "fixed_assets",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("number", sa.String(32), nullable=False),
            sa.Column("name", sa.String(256), nullable=False),
            sa.Column("category", sa.String(48), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("serial_number", sa.String(128), nullable=True),
            sa.Column("location", sa.String(128), nullable=True),
            sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("currency", sa.String(8), nullable=False),
            sa.Column("acquired_on", sa.Date(), nullable=False),
            sa.Column("in_service_on", sa.Date(), nullable=False),
            sa.Column("depreciation_start", sa.Date(), nullable=False),
            sa.Column("cost", sa.BigInteger(), nullable=False),
            sa.Column("residual", sa.BigInteger(), nullable=False),
            sa.Column("method", sa.String(24), nullable=False),
            sa.Column("life_months", sa.Integer(), nullable=True),
            sa.Column("rate_bps", sa.Integer(), nullable=True),
            sa.Column("opening_accumulated", sa.BigInteger(), nullable=False),
            sa.Column("opening_date", sa.Date(), nullable=True),
            sa.Column("asset_account_code", sa.String(16), nullable=False),
            sa.Column("accumulated_account_code", sa.String(16), nullable=False),
            sa.Column("expense_account_code", sa.String(16), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("acquisition_transaction_id", UUID(as_uuid=True),
                      sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("disposed_on", sa.Date(), nullable=True),
            sa.Column("disposal_proceeds", sa.BigInteger(), nullable=True),
            sa.Column("disposal_transaction_id", UUID(as_uuid=True),
                      sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        for col in ("company_id", "number", "entity_id", "depreciation_start", "status"):
            op.create_index(f"ix_fixed_assets_{col}", "fixed_assets", [col])
    if not insp.has_table("fixed_asset_depreciation"):
        op.create_table(
            "fixed_asset_depreciation",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("asset_id", UUID(as_uuid=True), sa.ForeignKey("fixed_assets.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("period_start", sa.Date(), nullable=False),
            sa.Column("amount", sa.BigInteger(), nullable=False),
            sa.Column("transaction_id", UUID(as_uuid=True), sa.ForeignKey("transactions.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("asset_id", "period_start", name="uq_fixed_asset_depreciation_month"),
        )
        for col in ("company_id", "asset_id", "transaction_id"):
            op.create_index(f"ix_fixed_asset_depreciation_{col}", "fixed_asset_depreciation", [col])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for table in ("fixed_asset_depreciation", "fixed_assets"):
        if insp.has_table(table):
            op.drop_table(table)

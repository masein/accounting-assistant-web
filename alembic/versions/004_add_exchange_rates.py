"""Add exchange_rates table.

Revision ID: 004
Revises: 003
Create Date: 2026-04-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(
        sa.text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'exchange_rates'"
        )
    ).first()
    if exists:
        return
    op.create_table(
        "exchange_rates",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("from_currency", sa.String(8), nullable=False),
        sa.Column("to_currency", sa.String(8), nullable=False),
        sa.Column("rate", sa.Float, nullable=False),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("note", sa.String(256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "from_currency", "to_currency", "effective_date",
            name="uq_exchange_rates_from_to_date",
        ),
    )
    op.create_index("ix_exchange_rates_from_currency", "exchange_rates", ["from_currency"])
    op.create_index("ix_exchange_rates_to_currency", "exchange_rates", ["to_currency"])
    op.create_index("ix_exchange_rates_effective_date", "exchange_rates", ["effective_date"])


def downgrade() -> None:
    op.drop_index("ix_exchange_rates_effective_date", table_name="exchange_rates")
    op.drop_index("ix_exchange_rates_to_currency", table_name="exchange_rates")
    op.drop_index("ix_exchange_rates_from_currency", table_name="exchange_rates")
    op.drop_table("exchange_rates")

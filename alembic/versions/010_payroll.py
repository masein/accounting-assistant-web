"""Payroll: employee pay profiles, pay runs and pay-run lines.

Revision ID: 010
Revises: 009
Create Date: 2026-06-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
            {"t": table},
        ).first()
    )


def upgrade() -> None:
    conn = op.get_bind()
    UUID = sa.dialects.postgresql.UUID

    if not _table_exists(conn, "employee_pay_profiles"):
        op.create_table(
            "employee_pay_profiles",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("entity_id", UUID(as_uuid=True),
                      sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
            sa.Column("pay_type", sa.String(16), nullable=False, server_default="salaried"),
            sa.Column("base_salary", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("hourly_rate", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("standard_hours", sa.Numeric(8, 2), nullable=False, server_default="0"),
            sa.Column("overtime_multiplier", sa.Numeric(5, 2), nullable=False, server_default="1.5"),
            sa.Column("income_tax_rate", sa.Numeric(6, 4), nullable=False, server_default="0"),
            sa.Column("social_security_rate", sa.Numeric(6, 4), nullable=False, server_default="0"),
            sa.Column("pension_rate", sa.Numeric(6, 4), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(8), nullable=False, server_default="IRR"),
            sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_employee_pay_profiles_entity_id", "employee_pay_profiles",
                        ["entity_id"], unique=True)
        op.create_index("ix_employee_pay_profiles_pay_type", "employee_pay_profiles", ["pay_type"])
        op.create_index("ix_employee_pay_profiles_active", "employee_pay_profiles", ["active"])

    if not _table_exists(conn, "pay_runs"):
        op.create_table(
            "pay_runs",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("period_start", sa.Date, nullable=False),
            sa.Column("period_end", sa.Date, nullable=False),
            sa.Column("pay_date", sa.Date, nullable=False),
            sa.Column("currency", sa.String(8), nullable=False, server_default="IRR"),
            sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
            sa.Column("total_gross", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("total_tax", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("total_social", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("total_deductions", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("total_net", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("post_transaction_id", UUID(as_uuid=True),
                      sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("pay_transaction_id", UUID(as_uuid=True),
                      sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_pay_runs_status", "pay_runs", ["status"])
        op.create_index("ix_pay_runs_period_start", "pay_runs", ["period_start"])

    if not _table_exists(conn, "pay_run_lines"):
        op.create_table(
            "pay_run_lines",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("run_id", UUID(as_uuid=True),
                      sa.ForeignKey("pay_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("entity_id", UUID(as_uuid=True),
                      sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
            sa.Column("employee_name", sa.String(256), nullable=False),
            sa.Column("hours", sa.Numeric(8, 2), nullable=False, server_default="0"),
            sa.Column("overtime_hours", sa.Numeric(8, 2), nullable=False, server_default="0"),
            sa.Column("proration", sa.Numeric(6, 4), nullable=False, server_default="1"),
            sa.Column("gross", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("pre_tax_deductions", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("taxable_base", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("income_tax", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("social_security", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("net_pay", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_pay_run_lines_run_id", "pay_run_lines", ["run_id"])
        op.create_index("ix_pay_run_lines_entity_id", "pay_run_lines", ["entity_id"])


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "pay_run_lines"):
        op.drop_table("pay_run_lines")
    if _table_exists(conn, "pay_runs"):
        op.drop_table("pay_runs")
    if _table_exists(conn, "employee_pay_profiles"):
        op.drop_table("employee_pay_profiles")

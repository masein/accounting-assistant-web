"""Time-based billing: projects, billing-rate overrides, time entries,
and a billable_rate on the employee pay profile.

Revision ID: 014
Revises: 013
Create Date: 2026-06-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"), {"t": table}
    ).first())


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    UUID = sa.dialects.postgresql.UUID

    if _table_exists(conn, "employee_pay_profiles") and not _column_exists(
        conn, "employee_pay_profiles", "billable_rate"
    ):
        op.add_column("employee_pay_profiles", sa.Column("billable_rate", sa.Numeric(14, 2), nullable=True))

    if not _table_exists(conn, "projects"):
        op.create_table(
            "projects",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("client_id", UUID(as_uuid=True),
                      sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(256), nullable=False),
            sa.Column("code", sa.String(64), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("default_currency", sa.String(8), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_projects_client_id", "projects", ["client_id"])
        op.create_index("ix_projects_name", "projects", ["name"])
        op.create_index("ix_projects_status", "projects", ["status"])

    if not _table_exists(conn, "billing_rate_overrides"):
        op.create_table(
            "billing_rate_overrides",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("employee_id", UUID(as_uuid=True),
                      sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
            sa.Column("client_id", UUID(as_uuid=True),
                      sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=True),
            sa.Column("project_id", UUID(as_uuid=True),
                      sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
            sa.Column("rate", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(8), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_billing_rate_overrides_employee_id", "billing_rate_overrides", ["employee_id"])
        op.create_index("ix_billing_rate_overrides_client_id", "billing_rate_overrides", ["client_id"])
        op.create_index("ix_billing_rate_overrides_project_id", "billing_rate_overrides", ["project_id"])

    if not _table_exists(conn, "time_entries"):
        op.create_table(
            "time_entries",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("employee_id", UUID(as_uuid=True),
                      sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
            sa.Column("client_id", UUID(as_uuid=True),
                      sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
            sa.Column("project_id", UUID(as_uuid=True),
                      sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
            sa.Column("work_date", sa.Date, nullable=False),
            sa.Column("hours", sa.Numeric(8, 2), nullable=False, server_default="0"),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("billable", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("status", sa.String(16), nullable=False, server_default="unbilled"),
            sa.Column("invoice_id", UUID(as_uuid=True),
                      sa.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True),
            sa.Column("rate_snapshot", sa.Numeric(14, 2), nullable=True),
            sa.Column("currency", sa.String(8), nullable=True),
            sa.Column("created_by", sa.String(128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_time_entries_employee_id", "time_entries", ["employee_id"])
        op.create_index("ix_time_entries_client_id", "time_entries", ["client_id"])
        op.create_index("ix_time_entries_project_id", "time_entries", ["project_id"])
        op.create_index("ix_time_entries_work_date", "time_entries", ["work_date"])
        op.create_index("ix_time_entries_status", "time_entries", ["status"])
        op.create_index("ix_time_entries_invoice_id", "time_entries", ["invoice_id"])


def downgrade() -> None:
    conn = op.get_bind()
    for tbl in ("time_entries", "billing_rate_overrides", "projects"):
        if _table_exists(conn, tbl):
            op.drop_table(tbl)
    if _column_exists(conn, "employee_pay_profiles", "billable_rate"):
        op.drop_column("employee_pay_profiles", "billable_rate")

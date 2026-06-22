"""Company branding profile + richer client/supplier (Entity) billing fields.

Revision ID: 016
Revises: 015
Create Date: 2026-06-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "016"
down_revision: Union[str, None] = "015"
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


ENTITY_COLUMNS = [
    ("legal_name", sa.String(256)),
    ("address", sa.Text()),
    ("email", sa.String(256)),
    ("phone", sa.String(64)),
    ("website", sa.String(256)),
    ("tax_id", sa.String(128)),
    ("contact_person", sa.String(256)),
    ("payment_terms", sa.String(128)),
    ("currency", sa.String(8)),
    ("notes", sa.Text()),
]


def upgrade() -> None:
    conn = op.get_bind()
    UUID = sa.dialects.postgresql.UUID

    if not _table_exists(conn, "company_profiles"):
        op.create_table(
            "company_profiles",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True),
                      sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
            sa.Column("legal_name", sa.String(256), nullable=True),
            sa.Column("brand_color", sa.String(16), nullable=False, server_default="#0f766e"),
            sa.Column("logo_path", sa.String(512), nullable=True),
            sa.Column("signature_path", sa.String(512), nullable=True),
            sa.Column("address", sa.Text(), nullable=True),
            sa.Column("tax_id", sa.String(128), nullable=True),
            sa.Column("registration_number", sa.String(128), nullable=True),
            sa.Column("email", sa.String(256), nullable=True),
            sa.Column("phone", sa.String(64), nullable=True),
            sa.Column("website", sa.String(256), nullable=True),
            sa.Column("bank_details", sa.Text(), nullable=True),
            sa.Column("default_payment_terms", sa.String(128), nullable=True),
            sa.Column("invoice_footer", sa.Text(), nullable=True),
            sa.Column("invoice_number_prefix", sa.String(32), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_unique_constraint("uq_company_profile_company", "company_profiles", ["company_id"])
        op.create_index("ix_company_profiles_company_id", "company_profiles", ["company_id"])

    if _table_exists(conn, "entities"):
        for name, coltype in ENTITY_COLUMNS:
            if not _column_exists(conn, "entities", name):
                op.add_column("entities", sa.Column(name, coltype, nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "entities"):
        for name, _ in ENTITY_COLUMNS:
            if _column_exists(conn, "entities", name):
                op.drop_column("entities", name)
    op.drop_table("company_profiles")

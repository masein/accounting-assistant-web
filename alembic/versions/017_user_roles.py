"""Multi-user RBAC: per-user role + optional employee-entity link.

Adds `users.role` (owner|cfo|accountant|manager|employee|viewer) and
`users.entity_id` (nullable FK -> entities.id). Backfills every existing login
to `owner` so nothing breaks — the pre-RBAC single company login stays fully
capable. No destructive changes.

Revision ID: 017
Revises: 016
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


def _constraint_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.table_constraints WHERE constraint_name = :n"),
        {"n": name},
    ).first())


def _index_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": name}
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    UUID = sa.dialects.postgresql.UUID

    # role — NOT NULL with a server default so existing rows backfill to owner.
    if not _column_exists(conn, "users", "role"):
        op.add_column(
            "users",
            sa.Column("role", sa.String(16), nullable=False, server_default="owner"),
        )
    # Belt-and-suspenders: any pre-existing NULL/blank role -> owner.
    conn.execute(sa.text(
        "UPDATE users SET role = 'owner' WHERE role IS NULL OR btrim(role) = ''"
    ))

    # entity_id — nullable FK to entities.id (SET NULL so deleting an employee
    # entity just unlinks the login, never deletes it).
    if not _column_exists(conn, "users", "entity_id"):
        op.add_column("users", sa.Column("entity_id", UUID(as_uuid=True), nullable=True))
    if not _constraint_exists(conn, "fk_users_entity"):
        op.create_foreign_key(
            "fk_users_entity", "users", "entities", ["entity_id"], ["id"], ondelete="SET NULL"
        )
    if not _index_exists(conn, "ix_users_entity_id"):
        op.create_index("ix_users_entity_id", "users", ["entity_id"])


def downgrade() -> None:
    conn = op.get_bind()
    if _index_exists(conn, "ix_users_entity_id"):
        op.drop_index("ix_users_entity_id", table_name="users")
    if _constraint_exists(conn, "fk_users_entity"):
        op.drop_constraint("fk_users_entity", "users", type_="foreignkey")
    if _column_exists(conn, "users", "entity_id"):
        op.drop_column("users", "entity_id")
    if _column_exists(conn, "users", "role"):
        op.drop_column("users", "role")

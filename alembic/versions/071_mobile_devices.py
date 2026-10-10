"""Phones signed in to the app (roadmap ROADMAP_ANDROID_CHAT P0.1).

One row per signed-in phone: its name, platform and app version, the hash of
its current refresh token and of the one before (a reused old token revokes
the device), the user's token_version at sign-in, and when it was last seen or
revoked. Safe to run twice.

Revision ID: 071
Revises: 070
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "071"
down_revision: Union[str, None] = "070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "mobile_devices" in _tables():
        return
    op.create_table(
        "mobile_devices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False, server_default="android"),
        sa.Column("app_version", sa.String(32), nullable=True),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refresh_hash", sa.String(64), nullable=False),
        sa.Column("previous_refresh_hash", sa.String(64), nullable=True),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="fk_mobile_devices_user"),
    )
    op.create_index("ix_mobile_devices_user_id", "mobile_devices", ["user_id"])
    op.create_index("ix_mobile_devices_refresh_hash", "mobile_devices", ["refresh_hash"], unique=True)
    op.create_index("ix_mobile_devices_previous_refresh_hash", "mobile_devices", ["previous_refresh_hash"])


def downgrade() -> None:
    if "mobile_devices" in _tables():
        op.drop_table("mobile_devices")

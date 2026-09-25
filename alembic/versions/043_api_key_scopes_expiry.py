"""API key scopes and expiry (roadmap §1.13).

Existing keys keep every scope the API had (time:read, time:write) and no
expiry, so running integrations keep working; new keys default to a year.

Revision ID: 043
Revises: 042
Create Date: 2026-09-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "043"
down_revision: Union[str, None] = "042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {c["name"] for c in sa.inspect(conn).get_columns("api_keys")}
    if "scopes" not in cols:
        op.add_column("api_keys", sa.Column("scopes", sa.Text(), nullable=False,
                                            server_default="time:read,time:write"))
    if "expires_at" not in cols:
        op.add_column("api_keys", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        op.create_index("ix_api_keys_expires_at", "api_keys", ["expires_at"])


def downgrade() -> None:
    conn = op.get_bind()
    cols = {c["name"] for c in sa.inspect(conn).get_columns("api_keys")}
    if "expires_at" in cols:
        op.drop_index("ix_api_keys_expires_at", table_name="api_keys")
        op.drop_column("api_keys", "expires_at")
    if "scopes" in cols:
        op.drop_column("api_keys", "scopes")

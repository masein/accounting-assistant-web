"""Two-factor sign-in columns on users (roadmap §1.13).

All nullable: nobody has 2FA until they turn it on, so existing logins are
unaffected.

Revision ID: 044
Revises: 043
Create Date: 2026-09-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "044"
down_revision: Union[str, None] = "043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("totp_secret", sa.Text()),
    ("totp_pending_secret", sa.Text()),
    ("totp_enabled_at", sa.DateTime(timezone=True)),
    ("totp_last_step", sa.BigInteger()),
    ("totp_recovery", sa.Text()),
)


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    for name, type_ in _COLUMNS:
        if name not in cols:
            op.add_column("users", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    for name, _type in reversed(_COLUMNS):
        if name in cols:
            op.drop_column("users", name)

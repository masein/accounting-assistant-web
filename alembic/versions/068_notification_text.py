"""Notifications worded for each reader's language.

A notification stored its title and message in English; the bell, and web
push, showed them so to a Persian user. Two nullable columns hold what it
says instead — a text key and its values — and the feed renders it in the
reader's language (app/services/notification_text.py). Existing rows keep
their English text; the next refresh fills the new columns.

Revision ID: 068
Revises: 067
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "068"
down_revision: Union[str, None] = "067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    have = _cols("notifications")
    if "text_key" not in have:
        op.add_column("notifications", sa.Column("text_key", sa.String(length=48), nullable=True))
    if "params" not in have:
        op.add_column("notifications", sa.Column("params", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
                                                 nullable=True))


def downgrade() -> None:
    have = _cols("notifications")
    if "params" in have:
        op.drop_column("notifications", "params")
    if "text_key" in have:
        op.drop_column("notifications", "text_key")

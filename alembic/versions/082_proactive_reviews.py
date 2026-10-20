"""Weekly and monthly reviews, suggestions and a nudge, made by the app on its own.

The owner asked for the AI to report on its own each week or month, in the chat
and as a notification, with suggestions (2026-10-20, scenarios AL1–AL16). A
review is worked out from the books once per company and period; each person
who reads the books is shown it once (``proactive_review_seen``) unless they
turned that kind off (``users.review_prefs``, the kinds switched off).

Revision ID: 082
Revises: 081
Create Date: 2026-10-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "082"
down_revision: Union[str, None] = "081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("proactive_reviews"):
        op.create_table(
            "proactive_reviews",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("period_key", sa.String(32), nullable=False),
            sa.Column("facts", _JSON, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("company_id", "kind", "period_key", name="uq_proactive_reviews_period"),
        )
        op.create_index("ix_proactive_reviews_company_id", "proactive_reviews", ["company_id"])
        op.create_index("ix_proactive_reviews_created_at", "proactive_reviews", ["created_at"])
    if not inspector.has_table("proactive_review_seen"):
        op.create_table(
            "proactive_review_seen",
            sa.Column("review_id", UUID(as_uuid=True), sa.ForeignKey("proactive_reviews.id", ondelete="CASCADE"),
                      primary_key=True),
            sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    if "review_prefs" not in {c["name"] for c in inspector.get_columns("users")}:
        op.add_column("users", sa.Column("review_prefs", sa.String(64), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "review_prefs" in {c["name"] for c in inspector.get_columns("users")}:
        op.drop_column("users", "review_prefs")
    for table in ("proactive_review_seen", "proactive_reviews"):
        if inspector.has_table(table):
            op.drop_table(table)

"""Web push (roadmap 2026-09 §4.10, part 2): device subscriptions, and when
each alert was pushed. Alerts that exist already count as pushed, so turning
push on never sends a backlog. Idempotent on fresh and migrated databases.

Revision ID: 051
Revises: 050
Create Date: 2026-09-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "051"
down_revision: Union[str, None] = "050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "pushed_at" not in {c["name"] for c in insp.get_columns("notifications")}:
        op.add_column("notifications", sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True))
        op.execute("UPDATE notifications SET pushed_at = COALESCE(created_at, now()) WHERE pushed_at IS NULL")
    if not insp.has_table("push_subscriptions"):
        op.create_table(
            "push_subscriptions",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("endpoint", sa.String(1024), nullable=False),
            sa.Column("p256dh", sa.String(128), nullable=False),
            sa.Column("auth", sa.String(64), nullable=False),
            sa.Column("user_agent", sa.String(256), nullable=True),
            sa.Column("failures", sa.Integer(), nullable=False),
            sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("company_id", "endpoint", name="uq_push_subscription_endpoint"),
        )
        for col in ("company_id", "user_id"):
            op.create_index(f"ix_push_subscriptions_{col}", "push_subscriptions", [col])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table("push_subscriptions"):
        op.drop_table("push_subscriptions")
    if "pushed_at" in {c["name"] for c in insp.get_columns("notifications")}:
        op.drop_column("notifications", "pushed_at")

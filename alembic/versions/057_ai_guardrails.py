"""AI guardrails (roadmap 2026-09 §5.6): what each proposal moves and said, and
its two-person approval. Idempotent.

Revision ID: 057
Revises: 056
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "057"
down_revision: Union[str, None] = "056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ("summary", lambda: sa.Column("summary", sa.Text(), nullable=True)),
    ("amount", lambda: sa.Column("amount", sa.BigInteger(), nullable=True)),
    ("approval_status", lambda: sa.Column("approval_status", sa.String(16), nullable=True)),
    ("approval_requested_at", lambda: sa.Column("approval_requested_at", sa.DateTime(timezone=True), nullable=True)),
    ("approved_by", lambda: sa.Column("approved_by", sa.String(64), nullable=True)),
    ("approved_at", lambda: sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True)),
    ("approval_note", lambda: sa.Column("approval_note", sa.Text(), nullable=True)),
)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns("ai_proposals")}
    for name, make in COLUMNS:
        if name not in have:
            op.add_column("ai_proposals", make())
    if "ix_ai_proposals_approval_status" not in {i["name"] for i in insp.get_indexes("ai_proposals")}:
        op.create_index("ix_ai_proposals_approval_status", "ai_proposals", ["approval_status"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "ix_ai_proposals_approval_status" in {i["name"] for i in insp.get_indexes("ai_proposals")}:
        op.drop_index("ix_ai_proposals_approval_status", table_name="ai_proposals")
    have = {c["name"] for c in insp.get_columns("ai_proposals")}
    for name, _make in reversed(COLUMNS):
        if name in have:
            op.drop_column("ai_proposals", name)

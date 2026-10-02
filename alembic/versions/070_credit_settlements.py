"""A credit's refunds and applications name the credit they draw on.

A credit note may now go beyond what an invoice still owes (a refund after
payment): the excess is the party's credit, which can be paid back or used
on another invoice. Those rows (note_type 'refund' / 'applied') point at the
credit row they consume through credit_notes.credit_id; what is left of a
credit is its amount less theirs. Adds the nullable column and its index;
safe to run twice.

Revision ID: 070
Revises: 069
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "070"
down_revision: Union[str, None] = "069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table):
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if "credit_id" not in _cols("credit_notes"):
        op.add_column("credit_notes", sa.Column("credit_id", UUID(as_uuid=True), nullable=True))
        op.create_foreign_key("fk_credit_notes_credit_id", "credit_notes", "credit_notes",
                              ["credit_id"], ["id"], ondelete="CASCADE")
    if "ix_credit_notes_credit_id" not in _indexes("credit_notes"):
        op.create_index("ix_credit_notes_credit_id", "credit_notes", ["credit_id"])


def downgrade() -> None:
    if "ix_credit_notes_credit_id" in _indexes("credit_notes"):
        op.drop_index("ix_credit_notes_credit_id", table_name="credit_notes")
    if "credit_id" in _cols("credit_notes"):
        op.drop_constraint("fk_credit_notes_credit_id", "credit_notes", type_="foreignkey")
        op.drop_column("credit_notes", "credit_id")

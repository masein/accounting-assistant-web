"""A statement read in the background, page by page.

A 22-page scanned statement was read 4 pages in one call and the rest never
said (2026-10-20). It is now read a page at a time after the upload returns:
``pages_total`` and ``pages_read`` say how far, ``read_report`` what was read
— pages, rows, the running balance's check, pages that couldn't be read.

Revision ID: 081
Revises: 080
Create Date: 2026-10-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "081"
down_revision: Union[str, None] = "080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (("pages_total", sa.Integer()), ("pages_read", sa.Integer()), ("read_report", sa.Text()))


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("bank_statements")}
    for name, kind in _COLUMNS:
        if name not in have:
            op.add_column("bank_statements", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("bank_statements")}
    for name, _kind in _COLUMNS:
        if name in have:
            op.drop_column("bank_statements", name)

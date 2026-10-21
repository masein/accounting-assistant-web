"""An account's class: the app's own code for what it is.

The app reads what an account is from its code's digits (1 assets, 2 liabilities, 4 revenue, 11 cash…),
in about 95 places. A school that keeps its own codes (creditors 1600, tuition 2000, expenses 3000–5000)
can't use them as they are: creditors would read as assets and tuition as a liability (2026-10-21,
docs/PLAN_OWN_CODES.md). ``accounts.class_code`` keeps the app's code for what the account is when its
``code`` is the company's own; empty for every chart made here, where the class is the code.

Revision ID: 083
Revises: 082
Create Date: 2026-10-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "083"
down_revision: Union[str, None] = "082"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("accounts")}
    if "class_code" not in columns:
        op.add_column("accounts", sa.Column("class_code", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "class_code")

"""A travel category in every person's books.

A person's chart had no travel category: «Amir paid for these» and nine
Airbnbs and tickets went under «متفرقه» (2026-10-18). The personal seed now
has 6185 «سفر» (Travel) under living expenses; this adds it to the books kept
from before, in the language the seed names them in. Data only — no column
changes — and safe to run twice: a book that has a 6185 already keeps it.

Revision ID: 079
Revises: 078
Create Date: 2026-10-18
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "079"
down_revision: Union[str, None] = "078"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CODE, _PARENT = "6185", "61"


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"companies", "accounts"} <= tables:
        return
    pg = bind.dialect.name == "postgresql"
    cast = (lambda p, t: f"CAST(:{p} AS {t})") if pg else (lambda p, t: f":{p}")
    people = bind.execute(sa.text("SELECT id, locale FROM companies WHERE kind = 'personal'")).fetchall()
    for company_id, locale in people:
        if bind.execute(sa.text("SELECT 1 FROM accounts WHERE company_id = :c AND code = :code"),
                        {"c": company_id, "code": _CODE}).first():
            continue
        parent = bind.execute(sa.text("SELECT id FROM accounts WHERE company_id = :c AND code = :code"),
                              {"c": company_id, "code": _PARENT}).first()
        if parent is None:                       # not the personal chart's shape: leave it
            continue
        # the id in the form the rows beside it are stored in (SQLite keeps 32 hex digits)
        new_id = str(uuid.uuid4()) if pg or "-" in str(parent[0]) else uuid.uuid4().hex
        bind.execute(sa.text(
            f"INSERT INTO accounts (id, company_id, code, name, level, parent_id) VALUES "
            f"({cast('id', 'uuid')}, :c, :code, :name, {cast('level', 'account_level')}, :parent)"),
            {"id": new_id, "c": company_id, "code": _CODE,
             "name": "Travel" if (locale or "").strip().lower() == "uk" else "سفر",
             "level": "GENERAL", "parent": parent[0]})


def downgrade() -> None:
    # An account a person may have posted to; nothing to undo.
    pass

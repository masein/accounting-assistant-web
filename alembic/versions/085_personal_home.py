"""A home-supplies category in every person's books.

A person's chart had nowhere for the things a home needs: two power adapters,
stationery and a key cut went under «خانواده و هدایا» (Family & gifts) on the
owner's phone (2026-10-21). The personal seed now has 6125 «لوازم و تعمیرات
خانه» (Home supplies & repairs) under living expenses; this adds it to the
books kept from before, as 079 added Travel. Data only, and safe to run twice:
a book that has a 6125 already keeps it.

Revision ID: 085
Revises: 084
Create Date: 2026-10-21
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "085"
down_revision: Union[str, None] = "084"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CODE, _PARENT = "6125", "61"


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
             "name": "Home supplies & repairs" if (locale or "").strip().lower() == "uk" else "لوازم و تعمیرات خانه",
             "level": "GENERAL", "parent": parent[0]})


def downgrade() -> None:
    # An account a person may have posted to; nothing to undo.
    pass

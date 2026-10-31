"""Net worth is «دارایی خالص» in a person's books too.

The owner chose «دارایی خالص» for net worth (2026-10-31), and the labels now
say it; the personal seed named the group 31 «خالص دارایی» and the opening
account 3110 «خالص دارایی اولیه», so a card showed one word on its title and
the other on its lines. The seed now says «دارایی خالص» / «دارایی خالص
اولیه»; this renames them in the books kept from before. Only a name that is
still the seed's own changes — one the person renamed keeps theirs — and it
is safe to run twice. Data only.

Revision ID: 086
Revises: 085
Create Date: 2026-10-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "086"
down_revision: Union[str, None] = "085"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# code → (the seed's old name, its new one)
RENAMES = {"31": ("خالص دارایی", "دارایی خالص"), "3110": ("خالص دارایی اولیه", "دارایی خالص اولیه")}


def _rename(frm: int, to: int) -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if not {"companies", "accounts"} <= tables:
        return
    for code, names in RENAMES.items():
        bind.execute(sa.text(
            "UPDATE accounts SET name = :new WHERE code = :code AND name = :old "
            "AND company_id IN (SELECT id FROM companies WHERE kind = 'personal')"),
            {"code": code, "old": names[frm], "new": names[to]})


def upgrade() -> None:
    _rename(0, 1)


def downgrade() -> None:
    _rename(1, 0)

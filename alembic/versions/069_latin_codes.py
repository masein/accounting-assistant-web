"""Party codes, SKUs and barcodes in 0–9.

Typed on a Persian keyboard, a party's code was stored as «۱۰۱» and a barcode
as «۶۲۹۱…»: a lookup or export by 101 missed the party, and a scanner (which
sends 0–9) never matched the item (deep browser test, 2026-10-02). New input is
normalised by the schemas; this rewrites the rows already stored. Data only —
no column changes — and safe to run twice.

Revision ID: 069
Revises: 068
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "069"
down_revision: Union[str, None] = "068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_COLUMNS = {"entities": ("code",), "inventory_items": ("sku", "barcode")}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for table, columns in _COLUMNS.items():
        if table not in tables:
            continue
        for column in columns:
            rows = bind.execute(sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")).fetchall()
            for row_id, value in rows:
                latin = value.translate(_TABLE)
                if latin != value:
                    bind.execute(sa.text(f"UPDATE {table} SET {column} = :v WHERE id = :id"), {"v": latin, "id": row_id})


def downgrade() -> None:
    # 0–9 is what the codes should always have been; nothing to undo.
    pass

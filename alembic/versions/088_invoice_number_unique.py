"""Invoice numbers and سامانه مؤدیان serials unique in the database (review 2026-11-28).

Invoice numbers were unique per (company, kind) only in the API
(``_assert_number_free``); two requests at the same moment could both pass the
check, and the OCR import never ran it. Production holds one such pair: a sales
number shared by an issued invoice and a voided one of the same party. A plain
UNIQUE (company_id, kind, number) would refuse that pair and fail every deploy,
and a voided invoice is meant to give its number up anyway, so the index is
partial: voided and canceled invoices are left out (the API follows the same
rule).

The Moadian serial (the sequence inside the 22-character tax number) was
allocated as max + 1 with nothing stopping two exports taking the same one;
``uq_invoices_company_moadian_serial`` now refuses a second invoice with a
company's serial, and the allocation retries (``builder.assign_serial``).

Both are partial unique indexes, which PostgreSQL and SQLite both support. If
rows already break one of them, that index is not created and a warning names
the rows: a deploy is never blocked on a data decision someone has to make.
A fresh database gets both from ``create_all``; safe to run twice.

Revision ID: 088
Revises: 087
Create Date: 2026-11-28
"""
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "088"
down_revision: str | None = "087"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "invoices"
LIVE = "status NOT IN ('voided', 'canceled')"
SERIAL_SET = "moadian_serial IS NOT NULL"
INDEXES = (
    # name, columns, where
    ("uq_invoices_company_kind_number", ("company_id", "kind", "number"), LIVE),
    ("uq_invoices_company_moadian_serial", ("company_id", "moadian_serial"), SERIAL_SET),
)

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if TABLE not in insp.get_table_names():
        return
    have = {i["name"] for i in insp.get_indexes(TABLE)}
    for name, cols, where in INDEXES:
        if name in have:
            continue
        col_list = ", ".join(cols)
        clashes = bind.execute(sa.text(
            f"SELECT {col_list}, COUNT(*) AS n FROM {TABLE} WHERE {where} "
            f"GROUP BY {col_list} HAVING COUNT(*) > 1")).all()
        if clashes:
            log.warning("088: %s not created: %d group(s) of invoices already share %s: %s",
                        name, len(clashes), col_list, [tuple(r) for r in clashes[:20]])
            continue
        op.execute(f"CREATE UNIQUE INDEX {name} ON {TABLE} ({col_list}) WHERE {where}")


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return
    have = {i["name"] for i in insp.get_indexes(TABLE)}
    for name, _cols, _where in INDEXES:
        if name in have:
            op.drop_index(name, table_name=TABLE)

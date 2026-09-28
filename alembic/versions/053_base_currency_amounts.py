"""Base-currency amounts on every journal line (roadmap 2026-09 §4.6, option 2).

Each entry keeps the rate it was converted at (``transactions.fx_rate``) and
each line its value in the company's base currency (``base_debit`` /
``base_credit``), as Xero and QuickBooks do. Here:

* entries already in the company's base currency get rate 1 and base = amount;
* foreign entries stay NULL — the app converts them at the rate of their date
  on the next boot (``app.services.fx_base.fill_pending``), and any with no
  rate at all wait until one is added;
* the placeholder USD→IRR rate (150,000) once seeded is removed if untouched;
* a revaluation posted before today mirrored foreign balances in the base
  currency; in base terms that would count them twice, so those entries keep
  their lines but get base amounts of 0 (``fx_role = 'legacy_revaluation'``)
  and the next revaluation starts from the real base values.

Idempotent on fresh and migrated databases.

Revision ID: 053
Revises: 052
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "053"
down_revision: Union[str, None] = "052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tcols = {c["name"] for c in insp.get_columns("transactions")}
    if "fx_rate" not in tcols:
        op.add_column("transactions", sa.Column("fx_rate", sa.Float(), nullable=True))
    if "fx_role" not in tcols:
        op.add_column("transactions", sa.Column("fx_role", sa.String(24), nullable=True))
    lcols = {c["name"] for c in insp.get_columns("transaction_lines")}
    for name in ("base_debit", "base_credit"):
        if name not in lcols:
            op.add_column("transaction_lines", sa.Column(name, sa.BigInteger(), nullable=True))

    # The placeholder USD→IRR rate the app used to seed: it would be fixed into
    # every USD entry now. Only the untouched seed row goes.
    op.execute("""
        DELETE FROM exchange_rates
        WHERE company_id IS NULL AND from_currency = 'USD' AND to_currency = 'IRR' AND rate = 150000
          AND note = 'Default seed rate — update in Settings → Currency & FX'
    """)
    # Old mirror revaluations: kept, but worth nothing in base terms.
    op.execute("""
        UPDATE transactions t SET fx_role = 'legacy_revaluation'
        WHERE t.fx_role IS NULL
          AND (t.reference LIKE 'FX-REVAL-%' OR t.description LIKE 'FX revaluation to %')
          AND EXISTS (SELECT 1 FROM transaction_lines l
                      WHERE l.transaction_id = t.id AND l.line_description LIKE 'Reval %->%')
    """)
    op.execute("""
        UPDATE transaction_lines l SET base_debit = 0, base_credit = 0
        FROM transactions t
        WHERE l.transaction_id = t.id AND t.fx_role = 'legacy_revaluation' AND l.base_debit IS NULL
    """)
    # Entries in the company's own base currency: rate 1, base = amount.
    op.execute("""
        UPDATE transactions t SET fx_rate = 1
        FROM companies c
        WHERE t.company_id = c.id AND t.fx_rate IS NULL AND t.fx_role IS NULL
          AND UPPER(COALESCE(t.currency, 'IRR')) = UPPER(COALESCE(c.base_currency, 'IRR'))
    """)
    op.execute("""
        UPDATE transaction_lines l SET base_debit = l.debit, base_credit = l.credit
        FROM transactions t
        WHERE l.transaction_id = t.id AND t.fx_rate = 1 AND t.fx_role IS NULL AND l.base_debit IS NULL
    """)


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    lcols = {c["name"] for c in insp.get_columns("transaction_lines")}
    for name in ("base_credit", "base_debit"):
        if name in lcols:
            op.drop_column("transaction_lines", name)
    tcols = {c["name"] for c in insp.get_columns("transactions")}
    for name in ("fx_role", "fx_rate"):
        if name in tcols:
            op.drop_column("transactions", name)

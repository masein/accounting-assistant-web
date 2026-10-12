"""Money columns keep pence: BIGINT → NUMERIC (no fixed scale).

The books kept whole units of every currency, so a £7.50 bank fee went in as
£7 (owner's decision, 2026-10-12: keep pence; docs/PLAN_PENCE.md). NUMERIC
without a scale keeps each value as written: every existing amount stays the
same integer, sums of whole amounts stay whole, and an amount with pence keeps
them. Rials are unchanged byte for byte. Each table is rewritten once (one
ALTER per table). Safe to run twice: a column already numeric is left alone.

Revision ID: 075
Revises: 074
Create Date: 2026-10-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "075"
down_revision: Union[str, None] = "074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# app/core/money.MoneyType columns (the models' metadata, 66 of them)
MONEY = {
    "adjustments": ("amount", "residual"),
    "ai_proposals": ("amount",),
    "bank_statement_rows": ("balance", "credit", "debit"),
    "budget_limits": ("limit_amount",),
    "commitments": ("amount",),
    "companies": ("registered_capital",),
    "credit_notes": ("amount",),
    "digest_settings": ("cash_threshold",),
    "employee_pay_profiles": ("base_salary", "hourly_rate"),
    "equity_events": ("amount",),
    "fixed_asset_depreciation": ("amount",),
    "fixed_assets": ("cost", "disposal_proceeds", "opening_accumulated", "residual"),
    "inventory_items": ("list_price",),
    "inventory_movements": ("unit_cost",),
    "invoice_items": ("line_total", "unit_cost", "unit_price"),
    "invoices": ("amount",),
    "mileage_claims": ("amount",),
    "pay_run_lines": ("allowances", "eidi", "employer_social", "gross", "income_tax", "insurable_wage",
                      "net_pay", "pre_tax_deductions", "sanavat", "social_security", "taxable_base"),
    "pay_runs": ("total_deductions", "total_employer_social", "total_gross", "total_net", "total_social",
                 "total_tax"),
    "payments": ("amount",),
    "petty_cash_transactions": ("amount", "signed_amount"),
    "projects": ("budget_amount",),
    "purchase_order_lines": ("line_total", "unit_price"),
    "quote_items": ("line_total", "unit_cost", "unit_price"),
    "quotes": ("amount",),
    "recurring_invoices": ("amount",),
    "recurring_rules": ("amount",),
    "savings_goals": ("target_amount",),
    "shareholdings": ("par_value",),
    "transaction_entities": ("amount",),
    "transaction_lines": ("base_credit", "base_debit", "credit", "debit"),
    "trial_balance_lines": ("credit_balance", "credit_turnover", "debit_balance", "debit_turnover"),
}


def _retype(to_numeric: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return                                  # SQLite (tests) builds the schema from the models
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    for table, columns in MONEY.items():
        if table not in tables:
            continue
        have = {c["name"]: c["type"] for c in insp.get_columns(table)}
        if to_numeric:
            todo = [c for c in columns if c in have and not isinstance(have[c], sa.Numeric)]
            clauses = [f'ALTER COLUMN "{c}" TYPE NUMERIC USING "{c}"::numeric' for c in todo]
        else:
            todo = [c for c in columns if c in have and isinstance(have[c], sa.Numeric)
                    and not isinstance(have[c], sa.BigInteger)]
            # back to whole units: pence are rounded half-up and lost
            clauses = [f'ALTER COLUMN "{c}" TYPE BIGINT USING round("{c}")::bigint' for c in todo]
        if clauses:
            op.execute(f'ALTER TABLE "{table}" ' + ", ".join(clauses))


def upgrade() -> None:
    _retype(True)


def downgrade() -> None:
    _retype(False)

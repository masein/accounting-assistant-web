"""Review 2026-11-16: both income statements floored every account's period
balance at 0, so a contra balance — sales returns on a revenue account, a
recovered expense, an FX gain booked on an expense-class account — vanished
and profit was overstated. The Iranian statement also read the "other
non-operating" (63) and "discontinued operations" (68) rows in the expense
sense, so a net loss there was ADDED to profit and a net gain dropped."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest


def _acc(code: str):
    return SimpleNamespace(id=uuid.uuid4(), code=code, name=f"acc {code}")


@pytest.fixture()
def books(monkeypatch):
    """Accounts with a period's (debit, credit) turnovers, fed to both builders."""
    from app.services.reporting import financial_statement_service as fss
    from app.services.reporting import iran_statement_service as iss

    def install(rows: list[tuple[str, int, int]]):
        accounts = [_acc(code) for code, _d, _c in rows]
        turnovers = [(a.id, d, c) for a, (_code, d, c) in zip(accounts, rows)]
        for mod in (fss, iss):
            monkeypatch.setattr(mod, "list_accounts", lambda _db: accounts)
            monkeypatch.setattr(mod, "account_turnovers_between", lambda *_a, **_k: turnovers)
        return accounts
    return install


def test_contra_revenue_reduces_revenue_and_profit(db, books):
    from datetime import date

    from app.services.reporting.financial_statement_service import build_income_statement
    books([
        ("4110", 0, 1_000_000),        # sales
        ("4120", 150_000, 0),          # sales returns: a debit balance on revenue
        ("6110", 300_000, 0),          # wages
        ("6112", 0, 40_000),           # an expense recovered in the period: credit balance
    ])
    st = build_income_statement(db, date(2026, 1, 1), date(2026, 1, 31))
    assert st.totals["revenue"] == 850_000
    assert st.totals["operating_expenses"] == 260_000
    assert st.totals["net_profit"] == 590_000
    returns = next(n for n in st.sections["revenues"].items if n.account_code == "4120")
    assert returns.balance == -150_000


def test_iran_buckets_keep_contra_balances_and_read_net_rows_as_income(db, books):
    from datetime import date

    from app.services.reporting.iran_statement_service import _bucket_totals
    accounts = books([
        ("4110", 0, 1_000_000),
        ("4120", 150_000, 0),          # returns
        ("6110", 300_000, 0),
        ("6310", 70_000, 0),           # non-operating: a net loss …
        ("6320", 0, 20_000),           # … and a smaller gain
        ("6810", 0, 5_000),            # discontinued operations: a gain
    ])
    b = _bucket_totals(db, accounts, date(2026, 1, 1), date(2026, 1, 31), None)
    assert b["revenue_operating"] == 850_000
    assert b["opex_sga"] == 300_000
    assert b["non_operating_net"] == -50_000          # a loss takes from profit
    assert b["discontinued_ops"] == 5_000


def test_iran_net_profit_matches_the_books(db, books):
    from datetime import date

    from app.services.reporting.iran_statement_service import _period_net_profit
    books([
        ("4110", 0, 1_000_000),
        ("4120", 150_000, 0),
        ("6110", 300_000, 0),
        ("6310", 70_000, 0),
        ("6320", 0, 20_000),
    ])
    # income 1,000,000 + 20,000 − returns 150,000 − wages 300,000 − loss 70,000
    assert _period_net_profit(db, date(2026, 1, 1), date(2026, 1, 31), None) == 500_000

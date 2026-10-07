"""CEO Mode's balance-sheet summary balances (scenario L9).

It summed asset, liability and equity accounts only, so before the year was
closed the period's result was missing from equity: in retest 3 (2026-10-07)
Arman showed assets 1,970,825,000 against liabilities 107,535,000 and equity
1,900,000,000 — a gap of exactly its 36,710,000 loss. The formal balance
sheet carries the result in equity; so does this summary now, as its own line.
"""
from __future__ import annotations

from app.db.tenant import use_company
from tests.test_cfo_receivables import _code, _journal, co  # noqa: F401 — the fixture


def test_the_summary_balances_with_the_period_result_in_equity(co, db):
    from app.services.cfo_intelligence import build_ceo_report
    bank, capital = _code(db, co, "bank"), _code(db, co, "share_capital")
    revenue, expense = _code(db, co, "revenue"), _code(db, co, "expense")
    _journal(db, co, (bank, 1_000, 0), (capital, 0, 1_000))       # the owners put in 1,000
    _journal(db, co, (bank, 300, 0), (revenue, 0, 300))           # a sale of 300
    _journal(db, co, (expense, 500, 0), (bank, 0, 500))           # an expense of 500
    with use_company(co["cid"]):
        r = build_ceo_report(db, lang="fa")
    assert (r.total_assets, r.total_liabilities, r.total_equity) == (800, 0, 800)   # equity was 1,000
    assert r.total_assets == r.total_liabilities + r.total_equity
    lines = {e["name"]: e["balance"] for e in r.equity_breakdown}
    assert lines == {next(e["name"] for e in r.equity_breakdown if e["code"] == capital): 1_000,
                     "سود (زیان) دوره جاری": -200}


def test_a_profit_adds_to_equity_and_a_memo_account_stays_out(co, db):
    from app.models.account import Account, AccountLevel
    from app.services.cfo_intelligence import build_ceo_report
    bank, revenue = _code(db, co, "bank"), _code(db, co, "revenue")
    with use_company(co["cid"]):
        db.add_all([Account(code="9110", name="memo debit", level=AccountLevel.GENERAL),
                    Account(code="9120", name="memo credit", level=AccountLevel.GENERAL)])
        db.commit()
    _journal(db, co, (bank, 700, 0), (revenue, 0, 700))
    _journal(db, co, ("9110", 5_000, 0), ("9120", 0, 5_000))      # off the balance sheet
    with use_company(co["cid"]):
        r = build_ceo_report(db, lang="en")
    assert (r.total_assets, r.total_liabilities, r.total_equity) == (700, 0, 700)
    assert {"code": "", "name": "Current period profit (loss)", "balance": 700} in r.equity_breakdown


def test_no_activity_no_result_line(co, db):
    from app.services.cfo_intelligence import build_ceo_report
    bank, capital = _code(db, co, "bank"), _code(db, co, "share_capital")
    _journal(db, co, (bank, 1_000, 0), (capital, 0, 1_000))
    with use_company(co["cid"]):
        r = build_ceo_report(db)
    assert r.total_equity == 1_000 and all(e["code"] for e in r.equity_breakdown)


def test_the_report_sends_the_lines_behind_the_totals(co, db):
    """The donut's drill-down lists them; the endpoint never sent them."""
    bank, capital, expense = _code(db, co, "bank"), _code(db, co, "share_capital"), _code(db, co, "expense")
    _journal(db, co, (bank, 1_000, 0), (capital, 0, 1_000))
    _journal(db, co, (expense, 400, 0), (bank, 0, 400))
    d = co["api"].get("/brain/ceo/report").json()
    assert [(e["code"], e["balance"]) for e in d["assets_breakdown"]] == [(bank, 600)]
    assert sorted((e["code"], e["balance"]) for e in d["equity_breakdown"]) == [("", -400), (capital, 1_000)]
    assert d["total_assets"] == d["total_liabilities"] + d["total_equity"] == 600

"""Balance sheet best practice (QA 2026-09-24, MEDIUM 2.24).

The standard balance sheet clamped every balance to zero and had no line for
the period's unclosed profit, so assets never equalled liabilities + equity
(1110 at −701,820 rendered 0; net profit 3,348,180 missing from equity).
Now balances keep their sign and unclosed earnings appear under equity, on
the standard, Iran and trend (periods) statements.

Each test posts its vouchers in a private currency code so the shared test
database cannot leak into the equation.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from tests.conftest import _CSRFTestClient

CASH, CAPITAL, REVENUE, EXPENSE = "1110", "3110", "4110", "6112"
DAY = date(2026, 5, 10)
AS_OF = "2026-12-31"


def _api(client):
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner")
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def _post(db, make_transaction, currency, lines):
    tx = make_transaction(lines, tx_date=DAY, reference=f"bs-{uuid.uuid4().hex[:6]}")
    tx.currency = currency
    return tx


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.get("children") or [])


@pytest.fixture()
def funded_books(db, make_transaction):
    """Capital 5M, sales 3M, expenses 1M → assets 7M = equity 5M + earnings 2M."""
    ccy = "X" + uuid.uuid4().hex[:5].upper()
    _post(db, make_transaction, ccy, [(CASH, 5_000_000, 0), (CAPITAL, 0, 5_000_000)])
    _post(db, make_transaction, ccy, [(CASH, 3_000_000, 0), (REVENUE, 0, 3_000_000)])
    _post(db, make_transaction, ccy, [(EXPENSE, 1_000_000, 0), (CASH, 0, 1_000_000)])
    db.commit()
    return ccy


@pytest.fixture()
def overdrawn_books(db, make_transaction):
    """An expense paid from an empty cash account: cash −1M, loss −1M."""
    ccy = "N" + uuid.uuid4().hex[:5].upper()
    _post(db, make_transaction, ccy, [(EXPENSE, 1_000_000, 0), (CASH, 0, 1_000_000)])
    db.commit()
    return ccy


def test_standard_balance_sheet_balances_with_unclosed_earnings(client, funded_books):
    data = _api(client).get("/manager-reports/financial/balance-sheet",
                            params={"to_date": AS_OF, "currency": funded_books}).json()
    totals = data["totals"]
    assert totals["assets"] == 7_000_000
    assert totals["liabilities"] == 0
    assert totals["equity"] == 7_000_000  # 5M capital + 2M unclosed profit
    assert totals["liabilities_and_equity"] == 7_000_000
    assert totals["assets"] == totals["liabilities_and_equity"]

    earnings = [n for n in _walk(data["sections"]["equity"]["items"]) if n["is_computed"]]
    assert len(earnings) == 1
    assert earnings[0]["balance"] == 2_000_000
    assert earnings[0]["account_type"] == "EQUITY"
    assert not any("does not balance" in w for w in data["analysis"]["warnings"])


def test_standard_balance_sheet_keeps_negative_asset_sign(client, overdrawn_books):
    data = _api(client).get("/manager-reports/financial/balance-sheet",
                            params={"to_date": AS_OF, "currency": overdrawn_books}).json()
    cash = next(n for n in _walk(data["sections"]["assets"]["items"]) if n["account_code"] == CASH)
    assert cash["balance"] == -1_000_000  # not clamped to 0
    totals = data["totals"]
    assert totals["assets"] == -1_000_000
    assert totals["equity"] == -1_000_000  # the loss
    assert totals["assets"] == totals["liabilities_and_equity"]


def test_standard_balance_sheet_without_earnings_has_no_computed_line(client, db, make_transaction):
    ccy = "Z" + uuid.uuid4().hex[:5].upper()
    _post(db, make_transaction, ccy, [(CASH, 500, 0), (CAPITAL, 0, 500)])
    db.commit()
    data = _api(client).get("/manager-reports/financial/balance-sheet",
                            params={"to_date": AS_OF, "currency": ccy}).json()
    assert not [n for n in _walk(data["sections"]["equity"]["items"]) if n["is_computed"]]
    assert data["totals"]["assets"] == data["totals"]["liabilities_and_equity"] == 500


def test_balance_sheet_trend_includes_unclosed_earnings(client, funded_books):
    data = _api(client).get("/manager-reports/financial/balance-sheet-periods",
                            params={"from_date": "2026-05-01", "to_date": "2026-06-30",
                                    "granularity": "monthly", "currency": funded_books}).json()
    last = data["periods"][-1]
    assert last["assets"] == 7_000_000
    assert last["equity"] == 7_000_000
    assert last["assets"] == last["liabilities"] + last["equity"]


def test_iran_balance_sheet_keeps_negative_cash_and_balances(client, overdrawn_books):
    data = _api(client).get("/manager-reports/financial/iran/balance-sheet",
                            params={"as_of": AS_OF, "currency": overdrawn_books}).json()
    rows = {r["key"]: r for r in data["rows"]}
    assert rows["ca_cash"]["amount_current"] == -1_000_000
    assert rows["eq_retained_earnings"]["amount_current"] == -1_000_000
    assert rows["total_assets"]["amount_current"] == rows["total_equity_and_liabilities"]["amount_current"]
    assert data["metadata"]["balances"]["assets_equal_equity_plus_liabilities"] is True


def test_frontend_shows_the_equation_check():
    js = open("app/static/js/07-chat-reports.js", encoding="utf-8").read()
    assert "liabilities_and_equity" in js and "bsNotBalanced" in js
    i18n = open("app/static/js/02-i18n.js", encoding="utf-8").read()
    for key in ("bsLiabilitiesAndEquity:", "bsBalanced:", "bsNotBalanced:"):
        assert i18n.count(key) == 4, key

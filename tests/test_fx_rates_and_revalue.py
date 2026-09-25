"""FX rates, conversion and period-end revaluation (roadmap 2026-09 §6,
suite 7): half-up rounding (Python's round() is half-to-even), rial sums
far beyond float precision, rate CRUD and lookup rules, and revaluation that
posts every account's adjustment (even when gains and losses net to zero)
and is idempotent."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.services import fx_service


# ─── 1. Exact, half-up conversion ──────────────────────────────────────

@pytest.mark.parametrize("amount,rate,expected", [
    (5, 0.5, 3),                 # 2.5 → 3 (round() gives 2)
    (3, 0.5, 2),                 # 1.5 → 2
    (-5, 0.5, -3),               # symmetric away from zero
    (1, 0.4, 0),
    (7, 150_000.0, 1_050_000),
    (123_456_789_012_345_678, 1.0, 123_456_789_012_345_678),   # 1.2e17 rial survives
    (10 ** 17 + 1, 0.5, 5 * 10 ** 16 + 1),                       # 5e16 + 0.5 → up
])
def test_convert_minor_is_exact_and_half_up(amount, rate, expected):
    assert fx_service.convert_minor(amount, rate) == expected


def test_float_path_would_have_lost_units():
    """The pin: the old float product is off for large rial amounts."""
    amount = 123_456_789_012_345_679
    assert int(round(float(amount) * 1.0)) != amount
    assert fx_service.convert_minor(amount, 1.0) == amount


# ─── 2. Rates over HTTP ────────────────────────────────────────────────

@pytest.fixture()
def co(client, db):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.db.seed import seed_chart_if_empty
    from app.db.tenant import use_company
    from app.models.company import Company
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company

    c = Company(id=uuid.uuid4(), name="FX Co", slug=f"fx-{uuid.uuid4().hex[:6]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner",
                               company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    db.expunge_all()
    yield _CSRFTestClient(client, csrf), cid
    client.cookies.clear()
    _purge_company(db, cid)


def _rate(api, fc, tc, rate, when):
    r = api.post("/fx/rates", json={"from_currency": fc, "to_currency": tc, "rate": rate, "effective_date": when})
    assert r.status_code == 201, r.text
    return r.json()


def test_rates_crud_and_lookup_rules(co, db):
    api, cid = co
    a = _rate(api, "usd", "irr", 1_000_000, "2026-01-01")
    assert a["from_currency"] == "USD" and a["to_currency"] == "IRR"
    again = _rate(api, "USD", "IRR", 1_050_000, "2026-01-01")                 # same day → updated, not duplicated
    assert again["id"] == a["id"] and again["rate"] == 1_050_000
    _rate(api, "USD", "IRR", 1_100_000, "2026-06-01")
    assert api.post("/fx/rates", json={"from_currency": "EUR", "to_currency": "eur", "rate": 1,
                                       "effective_date": "2026-01-01"}).status_code == 400
    assert api.post("/fx/rates", json={"from_currency": "EUR", "to_currency": "IRR", "rate": 0,
                                       "effective_date": "2026-01-01"}).status_code == 422
    listed = api.get("/fx/rates", params={"from_currency": "usd"}).json()
    assert [r["effective_date"] for r in listed] == ["2026-06-01", "2026-01-01"]

    from app.db.tenant import use_company
    with use_company(cid):
        assert fx_service.get_rate(db, "USD", "IRR", date(2026, 3, 1)) == 1_050_000     # latest on/before
        assert fx_service.get_rate(db, "USD", "IRR", date(2026, 7, 1)) == 1_100_000
        assert fx_service.get_rate(db, "IRR", "USD", date(2026, 7, 1)) == pytest.approx(1 / 1_100_000)
        assert fx_service.get_rate(db, "USD", "USD") == 1.0
        assert fx_service.get_rate(db, "GBP", "IRR") is None

    conv = api.post("/fx/convert", json={"amount": 3, "from_currency": "USD", "to_currency": "IRR",
                                         "on_date": "2026-07-01"}).json()
    assert conv["rate"] == 1_100_000 and conv["converted"] == 3_300_000
    missing = api.post("/fx/convert", json={"amount": 1, "from_currency": "GBP", "to_currency": "IRR"}).json()
    assert missing["converted"] is None and "No rate" in missing["error"]
    assert api.delete(f"/fx/rates/{a['id']}").status_code == 204
    assert api.delete(f"/fx/rates/{a['id']}").status_code == 404


# ─── 3. Revaluation ────────────────────────────────────────────────────

def _txn(api, when, lines, currency):
    r = api.post("/transactions", json={"date": when, "description": "fx", "currency": currency, "lines": lines})
    assert r.status_code == 201, r.text
    return r.json()


def _l(code, dr=0, cr=0):
    return {"account_code": code, "debit": dr, "credit": cr}


def _reval(api, as_of, *, codes, dry_run=True):
    body = {"as_of": as_of, "target_currency": "IRR", "account_codes": codes, "dry_run": dry_run}
    if not dry_run:
        body.update({"gain_account_code": "4110", "loss_account_code": "6210"})
    return api.post("/fx/revalue", json=body)


def _lines_of(db, cid, tid):
    from app.db.tenant import use_company
    from app.models.account import Account
    from app.models.transaction import TransactionLine
    with use_company(cid):
        rows = db.execute(select(Account.code, TransactionLine.debit, TransactionLine.credit)
                          .join(Account, Account.id == TransactionLine.account_id)
                          .where(TransactionLine.transaction_id == uuid.UUID(tid))).all()
    return {code: (int(d), int(c)) for code, d, c in rows}


def test_revaluation_posts_every_adjustment_and_is_idempotent(co, db):
    api, cid = co
    _rate(api, "USD", "IRR", 1_000_000, "2026-01-01")
    # USD cash in, owed to a supplier in USD: an asset and a liability.
    _txn(api, "2026-02-01", [_l("1110", dr=100), _l("2110", cr=100)], "USD")
    pv = _reval(api, "2026-03-31", codes=["1110", "2110"]).json()
    adj = {x["account_code"]: x["adjustment"] for x in pv["lines"]}
    assert adj == {"1110": 100_000_000, "2110": -100_000_000} and pv["total_adjustment"] == 0

    posted = _reval(api, "2026-03-31", codes=["1110", "2110"], dry_run=False).json()
    # Gains and losses net to zero, but both balances must still move.
    assert posted["posted_transaction_id"], posted
    lines = _lines_of(db, cid, posted["posted_transaction_id"])
    assert lines["1110"] == (100_000_000, 0) and lines["2110"] == (0, 100_000_000)
    assert "4110" not in lines and "6210" not in lines

    # Run again: nothing left to adjust, nothing posted.
    again = _reval(api, "2026-03-31", codes=["1110", "2110"], dry_run=False).json()
    assert again["total_adjustment"] == 0 and again["posted_transaction_id"] is None
    assert all(x["adjustment"] == 0 for x in again["lines"])


def test_a_rate_change_posts_only_the_difference_as_gain(co, db):
    api, cid = co
    _rate(api, "USD", "IRR", 1_000_000, "2026-01-01")
    _txn(api, "2026-02-01", [_l("1110", dr=50), _l("3110", cr=50)], "USD")
    first = _reval(api, "2026-03-31", codes=["1110"], dry_run=False).json()
    assert first["total_adjustment"] == 50_000_000
    assert _lines_of(db, cid, first["posted_transaction_id"])["4110"] == (0, 50_000_000)   # gain
    _rate(api, "USD", "IRR", 900_000, "2026-06-01")
    second = _reval(api, "2026-06-30", codes=["1110"], dry_run=False).json()
    assert second["total_adjustment"] == -5_000_000                                     # 50 × −100,000
    assert _lines_of(db, cid, second["posted_transaction_id"])["6210"] == (5_000_000, 0)  # loss
    assert _reval(api, "2026-06-30", codes=["1110"]).json()["total_adjustment"] == 0


def test_revaluation_rounds_half_up(co):
    api, _ = co
    _rate(api, "USD", "IRR", 0.5, "2026-01-01")
    _txn(api, "2026-02-01", [_l("1110", dr=5), _l("3110", cr=5)], "USD")
    pv = _reval(api, "2026-03-31", codes=["1110"]).json()
    assert pv["lines"][0]["target_balance"] == 3                                        # 2.5 → 3


def test_revaluation_guards(co):
    api, _ = co
    _txn(api, "2026-02-01", [_l("1110", dr=10), _l("3110", cr=10)], "GBP")
    pv = _reval(api, "2026-03-31", codes=["1110"]).json()
    assert any("Missing rate GBP->IRR" in e for e in pv["errors"]) and pv["posted_transaction_id"] is None
    _rate(api, "GBP", "IRR", 1_500_000, "2026-01-01")
    r = api.post("/fx/revalue", json={"as_of": "2026-03-31", "target_currency": "IRR", "account_codes": ["1110"],
                                      "dry_run": False})
    assert r.status_code == 400                                                         # no gain/loss accounts
    assert _reval(api, "2026-03-31", codes=["9999"]).status_code == 400
    assert api.put("/admin/closed-period", json={"closed_period": "2026-03-31"}).status_code == 200
    assert _reval(api, "2026-03-31", codes=["1110"], dry_run=False).status_code in (409, 422)

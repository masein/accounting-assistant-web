"""The reviews' figures (chat review 2026-11-10, scenarios BW1–BW3).

From production: a person's monthly review said net worth rose in a month of spending — the starting balances
recorded that month and money changed from dollars counted as gains; a company's review said cash lasts 1.7
months beside an insight's 0.4 on the same day — one averaged the gross costs with the month just begun, the
other the net burn of the last three complete months; and the review said the runway twice. Figures made up.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.db.tenant import use_company
from app.services.ai_eval.fixture import seed_personal_books
from tests.test_statement_export import _company


@pytest.fixture()
def person(db):
    ctx = seed_personal_books(db)
    yield ctx
    from tests.test_admin_audit import _purge_company
    _purge_company(db, ctx.company_id)


def test_bw1_starting_balances_are_not_gains(db, person):
    from app.services.report_card import net_worth_change, report_card
    today = date.today()
    with use_company(person.company_id):
        worth = net_worth_change(db, today - timedelta(days=40), today, "OMR")
        # 100 + 60 OMR recorded as already there; 12.5 + 20.25 + 3.75 spent; Amir's 300 lent is owed back
        assert worth["recorded"] == Decimal("160") and worth["change"] == Decimal("-36.5")
        assert worth["end"] - worth["start"] == Decimal("123.5")
        card = report_card(db, today.strftime("%Y-%m"), currency="OMR", today=today)
    assert card["net_worth"]["change"] == card["net_worth"]["end"] - card["net_worth"]["start"] - card["net_worth"]["recorded"]


@pytest.fixture()
def co(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "ir", "IRR")
    yield api, cid
    client.cookies.clear()
    _purge_company(db, cid)


def test_bw2_one_runway(db, co):
    from app.services.calendar_periods import company_calendar, key_bounds, previous_month_key, shift
    from app.services.cfo_intelligence import build_cfo_report
    from app.services.insight_service import compute_insights, invalidate_insights_cache
    api, cid = co
    today = date.today()

    def post(day, dr, cr, amount, desc):
        r = api.post("/transactions", json={"date": day.isoformat(), "description": desc, "currency": "IRR", "lines": [
            {"account_code": dr, "debit": amount, "credit": 0}, {"account_code": cr, "debit": 0, "credit": amount}]})
        assert r.status_code == 201, r.text

    with use_company(cid):
        cal = company_calendar(db)
    key = previous_month_key(today, cal)
    y, m = int(key[:4]), int(key[5:7])
    months = [key] + ["%04d-%02d" % shift(y, m, -i) for i in (1, 2)]
    first = key_bounds(months[-1])[0]
    post(first - timedelta(days=5), "1110", "3110", 1_200_000_000, "capital")
    for k in months:                                       # each complete month: 400 out, 100 in → net 300
        mid = key_bounds(k)[0] + timedelta(days=10)
        post(mid, "6112", "1110", 400_000_000, "costs")
        post(mid, "1110", "4110", 100_000_000, "sales")
    if today.day > 1:
        post(today, "6112", "1110", 1_000_000, "the month under way")
    with use_company(cid):
        cfo = build_cfo_report(db)
        invalidate_insights_cache()
        runway = next(i for i in compute_insights(db, today=today, use_cache=False) if i.kind == "runway")
    # cash 1,200 − 1,200 + 300 − 1 = 299 (millions); net burn 300 a month
    assert cfo.runway_months == runway.data["runway_months"] == round(299 / 300, 1)
    assert cfo.net_burn == 300_000_000 and cfo.burn_rate == 400_000_000


def test_bw3_the_review_says_the_runway_once():
    from app.services.proactive_reviews import _company_lines
    m = lambda v: str(v)  # noqa: E731
    facts = {"cash": 299, "runway": 1.0, "suggestions": [{"key": "runway", "params": {"months": 1.0}}]}
    said = " ".join(_company_lines(facts, "en", m))
    assert "months" not in said and "299" in said
    said = " ".join(_company_lines({**facts, "suggestions": []}, "en", m))
    assert "about 1 months" in said
    said = " ".join(_company_lines(facts, "en", m, ideas=False))             # suggestions switched off
    assert "about 1 months" in said

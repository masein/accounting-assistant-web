"""One runway (chat review 2026-11-10, scenario BW2; the owner chose the usual definition, 2026-11-14).

A company's review said cash lasts 1.7 months while the insight said 0.4 on the same day: the owner dashboard and
reviews averaged the gross costs of the last three months with the month just begun, the insight divided cash by
the net burn (costs less sales) of the last three complete months. Both are now the latter. Figures made up.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.db.tenant import use_company
from tests.test_statement_export import _company


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
    post(key_bounds(months[-1])[0] - timedelta(days=5), "1110", "3110", 1_200_000_000, "capital")
    for k in months:                                       # each complete month: 400 out, 100 in → net 300
        mid = key_bounds(k)[0] + timedelta(days=10)
        post(mid, "6112", "1110", 400_000_000, "costs")
        post(mid, "1110", "4110", 100_000_000, "sales")
    if key_bounds(previous_month_key(today, cal))[1] < today - timedelta(days=1):
        post(today, "6112", "1110", 1_000_000, "the month under way")        # a few days of it: not a month
    with use_company(cid):
        cfo = build_cfo_report(db)
        invalidate_insights_cache()
        runway = next(i for i in compute_insights(db, today=today, use_cache=False) if i.kind == "runway")
    assert cfo.net_burn == 300_000_000 and cfo.burn_rate == 400_000_000
    dash = {k["key"]: k["value"] for k in api.get("/reports/owner-dashboard").json()["kpis"]}
    assert cfo.runway_months == runway.data["runway_months"] == dash["runway_months"]
    assert dash["burn_rate"] == 400_000_000
    assert 0.9 < cfo.runway_months < 1.1                                   # about 300 of cash for 300 a month

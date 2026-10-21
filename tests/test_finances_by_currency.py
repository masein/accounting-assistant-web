"""Each currency on My finances and the phone's widget (scenarios AQ1–AQ5, docs/PLAN_CURRENCIES.md phase 2).

My finances and the widget showed one figure in the books' currency: Omani rials with no rate, in books
that count in rials, showed as nothing."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from tests.test_personal_money import API, person, scripted  # noqa: F401 — fixtures
from tests.test_reviews_by_currency import _post

TODAY = date.today()


def _two_currencies(db, cid):
    """10,000,000 IRR at the bank and 100 OMR in the wallet; this month 3,000,000 IRR of groceries and
    41.500 OMR of food, dining and transport."""
    _post(db, cid, TODAY, "موجودی اولیه", "1110", "3110", 10_000_000, "IRR")
    _post(db, cid, TODAY, "موجودی اولیه", "1120", "3110", Decimal("100"), "OMR")
    _post(db, cid, TODAY, "خرید ماهانه", "6110", "1110", 3_000_000, "IRR")
    for code, amount in (("6110", "20.250"), ("6180", "17.500"), ("6130", "3.750")):
        _post(db, cid, TODAY, "خرج", code, "1120", Decimal(amount), "OMR")


def test_aq1_a_row_for_each_currency(person, db):
    _two_currencies(db, person["cid"])
    api = person["web"]()
    out = api.get("/personal/currencies").json()
    assert out["books_currency"] == "IRR"
    assert [(c["currency"], c["name"], c["cash"], c["spent"], c["income"]) for c in out["currencies"]] == [
        ("IRR", "Iranian rial", 7_000_000, 3_000_000, 0), ("OMR", "Omani rial", 58.5, 41.5, 0)]


def test_aq2_the_details_in_one_currency(person, db):
    _two_currencies(db, person["cid"])
    api = person["web"]()
    omr = api.get("/reports/owner-dashboard", params={"currency": "OMR"}).json()
    assert sorted(c["amount"] for c in omr["expense_by_category"]) == [3.75, 17.5, 20.25]
    card = api.get("/personal/report-card", params={"month": _this_month(db, person["cid"]), "currency": "OMR"}).json()
    assert (card["currency"], card["spending"]) == ("OMR", 41.5)


def _this_month(db, cid):
    from app.db.tenant import use_company
    from app.services.calendar_periods import company_calendar, month_key
    with use_company(cid):
        return month_key(TODAY, company_calendar(db))


def test_aq3_only_omani_rials_moved(person, db):
    cid = person["cid"]
    _post(db, cid, TODAY, "موجودی اولیه", "1120", "3110", Decimal("100"), "OMR")
    _post(db, cid, TODAY, "خرج", "6180", "1120", Decimal("41.500"), "OMR")
    out = person["web"]().get("/personal/currencies").json()
    assert [(c["currency"], c["cash"], c["spent"]) for c in out["currencies"]] == [("OMR", 58.5, 41.5)]


def test_aq4_the_widget_shows_each_currency(person, db):
    _two_currencies(db, person["cid"])
    phone = person["phone"]()
    s = person["client"].get(f"{API}/summary", headers=phone).json()
    assert s["cash"]["by_currency"] == [{"currency": "IRR", "total": 7_000_000}, {"currency": "OMR", "total": 58.5}]
    assert (s["cash"]["total"], s["cash"]["currency"]) == (7_000_000, "IRR")


def test_aq4_only_omani_rials_the_widget_says_them(person, db):
    cid = person["cid"]
    _post(db, cid, TODAY, "موجودی اولیه", "1120", "3110", Decimal("77.500"), "OMR")
    s = person["client"].get(f"{API}/summary", headers=person["phone"]()).json()
    assert (s["cash"]["total"], s["cash"]["currency"]) == (77.5, "OMR")                 # not «۰ ریال»


def test_aq5_one_currency_as_before(person, db):
    cid = person["cid"]
    _post(db, cid, TODAY, "موجودی اولیه", "1110", "3110", 10_000_000, "IRR")
    out = person["web"]().get("/personal/currencies").json()
    assert [c["currency"] for c in out["currencies"]] == ["IRR"]
    s = person["client"].get(f"{API}/summary", headers=person["phone"]()).json()
    assert (s["cash"]["total"], s["cash"]["currency"], s["cash"]["by_currency"]) == (
        10_000_000, "IRR", [{"currency": "IRR", "total": 10_000_000}])


def test_aq1_aq2_the_page_reads_them():
    html = Path("app/static/index.html").read_text()
    js = Path("app/static/js/05-reports-manager.js").read_text()
    assert 'id="pd-ccy-rows"' in html and 'id="pd-ccy-pick"' in html
    assert "API + '/personal/currencies'" in js and "q.set('currency', _pdCurrency)" in js

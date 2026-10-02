"""A date inside an error message follows the company's calendar: an Iranian
company was told "the period is closed through 2026-09-22" (deep browser test,
2026-10-02, finding #31)."""
from __future__ import annotations

import pytest

from app.core.messages import localize_detail
from app.db.tenant import use_company

CLOSED = ("Period is closed through 2026-06-30; cannot post or back-date an entry dated 2026-05-01. "
          "Reopen the period or use a later date.")


def test_the_dates_in_a_message_can_be_written_in_jalali():
    fa = localize_detail(CLOSED, "fa", jalali=True)
    assert "1405/04/09" in fa and "1405/02/11" in fa and "2026-" not in fa
    en = localize_detail(CLOSED, "en", jalali=True)
    assert en.startswith("Period is closed through 1405/04/09; cannot post or back-date an entry dated 1405/02/11.")
    assert localize_detail(CLOSED, "fa") .count("2026-") == 2          # a Gregorian company's, as before
    assert localize_detail("Not a date: 2026-13-45", "en", jalali=True) == "Not a date: 2026-13-45"


@pytest.fixture()
def company(client, db):
    from tests.test_admin_audit import _purge_company
    from tests.test_statement_export import _company
    made = []

    def make(locale, currency):
        api, cid = _company(client, db, locale, currency)
        made.append(cid)
        return api, cid
    yield make
    for cid in made:
        _purge_company(db, cid)


@pytest.mark.parametrize("locale, currency, lang, closed, entry", [
    ("ir", "IRR", "fa", "1405/04/09", "1405/02/11"),
    ("ir", "IRR", "en", "1405/04/09", "1405/02/11"),
    ("uk", "GBP", "en", "2026-06-30", "2026-05-01"),
])
def test_a_closed_period_names_its_date_in_the_companys_calendar(company, locale, currency, lang, closed, entry):
    api, cid = company(locale, currency)
    with use_company(cid):
        accounts = sorted(a["code"] for a in api.get("/accounts").json() if a["code"].isdigit())
        assert api.put("/admin/closed-period", json={"closed_period": "2026-06-30"}).status_code == 200
        r = api.post("/transactions", headers={"X-UI-Language": lang}, json={
            "date": "2026-05-01", "description": "back-dated", "currency": currency, "lines": [
                {"account_code": accounts[-1], "debit": 100, "credit": 0},
                {"account_code": accounts[0], "debit": 0, "credit": 100}]})
    assert r.status_code in (400, 409, 422), r.text
    detail = r.json()["detail"]
    assert closed in detail and entry in detail, detail

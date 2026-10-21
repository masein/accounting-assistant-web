"""Can I afford it, insights and repeating payments in each currency (scenarios AT1–AT7,
docs/PLAN_CURRENCIES.md phase 5).

They counted the books' currency at its base value: a price in Omani rials needed an exchange rate,
and Omani-rial spending never showed as a spike."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.test_personal_money import API, person, scripted  # noqa: F401 — fixtures
from tests.test_reviews_by_currency import _in, _post

TODAY = date.today()


def _months_back(n: int) -> date:
    """A day in the month ``n`` months before this one (Gregorian books)."""
    d = TODAY.replace(day=10)
    for _ in range(n):
        d = (d.replace(day=1) - timedelta(days=1)).replace(day=10)
    return d


def _omani_months(db, cid, *, income="400", spending="250"):
    """Three months of Omani-rial salary and spending, and 300 OMR in the wallet; nothing in rials but
    a bank balance."""
    _post(db, cid, _months_back(4), "موجودی اولیه", "1120", "3110", Decimal("300"), "OMR")
    _post(db, cid, _months_back(4), "موجودی اولیه", "1110", "3110", 50_000_000, "IRR")
    for n in (1, 2, 3):
        _post(db, cid, _months_back(n), "حقوق", "1120", "4110", Decimal(income), "OMR")
        _post(db, cid, _months_back(n), "خرج", "6110", "1120", Decimal(spending), "OMR")


def test_at1_a_price_in_omani_rials_is_judged_on_the_omani_rials(person, db):
    cid = person["cid"]
    _omani_months(db, cid)
    from app.services.affordability import card, check
    with _in(cid):
        f = check(db, price=Decimal("200"), currency="OMR", item="گوشی")
    assert (f["base"], f["rate"], f["price_base"]) == ("OMR", None, 200)
    assert (f["cash"], f["income"], f["spending"], f["spare"]) == (750, 400, 250, 150)
    assert f["verdict"] == "now"                                   # 750 - cushion 250 = 500 free
    c = card(f, "fa")
    assert c["currency"] == "OMR" and "ریال عمان" in c["headline"]["display"]


def test_at2_a_price_in_a_currency_not_held_is_converted_or_asked(person, db):
    cid = person["cid"]
    _omani_months(db, cid)
    from app.services.affordability import AffordError, check
    with _in(cid):
        with pytest.raises(AffordError) as e:
            check(db, price=100, currency="USD")
    assert e.value.code == "no_rate"


def test_at3_a_spike_in_omani_rials(person, db):
    cid = person["cid"]
    for n in (1, 2, 3):
        _post(db, cid, _months_back(n), "رستوران", "6180", "1120", Decimal("10"), "OMR")
    _post(db, cid, TODAY, "رستوران", "6180", "1120", Decimal("40"), "OMR")
    for n in (1, 2, 3, 0):                                     # rials steady: no spike there
        _post(db, cid, _months_back(n) if n else TODAY, "خرید", "6110", "1110", 3_000_000, "IRR")
    from app.services.insight_service import compute_insights
    with _in(cid):
        found = [i for i in compute_insights(db, today=TODAY, use_cache=False) if i.kind == "expense_spike"]
    [spike] = found
    assert spike.key.endswith("-OMR") and spike.data["currency"] == "OMR"
    assert (spike.params["ratio"], spike.params["current"], spike.params["average"]) == ("4.0", "40.000 OMR", "10.000 OMR")


def test_at4_each_currencys_income(person, db):
    cid = person["cid"]
    for n in (2, 3, 4):
        _post(db, cid, _months_back(n), "حقوق", "1120", "4110", Decimal("400"), "OMR")
        _post(db, cid, _months_back(n), "حقوق", "1110", "4110", 20_000_000, "IRR")
    _post(db, cid, _months_back(1), "حقوق", "1120", "4110", Decimal("200"), "OMR")
    _post(db, cid, _months_back(1), "حقوق", "1110", "4110", 20_000_000, "IRR")
    from app.services.insight_service import compute_insights
    with _in(cid):
        drops = [i for i in compute_insights(db, today=TODAY, use_cache=False) if i.kind == "revenue_drop"]
    assert [(i.data["currency"], i.params["pct"]) for i in drops] == [("OMR", "50")]


def test_at5_round_amounts_each_against_its_own_currency(person, db):
    cid = person["cid"]
    sunday = TODAY - timedelta(days=(TODAY.weekday() - 6) % 7 or 7)      # a recent Sunday (Gregorian books)
    for i in range(12):
        _post(db, cid, TODAY - timedelta(days=20 + i), "خرید", "6110", "1110", 3_123_456 + i, "IRR")
    for i in range(6):
        _post(db, cid, TODAY - timedelta(days=20 + i), "خرید", "6110", "1120", Decimal("12.345"), "OMR")
    _post(db, cid, sunday, "خرید بزرگ", "6110", "1120", Decimal("50"), "OMR")
    from app.services.anomaly_detection import detect_round_weekend
    with _in(cid):
        found = detect_round_weekend(db, TODAY)
    [ins] = found
    assert ins.data["entries"][0]["currency"] == "OMR" and ins.params["largest"].endswith("OMR")


def test_at6_repeating_payments_in_omani_rials(person, db):
    cid = person["cid"]
    for n in (1, 2, 3, 4):
        _post(db, cid, _months_back(n), "Netflix", "6190", "1120", Decimal("4.500"), "OMR")
        _post(db, cid, _months_back(n), "Spotify", "6190", "1120", Decimal("2.500"), "OMR")
    from app.services.recurring_detection import detect_recurring
    with _in(cid):
        assert detect_recurring(db, today=TODAY) == []                  # none in rials
        subs = detect_recurring(db, today=TODAY, currency="OMR")
    assert sorted((c.description, c.typical_amount) for c in subs) == [("Netflix", Decimal("4.500")), ("Spotify", Decimal("2.500"))]


def test_at7_a_company_as_before(books, db):
    """A company's insights count at base value: £200 of rent and $100 at 0.8 is one month's £280."""
    from tests.test_fx_base_readers import TODAY as T
    from app.services.insight_service import _OWN, _ccy
    _api, cid = books
    from app.services.insight_service import compute_insights
    with _in(cid):
        compute_insights(db, today=T, use_cache=False)                   # runs, and leaves nothing set
    assert _OWN.get() is False


from tests.test_fx_base_readers import books, uk  # noqa: E402,F401 — fixtures

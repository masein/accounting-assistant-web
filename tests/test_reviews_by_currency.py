"""Each currency in a person's reviews (scenarios AP1–AP7, docs/PLAN_CURRENCIES.md phase 1).

The owner (2026-10-21): «i want to have different currencies. isn't that ok?» It is: a person keeps
rials at a bank and Omani rials in a wallet, each as it is. The weekly and monthly reviews totalled
everything in the books' currency at its base value, and Omani rials with no rate have none — a week
spent in Omani rials, in books that count in rials, read as nothing."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from tests.test_personal_money import API, person, scripted  # noqa: F401 — fixtures
from tests.test_proactive_reviews import MONDAY, _in, _make, _name, _reviews

LAST = MONDAY - timedelta(days=5)          # Wednesday 14 October, in last week (12–18 October)


def _post(db, cid, on, desc, dr, cr, amount, currency):
    from app.schemas.transaction import TransactionCreate, TransactionLineCreate
    from app.services.ledger_posting import create_transaction_from_payload
    with _in(cid):
        create_transaction_from_payload(db, TransactionCreate(date=on, description=desc, currency=currency, lines=[
            TransactionLineCreate(account_code=dr, debit=amount, credit=0),
            TransactionLineCreate(account_code=cr, debit=0, credit=amount)]))
        db.commit()


def _omani_week(db, cid, on=LAST):
    """Groceries 20.250, a restaurant 12.500 and a café 5.000, a taxi 3.750: 41.500 OMR from the wallet."""
    for desc, code, amount in (("لولو", "6110", "20.250"), ("رستوران", "6180", "12.500"), ("کافه", "6180", "5.000"),
                               ("تاکسی", "6130", "3.750")):
        _post(db, cid, on, desc, code, "1120", Decimal(amount), "OMR")


def _usual_omani_weeks(db, cid):
    for w in range(1, 5):
        _post(db, cid, LAST - timedelta(days=7 * w), "رستوران", "6180", "1120", Decimal("10"), "OMR")


def test_ap1_a_week_in_two_currencies_is_two_cards(person, db):
    cid = person["cid"]
    _usual_omani_weeks(db, cid)
    _omani_week(db, cid)
    _post(db, cid, LAST, "خرید ماهانه", "6110", "1110", 3_000_000, "IRR")
    assert _make(db, cid) == ["weekly"]
    [review] = _reviews(db, cid)
    f = review.facts
    assert (f["currency"], f["spent"], f["usual"]) == ("IRR", 3_000_000, None)        # the books' own first
    [omr] = f["others"]
    groceries, dining, transport = (_name(db, cid, c) for c in ("6110", "6180", "6130"))
    assert (omr["currency"], omr["spent"], omr["usual"]) == ("OMR", 41.5, 10)
    assert [(c["name"], c["amount"]) for c in omr["top"]] == [(groceries, 20.25), (dining, 17.5), (transport, 3.75)]
    assert omr["biggest"]["what"] == "لولو" and omr["biggest"]["amount"] == 20.25
    assert [b["spent"] for b in omr["series"]] == [10, 10, 10, 10, 41.5]

    from app.services.proactive_reviews import render
    fa = render(review, "fa")
    assert len(fa["cards"]) == 2 and fa["card"] is fa["cards"][0]
    rials, omani = fa["cards"]
    assert rials["title"].endswith("— ریال ایران") and omani["title"].endswith("— ریال عمان")
    assert rials["headline"]["value"] == 3_000_000 and omani["headline"]["value"] == 41.5
    assert omani["headline"]["display"] == "۴۱٫۵۰۰ ریال عمان"
    assert [c["name"] for c in omani["categories"]] == [groceries, dining, transport]
    assert [b["value"] for b in omani["chart"]["bars"]] == [10, 10, 10, 10, 41.5]
    assert [b["id"] for b in fa["blocks"]] == [f"review:{review.id}", f"review:{review.id}:OMR"]
    assert fa["headline"].startswith("این هفته ۳٬۰۰۰٬۰۰۰ ریال ایران")                  # the bell: the first
    assert "۴۱٫۵۰۰ ریال عمان" in fa["text"] and "۳٬۰۰۰٬۰۰۰ ریال ایران" in fa["text"]


def test_ap2_a_week_in_omani_rials_only_is_told_in_omani_rials(person, db):
    cid = person["cid"]
    _usual_omani_weeks(db, cid)
    _omani_week(db, cid)
    assert _make(db, cid) == ["weekly"]
    [review] = _reviews(db, cid)
    f = review.facts
    assert (f["currency"], f["spent"], f["usual"], "others" in f) == ("OMR", 41.5, 10, False)
    from app.services.proactive_reviews import render
    fa = render(review, "fa")
    assert len(fa["cards"]) == 1 and "—" not in fa["card"]["title"]
    assert fa["headline"].startswith("این هفته ۴۱٫۵۰۰ ریال عمان خرج کردید")
    assert "ریال ایران" not in fa["text"]                                           # never «۰ ریال ایران»


def test_ap3_a_month_in_two_currencies(person, db):
    cid = person["cid"]
    for month in (8, 9):
        _post(db, cid, date(2026, month, 10), "رستوران", "6180", "1120", Decimal("30"), "OMR")
    _post(db, cid, date(2026, 10, 1), "حقوق", "1110", "4110", 20_000_000, "IRR")
    _post(db, cid, date(2026, 10, 3), "خرید ماهانه", "6110", "1110", 3_000_000, "IRR")
    _post(db, cid, date(2026, 10, 2), "حقوق عمان", "1120", "4110", Decimal("400"), "OMR")
    _omani_week(db, cid, date(2026, 10, 14))
    assert "monthly" in _make(db, cid, date(2026, 11, 2))
    [review] = _reviews(db, cid, "monthly")
    f = review.facts
    assert (f["currency"], f["income"], f["spent"], f["saved"]) == ("IRR", 20_000_000, 3_000_000, 17_000_000)
    assert "budgets" in f and "goals" in f
    [omr] = f["others"]
    assert (omr["currency"], omr["income"], omr["spent"], omr["saved"], omr["rate"]) == ("OMR", 400, 41.5, 358.5, 89.6)
    assert [s["spent"] for s in omr["series"]] == [0, 30, 30, 41.5]                    # July to October
    assert omr["budgets"] == {"set": 0, "kept": 0, "over": []} and "suggestions" not in omr     # its own (phase 4)
    from app.services.proactive_reviews import render
    en = render(review, "en")
    rials, omani = en["cards"]
    assert omani["ring"]["display"] == "358.500 Omani rial" and omani["headline"]["value"] == 41.5
    assert [b["label"] for b in omani["chart"]["bars"]] == ["Jul", "Aug", "Sep", "Oct"]


def test_ap4_no_budget_in_rials_for_a_category_spent_in_omani_rials(person, db):
    cid = person["cid"]
    _usual_omani_weeks(db, cid)
    _post(db, cid, LAST, "شام تولد", "6180", "1120", Decimal("40"), "OMR")         # four times its usual week
    assert _make(db, cid) == ["weekly"]
    [review] = _reviews(db, cid)
    assert review.facts["currency"] == "OMR" and review.facts["top"][0]["amount"] == 40
    # since budgets have a currency (phase 4, AS4) the idea comes in Omani rials, never in rials
    assert [(s["key"], s["params"]["currency"]) for s in review.facts["suggestions"]] == [("hot_category", "OMR")]


def test_ap5_the_report_card_one_currency_and_the_others(person, db):
    cid = person["cid"]
    _post(db, cid, date(2026, 10, 1), "حقوق", "1110", "4110", 20_000_000, "IRR")
    _post(db, cid, date(2026, 10, 3), "خرید ماهانه", "6110", "1110", 3_000_000, "IRR")
    _omani_week(db, cid, date(2026, 10, 14))
    from app.services.report_card import report_card
    with _in(cid):
        rc = report_card(db, "2026-10", lang="en", today=date(2026, 11, 2))
        omr = report_card(db, "2026-10", lang="en", today=date(2026, 11, 2), currency="omr")
    assert (rc["currency"], rc["currencies"], rc["spending"]) == ("IRR", ["IRR", "OMR"], 3_000_000)
    [other] = rc["others"]
    assert (other["currency"], other["spending"], other["categories"][0]["amount"]) == ("OMR", 41.5, 20.25)
    assert (omr["currency"], omr["income"], omr["spending"]) == ("OMR", 0, 41.5)
    assert [o["currency"] for o in omr["others"]] == ["IRR"]
    api = person["web"]()
    page = api.get("/personal/report-card", params={"month": "2026-10", "currency": "OMR"}).json()
    assert (page["currency"], page["spending"]) == ("OMR", 41.5)


def test_ap6_the_web_chat_draws_each_card_again_after_a_reload(person, db):
    cid = person["cid"]
    _omani_week(db, cid)
    _post(db, cid, LAST, "خرید ماهانه", "6110", "1110", 3_000_000, "IRR")
    _make(db, cid)
    web = person["web"]()
    out = web.post("/ai-accountant/reviews", json={}).json()
    [said] = out["reviews"]
    assert [c["headline"]["value"] for c in said["cards"]] == [3_000_000, 41.5]
    msgs = web.get(f"/ai-accountant/sessions/{out['session_id']}/messages").json()
    [kept] = [m["content"] for m in msgs if (m.get("content") or {}).get("review")]
    assert [c["headline"]["value"] for c in kept["cards"]] == [3_000_000, 41.5]


def test_ap6_the_page_draws_every_card():
    from pathlib import Path
    js = Path("app/static/js/15-ai-chat.js").read_text()
    assert "c.cards.length ? c.cards : [c.card]" in js and "rv.cards.length ? rv.cards : [rv.card]" in js


def test_ap7_a_company_counts_at_its_rates_as_before(books, db):
    """A company's review keeps one total in its base currency: £200 of rent and $100 at 0.8 (scenario AP7)."""
    from tests.test_fx_base_readers import TODAY
    from app.services.proactive_reviews import weekly_facts
    _api, cid = books
    with _in(cid):
        f = weekly_facts(db, TODAY + timedelta(days=7))
    assert (f["currency"], f["spent"], f["personal"], "others" in f) == ("GBP", 280, False, False)


from tests.test_fx_base_readers import books, uk  # noqa: E402,F401 — fixtures

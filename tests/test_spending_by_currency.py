"""Spending in the currency it was spent in (scenarios AO1–AO6).

From the owner's phone: personal books that count in Iranian rials, a week of
spending in Omani rials with no rate between the two. «هفته گذشته خرج ما چیا
بوده؟» was answered "zero rials", and «خب ریال عمانش رو بگو» with "this report
only gives the books' currency": the summary added every currency up at its
value in the books' currency, and a line with no rate has none."""
from __future__ import annotations

import asyncio
from datetime import date

from app.db.tenant import use_company
from tests.test_personal_money import API, person, scripted  # noqa: F401  (fixtures)

TODAY = date.today()


def _post(api, debit_code, credit_code, amount, currency):
    r = api.post("/transactions", json={
        "date": TODAY.isoformat(), "description": "spent", "currency": currency,
        "lines": [{"account_code": debit_code, "debit": amount, "credit": 0},
                  {"account_code": credit_code, "debit": 0, "credit": amount}]})
    assert r.status_code == 201, r.text


def _week_in_omani_rials(person):
    """A restaurant 12.500, groceries 20.250, a taxi 3.750 and a café 5.000, all
    from cash in Omani rials, in books that count in Iranian rials."""
    api = person["web"]()
    for code, amount in (("6180", "12.500"), ("6110", "20.250"), ("6130", "3.750"), ("6180", "5.000")):
        _post(api, code, "1120", amount, "OMR")
    return api


def _summary(db, person, **kw):
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.spending_tools import GetSpendingSummary, GetSpendingSummaryInput
    kw.setdefault("period", "custom")
    kw.setdefault("from_date", TODAY)
    kw.setdefault("to_date", TODAY)
    with use_company(person["cid"]):
        return asyncio.run(GetSpendingSummary().run(ToolContext(db=db, user_id="u", mode="personal"),
                                                    GetSpendingSummaryInput(**kw)))


def _cats(result):
    return [(c["category_code"], c["amount"]) for c in result["by_category"]]


def test_ao1_a_week_in_omani_rials_is_told_in_omani_rials(person, db):
    _week_in_omani_rials(person)
    r = _summary(db, person)
    assert (r["currency"], r["total"], r["transaction_count"]) == ("OMR", 41.5, 4)
    assert _cats(r) == [("6110", 20.25), ("6180", 17.5), ("6130", 3.75)]
    assert "by_currency" not in r                     # one currency, and the answer is in it
    assert "No spending" not in (r.get("note") or "")


def test_ao2_asked_for_one_currency(person, db):
    _week_in_omani_rials(person)
    omr = _summary(db, person, currency="omr")
    assert (omr["currency"], omr["total"], omr["transaction_count"]) == ("OMR", 41.5, 4)
    assert _cats(omr)[0] == ("6110", 20.25) and "unconverted" not in omr
    irr = _summary(db, person, currency="IRR")
    assert (irr["currency"], irr["total"], irr["by_category"]) == ("IRR", 0, [])
    assert irr["note"].startswith("No spending recorded")


def test_ao3_two_currencies_each_on_its_own(person, db):
    api = _week_in_omani_rials(person)
    _post(api, "6110", "1110", 3_000_000, "IRR")
    r = _summary(db, person)
    assert (r["currency"], r["total"]) == ("IRR", 3_000_000)          # the books' currency, with nothing added in
    assert [(h["currency"], h["total"], h["transaction_count"]) for h in r["by_currency"]] == [
        ("IRR", 3_000_000, 1), ("OMR", 41.5, 4)]
    assert [(c["category_code"], c["amount"]) for c in r["by_currency"][1]["by_category"]] == [
        ("6110", 20.25), ("6180", 17.5), ("6130", 3.75)]
    assert "41.5 OMR" in r["note"] and "never say it is zero" in r["note"]


def test_ao4_ao5_the_quick_answer_and_the_card_list_each_currency(person, scripted):
    _week_in_omani_rials(person)
    phone = person["phone"]()
    r = person["client"].post(f"{API}/chat", headers=phone, json={"message": "این ماه چقدر خرج کردم؟"})
    assert r.status_code == 200, r.text
    words = [b for b in r.json()["blocks"] if b["type"] == "text"][-1]["text"]
    assert words == "خرج این ماه تا امروز: ۴۱٫۵۰۰ ریال عمان."               # no model: a quick answer

    _post(person["web"](), "6110", "1110", 3_000_000, "IRR")         # the phone's login ended the web session
    phone = person["phone"]()
    reply = person["client"].post(f"{API}/chat", headers=phone, json={"message": "این ماه چقدر خرج کردم؟"}).json()
    words = [b for b in reply["blocks"] if b["type"] == "text"][-1]["text"]
    assert words == "خرج این ماه تا امروز، هر ارز جداگانه: ۳,۰۰۰,۰۰۰ ریال، ۴۱٫۵۰۰ ریال عمان."
    table = next(b for b in reply["blocks"] if b["type"] == "table")
    assert table["kind"] == "spending"
    assert [(row["sub"], row["value"], row["currency"]) for row in table["rows"]] == [
        ("6110", 3_000_000, "IRR"), ("6110", 20.25, "OMR"), ("6180", 17.5, "OMR"), ("6130", 3.75, "OMR")]
    assert table["fallback_text"] == "3,000,000 IRR · 41.50 OMR"            # as the cash card writes it


def test_ao1_the_prompt_says_each_currency_on_its_own():
    from app.services.ai_accountant.orchestrator import PERSONAL_MODE_ADDENDUM, SYSTEM_PROMPT
    assert "by_currency" in SYSTEM_PROMPT and "never add them together" in PERSONAL_MODE_ADDENDUM
    assert "``currency`` OMR" in PERSONAL_MODE_ADDENDUM

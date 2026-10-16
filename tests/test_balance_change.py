"""What changed on the balance sheet (scenarios AA1–AA6): the accounts behind a
line on both dates, biggest change first, adding up to the line — so "assets
rose from 27.95 to 47 billion" can be read as cash, receivables or else."""
from __future__ import annotations

import pytest

from tests.test_close_pack import _post
from tests.test_statement_export import _company

THEN, NOW = "2025-06-30", "2026-06-30"


@pytest.fixture()
def books(client, db):
    made = []

    def make(locale, currency):
        api, cid = _company(client, db, locale, currency)
        made.append(cid)
        return api
    yield make
    from tests.test_admin_audit import _purge_company
    client.cookies.clear()
    for cid in made:
        _purge_company(db, cid)


def _sheet(api, statement):
    r = api.get(f"/manager-reports/financial/{statement}/balance-sheet", params={"as_of": NOW, "comparative_as_of": THEN})
    assert r.status_code == 200, r.text
    return {row["key"]: row for row in r.json()["rows"]}


def _line(api, statement, key):
    r = api.get(f"/manager-reports/financial/{statement}/balance-sheet/lines/{key}",
                params={"as_of": NOW, "comparative_as_of": THEN})
    assert r.status_code == 200, r.text
    return r.json()


def test_aa1_aa2_behind_each_iranian_line_its_accounts(books):
    api = books("ir", "IRR")
    _post(api, "2025-06-01", "capital", "1110", "3110", 10_000_000, "IRR")
    _post(api, "2026-03-01", "a sale for cash", "1110", "4110", 5_000_000, "IRR")
    _post(api, "2026-04-01", "a sale on credit", "1112", "4110", 3_000_000, "IRR")
    sheet = _sheet(api, "iran")
    cash = _line(api, "iran", "ca_cash")
    assert [(a["code"], a["prior"], a["current"], a["change"]) for a in cash["accounts"]] == [
        ("1110", 10_000_000, 15_000_000, 5_000_000)]
    # every line with money on it is the sum of its accounts, on both dates
    for key, row in sheet.items():
        if row["row_type"] != "line" or not (row["amount_current"] or row["amount_prior"]):
            continue
        got = _line(api, "iran", key)["total"]
        assert (got["current"], got["prior"]) == (row["amount_current"], row["amount_prior"]), key
    # AA4: retained earnings carries the result not yet closed, as a row of its own
    re_ = _line(api, "iran", "eq_retained_earnings")
    assert [(a.get("kind"), a["current"], a["prior"]) for a in re_["accounts"]] == [("pl_to_date", 8_000_000, 0)]
    # a line that didn't move still lists its accounts, with no change
    cap = _line(api, "iran", "eq_capital")
    assert [(a["code"], a["change"]) for a in cap["accounts"]] == [("3110", 0)]


def test_aa1_the_biggest_change_comes_first(books):
    api = books("ir", "IRR")
    for code, name in (("111001", "بانک ملت"), ("111002", "صندوق")):
        r = api.post("/accounts", json={"code": code, "name": name, "parent_code": "1110"})
        assert r.status_code in (200, 201), r.text
    _post(api, "2025-01-01", "capital", "111001", "3110", 50_000_000, "IRR")
    _post(api, "2026-02-01", "to the cash box", "111002", "111001", 1_000_000, "IRR")   # moves within the cash line
    _post(api, "2026-02-02", "a big sale", "111001", "4110", 20_000_000, "IRR")
    accounts = _line(api, "iran", "ca_cash")["accounts"]
    assert [(a["code"], a["change"]) for a in accounts] == [("111001", 19_000_000), ("111002", 1_000_000)]
    assert [abs(a["change"]) for a in accounts] == sorted((abs(a["change"]) for a in accounts), reverse=True)


def test_aa6_a_uk_line_in_pounds_and_pence(books):
    api = books("uk", "GBP")
    _post(api, "2025-05-01", "capital", "1200", "3000", "1000.00", "GBP")
    _post(api, "2026-05-01", "an invoice paid", "1200", "4000", "250.75", "GBP")
    _post(api, "2026-05-02", "an invoice owed", "1100", "4000", "99.99", "GBP")
    sheet = _sheet(api, "uk")
    cash = _line(api, "uk", "ca_cash")
    assert [(a["code"], a["prior"], a["current"]) for a in cash["accounts"]] == [("1200", 1000, 1250.75)]
    assert cash["total"]["current"] == sheet["ca_cash"]["amount_current"]
    debtors = _line(api, "uk", "ca_debtors")
    assert debtors["total"] == {"current": 99.99, "prior": 0, "change": 99.99}
    pl = _line(api, "uk", "eq_pl_account")
    assert [(a.get("kind"), a["current"]) for a in pl["accounts"]] == [("pl_to_date", 350.74)]


def test_only_the_two_statements(books):
    api = books("ir", "IRR")
    assert api.get("/manager-reports/financial/fr/balance-sheet/lines/ca_cash").status_code == 404
    out = api.get("/manager-reports/financial/iran/balance-sheet/lines/nowhere").json()
    assert out["accounts"] == [] and out["total"] == {"current": 0, "prior": 0, "change": 0}

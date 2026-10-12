"""A UK company in English (scenarios N40, N41, 2026-10-12).

On the phone, a UK company's answers read «23,923 GBP» beside a card saying
«£23,923» and «1 sales invoices are still open»; a Barclays CSV (Date,
Description, Money out, Money in, Balance; days first) came back as «I
couldn't tell the statement's columns apart»."""
from __future__ import annotations

import pytest

from app.services.ai_accountant import fast_paths as fp
from app.services.bank_statement_parser import parse_csv


@pytest.mark.parametrize("name,text", [
    ("barclays", "Date,Description,Money out,Money in,Balance\n01/10/2026,HARTLEY PROPERTIES RENT,950.00,,24050.00\n"
                 "12/10/2026,ARIA DESIGN LTD,,600.00,24650.00\n"),
    ("hsbc", "Date,Details,Paid out,Paid in,Balance\n01/10/2026,HARTLEY PROPERTIES RENT,950.00,,24050.00\n"
             "12/10/2026,ARIA DESIGN LTD,,600.00,24650.00\n"),
    ("monzo", "Date,Name,Amount,Currency\n01/10/2026,HARTLEY PROPERTIES RENT,-950.00,GBP\n"
              "12/10/2026,ARIA DESIGN LTD,600.00,GBP\n"),
])
def test_a_uk_banks_statement_reads_itself(name, text):
    r = parse_csv(text, bank_name="Barclays")
    assert not r.needs_mapping, (name, r.errors)
    rows = [(str(x.tx_date), x.debit, x.credit, x.description) for x in r.rows]
    assert rows == [("2026-10-01", 950, 0, "HARTLEY PROPERTIES RENT"), ("2026-10-12", 0, 600, "ARIA DESIGN LTD")], name


def test_a_header_that_only_contains_in_or_out_is_not_money():
    r = parse_csv("Date,Description,Debit,Credit,Account in use\n01/10/2026,Rent,950,,Main\n")
    assert not r.needs_mapping and [(x.debit, x.credit) for x in r.rows] == [(950, 0)]


def test_amounts_read_as_the_reader_writes_them():
    assert fp._priced(23923, "GBP", "en") == "£23,923"
    assert fp._priced(-50, "GBP", "en") == "-£50"
    assert fp._priced(1440, "EUR", "es") == "€1,440"
    assert fp._priced(1440, "IRR", "en") == "1,440 IRR"
    assert fp._priced(23923, "IRR", "fa") == "۲۳٬۹۲۳ ریال"
    assert fp._say("cash", "en", total="23,923", cur="GBP", total_p="£23,923") == "Cash and bank today: £23,923."
    assert fp._say("cash", "fa", total="۲۳٬۹۲۳", cur="ریال", total_p="x") == "موجودی نقد و بانک امروز: ۲۳٬۹۲۳ ریال."
    assert fp._say("budget", "en", actual="1", budget="2", left="1", cur="GBP", actual_p="£1,077",
                   budget_p="£2,000", left_p="£923") == "This month: £1,077 of £2,000 spent, £923 left."


def test_one_open_invoice_is_one():
    assert fp._say("receivables_one", "en", n="1", amounts="£1,440") == "1 sales invoice is still open: £1,440 to collect."
    assert fp._say("payables_one", "en", n="1", amounts="£360") == "1 supplier bill is still open: £360 to pay."
    assert set(fp._T["receivables_one"]) == set(fp._T["receivables"]) == {"en", "fa", "es", "ar"}


@pytest.fixture()
def uk_banks(client, db):
    from tests.test_admin_audit import _purge_company
    from tests.test_statement_export import _company
    api, cid = _company(client, db, "uk", "GBP")
    made = {}
    for name in ("Barclays Business", "HSBC Current", "Starling"):
        r = api.post("/entities", json={"type": "bank", "name": name})
        assert r.status_code == 201, r.text
        made[name] = r.json()["code"]
    yield api, cid, made
    client.cookies.clear()
    _purge_company(db, cid)


def test_a_statement_from_barclays_is_the_barclays_account(uk_banks, db):
    from tests.test_statement_bank_match import _account_for
    from app.db.tenant import use_company
    from app.services.account_resolver import resolve_account_code
    api, cid, made = uk_banks
    assert _account_for(db, cid, bank_name="Barclays") == made["Barclays Business"]
    assert _account_for(db, cid, bank_name="HSBC") == made["HSBC Current"]
    assert _account_for(db, cid, bank_name="Starling") == made["Starling"]
    with use_company(cid):
        default = resolve_account_code(db, "bank")
    assert _account_for(db, cid, bank_name="Bar") == default            # a piece of a word isn't the bank
    api.post("/entities", json={"type": "bank", "name": "Barclays Savings"})
    assert _account_for(db, cid, bank_name="Barclays") == default       # two Barclays: not a guess

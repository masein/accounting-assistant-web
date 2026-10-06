"""The CFO and CEO reports' receivables and payables (scenarios L1–L8).

They added every open invoice's gross amount to the ledger's receivable
movement of the last 12 months: an issued invoice — which posts its own
receivable — counted twice, drafts counted, part payments didn't, a USD
invoice was added to the rials as a raw number, a UK company's prepayments
and VAT were "receivables" and every 21xx liability was a "payable".
Receivables are now the trade receivable accounts (and, in Iranian books,
cheques not yet cleared) to date, plus what invoices that never posted are
still owed; payables likewise.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.db.tenant import use_company


@pytest.fixture(params=["ir"])
def co(request, client, db):
    """A company of the given locale with its chart, a customer, a supplier and an owner."""
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.db.seed import seed_chart_if_empty
    from app.models.company import Company
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company

    locale = request.param
    ccy = "GBP" if locale == "uk" else "IRR"
    c = Company(id=uuid.uuid4(), name="Owed Co", slug=f"owed-{uuid.uuid4().hex[:6]}", locale=locale,
                base_currency=ccy, status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        seed_chart_if_empty(db, locale=locale)
        db.commit()
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner",
                               company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    db.expunge_all()
    api = _CSRFTestClient(client, csrf)
    ids = {}
    for kind in ("client", "supplier"):
        r = api.post("/entities", json={"type": kind, "name": f"{kind} {uuid.uuid4().hex[:4]}"})
        assert r.status_code == 201, r.text
        ids[kind] = r.json()["id"]
    yield {"api": api, "cid": cid, "locale": locale, "ccy": ccy, **ids}
    client.cookies.clear()
    _purge_company(db, cid)


uk = pytest.mark.parametrize("co", ["uk"], indirect=True)


def _invoice(co, kind="sales", price=10_000_000, *, tax=10, status="issued", days_ago=5, currency=None):
    issue = date.today() - timedelta(days=days_ago)
    r = co["api"].post("/invoices", json={
        "number": f"{'S' if kind == 'sales' else 'B'}-{uuid.uuid4().hex[:6]}", "kind": kind, "status": status,
        "issue_date": issue.isoformat(), "due_date": (issue + timedelta(days=30)).isoformat(), "amount": 0,
        "currency": currency or co["ccy"], "entity_id": co["client" if kind == "sales" else "supplier"],
        "items": [{"product_name": "Work", "quantity": 1, "unit_price": price, "tax_rate": tax}],
    })
    assert r.status_code == 201, r.text
    return r.json()


def _pay(co, inv, amount):
    r = co["api"].post(f"/invoices/{inv['id']}/payments", json={"amount": amount, "date": date.today().isoformat()})
    assert r.status_code == 201, r.text


def _owed(co, currency=None):
    """(receivables, payables) as CFO Mode shows them."""
    r = co["api"].get("/brain/cfo/report", params={"currency": currency} if currency else {})
    assert r.status_code == 200, r.text
    k = {x["key"]: x["value"] for x in r.json()["kpis"]}
    return k["accounts_receivable"], k["accounts_payable"]


def _code(db, co, category):
    """The posting account of a category, created if the chart lacks it."""
    from app.services.account_resolver import resolve_account_code
    with use_company(co["cid"]):
        code = resolve_account_code(db, category)
        db.commit()
    return code


def _journal(db, co, *lines, days_ago=1):
    """A balanced entry in the company's currency: lines are (code, debit, credit)."""
    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    with use_company(co["cid"]):
        acc = {a.code: a for a in db.execute(select(Account)).scalars()}
        t = Transaction(id=uuid.uuid4(), date=date.today() - timedelta(days=days_ago), reference="J",
                        description="journal", currency=co["ccy"])
        db.add(t)
        db.flush()
        db.add_all([TransactionLine(transaction_id=t.id, account_id=acc[code].id, debit=dr, credit=cr,
                                    base_debit=dr, base_credit=cr) for code, dr, cr in lines])
        db.commit()


def _ledger(db, co, category):
    from app.services.cfo_intelligence import ledger_balance
    with use_company(co["cid"]):
        return ledger_balance(db, (_code(db, co, category),), None, date.today())


# --- L1–L3 ------------------------------------------------------------------

def test_an_issued_invoice_counts_once(co, db):
    _invoice(co, price=10_000_000)                        # 11,000,000 with VAT
    _invoice(co, "purchase", price=5_000_000)             # 5,500,000
    assert _owed(co) == (11_000_000, 5_500_000)           # were 22,000,000 and 11,000,000
    assert _owed(co) == (_ledger(db, co, "ar"), -_ledger(db, co, "ap"))


def test_part_payments_and_credit_notes_reduce_it(co):
    inv = _invoice(co)
    _pay(co, inv, 4_000_000)
    r = co["api"].post(f"/invoices/{inv['id']}/credit-notes",
                       json={"amount": 1_000_000, "date": date.today().isoformat()})
    assert r.status_code == 201, r.text
    assert _owed(co)[0] == 6_000_000


def test_a_draft_is_not_owed(co):
    _invoice(co)
    _invoice(co, price=3_000_000, tax=0, status="draft")
    assert _owed(co)[0] == 11_000_000


# --- L4: books kept on a cash basis ----------------------------------------------

def test_an_invoice_that_never_posted_counts_its_open_balance(co, db):
    from app.models.invoice import Invoice
    from app.models.payment import Payment
    with use_company(co["cid"]):
        inv = Invoice(number=f"OLD-{uuid.uuid4().hex[:6]}", kind="sales", status="partially_paid",
                      issue_date=date.today() - timedelta(days=60), due_date=date.today() - timedelta(days=30),
                      amount=2_000_000, currency=co["ccy"], entity_id=uuid.UUID(co["client"]))
        db.add(inv)
        db.flush()
        db.add(Payment(invoice_id=inv.id, date=date.today(), amount=500_000, currency=co["ccy"], direction="in"))
        db.commit()
        assert inv.transaction_id is None                 # no recognition entry: the ledger doesn't hold it
        inv_id = inv.id
    assert _owed(co)[0] == 1_500_000
    with use_company(co["cid"]):
        row = db.get(Invoice, inv_id)
        db.add(Payment(invoice_id=inv_id, date=date.today(), amount=1_500_000, currency=co["ccy"], direction="in"))
        row.status = "paid"
        db.commit()
    assert _owed(co)[0] == 0


# --- L5, L6 ------------------------------------------------------------------

def test_an_invoice_older_than_a_year_still_counts(co):
    _invoice(co, days_ago=425)
    assert _owed(co)[0] == 11_000_000                     # the 12-month window dropped it


def test_one_currency_at_a_time(co):
    r = co["api"].post("/fx/rates", json={"from_currency": "USD", "to_currency": "IRR", "rate": 1_000_000,
                                          "effective_date": (date.today() - timedelta(days=30)).isoformat()})
    assert r.status_code == 201, r.text
    _invoice(co)                                          # 11,000,000 IRR
    _invoice(co, price=1_000, tax=0, currency="USD")      # 1,000 USD
    assert _owed(co, "IRR")[0] == 11_000_000
    assert _owed(co, "USD")[0] == 1_000
    assert _owed(co)[0] == 11_000_000 + 1_000 * 1_000_000  # the combined view: its rial value


# --- L7: only trade balances ---------------------------------------------------------

def test_iranian_books_count_cheques_until_they_clear_and_nothing_else(co, db):
    receivable, payable = _code(db, co, "ar"), _code(db, co, "ap")
    in_hand, at_bank, issued = (_code(db, co, c) for c in ("notes_receivable", "cheques_in_collection", "notes_payable"))
    bank, prepaid, wages, net_pay = (_code(db, co, c) for c in ("bank", "prepaid_expense", "wages_expense", "net_pay_payable"))
    _invoice(co)                                          # VAT 1,000,000 to 2130: not a payable
    _invoice(co, "purchase", price=5_000_000)
    _journal(db, co, (wages, 8_000_000, 0), (net_pay, 0, 8_000_000))     # payroll owed: not a payable
    _journal(db, co, (prepaid, 2_000_000, 0), (bank, 0, 2_000_000))      # a prepayment: not a receivable
    assert _owed(co) == (11_000_000, 5_500_000)

    _journal(db, co, (in_hand, 3_000_000, 0), (receivable, 0, 3_000_000))   # the customer pays by cheque
    _journal(db, co, (at_bank, 3_000_000, 0), (in_hand, 0, 3_000_000))     # sent for collection
    _journal(db, co, (payable, 5_500_000, 0), (issued, 0, 5_500_000))      # we pay the supplier by cheque
    assert _owed(co) == (11_000_000, 5_500_000)           # owed until the cheques clear

    _journal(db, co, (bank, 3_000_000, 0), (at_bank, 0, 3_000_000))
    _journal(db, co, (issued, 5_500_000, 0), (bank, 0, 5_500_000))
    assert _owed(co) == (8_000_000, 0)


@uk
def test_uk_receivables_leave_out_prepayments_vat_and_customer_credit(co, db):
    bank, prepaid = _code(db, co, "bank"), _code(db, co, "prepaid_expense")
    sale = _invoice(co, price=1_000)                      # 1,100 with the line's 10% VAT (2200)
    _invoice(co, "purchase", price=500)                   # VAT receivable 50 (1400): not a receivable
    _journal(db, co, (prepaid, 300, 0), (bank, 0, 300))   # a prepayment (1300): not a receivable
    over = _invoice(co, price=100, tax=0)
    _pay(co, over, 150)                                   # 50 of customer credit (2150): not a payable
    assert sale["amount"] == 1_100
    assert _owed(co) == (1_100, 550)


# --- L8: every place says the same --------------------------------------------------

def test_ceo_mode_answers_and_the_insight_agree(co, db):
    from app.services.cfo_intelligence import answer_cfo_question, build_ceo_report, trade_codes
    _invoice(co)
    _invoice(co, "purchase", price=5_000_000)
    ar, ap = _owed(co)
    ceo = co["api"].get("/brain/ceo/report").json()
    assert (ceo["accounts_receivable"], ceo["accounts_payable"]) == (ar, ap) == (11_000_000, 5_500_000)
    with use_company(co["cid"]):
        assert (build_ceo_report(db).accounts_receivable, build_ceo_report(db).accounts_payable) == (ar, ap)
        assert f"{ar:,}" in answer_cfo_question(db, "where are my cash leaks?")
        assert trade_codes(db) == {"ar": ("1112",), "ap": ("2110",)}   # the seed chart has no cheque accounts
    for category in ("notes_receivable", "cheques_in_collection", "notes_payable"):
        _code(db, co, category)                           # the first cheque adds them
    with use_company(co["cid"]):
        assert trade_codes(db) == {"ar": ("1112", "1113", "1114"), "ap": ("2110", "2111")}


@uk
def test_uk_trade_codes(co, db):
    from app.services.cfo_intelligence import trade_codes
    with use_company(co["cid"]):
        assert trade_codes(db) == {"ar": ("1100",), "ap": ("2100",)}   # a UK cheque is simply banked


def test_the_report_adds_no_accounts(co, db):
    """Reading the report resolves its accounts without creating any (the
    posting resolver self-heals a missing account — fine when posting, not
    when a report is read)."""
    from app.models.account import Account
    with use_company(co["cid"]):
        before = db.execute(select(func.count()).select_from(Account)).scalar()
    _owed(co)
    co["api"].get("/brain/ceo/report")
    with use_company(co["cid"]):
        assert db.execute(select(func.count()).select_from(Account)).scalar() == before

"""Realised exchange gains and losses (roadmap 2026-09 §4.6, part 3): a
payment on a foreign invoice clears the receivable (or payable) at the rate
the invoice was booked at, takes the cash at its own rate, and posts the
difference as a realised FX gain or loss — so the receivable ends at zero in
pounds when it ends at zero in dollars."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.company import Company
from app.models.exchange_rate import ExchangeRate
from app.models.transaction import Transaction, TransactionLine


def _login(client, cid):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner",
                               company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


@pytest.fixture()
def uk(client, db):
    from app.db.seed import seed_chart_if_empty
    from app.db.tenant import use_company
    from tests.test_admin_audit import _purge_company
    c = Company(id=uuid.uuid4(), name="Sterling Ltd", slug=f"gb-{uuid.uuid4().hex[:6]}", locale="uk",
                base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        seed_chart_if_empty(db, locale="uk")
        db.commit()
    db.expunge_all()
    yield _login(client, cid), cid
    client.cookies.clear()
    db.rollback()
    db.execute(ExchangeRate.__table__.delete().where(ExchangeRate.company_id.is_(None)))
    db.commit()
    _purge_company(db, cid)


def _rate(api, fc, rate, when, tc="GBP"):
    r = api.post("/fx/rates", json={"from_currency": fc, "to_currency": tc, "rate": rate, "effective_date": when})
    assert r.status_code == 201, r.text


def _invoice(api, amount, *, kind="sales", currency="USD", on="2026-08-01"):
    r = api.post("/invoices", json={"number": f"INV-{uuid.uuid4().hex[:6]}", "kind": kind, "issue_date": on,
                                    "due_date": "2026-12-31", "amount": amount, "currency": currency})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _pay(api, inv, amount, on):
    r = api.post(f"/invoices/{inv['id']}/payments", json={"amount": amount, "date": on})
    assert r.status_code == 201, r.text
    return r.json()


def _base_by_code(db, cid, txn_id) -> dict[str, tuple[int, int, int, int]]:
    """code → (debit, credit, base_debit, base_credit), summed per account."""
    from app.db.tenant import use_company
    from app.models.account import Account
    db.expire_all()
    out: dict[str, list[int]] = {}
    with use_company(cid):
        rows = db.execute(select(Account.code, TransactionLine.debit, TransactionLine.credit,
                                 TransactionLine.base_debit, TransactionLine.base_credit)
                          .join(Account, Account.id == TransactionLine.account_id)
                          .where(TransactionLine.transaction_id == uuid.UUID(str(txn_id)))).all()
    for code, d, c, bd, bc in rows:
        slot = out.setdefault(code, [0, 0, 0, 0])
        for i, v in enumerate((d, c, bd, bc)):
            slot[i] += int(v or 0)
    return {k: tuple(v) for k, v in out.items()}


def _balance(api, code, currency):
    rows = {r["account_code"]: r for r in api.get("/reports/ledger-summary", params={"currency": currency}).json()["rows"]}
    r = rows.get(code)
    return 0 if r is None else r["debit_turnover"] - r["credit_turnover"]


# ─── 1. A receipt at a better rate is a gain, a bill paid dearer a loss ────────

def test_a_sales_invoice_paid_at_a_higher_rate_realises_a_gain(uk, db):
    api, cid = uk
    _rate(api, "USD", 0.80, "2026-07-01")
    _rate(api, "USD", 0.85, "2026-08-15")
    inv = _invoice(api, 1000)
    pay = _pay(api, inv, 1000, "2026-09-01")
    lines = _base_by_code(db, cid, pay["transaction_id"])
    assert lines["1200"] == (1000, 0, 850, 0)                 # the pounds that arrived
    assert lines["1100"] == (0, 1000, 0, 800)                 # the receivable as booked
    assert lines["4210"] == (0, 0, 0, 50)                     # realised gain, base only
    assert db.get(Transaction, uuid.UUID(pay["transaction_id"])).fx_role == "settlement"
    assert _balance(api, "1100", "USD") == 0 and _balance(api, "1100", "ALL") == 0
    assert _balance(api, "4210", "ALL") == -50
    assert pay["realised_fx"] == 50 and pay["base_currency"] == "GBP"
    listed = api.get(f"/invoices/{inv['id']}/payments").json()
    assert [p["realised_fx"] for p in listed] == [50]
    from app.db.tenant import use_company
    from app.models.account import Account
    with use_company(cid):
        assert db.execute(select(Account.name).where(Account.code == "4210")).scalar_one() == "Foreign exchange gains"


def test_a_bill_paid_at_a_higher_rate_realises_a_loss(uk, db):
    api, cid = uk
    _rate(api, "USD", 0.80, "2026-07-01")
    _rate(api, "USD", 0.85, "2026-08-15")
    bill = _invoice(api, 500, kind="purchase")
    pay = _pay(api, bill, 500, "2026-09-01")
    lines = _base_by_code(db, cid, pay["transaction_id"])
    assert lines["2100"] == (500, 0, 400, 0)
    assert lines["1200"] == (0, 500, 0, 425)
    assert lines["7950"] == (0, 0, 25, 0)                     # realised loss
    assert pay["realised_fx"] == -25
    assert _balance(api, "2100", "ALL") == 0


def test_no_rate_change_no_fx_line(uk, db):
    api, cid = uk
    _rate(api, "USD", 0.80, "2026-07-01")
    pay = _pay(api, _invoice(api, 100), 100, "2026-09-01")
    lines = _base_by_code(db, cid, pay["transaction_id"])
    assert set(lines) == {"1200", "1100"} and lines["1200"][2] == 80
    assert pay["realised_fx"] is None


def test_a_base_currency_invoice_is_left_alone(uk, db):
    api, cid = uk
    pay = _pay(api, _invoice(api, 100, currency="GBP"), 100, "2026-09-01")
    t = db.get(Transaction, uuid.UUID(pay["transaction_id"]))
    assert t.fx_role is None and t.fx_rate == 1
    assert set(_base_by_code(db, cid, pay["transaction_id"])) == {"1200", "1100"}


# ─── 2. Part payments, overpayments, credit notes ─────────────────────────────

def test_part_payments_leave_no_penny_on_the_receivable(uk, db):
    api, cid = uk
    _rate(api, "USD", 0.80, "2026-07-01")               # $101 booked at £80.80 → £81
    inv = _invoice(api, 101)
    _rate(api, "USD", 0.90, "2026-08-10")
    first = _pay(api, inv, 50, "2026-08-20")                  # receivable share 50/101 × 81 = 40.1 → 40
    a = _base_by_code(db, cid, first["transaction_id"])
    assert a["1100"][3] == 40 and a["1200"][2] == 45 and a["4210"][3] == 5
    _rate(api, "USD", 0.70, "2026-09-01")
    last = _pay(api, inv, 51, "2026-09-10")                   # the last takes what is left: 81 − 40
    b = _base_by_code(db, cid, last["transaction_id"])
    assert b["1100"][3] == 41 and b["1200"][2] == 36 and b["7950"][2] == 5   # 35.7 → 36; loss 5
    assert _balance(api, "1100", "USD") == 0 and _balance(api, "1100", "ALL") == 0


def test_an_overpayment_is_a_new_credit_at_todays_rate(uk, db):
    api, cid = uk
    _rate(api, "USD", 0.80, "2026-07-01")
    inv = _invoice(api, 100)
    _rate(api, "USD", 0.90, "2026-08-10")
    pay = _pay(api, inv, 120, "2026-09-01")
    lines = _base_by_code(db, cid, pay["transaction_id"])
    assert lines["1200"] == (120, 0, 108, 0)
    assert lines["1100"] == (0, 100, 0, 80)
    assert lines["2150"] == (0, 20, 0, 18)                    # customer credit at the payment's rate
    assert lines["4210"] == (0, 0, 0, 10)


def test_a_credit_note_clears_its_share_at_the_invoice_rate(uk, db):
    api, cid = uk
    _rate(api, "USD", 0.80, "2026-07-01")
    inv = _invoice(api, 100)
    _rate(api, "USD", 0.90, "2026-08-10")
    r = api.post(f"/invoices/{inv['id']}/credit-notes", json={"amount": 40, "date": "2026-08-20", "reason": "returned"})
    assert r.status_code == 201, r.text
    note = _base_by_code(db, cid, r.json()["transaction_id"])
    assert note["1100"] == (0, 40, 0, 32) and note["4100"] == (40, 0, 36, 0) and note["4210"][3] == 4
    _rate(api, "USD", 0.85, "2026-09-01")
    last = _pay(api, inv, 60, "2026-09-05")
    b = _base_by_code(db, cid, last["transaction_id"])
    assert b["1100"][3] == 48 and b["1200"][2] == 51 and b["4210"][3] == 3
    assert _balance(api, "1100", "ALL") == 0


# ─── 3. Reversals, edits, late rates ───────────────────────────────────────────

def test_reversing_a_payment_undoes_the_realised_fx(uk, db):
    api, cid = uk
    _rate(api, "USD", 0.80, "2026-07-01")
    inv = _invoice(api, 100)
    _rate(api, "USD", 0.90, "2026-08-10")
    pay = _pay(api, inv, 100, "2026-09-01")
    assert _balance(api, "4210", "ALL") == -10
    r = api.post(f"/invoices/{inv['id']}/payments/{pay['id']}/reverse")
    assert r.status_code == 200, r.text
    assert _balance(api, "4210", "ALL") == 0 and _balance(api, "1100", "ALL") == 80
    assert _balance(api, "1100", "USD") == 100
    again = _pay(api, inv, 100, "2026-09-02")                 # settles again, at the invoice's rate
    assert _base_by_code(db, cid, again["transaction_id"])["1100"][3] == 80
    assert _balance(api, "1100", "ALL") == 0


def test_a_payment_without_a_rate_is_settled_once_one_arrives(uk, db):
    api, cid = uk
    inv = _invoice(api, 1000, currency="EUR")
    pay = _pay(api, inv, 1000, "2026-09-01")
    t = db.get(Transaction, uuid.UUID(pay["transaction_id"]))
    assert t.fx_rate is None and t.fx_role is None
    _rate(api, "EUR", 0.86, "2026-08-20")                     # the payment's date only: converted,
    t = db.get(Transaction, uuid.UUID(pay["transaction_id"]))  # but the invoice still waits
    db.refresh(t)
    assert t.fx_rate == 0.86 and t.fx_role is None
    _rate(api, "EUR", 0.84, "2026-07-01")                     # now the invoice too → settled
    lines = _base_by_code(db, cid, pay["transaction_id"])
    assert lines["1100"][3] == 840 and lines["1200"][2] == 860 and lines["4210"][3] == 20


def test_moving_a_settled_payment_to_another_date_settles_it_again(uk, db):
    api, cid = uk
    _rate(api, "USD", 0.80, "2026-07-01")
    inv = _invoice(api, 100)
    _rate(api, "USD", 0.90, "2026-08-10")
    _rate(api, "USD", 0.70, "2026-09-10")
    pay = _pay(api, inv, 100, "2026-09-01")
    assert _base_by_code(db, cid, pay["transaction_id"])["4210"][3] == 10
    r = api.patch(f"/transactions/{pay['transaction_id']}", json={"date": "2026-09-12"})
    assert r.status_code == 200, r.text
    lines = _base_by_code(db, cid, pay["transaction_id"])
    assert "4210" not in lines and lines["7950"][2] == 10 and lines["1200"][2] == 70   # now a loss
    assert db.get(Transaction, uuid.UUID(pay["transaction_id"])).fx_role == "settlement"


def test_a_closed_period_is_not_resettled(uk, db):
    api, cid = uk
    inv = _invoice(api, 100, currency="EUR", on="2026-06-01")
    pay = _pay(api, inv, 100, "2026-06-15")
    assert api.put("/admin/closed-period", json={"closed_period": "2026-06-30"}).status_code == 200
    _rate(api, "EUR", 0.84, "2026-05-01")
    t = db.get(Transaction, uuid.UUID(pay["transaction_id"]))
    db.refresh(t)
    assert t.fx_role is None                                  # converted, but its figures stay as closed


# ─── 4. With revaluation and a new base currency ─────────────────────────────────

def test_after_settlement_there_is_nothing_left_to_revalue(uk, db):
    api, _ = uk
    _rate(api, "USD", 0.80, "2026-07-01")
    inv = _invoice(api, 100)
    _rate(api, "USD", 0.90, "2026-08-10")
    _pay(api, inv, 100, "2026-09-01")
    pv = api.post("/fx/revalue", json={"as_of": "2026-09-30"}).json()
    assert all(x["adjustment"] == 0 for x in pv["lines"] if x["account_code"] == "1100")


def test_a_new_base_currency_drops_settlements_that_no_longer_apply(uk, db):
    api, cid = uk
    _rate(api, "USD", 0.80, "2026-07-01")
    inv = _invoice(api, 100)
    _rate(api, "USD", 0.90, "2026-08-10")
    pay = _pay(api, inv, 100, "2026-09-01")
    _rate(api, "GBP", 1.25, "2026-01-01", tc="USD")
    assert api.put("/fx/reporting-currency", json={"currency": "USD"}).status_code == 200
    t = db.get(Transaction, uuid.UUID(pay["transaction_id"]))
    db.refresh(t)
    assert t.fx_role is None and t.fx_rate == 1               # a dollar invoice in a dollar book
    assert set(_base_by_code(db, cid, pay["transaction_id"])) == {"1200", "1100"}

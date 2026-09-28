"""The cheque lifecycle (roadmap 2026-09 §3.4): in Iranian books a received
cheque moves notes receivable (1113) → cheques in collection (1114) → bank,
a bounce puts the debt back on the customer and it can be deposited again or
returned, it can be passed on to a supplier; an issued cheque sits in notes
payable (2111) until it clears. A cheque given for an invoice is its payment.
UK and personal books — and cheques recorded before — post only when the
cheque clears. Sayad ids are checked and unregistered cheques nag."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.db.tenant import use_company
from app.models.account import Account
from app.models.commitment import Commitment
from app.models.company import Company
from app.models.transaction import TransactionLine

D = date(2026, 9, 1)            # all in the past: postings refuse future dates
SAYAD = "1234567890123456"


def _login(client, cid, role="owner"):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    tok = create_session_token(user_id=str(uuid.uuid4()), username=role, is_admin=role == "owner", role=role,
                               company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def _company(db, locale, currency, kind="business"):
    from app.db.seed import seed_chart_if_empty
    c = Company(id=uuid.uuid4(), name=f"Cheques {locale}", slug=f"chq-{uuid.uuid4().hex[:6]}", locale=locale,
                base_currency=currency, status="active", token_version=0, kind=kind)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        seed_chart_if_empty(db, locale=locale, chart="personal" if kind == "personal" else None)
        if currency != "IRR":
            from app.services.fx_service import set_reporting_currency
            set_reporting_currency(db, currency)
        db.commit()
    db.expunge_all()
    return cid


class Books:
    def __init__(self, db, api, cid):
        self.db, self.api, self.cid = db, api, cid

    def bal(self, code):
        """Debit − credit on an account, in this company."""
        with use_company(self.cid):
            self.db.expire_all()
            dr, cr = self.db.execute(
                select(func.coalesce(func.sum(TransactionLine.debit), 0), func.coalesce(func.sum(TransactionLine.credit), 0))
                .join(Account, Account.id == TransactionLine.account_id).where(Account.code == code)).one()
        return int(dr) - int(cr)

    def party(self, kind="client", name=None):
        r = self.api.post("/entities", json={"type": kind, "name": name or f"{kind} {uuid.uuid4().hex[:5]}"})
        assert r.status_code in (200, 201), r.text
        return r.json()

    def invoice(self, kind="sales", amount=5_000_000, currency="IRR", party=None):
        party = party or self.party("client" if kind == "sales" else "supplier")
        r = self.api.post("/invoices", json={
            "number": f"{'S' if kind == 'sales' else 'B'}-{uuid.uuid4().hex[:6]}", "kind": kind, "status": "issued",
            "issue_date": "2026-08-20", "due_date": "2026-09-20", "amount": amount, "currency": currency,
            "entity_id": party["id"]})
        assert r.status_code == 201, r.text
        return r.json()

    def inv(self, inv_id):
        return next(i for i in self.api.get("/invoices").json() if i["id"] == inv_id)

    def cheque(self, **kw):
        body = {"title": "Cheque", "amount": 5_000_000, "due_date": "2026-09-20", "direction": "receive",
                "on": D.isoformat(), **kw}
        r = self.api.post("/commitments/cheques", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    def step(self, cheque, action, ok=200, **body):
        r = self.api.post(f"/commitments/{cheque['id']}/{action}", json=body)
        assert r.status_code == ok, r.text
        return r.json()

    def history(self, cheque):
        return [e["action"] for e in self.api.get(f"/commitments/{cheque['id']}/history").json()["events"]]


@pytest.fixture()
def ir(client, db):
    from tests.test_admin_audit import _purge_company
    cid = _company(db, "ir", "IRR")
    yield Books(db, _login(client, cid), cid)
    client.cookies.clear()
    db.rollback()
    _purge_company(db, cid)


@pytest.fixture()
def uk(client, db):
    from tests.test_admin_audit import _purge_company
    cid = _company(db, "uk", "GBP")
    yield Books(db, _login(client, cid), cid)
    client.cookies.clear()
    db.rollback()
    _purge_company(db, cid)


# ─── Received cheques in Iranian books ───────────────────────────────────────

def test_a_received_cheque_travels_through_the_notes_accounts(ir):
    c = ir.cheque(counter_account_code="1112", reference="881234", bank_name="Mellat")
    assert c["ledger_mode"] == "notes" and c["status"] == "pending"
    assert ir.bal("1113") == 5_000_000 and ir.bal("1112") == -5_000_000        # the customer paid with a note
    assert ir.api.get("/commitments/summary").json()["cheques_in_hand"] == 5_000_000

    c = ir.step(c, "deposit", on="2026-09-10")
    assert c["status"] == "deposited" and c["deposited_on"] == "2026-09-10"
    assert ir.bal("1113") == 0 and ir.bal("1114") == 5_000_000
    assert ir.api.get("/commitments/summary").json()["cheques_at_bank"] == 5_000_000

    c = ir.step(c, "settle", on="2026-09-20")
    assert c["status"] == "settled" and c["settled_transaction_id"]
    assert ir.bal("1114") == 0 and ir.bal("1110") == 5_000_000
    assert ir.history(c) == ["received", "deposited", "cleared"]
    events = ir.api.get(f"/commitments/{c['id']}/history").json()["events"]
    assert all(e["transaction_id"] for e in events)


def test_a_bounce_puts_the_debt_back_and_it_can_be_deposited_again(ir):
    c = ir.cheque(counter_account_code="1112")
    ir.step(c, "deposit", on="2026-09-10")
    c = ir.step(c, "bounce", on="2026-09-21", note="insufficient funds")
    assert c["status"] == "bounced"
    assert ir.bal("1112") == 0 and ir.bal("1114") == 0 and ir.bal("1113") == 0   # the customer owes it again
    summary = ir.api.get("/commitments/summary").json()
    assert summary["cheques_bounced"] == 5_000_000 and summary["receivable"] == 5_000_000

    c = ir.step(c, "deposit", on="2026-09-24")                                      # presented again
    assert c["status"] == "deposited" and ir.bal("1114") == 5_000_000 and ir.bal("1112") == -5_000_000
    c = ir.step(c, "settle", on="2026-09-25")
    assert ir.bal("1110") == 5_000_000 and ir.bal("1114") == 0 and ir.bal("1112") == -5_000_000
    assert ir.history(c) == ["received", "deposited", "bounced", "redeposited", "cleared"]
    note = ir.api.get(f"/commitments/{c['id']}/history").json()["events"][2]["note"]
    assert note == "insufficient funds"


def test_a_cheque_for_an_invoice_pays_it_and_a_bounce_reopens_it(ir):
    inv = ir.invoice(amount=5_000_000)
    c = ir.cheque(invoice_id=inv["id"], sayad_id=SAYAD)
    assert c["invoice_number"] == inv["number"] and c["counter_account_code"] == "1112"
    assert ir.inv(inv["id"])["status"] == "paid" and ir.bal("1113") == 5_000_000 and ir.bal("1112") == 0
    pays = ir.api.get(f"/invoices/{inv['id']}/payments").json()
    assert [p["method"] for p in pays] == ["cheque"]

    # the payment follows the cheque, not the invoice screen
    assert ir.api.post(f"/invoices/{inv['id']}/payments/{pays[0]['id']}/reverse").status_code == 409
    assert ir.api.post(f"/invoices/{inv['id']}/void").status_code == 409

    ir.step(c, "deposit", on="2026-09-10")
    ir.step(c, "bounce", on="2026-09-21")
    reopened = ir.inv(inv["id"])
    assert reopened["balance_due"] == 5_000_000 and reopened["status"] != "paid"
    assert ir.bal("1112") == 5_000_000 and ir.bal("1114") == 0                    # the customer owes again

    ir.step(c, "deposit", on="2026-09-24")
    assert ir.inv(inv["id"])["status"] == "paid" and ir.bal("1112") == 0
    ir.step(c, "settle", on="2026-09-25")
    assert ir.bal("1110") == 5_000_000 and ir.bal("1113") == ir.bal("1114") == 0
    assert ir.inv(inv["id"])["status"] == "paid"


def test_a_cheque_for_the_wrong_kind_of_invoice_is_refused(ir):
    bill = ir.invoice(kind="purchase")
    r = ir.api.post("/commitments/cheques", json={"title": "x", "amount": 10, "due_date": "2026-09-20",
                                                   "direction": "receive", "invoice_id": bill["id"]})
    assert r.status_code == 400 and "sales invoice" in r.json()["detail"]


def test_returning_an_unused_cheque_reverses_its_receipt(ir):
    c = ir.cheque(counter_account_code="1112")
    c = ir.step(c, "return", on="2026-09-05", note="paid in cash instead")
    assert c["status"] == "returned" and ir.bal("1113") == 0 and ir.bal("1112") == 0
    ir.step(c, "deposit", ok=409)
    ir.step(c, "settle", ok=409)


def test_a_bounced_cheque_handed_back_posts_nothing_more(ir):
    c = ir.cheque(counter_account_code="1112")
    ir.step(c, "bounce", on="2026-09-21")
    before = (ir.bal("1112"), ir.bal("1113"))
    c = ir.step(c, "return", on="2026-09-22")
    assert c["status"] == "returned" and (ir.bal("1112"), ir.bal("1113")) == before == (0, 0)
    assert ir.api.get("/commitments?status=open").json() == []


def test_passing_a_cheque_on_pays_a_supplier_bill(ir):
    supplier = ir.party("supplier", "Pars Paper")
    bill = ir.invoice(kind="purchase", amount=2_000_000, party=supplier)
    c = ir.cheque(amount=2_000_000, counter_account_code="1112")
    c = ir.step(c, "endorse", on="2026-09-06", invoice_id=bill["id"])
    assert c["status"] == "endorsed" and c["endorsed_to"] == "Pars Paper"
    assert ir.inv(bill["id"])["status"] == "paid"
    assert ir.bal("1113") == 0 and ir.bal("2110") == 0                            # bill booked and paid
    assert ir.history(c) == ["received", "endorsed"]


def test_passing_a_cheque_on_to_an_account(ir):
    c = ir.cheque(amount=700_000, counter_account_code="1112")
    ir.step(c, "endorse", ok=422, to="Landlord")                                   # which account?
    c = ir.step(c, "endorse", to="Landlord", account_code="2140")
    assert c["endorsed_to"] == "Landlord" and ir.bal("2140") == 700_000 and ir.bal("1113") == 0


# ─── Issued cheques in Iranian books ─────────────────────────────────────────

def test_an_issued_cheque_pays_a_bill_and_clears_from_notes_payable(ir):
    bill = ir.invoice(kind="purchase", amount=3_000_000)
    c = ir.cheque(direction="pay", amount=3_000_000, invoice_id=bill["id"])
    assert ir.inv(bill["id"])["status"] == "paid" and ir.bal("2111") == -3_000_000 and ir.bal("2110") == 0
    ir.step(c, "deposit", ok=400)                                                  # issued cheques aren't deposited
    ir.step(c, "endorse", ok=409, to="x")
    c = ir.step(c, "settle", on="2026-09-20")
    assert ir.bal("2111") == 0 and ir.bal("1110") == -3_000_000
    assert ir.history(c) == ["issued", "cleared"]


def test_an_issued_cheque_that_bounces_leaves_the_expense_and_owes_the_supplier(ir):
    c = ir.cheque(direction="pay", amount=800_000, counter_account_code="6112", title="Rent")
    assert ir.bal("6112") == 800_000 and ir.bal("2111") == -800_000
    ir.step(c, "bounce", on="2026-09-21")
    assert ir.bal("6112") == 800_000                                               # the rent is still the rent
    assert ir.bal("2111") == 0 and ir.bal("2110") == -800_000                      # the landlord is owed
    ir.step(c, "settle", on="2026-09-24")                                          # presented again, paid
    assert ir.bal("2110") == 0 and ir.bal("1110") == -800_000


def test_a_bounced_bill_cheque_reopens_the_bill(ir):
    bill = ir.invoice(kind="purchase", amount=1_000_000)
    c = ir.cheque(direction="pay", amount=1_000_000, invoice_id=bill["id"])
    ir.step(c, "bounce", on="2026-09-21")
    assert ir.inv(bill["id"])["balance_due"] == 1_000_000 and ir.bal("2111") == 0 and ir.bal("2110") == -1_000_000
    ir.step(c, "settle", on="2026-09-24")
    assert ir.inv(bill["id"])["status"] == "paid" and ir.bal("2110") == 0 and ir.bal("1110") == -1_000_000


def test_steps_out_of_order_are_refused(ir):
    c = ir.cheque(counter_account_code="1112")
    ir.step(c, "deposit")
    ir.step(c, "return", ok=409)                                                   # it's at the bank
    ir.step(c, "endorse", ok=409, to="x")
    ir.step(c, "settle")
    ir.step(c, "bounce", ok=409)
    ir.step(c, "settle", ok=400)
    posted = ir.cheque(counter_account_code="1112")
    assert ir.api.delete(f"/commitments/{posted['id']}").status_code == 409        # in the books: return it
    tracked = ir.cheque()                                                          # no account: tracking only
    assert tracked["ledger_mode"] == "direct"
    assert ir.api.delete(f"/commitments/{tracked['id']}").status_code == 204


def test_statements_see_a_cheque_in_hand_as_a_receivable_not_cash(ir):
    c = ir.cheque(counter_account_code="1112", amount=4_000_000)
    ir.step(c, "deposit", on="2026-09-10")
    bs = ir.api.get("/manager-reports/financial/iran/balance-sheet", params={"as_of": "2026-09-15"}).json()
    rows = {r["key"]: r for r in bs["rows"]}
    assert rows["ca_cash"]["amount_current"] == 0
    cf = ir.api.get("/manager-reports/financial/iran/cash-flow",
                    params={"from_date": "2026-09-01", "to_date": "2026-09-15"}).json()
    assert {r["key"]: r for r in cf["rows"]}["net_cash_change"]["amount_current"] == 0
    ir.step(c, "settle", on="2026-09-20")
    cf = ir.api.get("/manager-reports/financial/iran/cash-flow",
                    params={"from_date": "2026-09-01", "to_date": "2026-09-25"}).json()
    rows = {r["key"]: r for r in cf["rows"]}
    assert rows["operating_net"]["amount_current"] == 4_000_000 and rows["fx_rate_effect"]["amount_current"] == 0


# ─── Sayad ───────────────────────────────────────────────────────────────────

def test_sayad_ids_are_16_digits_and_unique(ir):
    bad = ir.api.post("/commitments/cheques", json={"title": "x", "amount": 1, "due_date": "2026-09-20",
                                                    "sayad_id": "12345"})
    assert bad.status_code == 422
    c = ir.cheque(sayad_id="۱۲۳۴ ۵۶۷۸ ۹۰۱۲ ۳۴۵۶")                                   # Persian digits, spaced
    assert c["sayad_id"] == SAYAD
    dup = ir.api.post("/commitments/cheques", json={"title": "y", "amount": 1, "due_date": "2026-09-20",
                                                    "sayad_id": SAYAD})
    assert dup.status_code == 409


def test_an_unregistered_cheque_nags_until_it_is_registered(ir):
    soon = (date.today() + timedelta(days=10)).isoformat()
    far = (date.today() + timedelta(days=90)).isoformat()
    c = ir.cheque(direction="pay", title="Supplier cheque", due_date=soon, amount=100)
    ir.cheque(direction="pay", title="Far cheque", due_date=far, amount=100)
    assert c["needs_sayad"] is True
    feed = ir.api.get("/notifications/feed").json()
    titles = [i["title"] for i in feed]
    assert "Register cheque in Sayad: Supplier cheque" in titles
    assert not any("Far cheque" in t for t in titles)
    c = ir.step(c, "sayad", sayad_id=SAYAD, on="2026-09-02")
    assert c["needs_sayad"] is False and c["sayad_registered_on"] == "2026-09-02"
    assert "Register cheque in Sayad: Supplier cheque" not in [i["title"] for i in ir.api.get("/notifications/feed").json()]
    assert "sayad_registered" in ir.history(c)


def test_a_received_cheque_asks_to_be_confirmed(ir):
    soon = (date.today() + timedelta(days=3)).isoformat()
    ir.cheque(title="Customer cheque", due_date=soon, amount=100)
    assert "Confirm cheque in Sayad: Customer cheque" in [i["title"] for i in ir.api.get("/notifications/feed").json()]


# ─── Books that bank cheques when they clear ─────────────────────────────────

def test_uk_books_bank_a_cheque_when_it_clears(uk):
    c = uk.cheque(counter_account_code="1100", amount=250)
    assert c["ledger_mode"] == "direct" and uk.bal("1100") == 0 and c["needs_sayad"] is False
    c = uk.step(c, "deposit", on="2026-09-10")
    assert c["status"] == "deposited" and uk.bal("1200") == 0                      # nothing until it clears
    c = uk.step(c, "settle", on="2026-09-20")
    assert uk.bal("1200") == 250 and uk.bal("1100") == -250
    assert uk.history(c) == ["received", "deposited", "cleared"]


def test_uk_invoice_cheque_pays_the_invoice_when_it_clears(uk):
    inv = uk.invoice(amount=900, currency="GBP")
    c = uk.cheque(invoice_id=inv["id"], amount=900)
    assert uk.inv(inv["id"])["balance_due"] == 900                                 # not yet
    uk.step(c, "bounce", on="2026-09-12")
    assert uk.inv(inv["id"])["balance_due"] == 900
    uk.step(c, "settle", on="2026-09-20")
    assert uk.inv(inv["id"])["status"] == "paid" and uk.bal("1200") == 900


def test_a_cheque_in_another_currency_than_the_invoice_is_refused(uk):
    inv = uk.invoice(amount=900, currency="EUR")
    r = uk.api.post("/commitments/cheques", json={"title": "x", "amount": 900, "due_date": "2026-09-20",
                                                   "direction": "receive", "invoice_id": inv["id"]})
    assert r.status_code == 400 and "GBP" in r.json()["detail"]


def test_personal_books_keep_it_simple(client, db):
    from tests.test_admin_audit import _purge_company
    cid = _company(db, "ir", "IRR", kind="personal")
    api = _login(client, cid, role="personal")
    try:
        from app.services.account_resolver import resolve_account_code
        with use_company(cid):
            ar = resolve_account_code(db, "bank")
        c = api.post("/commitments/cheques", json={"title": "Rent cheque", "amount": 100, "due_date": "2026-09-20",
                                                    "direction": "pay", "counter_account_code": ar}).json()
        assert c["ledger_mode"] == "direct"
    finally:
        client.cookies.clear()
        db.rollback()
        _purge_company(db, cid)


def test_cheques_recorded_before_keep_clearing_as_before(ir):
    with use_company(ir.cid):
        row = Commitment(kind="cheque", direction="receive", title="Old cheque", amount=300, due_date=D,
                         status="pending", counter_account_code="1112")
        ir.db.add(row)
        ir.db.commit()
        cid = str(row.id)
    assert ir.api.get("/commitments").json()[0]["ledger_mode"] == "direct"
    ir.step({"id": cid}, "settle", on="2026-09-20")
    assert ir.bal("1110") == 300 and ir.bal("1112") == -300 and ir.bal("1113") == 0
    assert ir.history({"id": cid}) == ["cleared"]


# ─── Cash forecast, AI tools, roles ──────────────────────────────────────────

def test_a_deposited_cheque_is_money_on_its_way(ir):
    from app.services.cash_forecast import forecast
    due = date.today() + timedelta(days=5)
    c = ir.cheque(counter_account_code="1112", amount=600_000, due_date=due.isoformat())
    ir.step(c, "deposit")
    with use_company(ir.cid):
        f = forecast(ir.db, today=date.today())
    items = [i for w in f["weeks"] for i in w["items"]]
    assert any(i["source_id"] == c["id"] and i["amount"] == 600_000 for i in items)


def test_an_invoice_cheque_is_counted_once_in_the_forecast(uk):
    from app.services.cash_forecast import forecast
    inv = uk.invoice(amount=400, currency="GBP")
    due = date.today() + timedelta(days=20)
    c = uk.cheque(invoice_id=inv["id"], amount=400, due_date=due.isoformat())
    with use_company(uk.cid):
        f = forecast(uk.db, today=date.today())
    items = [i for w in f["weeks"] for i in w["items"]]
    mine = [i for i in items if i["source_id"] in (c["id"], inv["id"])]
    assert len(mine) == 1 and mine[0]["source_id"] == c["id"] and mine[0].get("covers_invoice") == inv["number"]


def _ctx(db):
    from app.services.ai_accountant.base import ToolContext
    return ToolContext(db=db, user_id="u1", username="tester", is_admin=True)


def test_the_assistant_records_and_moves_cheques(ir):
    from app.services.ai_accountant.commitment_tools import (
        ProposeChequeStep,
        ProposeChequeStepInput,
        ProposeCreateCheque,
        ProposeCreateChequeInput,
    )
    from app.services.ai_accountant.execute_service import execute_proposal
    from app.services.ai_accountant.base import ToolError
    inv = ir.invoice(amount=1_500_000)
    with use_company(ir.cid):
        ctx = _ctx(ir.db)
        card = asyncio.run(ProposeCreateCheque().run(ctx, ProposeCreateChequeInput(
            title="Customer cheque", amount=1_500_000, due_date=date(2026, 9, 20), direction="receive",
            sayad_id="1111222233334444", invoice=inv["number"])))
        assert "Sayad 1111222233334444" in card["summary"] and inv["number"] in card["summary"]
        execute_proposal(ir.db, confirmation_token=card["confirmation_token"], actor_user_id="u1", actor_username="t")
        ir.db.commit()
        with pytest.raises(ToolError):
            asyncio.run(ProposeCreateCheque().run(ctx, ProposeCreateChequeInput(
                title="x", amount=1, due_date=date(2026, 9, 20), sayad_id="123")))
        step = asyncio.run(ProposeChequeStep().run(ctx, ProposeChequeStepInput(
            cheque="1111222233334444", action="deposit", date=date(2026, 9, 10))))
        res = execute_proposal(ir.db, confirmation_token=step["confirmation_token"], actor_user_id="u1",
                               actor_username="t")
        ir.db.commit()
        assert res.transaction_id
        with pytest.raises(ToolError):                                             # at the bank: can't return
            asyncio.run(ProposeChequeStep().run(ctx, ProposeChequeStepInput(cheque="1111222233334444", action="return")))
    assert ir.inv(inv["id"])["status"] == "paid" and ir.bal("1114") == 1_500_000


def test_the_assistant_passes_a_cheque_on(ir):
    from app.services.ai_accountant.commitment_tools import ProposeChequeStep, ProposeChequeStepInput
    from app.services.ai_accountant.execute_service import execute_proposal
    bill = ir.invoice(kind="purchase", amount=500_000)
    c = ir.cheque(amount=500_000, counter_account_code="1112", reference="777")
    with use_company(ir.cid):
        step = asyncio.run(ProposeChequeStep().run(_ctx(ir.db), ProposeChequeStepInput(
            cheque="777", action="endorse", bill=bill["number"], date=date(2026, 9, 6))))
        assert bill["number"] in step["summary"]
        execute_proposal(ir.db, confirmation_token=step["confirmation_token"], actor_user_id="u1", actor_username="t")
        ir.db.commit()
    assert ir.inv(bill["id"])["status"] == "paid"
    assert next(r for r in ir.api.get("/commitments").json() if r["id"] == c["id"])["status"] == "endorsed"


def test_viewers_read_cheques_but_cannot_move_them(client, ir):
    c = ir.cheque(counter_account_code="1112")
    viewer = _login(client, ir.cid, role="viewer")
    assert viewer.get(f"/commitments/{c['id']}/history").status_code == 200
    for action in ("deposit", "return", "endorse", "sayad"):
        assert viewer.post(f"/commitments/{c['id']}/{action}", json={}).status_code == 403

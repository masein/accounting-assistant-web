"""13-week cash forecast that learns (roadmap 2026-09 §5.3): each scheduled
source on its learned date, the unscheduled baseline without double
counting, the lowest point, scenarios (bounced cheque, unpaid invoice, late
payer, one-off), the routes, the dashboard and the AI accountant tool."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.seed import seed_chart_if_empty
from app.db.tenant import use_company
from app.models.account import Account
from app.models.commitment import Commitment
from app.models.company import Company
from app.models.credit_note import CreditNote
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.pay_run import PayRun
from app.models.payment import Payment
from app.models.recurring import RecurringRule
from app.models.recurring_invoice import RecurringInvoice
from app.models.transaction import Transaction, TransactionLine
from app.services.cash_forecast import (
    Scenario, baseline, compare, customer_lateness, forecast, resolve_scenario,
)

TODAY = date(2026, 9, 23)            # a Wednesday; the forecast's first week starts Mon 21 Sep
MONDAY = date(2026, 9, 21)
HORIZON_END = MONDAY + timedelta(weeks=13) - timedelta(days=1)   # Sun 20 Dec


@pytest.fixture()
def co(db, client):
    c = Company(id=uuid.uuid4(), name="Forecast Co", slug=f"fc-{uuid.uuid4().hex[:8]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    with use_company(c.id):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
        acc = {a.code: a for a in db.execute(select(Account)).scalars()}
        ents = {n: Entity(id=uuid.uuid4(), name=n, type=t, company_id=c.id)
                for n, t in (("Aria Trading", "client"), ("Behsaz", "client"), ("Caspian", "client"),
                             ("Delta Supplies", "supplier"), ("Pars Leasing", "supplier"))}
        db.add_all(ents.values())
        db.commit()

    def login(role="owner"):
        tok = create_session_token(user_id=str(uuid.uuid4()), username=role, is_admin=(role == "owner"),
                                   company_id=str(c.id), role=role)
        csrf = generate_csrf_token()
        client.cookies.set(settings.auth_cookie_name, tok)
        client.cookies.set(CSRF_COOKIE, csrf)
        from tests.conftest import _CSRFTestClient
        return _CSRFTestClient(client, csrf)
    yield {"company": c, "acc": acc, "ents": ents, "login": login}
    from tests.test_admin_audit import _purge_company
    _purge_company(db, str(c.id))


def _code(co, prefix):
    return sorted(code for code in co["acc"] if code.startswith(prefix) and len(code) >= 4)[0]


def _add(db, co, *rows):
    with use_company(co["company"].id):
        db.add_all(rows)
        db.commit()
    return rows[0] if len(rows) == 1 else rows


def _journal(db, co, on, amount, *, cash_in=True, reference="J", currency="IRR", deleted=False):
    """A cash journal: money in (Dr cash / Cr revenue) or out (Dr expense / Cr cash)."""
    acc = co["acc"]
    other = acc[_code(co, "41") if cash_in else _code(co, "6")]
    t = Transaction(id=uuid.uuid4(), date=on, reference=reference, description="j", currency=currency)
    if deleted:
        t.deleted_at = datetime.now(timezone.utc)
    dr, cr = (acc["1110"], other) if cash_in else (other, acc["1110"])
    _add(db, co, t)
    _add(db, co, TransactionLine(transaction_id=t.id, account_id=dr.id, debit=amount, credit=0),
         TransactionLine(transaction_id=t.id, account_id=cr.id, debit=0, credit=amount))
    return t


def _invoice(db, co, entity, *, amount, due, kind="sales", status="issued", currency="IRR", **kw):
    return _add(db, co, Invoice(id=uuid.uuid4(), number=f"INV-{uuid.uuid4().hex[:6]}", kind=kind, status=status,
                                issue_date=due - timedelta(days=30), due_date=due, amount=amount,
                                currency=currency, entity_id=co["ents"][entity].id, **kw))


def _paid_invoice(db, co, entity, *, due, paid_on, amount=100_000):
    inv = _invoice(db, co, entity, amount=amount, due=due, status="paid")
    _add(db, co, Payment(invoice_id=inv.id, date=paid_on, amount=amount, currency="IRR", direction="in"))
    return inv


def _forecast(db, co, **kw):
    with use_company(co["company"].id):
        return forecast(db, today=kw.pop("today", TODAY), **kw)


def _items(f, kind=None):
    return [i for w in f["weeks"] for i in w["items"] if kind is None or i["kind"] == kind]


def _week_of(f, day):
    return next(i for i, w in enumerate(f["weeks"]) if w["week_start"] == (day - timedelta(days=day.weekday())).isoformat())


# --- shape -----------------------------------------------------------------------------------------

def test_empty_books_forecast_flat(db, co):
    f = _forecast(db, co)
    assert f["currency"] == "IRR" and f["as_of"] == TODAY.isoformat()
    assert [w["week_start"] for w in f["weeks"]][:2] == ["2026-09-21", "2026-09-28"] and len(f["weeks"]) == 13
    assert f["opening_cash"] == f["closing_cash"] == 0
    assert all(w["inflow"] == w["outflow"] == 0 and not w["risk"] for w in f["weeks"])
    assert f["first_negative_week"] is None and f["lowest"]["closing"] == 0
    assert f["baseline"] == {"inflow": 0, "outflow": 0, "weeks_of_history": 0}
    assert f["scenario"] is None and f["learned"] == {"company_days_late": 0, "customers": []}


def test_weeks_are_bounded(db, co):
    assert len(_forecast(db, co, weeks=4)["weeks"]) == 4
    assert len(_forecast(db, co, weeks=99)["weeks"]) == 26
    assert len(_forecast(db, co, weeks=0)["weeks"]) == 1


def test_opening_is_cash_on_hand_today(db, co):
    _journal(db, co, date(2025, 1, 5), 1_000_000)
    _journal(db, co, TODAY + timedelta(days=3), 555)             # a future-dated entry is not cash yet
    f = _forecast(db, co)
    assert f["opening_cash"] == 1_000_000 and f["closing_cash"] == 1_000_000


# --- learning: when customers really pay ----------------------------------------------------------

def _history(db, co):
    # Aria: three invoices, each paid 20 days late. Behsaz: two, on time.
    for m in (3, 4, 5):
        due = date(2026, m, 10)
        _paid_invoice(db, co, "Aria Trading", due=due, paid_on=due + timedelta(days=20))
    for m in (6, 7):
        due = date(2026, m, 1)
        _paid_invoice(db, co, "Behsaz", due=due, paid_on=due)


def test_customer_lateness_is_learned(db, co):
    _history(db, co)
    with use_company(co["company"].id):
        per, company, samples = customer_lateness(db, TODAY, "IRR")
    aria, behsaz = co["ents"]["Aria Trading"].id, co["ents"]["Behsaz"].id
    assert per == {aria: 20, behsaz: 0} and company == 20 and samples == {aria: 3, behsaz: 2}


def test_open_invoices_land_on_the_learned_date(db, co):
    _history(db, co)
    due = TODAY + timedelta(days=7)
    a = _invoice(db, co, "Aria Trading", amount=400_000, due=due)
    b = _invoice(db, co, "Behsaz", amount=300_000, due=due)
    c = _invoice(db, co, "Caspian", amount=200_000, due=due)      # no history → the company median
    f = _forecast(db, co)
    got = {i["source_id"]: (i["date"], i["days_late"]) for i in _items(f, "invoice_in")}
    assert got == {str(a.id): ((due + timedelta(days=20)).isoformat(), 20), str(b.id): (due.isoformat(), None),
                   str(c.id): ((due + timedelta(days=20)).isoformat(), 20)}
    learned = {r["name"]: (r["days_late"], r["paid_invoices"]) for r in f["learned"]["customers"]}
    assert learned == {"Aria Trading": (20, 3), "Behsaz": (0, 2)} and f["learned"]["company_days_late"] == 20
    assert f["learned"]["customers"][0]["name"] == "Aria Trading"      # the latest payer first
    assert next(i for i in _items(f) if i["source_id"] == str(a.id))["entity_name"] == "Aria Trading"


def test_few_paid_invoices_teach_nothing(db, co):
    _paid_invoice(db, co, "Aria Trading", due=date(2026, 5, 1), paid_on=date(2026, 6, 1))
    with use_company(co["company"].id):
        assert customer_lateness(db, TODAY, "IRR")[:2] == ({}, 0)
    _paid_invoice(db, co, "Aria Trading", due=date(2024, 5, 1), paid_on=date(2024, 9, 1))   # older than a year
    with use_company(co["company"].id):
        assert customer_lateness(db, TODAY, "IRR")[:2] == ({}, 0)


def test_overdue_items_land_this_week(db, co):
    inv = _invoice(db, co, "Caspian", amount=90_000, due=TODAY - timedelta(days=30))
    bill = _invoice(db, co, "Delta Supplies", amount=40_000, due=TODAY - timedelta(days=5), kind="purchase")
    f = _forecast(db, co)
    week0 = {i["source_id"]: i for i in f["weeks"][0]["items"]}
    assert week0[str(inv.id)]["date"] == TODAY.isoformat() and week0[str(inv.id)]["overdue"]
    assert week0[str(bill.id)]["amount"] == -40_000 and week0[str(bill.id)]["overdue"]


def test_the_open_balance_is_what_is_expected(db, co):
    inv = _invoice(db, co, "Caspian", amount=1_000_000, due=TODAY + timedelta(days=10), status="partially_paid")
    _add(db, co, Payment(invoice_id=inv.id, date=TODAY - timedelta(days=2), amount=300_000, currency="IRR",
                         direction="in"),
         CreditNote(invoice_id=inv.id, entity_id=inv.entity_id, kind="sales", date=TODAY, amount=200_000,
                    currency="IRR", note_type="reduction"),
         CreditNote(invoice_id=inv.id, entity_id=inv.entity_id, kind="sales", date=TODAY, amount=50_000,
                    currency="IRR", note_type="credit"))                # an entity credit: not a reduction
    _invoice(db, co, "Caspian", amount=700_000, due=TODAY + timedelta(days=10), status="draft")
    _invoice(db, co, "Caspian", amount=700_000, due=TODAY + timedelta(days=10), status="paid")
    _invoice(db, co, "Caspian", amount=700_000, due=TODAY + timedelta(days=10), status="voided")
    assert [i["amount"] for i in _items(_forecast(db, co), "invoice_in")] == [500_000]


def test_bills_go_out_on_their_scheduled_date(db, co):
    bill = _invoice(db, co, "Delta Supplies", amount=250_000, due=TODAY + timedelta(days=20), kind="purchase",
                    scheduled_payment_date=TODAY + timedelta(days=10))
    items = _items(_forecast(db, co), "bill_out")
    assert [(i["source_id"], i["date"], i["amount"]) for i in items] == \
        [(str(bill.id), (TODAY + timedelta(days=10)).isoformat(), -250_000)]


def test_beyond_the_horizon_is_left_out(db, co):
    _invoice(db, co, "Caspian", amount=1, due=HORIZON_END + timedelta(days=1))
    _invoice(db, co, "Caspian", amount=2, due=HORIZON_END)
    assert [i["amount"] for i in _items(_forecast(db, co))] == [2]


# --- cheques, installments, payroll, recurring ----------------------------------------------------

def _cheques(db, co):
    ents = co["ents"]
    incoming = Commitment(kind="cheque", direction="receive", title="Aria cheque 1182", amount=500_000,
                          due_date=TODAY + timedelta(days=14), bank_name="Mellat", reference="1182",
                          entity_id=ents["Aria Trading"].id)
    loan = Commitment(kind="installment", direction="pay", title="Car loan 3/12", amount=200_000,
                      due_date=TODAY + timedelta(days=35), entity_id=ents["Pars Leasing"].id)
    late = Commitment(kind="cheque", direction="pay", title="Rent cheque", amount=80_000,
                      due_date=TODAY - timedelta(days=3), bank_name="Saderat")
    done = [Commitment(kind="cheque", direction="receive", title="Old", amount=9, due_date=TODAY, status=s)
            for s in ("settled", "bounced", "cancelled")]
    _add(db, co, incoming, loan, late, *done)
    return incoming, loan, late


def test_pending_cheques_and_installments(db, co):
    incoming, loan, late = _cheques(db, co)
    f = _forecast(db, co)
    got = {i["source_id"]: (i["kind"], i["date"], i["amount"], i["overdue"]) for i in _items(f)}
    assert got == {
        str(incoming.id): ("cheque_in", incoming.due_date.isoformat(), 500_000, False),
        str(loan.id): ("installment_out", loan.due_date.isoformat(), -200_000, False),
        str(late.id): ("cheque_out", TODAY.isoformat(), -80_000, True),
    }
    assert f["totals_by_kind"] == {"cheque_in": 500_000, "installment_out": -200_000, "cheque_out": -80_000}


def test_a_cheque_given_for_an_invoice_is_counted_once(db, co):
    aria = co["ents"]["Aria Trading"].id
    inv = _invoice(db, co, "Aria Trading", amount=500_000, due=TODAY + timedelta(days=5))
    cheque = _add(db, co, Commitment(kind="cheque", direction="receive", title="Aria cheque", amount=500_000,
                                     due_date=TODAY + timedelta(days=20), bank_name="Mellat", entity_id=aria))
    other = _invoice(db, co, "Aria Trading", amount=120_000, due=TODAY + timedelta(days=5))   # no cheque for it
    f = _forecast(db, co)
    got = {i["source_id"]: (i["kind"], i["date"], i["amount"], i.get("covers_invoice")) for i in _items(f)}
    assert got == {str(cheque.id): ("cheque_in", cheque.due_date.isoformat(), 500_000, inv.number),
                   str(other.id): ("invoice_in", other.due_date.isoformat(), 120_000, None)}
    assert f["closing_cash"] == 620_000
    with use_company(co["company"].id):
        bounced = forecast(db, today=TODAY, scenario=Scenario(bounce_commitments={str(cheque.id)}))
        unpaid = forecast(db, today=TODAY, scenario=Scenario(skip_invoices={str(inv.id)}))
    assert bounced["closing_cash"] == unpaid["closing_cash"] == 120_000      # the invoice doesn't come back


def test_a_cheque_beyond_the_horizon_still_pays_its_invoice(db, co):
    _invoice(db, co, "Aria Trading", amount=500_000, due=TODAY + timedelta(days=5))
    _add(db, co, Commitment(kind="cheque", direction="receive", title="Post-dated", amount=500_000,
                            due_date=HORIZON_END + timedelta(days=30), entity_id=co["ents"]["Aria Trading"].id))
    assert _items(_forecast(db, co)) == []


def test_a_supplier_installment_pays_its_bill(db, co):
    bill = _invoice(db, co, "Delta Supplies", amount=90_000, due=TODAY + timedelta(days=3), kind="purchase")
    _add(db, co, Commitment(kind="installment", direction="pay", title="Delta 1/1", amount=90_000,
                            due_date=TODAY + timedelta(days=30), entity_id=co["ents"]["Delta Supplies"].id))
    items = _items(_forecast(db, co))
    assert [(i["kind"], i["amount"], i["covers_invoice"]) for i in items] == [("installment_out", -90_000, bill.number)]


def test_long_overdue_receivables_are_left_out_as_doubtful(db, co):
    old = _invoice(db, co, "Caspian", amount=700_000, due=TODAY - timedelta(days=120))
    recent = _invoice(db, co, "Behsaz", amount=60_000, due=TODAY - timedelta(days=60))
    _invoice(db, co, "Delta Supplies", amount=50_000, due=TODAY - timedelta(days=200), kind="purchase")  # still owed
    f = _forecast(db, co)
    assert {i["source_id"] for i in _items(f)} == {str(recent.id)} | {i["source_id"] for i in _items(f, "bill_out")}
    assert [i["amount"] for i in _items(f, "bill_out")] == [-50_000]
    d = f["doubtful"]
    assert (d["count"], d["total"], d["after_days"]) == (1, 700_000, 90)
    assert d["invoices"][0] == {"source_id": str(old.id), "name": old.number, "amount": 700_000,
                                "due_date": old.due_date.isoformat(), "days_overdue": 120,
                                "entity_id": str(co["ents"]["Caspian"].id), "entity_name": "Caspian"}


def test_a_late_payer_is_not_doubtful_too_soon(db, co):
    for m in (3, 4, 5):                                    # Aria pays 80 days late
        due = date(2026, m, 1)
        _paid_invoice(db, co, "Aria Trading", due=due, paid_on=due + timedelta(days=80))
    inv = _invoice(db, co, "Aria Trading", amount=10_000, due=TODAY - timedelta(days=150))   # 70 days past expected
    assert [i["source_id"] for i in _items(_forecast(db, co))] == [str(inv.id)]


def test_commitments_are_in_the_base_currency_only(db, co):
    _cheques(db, co)
    _invoice(db, co, "Caspian", amount=1_000, due=TODAY + timedelta(days=3), currency="USD")
    _journal(db, co, date(2025, 1, 5), 2_000, currency="USD")        # the USD books: offered as another view
    usd = _forecast(db, co, currency="usd")
    assert usd["currency"] == "USD" and [i["kind"] for i in _items(usd)] == ["invoice_in"]
    assert usd["opening_cash"] == 2_000
    irr = _forecast(db, co)
    assert "invoice_in" not in {i["kind"] for i in _items(irr)} and irr["other_currencies"] == ["USD"]


def test_payroll_unpaid_runs_and_the_months_ahead(db, co):
    _add(db, co,
         PayRun(period_start=date(2026, 7, 1), period_end=date(2026, 7, 31), pay_date=date(2026, 7, 25),
                status="paid", total_net=290_000),
         PayRun(period_start=date(2026, 8, 1), period_end=date(2026, 8, 31), pay_date=date(2026, 8, 25),
                status="paid", total_net=300_000),
         PayRun(period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), pay_date=date(2026, 9, 25),
                status="posted", total_net=320_000),
         PayRun(period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), pay_date=date(2026, 9, 25),
                currency="USD", status="draft", total_net=7))
    f = _forecast(db, co)
    assert [(i["kind"], i["date"], i["amount"]) for i in _items(f) if i["kind"].startswith("payroll")] == [
        ("payroll", "2026-09-25", -320_000),
        ("payroll_projected", "2026-10-25", -300_000),
        ("payroll_projected", "2026-11-25", -300_000),
    ]


def test_no_projected_payroll_once_payroll_stopped(db, co):
    _add(db, co, PayRun(period_start=date(2026, 5, 1), period_end=date(2026, 5, 31), pay_date=date(2026, 5, 25),
                        status="paid", total_net=300_000))
    assert not _items(_forecast(db, co))


def test_recurring_rules(db, co):
    _add(db, co,
         RecurringRule(name="Rent", direction="payment", frequency="monthly", amount=50_000,
                       start_date=date(2026, 1, 1), next_run_date=date(2026, 10, 1)),
         RecurringRule(name="Hosting", direction="payment", frequency="monthly", amount=9_000,
                       start_date=date(2026, 1, 15), next_run_date=date(2026, 10, 15), end_date=date(2026, 11, 1)),
         RecurringRule(name="Sublet", direction="receipt", frequency="quarterly", amount=120_000,
                       start_date=date(2026, 1, 1), next_run_date=date(2026, 7, 1)),     # behind: rolls forward
         RecurringRule(name="Paused", direction="payment", frequency="weekly", amount=1, status="paused",
                       start_date=date(2026, 1, 1), next_run_date=TODAY),
         RecurringRule(name="Reminder", direction="payment", frequency="weekly", amount=None,
                       start_date=date(2026, 1, 1), next_run_date=TODAY))
    got = [(i["name"], i["date"], i["amount"])
           for i in sorted(_items(_forecast(db, co)), key=lambda i: (i["date"], i["name"]))]
    assert got == [("Rent", "2026-10-01", -50_000), ("Sublet", "2026-10-01", 120_000),
                   ("Hosting", "2026-10-15", -9_000), ("Rent", "2026-11-01", -50_000),
                   ("Rent", "2026-12-01", -50_000)]


def test_recurring_invoices_are_expected_after_terms_and_lateness(db, co):
    _history(db, co)                                                   # Aria pays 20 days late
    ent = co["ents"]["Aria Trading"].id
    _add(db, co,
         RecurringInvoice(name="Retainer", entity_id=ent, currency="IRR", amount=100_000, frequency="monthly",
                          calendar="gregorian", start_date=date(2026, 9, 1), next_run_date=date(2026, 10, 1),
                          occurrences=1, max_occurrences=3, terms_days=10),
         RecurringInvoice(name="Paused", entity_id=ent, currency="IRR", amount=5, frequency="weekly",
                          start_date=date(2026, 9, 1), next_run_date=TODAY, status="paused"))
    got = [(i["date"], i["amount"], i["days_late"]) for i in _items(_forecast(db, co), "recurring_invoice_in")]
    assert got == [("2026-10-31", 100_000, 20), ("2026-12-01", 100_000, 20)]          # the 3rd would exceed max


# --- the unscheduled baseline -------------------------------------------------------------------------

def test_baseline_leaves_out_what_is_already_scheduled(db, co):
    for k in range(1, 9):                                       # the 8 weeks before this one
        monday = MONDAY - timedelta(weeks=k)
        _journal(db, co, monday, 100_000)                      # unexplained sales takings
        _journal(db, co, monday + timedelta(days=1), 40_000, cash_in=False)
        paid = _journal(db, co, monday, 1_000_000)             # an invoice payment
        inv = _invoice(db, co, "Caspian", amount=1_000_000, due=monday, status="paid")
        _add(db, co, Payment(invoice_id=inv.id, date=monday, amount=1_000_000, currency="IRR", direction="in",
                             transaction_id=paid.id))
        _journal(db, co, monday, 70_000, cash_in=False, reference=f"REC-RENT-{monday.isoformat()}")
        pay = _journal(db, co, monday, 500_000, cash_in=False)
        _add(db, co, PayRun(period_start=monday, period_end=monday, pay_date=monday, status="paid",
                            total_net=500_000, pay_transaction_id=pay.id))
        chq = _journal(db, co, monday, 30_000, cash_in=False)
        _add(db, co, Commitment(kind="cheque", direction="pay", title="c", amount=30_000, due_date=monday,
                                status="settled", settled_transaction_id=chq.id))
        _journal(db, co, monday, 9_999_999, deleted=True)
        _journal(db, co, monday, 8_888, currency="USD")
    with use_company(co["company"].id):
        assert baseline(db, TODAY, "IRR", "ir") == {"inflow": 100_000, "outflow": 40_000, "weeks_of_history": 8}
    f = _forecast(db, co)
    w0, w1 = f["weeks"][0], f["weeks"][1]
    assert (w0["baseline_in"], w0["baseline_out"]) == (int(100_000 * 5 / 7), int(40_000 * 5 / 7))  # Wed → 5 days left
    assert (w1["inflow"], w1["outflow"], w1["net"]) == (100_000, 40_000, 60_000)
    assert w1["closing"] - w0["closing"] == 60_000


def test_the_median_ignores_one_big_week(db, co):
    for k in range(1, 7):
        _journal(db, co, MONDAY - timedelta(weeks=k), 10_000)
    _journal(db, co, MONDAY - timedelta(weeks=2, days=-2), 5_000_000)      # a one-off asset sale
    with use_company(co["company"].id):
        assert baseline(db, TODAY, "IRR", "ir")["inflow"] == 10_000


def test_a_young_company_has_no_baseline(db, co):
    _journal(db, co, MONDAY - timedelta(weeks=1), 10_000)
    _journal(db, co, MONDAY - timedelta(weeks=2), 10_000)
    with use_company(co["company"].id):
        assert baseline(db, TODAY, "IRR", "ir") == {"inflow": 0, "outflow": 0, "weeks_of_history": 2}


def test_quiet_weeks_count_as_zero(db, co):
    for k in (1, 2, 3, 10):                                     # 10 weeks of history, 4 with takings
        _journal(db, co, MONDAY - timedelta(weeks=k), 10_000)
    with use_company(co["company"].id):
        assert baseline(db, TODAY, "IRR", "ir") == {"inflow": 0, "outflow": 0, "weeks_of_history": 10}


# --- lowest point ----------------------------------------------------------------------------------------

def test_lowest_point_and_the_first_negative_week(db, co):
    _journal(db, co, date(2025, 1, 5), 100_000)
    bill = TODAY + timedelta(days=15)
    _invoice(db, co, "Delta Supplies", amount=300_000, due=bill, kind="purchase")
    _invoice(db, co, "Caspian", amount=250_000, due=TODAY + timedelta(days=40))
    f = _forecast(db, co)
    k = _week_of(f, bill)
    assert f["first_negative_week"] == f["weeks"][k]["week_start"]
    assert f["lowest"] == {"week_start": f["weeks"][k]["week_start"], "closing": -200_000}
    assert [w["risk"] for w in f["weeks"][:k + 1]] == [False] * k + [True]
    assert f["closing_cash"] == 50_000 and not f["weeks"][-1]["risk"]


# --- scenarios ---------------------------------------------------------------------------------------------

def test_what_if_the_mellat_cheque_bounces(db, co):
    incoming, _loan, _late = _cheques(db, co)
    with use_company(co["company"].id):
        sc, notes = resolve_scenario(db, bounce_matching="mellat")
        assert sc.bounce_commitments == {str(incoming.id)}
        assert notes == [f"Assumed to bounce: Aria cheque 1182 (500,000, due {incoming.due_date.isoformat()})."]
        out = compare(db, sc, today=TODAY)
    # base low: −80k (the rent cheque this week); without the 500k the loan takes it to −280k
    assert out["closing_difference"] == -500_000 and out["lowest_difference"] == -200_000
    assert (out["base"]["lowest"]["closing"], out["scenario"]["lowest"]["closing"]) == (-80_000, -280_000)
    k = _week_of(out["base"], incoming.due_date)
    assert [d["closing_difference"] for d in out["difference"]] == [0] * k + [-500_000] * (13 - k)
    assert out["scenario"]["scenario"]["bounce_commitments"] == [str(incoming.id)]
    assert out["base"]["scenario"] is None


def test_bounce_matching_in_persian_and_by_party(db, co):
    incoming, loan, _late = _cheques(db, co)
    with use_company(co["company"].id):
        db.get(Commitment, incoming.id).bank_name = "ملت"
        db.commit()
        assert resolve_scenario(db, bounce_matching="ملت")[0].bounce_commitments == {str(incoming.id)}
        assert resolve_scenario(db, bounce_matching="leasing")[0].bounce_commitments == {str(loan.id)}
        sc, notes = resolve_scenario(db, bounce_matching="Tejarat")
    assert sc.empty and notes == ["No pending cheque or installment matches 'Tejarat'."]


def test_skip_delay_and_one_off(db, co):
    due = TODAY + timedelta(days=7)
    a = _invoice(db, co, "Aria Trading", amount=400_000, due=due)
    b = _invoice(db, co, "Behsaz", amount=300_000, due=due)
    with use_company(co["company"].id):
        sc, notes = resolve_scenario(
            db, skip_invoice_ids=[str(a.id)], delays=[{"entity_name": "behsaz", "days": 30}],
            one_offs=[{"date": (TODAY + timedelta(days=20)).isoformat(), "amount": -1_000_000, "label": "Machine"}])
        assert notes == []
        alt = forecast(db, today=TODAY, scenario=sc)
    items = {i["kind"]: i for i in _items(alt)}
    assert set(items) == {"invoice_in", "one_off_out"}
    assert items["invoice_in"]["source_id"] == str(b.id) and items["invoice_in"]["date"] == (due + timedelta(days=30)).isoformat()
    assert (items["one_off_out"]["amount"], items["one_off_out"]["name"]) == (-1_000_000, "Machine")
    assert alt["closing_cash"] == 300_000 - 1_000_000


def test_a_scenario_says_what_it_could_not_apply(db, co):
    paid = _invoice(db, co, "Caspian", amount=1, due=TODAY, status="paid")
    _add(db, co, Entity(name="Behsaz Two", type="client", company_id=co["company"].id))
    with use_company(co["company"].id):
        sc, notes = resolve_scenario(
            db, bounce_ids=["nope", str(uuid.uuid4())], skip_invoice_ids=[str(paid.id)],
            delays=[{"entity_name": "behs", "days": 10}, {"days": 5}],
            one_offs=[{"date": "tomorrow", "amount": 5}, {"date": TODAY.isoformat(), "amount": 0}])
    assert sc.empty and len(notes) == 6
    assert notes[0] == "nope is not a pending cheque or installment — ignored."
    assert "not a pending cheque" in notes[1] and notes[2] == f"{paid.id} is not an open invoice — ignored."
    assert notes[3] == "'behs' matches 2 parties — say which one; ignored."
    assert notes[4] == "A delay without a customer or supplier — ignored." and "no valid date" in notes[5]


def test_delays_are_clamped_and_also_move_bills_and_cheques(db, co):
    incoming, _loan, _late = _cheques(db, co)
    with use_company(co["company"].id):
        sc, _ = resolve_scenario(db, delays=[{"entity_id": str(co["ents"]["Aria Trading"].id), "days": 9999}])
        assert sc.delay_entities == {str(co["ents"]["Aria Trading"].id): 365}
        sc, _ = resolve_scenario(db, delays=[{"entity_id": str(co["ents"]["Aria Trading"].id), "days": 14}])
        alt = forecast(db, today=TODAY, scenario=sc)
    moved = next(i for i in _items(alt) if i["source_id"] == str(incoming.id))
    assert moved["date"] == (incoming.due_date + timedelta(days=14)).isoformat()


def test_scenario_object_round_trip():
    sc = Scenario(bounce_commitments={"b", "a"}, delay_entities={"x": 3})
    assert not sc.empty and sc.as_dict()["bounce_commitments"] == ["a", "b"]
    assert Scenario().empty


# --- tenancy ---------------------------------------------------------------------------------------------------

def test_another_companys_books_never_count(db, co):
    other = Company(id=uuid.uuid4(), name="Other", slug=f"oth-{uuid.uuid4().hex[:8]}", locale="ir",
                    base_currency="IRR", status="active", token_version=0)
    db.add(other)
    db.commit()
    try:
        with use_company(other.id):
            e = Entity(name="Theirs", type="client", company_id=other.id)
            db.add(e)
            db.commit()
            db.add_all([Invoice(number="X-1", kind="sales", status="issued", issue_date=TODAY, due_date=TODAY,
                                amount=7_777, currency="IRR", entity_id=e.id),
                        Commitment(kind="cheque", direction="receive", title="Theirs", amount=6_666,
                                   due_date=TODAY, bank_name="Mellat")])
            db.commit()
        f = _forecast(db, co)
        assert not _items(f)
        with use_company(co["company"].id):
            assert resolve_scenario(db, bounce_matching="Mellat")[0].empty
    finally:
        from tests.test_admin_audit import _purge_company
        _purge_company(db, str(other.id))


# --- HTTP, dashboard, AI tool -----------------------------------------------------------------------------

def test_routes_and_permissions(db, co):
    today = date.today()
    _add(db, co, Commitment(kind="cheque", direction="receive", title="Cheque", amount=500_000,
                            due_date=today + timedelta(days=10), bank_name="Mellat"))
    owner = co["login"]("owner")
    r = owner.get("/reports/cash-forecast")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["weeks"]) == 13 and body["closing_cash"] == 500_000
    cid = next(i["source_id"] for w in body["weeks"] for i in w["items"])
    assert len(owner.get("/reports/cash-forecast?weeks=4").json()["weeks"]) == 4
    assert owner.get("/reports/cash-forecast?weeks=40").status_code == 422

    r = owner.post("/reports/cash-forecast/scenario", json={"bounce_commitments": [cid]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["closing_difference"] == -500_000 and out["scenario_notes"] == []
    r = owner.post("/reports/cash-forecast/scenario", json={
        "one_offs": [{"on": (today + timedelta(days=3)).isoformat(), "amount": 1_000, "label": "Loan"}],
        "delays": [{"entity_id": str(co["ents"]["Behsaz"].id), "days": 30}]})
    assert r.json()["closing_difference"] == 1_000
    assert owner.post("/reports/cash-forecast/scenario", json={"delays": [{"entity_id": "x", "days": 1}]}).status_code == 422
    assert owner.post("/reports/cash-forecast/scenario", json={"weeks": 0}).status_code == 422

    assert co["login"]("viewer").get("/reports/cash-forecast").status_code == 200
    assert co["login"]("personal").get("/reports/cash-forecast").status_code == 200
    assert co["login"]("employee").get("/reports/cash-forecast").status_code == 403
    assert co["login"]("employee").post("/reports/cash-forecast/scenario", json={}).status_code == 403


def test_the_forecast_reads_a_fixed_number_of_queries(db, co):
    from tests.test_report_performance import count_queries
    owner = co["login"]("owner")

    def grow(n):
        for i in range(n):
            _paid_invoice(db, co, "Aria Trading", due=TODAY - timedelta(days=40 + i), paid_on=TODAY - timedelta(days=30))
            _invoice(db, co, ["Behsaz", "Caspian"][i % 2], amount=1_000 + i, due=date.today() + timedelta(days=i))
            _invoice(db, co, "Delta Supplies", amount=500 + i, due=date.today() + timedelta(days=i), kind="purchase")
            _add(db, co, Commitment(kind="cheque", direction="receive", title=f"c{i}", amount=10 + i,
                                    due_date=date.today() + timedelta(days=i), entity_id=co["ents"]["Behsaz"].id))
            _journal(db, co, date.today() - timedelta(days=7 * (i % 20) + 1), 100 + i)

    def queries():
        with count_queries() as q:
            assert owner.get("/reports/cash-forecast").status_code == 200
        return q["n"]

    grow(4)
    small = queries()
    grow(30)
    large = queries()
    assert large <= small <= 20, (small, large)


def test_the_dashboard_shows_the_same_forecast(db, co):
    from app.api.reports import invalidate_dashboard_cache
    today = date.today()
    _journal(db, co, today - timedelta(days=400), 1_000_000)
    _add(db, co, Commitment(kind="installment", direction="pay", title="Loan", amount=1_200_000,
                            due_date=today + timedelta(days=8)))
    invalidate_dashboard_cache()
    owner = co["login"]("owner")
    dash = owner.get("/reports/owner-dashboard").json()["forecast_13_weeks"]
    fc = owner.get("/reports/cash-forecast").json()["weeks"]
    assert [(d["week_start"], d["projected_cash"], d["risk"]) for d in dash] == \
        [(w["week_start"], w["closing"], w["risk"]) for w in fc]
    assert dash[0]["week_start"] == (today - timedelta(days=today.weekday())).isoformat()
    assert dash[-1]["projected_cash"] == -200_000 and dash[-1]["risk"]


def test_the_ai_tool_answers_what_if(db, co):
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.cash_tools import GetCashForecast, GetCashForecastInput
    today = date.today()
    _journal(db, co, today - timedelta(days=400), 100_000)
    _add(db, co, Commitment(kind="cheque", direction="receive", title="Aria cheque", amount=500_000,
                            due_date=today + timedelta(days=10), bank_name="Mellat"),
         Commitment(kind="cheque", direction="pay", title="Supplier cheque", amount=450_000,
                    due_date=today + timedelta(days=12), bank_name="Saderat"))
    tool = GetCashForecast()
    with use_company(co["company"].id):
        ctx = ToolContext(db=db, user_id="u", username="owner")
        base = asyncio.run(tool.run(ctx, GetCashForecastInput()))
        what_if = asyncio.run(tool.run(ctx, GetCashForecastInput(bounce_matching="Mellat")))
        nothing = asyncio.run(tool.run(ctx, GetCashForecastInput(bounce_matching="Pasargad")))
    assert base["closing_cash"] == 150_000 and base["first_negative_week"] is None
    assert len(base["weeks"]) == 13 and "main_items" in base["weeks"][0]
    assert what_if["closing_difference"] == -500_000 and what_if["scenario"]["closing_cash"] == -350_000
    assert what_if["scenario"]["first_negative_week"] is not None
    assert what_if["scenario_notes"][0].startswith("Assumed to bounce: Aria cheque")
    assert nothing["scenario_notes"] == ["No pending cheque or installment matches 'Pasargad'."]
    assert "closing_difference" not in nothing                        # nothing matched → the base forecast


def test_the_tool_is_offered_to_the_ai():
    from app.services.ai_accountant.orchestrator import build_default_registry, build_personal_registry
    assert "get_cash_forecast" in {t.name for t in build_default_registry()}
    assert "get_cash_forecast" in {t.name for t in build_personal_registry()}
    from app.services.ai_accountant.orchestrator import SYSTEM_PROMPT
    assert "get_cash_forecast" in SYSTEM_PROMPT


def test_the_dashboard_page_carries_the_explorer():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "js" / "05-reports-manager.js").read_text(encoding="utf-8")
    assert 'id="forecast-summary"' in html and 'id="forecast-explorer"' in html
    assert 'data-i18n="forecastExplorerTitle"' in html
    block = js.split("// --- 13-week cash forecast", 1)[1].split("async function loadPersonalDashboard", 1)[0]
    assert "/reports/cash-forecast/scenario" in block and "renderForecastSummary" in js
    assert "onclick" not in block and "onsubmit" not in block           # CSP: listeners only
    assert "escapeHtml(n)" in block                                     # party names are escaped

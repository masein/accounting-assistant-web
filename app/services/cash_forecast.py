"""13-week cash forecast that learns (roadmap 2026-09 §5.3).

Opening cash is the true cash-on-hand today. Each week then adds:

* **scheduled flows** the books already know about — open sales invoices
  (expected when *that customer* usually pays: due date + their median
  lateness on paid invoices, else the company's), open bills (their due or
  scheduled payment date), pending cheques and installments, unpaid pay
  runs and, once payroll is running, next months' payroll on the usual day,
  active recurring rules and recurring sales invoices (issue + terms +
  lateness);
* **the unscheduled rest** — the median weekly cash in and out of the last
  26 weeks, leaving out the journals the scheduled sources already explain
  (invoice payments, pay runs, cheque/installment settlements, recurring
  postings), so nothing is counted twice.

Overdue items land in the current week; receivables long past their expected
date are left out as doubtful, and a cheque that matches an open invoice
(same party and amount) is counted once, as that invoice's payment. A
**scenario** changes the inputs — a cheque that bounces, an invoice that
isn't paid, a customer who pays N days later, a one-off receipt or payment —
and ``compare`` returns both runs and the difference week by week. Only the
view currency is forecast.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

WEEKS = 13
HISTORY_WEEKS = 26
LATENESS_WINDOW_DAYS = 365
MIN_ENTITY_SAMPLES = 2
MIN_COMPANY_SAMPLES = 3
OPEN_INVOICE_STATUSES = ("issued", "partially_paid")
DOUBTFUL_AFTER_DAYS = 90      # a receivable this far past its expected date is not counted as cash


@dataclass
class Scenario:
    bounce_commitments: set[str] = field(default_factory=set)   # commitment ids that won't settle
    skip_invoices: set[str] = field(default_factory=set)        # invoice ids that won't be paid
    delay_entities: dict[str, int] = field(default_factory=dict)  # entity id -> extra days
    one_offs: list[dict[str, Any]] = field(default_factory=list)  # {date, amount (+in / -out), label}

    @property
    def empty(self) -> bool:
        return not (self.bounce_commitments or self.skip_invoices or self.delay_entities or self.one_offs)

    def as_dict(self) -> dict[str, Any]:
        return {"bounce_commitments": sorted(self.bounce_commitments), "skip_invoices": sorted(self.skip_invoices),
                "delay_entities": dict(self.delay_entities), "one_offs": list(self.one_offs)}


def _norm(text: str | None) -> str:
    from app.services.bank_sms import normalize
    return normalize(text or "").casefold().strip()


def _uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def resolve_scenario(db: Session, *, bounce_ids=(), bounce_matching: str | None = None, skip_invoice_ids=(),
                     delays=(), one_offs=()) -> tuple[Scenario, list[str]]:
    """Turn a what-if request into a checked :class:`Scenario`, with a note for
    everything that could not be applied (so the caller can say so instead of
    silently forecasting the base case)."""
    from app.models.commitment import DEPOSITED, PENDING, Commitment
    from app.models.entity import Entity
    from app.models.invoice import Invoice

    sc = Scenario()
    notes: list[str] = []
    pending = db.execute(select(Commitment).where(Commitment.status.in_((PENDING, DEPOSITED)))).scalars().all()
    by_id = {c.id: c for c in pending}
    for raw in bounce_ids or ():
        cid = _uuid(raw)
        if cid in by_id:
            sc.bounce_commitments.add(str(cid))
        else:
            notes.append(f"{raw} is not a pending cheque or installment — ignored.")
    if (bounce_matching or "").strip():
        needle = _norm(bounce_matching)
        entity_names = _entity_names(db, {c.entity_id for c in pending})
        hits = [c for c in pending if any(needle in _norm(v) for v in (
            c.title, c.bank_name, c.counterparty, c.reference, entity_names.get(c.entity_id)))]
        if not hits:
            notes.append(f"No pending cheque or installment matches '{bounce_matching}'.")
        for c in hits:
            sc.bounce_commitments.add(str(c.id))
            notes.append(f"Assumed to bounce: {c.title} ({c.amount:,}, due {c.due_date.isoformat()}).")
    wanted = [(raw, _uuid(raw)) for raw in skip_invoice_ids or ()]
    ids = [i for _r, i in wanted if i]
    open_ids = set(db.execute(select(Invoice.id).where(Invoice.id.in_(ids),
                                                       Invoice.status.in_(OPEN_INVOICE_STATUSES))).scalars()) if ids else set()
    for raw, iid in wanted:
        if iid in open_ids:
            sc.skip_invoices.add(str(iid))
        else:
            notes.append(f"{raw} is not an open invoice — ignored.")
    for d in delays or ():
        days = int(d.get("days") or 0)
        eid = _uuid(d.get("entity_id"))
        if eid is None and (d.get("entity_name") or "").strip():
            needle = _norm(d["entity_name"])
            ents = db.execute(select(Entity.id, Entity.name)).all()
            exact = [i for i, n in ents if _norm(n) == needle]
            loose = exact or [i for i, n in ents if needle in _norm(n)]
            if len(loose) == 1:
                eid = loose[0]
            else:
                notes.append(f"'{d['entity_name']}' matches {len(loose)} parties — say which one; ignored.")
                continue
        if eid is None:
            notes.append("A delay without a customer or supplier — ignored.")
            continue
        if days:
            sc.delay_entities[str(eid)] = max(-90, min(365, days))
    for o in one_offs or ():
        try:
            day = date.fromisoformat(str(o.get("date")))
            amount = int(o.get("amount") or 0)
        except (TypeError, ValueError):
            notes.append(f"One-off {o!r} has no valid date or amount — ignored.")
            continue
        if amount:
            sc.one_offs.append({"date": day.isoformat(), "amount": amount, "label": (o.get("label") or "One-off")[:120]})
    return sc, notes


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


# --- learning: when does each customer really pay? -------------------------------------------------

def customer_lateness(db: Session, today: date, currency: str) -> tuple[dict[Any, int], int, dict[Any, int]]:
    """({entity_id: median days late}, company median, {entity_id: samples}) from
    sales invoices fully paid in the last year: last payment date − due date."""
    from app.models.invoice import Invoice
    from app.models.payment import Payment
    rows = db.execute(
        select(Invoice.id, Invoice.entity_id, Invoice.due_date, func.max(Payment.date))
        .join(Payment, Payment.invoice_id == Invoice.id)
        .where(Invoice.kind == "sales", Invoice.status == "paid", Invoice.currency == currency,
               Invoice.due_date >= today - timedelta(days=LATENESS_WINDOW_DAYS))
        .group_by(Invoice.id, Invoice.entity_id, Invoice.due_date)
    ).all()
    per: dict = defaultdict(list)
    everyone: list[int] = []
    for _iid, eid, due, last in rows:
        if due is None or last is None:
            continue
        days = (last - due).days
        everyone.append(days)
        if eid is not None:
            per[eid].append(days)
    company = int(median(everyone)) if len(everyone) >= MIN_COMPANY_SAMPLES else 0
    return ({eid: int(median(v)) for eid, v in per.items() if len(v) >= MIN_ENTITY_SAMPLES}, company,
            {eid: len(v) for eid, v in per.items()})


# --- scheduled flows -------------------------------------------------------------------------------------

def _open_invoices(db: Session, currency: str):
    from app.models.credit_note import CreditNote
    from app.models.invoice import Invoice
    from app.models.payment import Payment
    invs = db.execute(select(Invoice).where(Invoice.status.in_(OPEN_INVOICE_STATUSES),
                                            Invoice.kind.in_(("sales", "purchase")),
                                            Invoice.currency == currency)).scalars().all()
    if not invs:
        return []
    ids = [i.id for i in invs]
    paid = dict(db.execute(select(Payment.invoice_id, func.coalesce(func.sum(Payment.amount), 0))
                           .where(Payment.invoice_id.in_(ids)).group_by(Payment.invoice_id)).all())
    credited = dict(db.execute(select(CreditNote.invoice_id, func.coalesce(func.sum(CreditNote.amount), 0))
                               .where(CreditNote.invoice_id.in_(ids), CreditNote.note_type == "reduction")
                               .group_by(CreditNote.invoice_id)).all())
    out = []
    for inv in invs:
        due = int(inv.amount or 0) - int(paid.get(inv.id, 0)) - int(credited.get(inv.id, 0))
        if due > 0:
            out.append((inv, due))
    return out


def _entity_names(db: Session, ids: set) -> dict:
    from app.models.entity import Entity
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict(db.execute(select(Entity.id, Entity.name).where(Entity.id.in_(ids))).all())


def scheduled_items(db: Session, today: date, horizon_end: date, currency: str, base_currency: str,
                    scenario: Scenario, lateness: dict, company_lateness: int,
                    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(the dated flows, the doubtful receivables left out of them)."""
    from app.models.commitment import DEPOSITED, PAY, PENDING, RECEIVE, Commitment
    from app.models.pay_run import PayRun
    from app.models.recurring import RecurringRule
    from app.models.recurring_invoice import RecurringInvoice
    from app.services.recurring_invoice_service import occurrence_date, template_total
    from app.services.recurring_service import _next_date

    items: list[dict[str, Any]] = []
    doubtful: list[dict[str, Any]] = []

    def add(kind, when, amount, label, *, name=None, source_id=None, entity_id=None, overdue=False,
            days_late=None, note=None):
        """``label``/``note`` are English for the AI accountant; the page builds
        its own wording from ``kind``, ``name`` and ``days_late``."""
        if amount == 0:
            return None
        when = max(when, today)
        if when > horizon_end:
            return None
        item = {"kind": kind, "date": when.isoformat(), "amount": int(amount), "label": label,
                "name": name, "source_id": str(source_id) if source_id else None,
                "entity_id": str(entity_id) if entity_id else None, "overdue": overdue,
                "days_late": days_late, "note": note}
        items.append(item)
        return item

    def delay_for(entity_id) -> int:
        return int(scenario.delay_entities.get(str(entity_id), 0)) if entity_id else 0

    # Commitments carry no currency: they are in the base currency.
    # A deposited cheque is money on its way too (§3.4).
    commitments = (db.execute(select(Commitment).where(Commitment.status.in_((PENDING, DEPOSITED)))).scalars().all()
                   if currency == base_currency else [])
    # A cheque (or installment) given for an invoice IS that invoice's payment:
    # pair a pending one with an open invoice of the same party, direction and
    # amount and count the money once, on the cheque's date. Bouncing the
    # cheque then takes the invoice's money out of the horizon too.
    # A cheque recorded for the invoice says so; otherwise the same party and
    # amount is taken as a match.
    linked = {c.invoice_id: c for c in commitments if c.invoice_id}
    unpaired: dict = defaultdict(list)
    for c in sorted(commitments, key=lambda c: c.due_date):
        if c.entity_id and not c.invoice_id:
            unpaired[(c.entity_id, c.direction, int(c.amount or 0))].append(c)
    covers: dict = {}
    skip_commitments: set[str] = set()
    for inv, due_amount in _open_invoices(db, currency):
        c = linked.get(inv.id)
        if c is None:
            paired = unpaired.get((inv.entity_id, RECEIVE if inv.kind == "sales" else PAY, due_amount))
            c = paired.pop(0) if paired else None
        if c is not None:
            covers[c.id] = inv
            if str(inv.id) in scenario.skip_invoices:
                skip_commitments.add(str(c.id))
            due_amount -= int(c.amount or 0)
            if due_amount <= 0:
                continue                         # the cheque is the whole payment
        if str(inv.id) in scenario.skip_invoices:
            continue
        if inv.kind == "sales":
            late = lateness.get(inv.entity_id, company_lateness)
            expected = inv.due_date + timedelta(days=late + delay_for(inv.entity_id))
            if (today - expected).days > DOUBTFUL_AFTER_DAYS:
                doubtful.append({"source_id": str(inv.id), "name": inv.number, "amount": due_amount,
                                 "due_date": inv.due_date.isoformat(), "days_overdue": (today - inv.due_date).days,
                                 "entity_id": str(inv.entity_id) if inv.entity_id else None})
                continue
            add("invoice_in", expected, due_amount, f"Invoice {inv.number}", name=inv.number, source_id=inv.id,
                entity_id=inv.entity_id, overdue=inv.due_date < today, days_late=late or None,
                note=(f"usually pays {late} day(s) after due" if late else None))
        else:
            when = inv.scheduled_payment_date or inv.due_date
            add("bill_out", when + timedelta(days=delay_for(inv.entity_id)), -due_amount, f"Bill {inv.number}",
                name=inv.number, source_id=inv.id, entity_id=inv.entity_id, overdue=inv.due_date < today)

    for c in commitments:
        if str(c.id) in scenario.bounce_commitments or str(c.id) in skip_commitments:
            continue
        sign = 1 if c.direction == RECEIVE else -1
        kind = "cheque" if c.kind == "cheque" else "installment"
        inv = covers.get(c.id)
        item = add(f"{kind}_{'in' if sign > 0 else 'out'}", c.due_date + timedelta(days=delay_for(c.entity_id)),
                   sign * int(c.amount or 0), f"{kind.capitalize()}: {c.title}", name=c.title, source_id=c.id,
                   entity_id=c.entity_id, overdue=c.due_date < today,
                   note=(f"pays invoice {inv.number}" if inv else None))
        if item is not None and inv is not None:
            item["covers_invoice"] = inv.number

    runs = db.execute(select(PayRun).where(PayRun.currency == currency)).scalars().all()
    for r in runs:
        if r.status != "paid" and r.pay_date and r.pay_date <= horizon_end:
            add("payroll", r.pay_date, -int(r.total_net or 0), f"Payroll {r.period_start}–{r.period_end}",
                name=f"{r.period_start.isoformat()} – {r.period_end.isoformat()}", source_id=r.id,
                overdue=r.pay_date < today)
    paid_runs = sorted((r for r in runs if r.status == "paid" and r.pay_date), key=lambda r: r.pay_date)
    if paid_runs and (today - paid_runs[-1].pay_date).days <= 62:
        # Payroll is running: expect next months' on the usual day, after the last known run.
        last = paid_runs[-1]
        known = max([r.pay_date for r in runs if r.pay_date] + [last.pay_date])
        from app.services.recurring_service import _add_months
        nxt = _add_months(last.pay_date, 1)
        while nxt <= horizon_end:
            if nxt > known:
                add("payroll_projected", nxt, -int(last.total_net or 0), "Payroll (projected from the last run)")
            nxt = _add_months(nxt, 1)

    if currency == base_currency:
        for rule in db.execute(select(RecurringRule).where(RecurringRule.status == "active",
                                                           RecurringRule.amount.is_not(None),
                                                           RecurringRule.amount > 0)).scalars():
            run_on = rule.next_run_date
            guard = 0
            while run_on < today and guard < 400:
                run_on, guard = _next_date(run_on, rule.frequency), guard + 1
            while run_on <= horizon_end and guard < 800:
                if rule.end_date and run_on > rule.end_date:
                    break
                sign = 1 if (rule.direction or "").lower() == "receipt" else -1
                add("recurring_in" if sign > 0 else "recurring_out", run_on, sign * int(rule.amount), rule.name,
                    name=rule.name, source_id=rule.id, entity_id=rule.entity_id)
                run_on, guard = _next_date(run_on, rule.frequency), guard + 1

    for t in db.execute(select(RecurringInvoice).where(RecurringInvoice.status == "active",
                                                       RecurringInvoice.currency == currency)).scalars():
        try:
            amount = template_total(db, t, t.next_run_date)
        except Exception:  # noqa: BLE001 — a broken template must not break the forecast
            amount = int(t.amount or 0)
        n = int(t.occurrences or 0)
        issue = t.next_run_date
        late = lateness.get(t.entity_id, company_lateness)
        for _ in range(60):
            if issue > horizon_end or (t.end_date and issue > t.end_date) or \
                    (t.max_occurrences is not None and n >= int(t.max_occurrences)):
                break
            expected = issue + timedelta(days=int(t.terms_days or 0) + late + delay_for(t.entity_id))
            add("recurring_invoice_in", expected, amount, f"Recurring invoice: {t.name}", name=t.name,
                source_id=t.id, entity_id=t.entity_id, days_late=late or None)
            n += 1
            issue = occurrence_date(t.start_date, t.frequency, t.calendar, n)

    for o in scenario.one_offs:
        try:
            when = date.fromisoformat(str(o.get("date")))
            amount = int(o.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        label = str(o.get("label") or "One-off")
        add("one_off_in" if amount > 0 else "one_off_out", when, amount, label, name=o.get("label"))
    return items, doubtful


# --- the unscheduled rest -----------------------------------------------------------------------------------

def baseline(db: Session, today: date, currency: str, locale: str) -> dict[str, Any]:
    """Median weekly cash in / out over the last 26 full weeks, without the
    journals the scheduled sources explain."""
    from app.models.account import Account
    from app.models.commitment import Commitment
    from app.models.pay_run import PayRun
    from app.models.payment import Payment
    from app.models.transaction import Transaction, TransactionLine
    from app.services.cash_service import cash_account_predicate

    is_cash = cash_account_predicate(locale)
    end = _week_start(today)
    start = end - timedelta(weeks=HISTORY_WEEKS)
    # Separate queries, not a UNION: the tenant filter is injected per ORM select.
    explained = set(db.execute(select(Payment.transaction_id)).scalars())
    for a, b in db.execute(select(PayRun.post_transaction_id, PayRun.pay_transaction_id)):
        explained |= {a, b}
    explained |= set(db.execute(select(Commitment.settled_transaction_id)).scalars())
    explained.discard(None)
    net: dict = defaultdict(int)
    when: dict = {}
    first_seen = None
    for tid, d, ref, code, debit, credit in db.execute(
        select(Transaction.id, Transaction.date, Transaction.reference, Account.code, TransactionLine.debit,
               TransactionLine.credit)
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .where(Transaction.deleted_at.is_(None), Transaction.currency == currency,
               Transaction.date >= start, Transaction.date < end)
    ):
        first_seen = d if first_seen is None or d < first_seen else first_seen
        if tid in explained or (ref or "").upper().startswith("REC-") or not is_cash(code or ""):
            continue
        net[tid] += int(debit or 0) - int(credit or 0)
        when[tid] = d
    weekly_in = defaultdict(int)
    weekly_out = defaultdict(int)
    for tid, amount in net.items():
        wk = _week_start(when[tid])
        if amount > 0:
            weekly_in[wk] += amount
        elif amount < 0:
            weekly_out[wk] += -amount
    if first_seen is None:
        return {"inflow": 0, "outflow": 0, "weeks_of_history": 0}
    weeks = [start + timedelta(weeks=i) for i in range(HISTORY_WEEKS)]
    weeks = [w for w in weeks if w >= _week_start(first_seen)]   # a young company: only the weeks it existed
    if len(weeks) < 4:
        return {"inflow": 0, "outflow": 0, "weeks_of_history": len(weeks)}
    return {"inflow": int(median(weekly_in.get(w, 0) for w in weeks)),
            "outflow": int(median(weekly_out.get(w, 0) for w in weeks)), "weeks_of_history": len(weeks)}


# --- the forecast -----------------------------------------------------------------------------------------------

def forecast(db: Session, *, today: date | None = None, weeks: int = WEEKS, currency: str | None = None,
             scenario: Scenario | None = None, locale: str | None = None,
             opening: int | None = None) -> dict[str, Any]:
    """The forecast in one currency. ``locale`` and ``opening`` (cash on hand
    today) can be passed by a caller that already has them."""
    from app.services.cash_service import cash_on_hand
    from app.services.fx_service import get_reporting_currency
    from app.services.locale_service import get_reporting_locale
    from app.services.reporting.repository import resolve_currency_view

    today = today or date.today()
    scenario = scenario or Scenario()
    weeks = max(1, min(int(weeks), 26))
    currency, others = resolve_currency_view(db, currency)
    base_currency = (get_reporting_currency(db) or "IRR").strip().upper()
    locale = locale or get_reporting_locale(db)
    first_week = _week_start(today)
    horizon_end = first_week + timedelta(weeks=weeks) - timedelta(days=1)
    lateness, company_lateness, samples = customer_lateness(db, today, currency)
    items, doubtful = scheduled_items(db, today, horizon_end, currency, base_currency, scenario, lateness,
                                      company_lateness)
    base = baseline(db, today, currency, locale)
    if opening is None:
        opening = cash_on_hand(db, locale=locale, currency=currency, as_of=today)

    by_week: dict = defaultdict(list)
    for it in items:
        by_week[_week_start(date.fromisoformat(it["date"]))].append(it)
    rows = []
    cash = opening
    for i in range(weeks):
        wk = first_week + timedelta(weeks=i)
        scheduled = sorted(by_week.get(wk, []), key=lambda x: (x["date"], -abs(x["amount"])))
        s_in = sum(x["amount"] for x in scheduled if x["amount"] > 0)
        s_out = -sum(x["amount"] for x in scheduled if x["amount"] < 0)
        # the current week has only its remaining days of unscheduled flow
        share = (7 - today.weekday()) / 7 if i == 0 else 1
        b_in, b_out = int(base["inflow"] * share), int(base["outflow"] * share)
        inflow, outflow = s_in + b_in, s_out + b_out
        cash += inflow - outflow
        rows.append({"week_start": wk.isoformat(), "inflow": inflow, "outflow": outflow, "net": inflow - outflow,
                     "closing": cash, "scheduled_in": s_in, "scheduled_out": s_out, "baseline_in": b_in,
                     "baseline_out": b_out, "risk": cash < 0, "items": scheduled})
    lowest = min(rows, key=lambda r: r["closing"])
    first_negative = next((r["week_start"] for r in rows if r["closing"] < 0), None)
    names = _entity_names(db, set(lateness) | {_uuid(it["entity_id"]) for it in [*items, *doubtful]
                                               if it["entity_id"]})
    named = {str(k): v for k, v in names.items()}
    for it in [*items, *doubtful]:
        if it["entity_id"]:
            it["entity_name"] = named.get(it["entity_id"])
    doubtful.sort(key=lambda d: -d["amount"])
    by_kind: dict = defaultdict(int)
    for it in items:
        by_kind[it["kind"]] += it["amount"]
    return {
        "currency": currency, "other_currencies": others, "as_of": today.isoformat(), "opening_cash": opening,
        "weeks": rows,
        "closing_cash": cash, "lowest": {"week_start": lowest["week_start"], "closing": lowest["closing"]},
        "first_negative_week": first_negative, "totals_by_kind": dict(by_kind),
        "baseline": base,
        "doubtful": {"count": len(doubtful), "total": sum(d["amount"] for d in doubtful),
                     "after_days": DOUBTFUL_AFTER_DAYS, "invoices": doubtful[:20]},
        "learned": {
            "company_days_late": company_lateness,
            "customers": sorted(({"entity_id": str(k), "name": named.get(str(k)), "days_late": v,
                                  "paid_invoices": samples.get(k, 0)} for k, v in lateness.items()),
                                key=lambda r: -r["days_late"]),
        },
        "scenario": None if scenario.empty else scenario.as_dict(),
        "notes": [
            "Sales invoices are expected when each customer usually pays (due date + their median lateness).",
            "Unscheduled flows are the median week of the last 26, without what invoices, payroll, cheques and "
            "recurring postings already explain.",
            "Overdue items are placed in the current week; a sales invoice more than "
            f"{DOUBTFUL_AFTER_DAYS} days past when it was expected is left out as doubtful.",
            "A cheque or installment for the same party and amount as an open invoice is taken as that "
            "invoice's payment and counted once, on the cheque's date.",
        ],
    }


def compare(db: Session, scenario: Scenario, **kw) -> dict[str, Any]:
    base = forecast(db, **kw)
    alt = forecast(db, scenario=scenario, **kw)
    delta = [{"week_start": a["week_start"], "closing_difference": b["closing"] - a["closing"]}
             for a, b in zip(base["weeks"], alt["weeks"])]
    return {"base": base, "scenario": alt, "difference": delta,
            "closing_difference": alt["closing_cash"] - base["closing_cash"],
            "lowest_difference": alt["lowest"]["closing"] - base["lowest"]["closing"]}

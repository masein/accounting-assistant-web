"""The owner dashboard and the forecast baseline are summed in SQL (roadmap §2.6).

Both used to read every journal line into Python. The line-by-line folds they
replaced are kept here, verbatim but for the journal order (now by date), as
references: on random books full of edge cases — blank and whitespace
descriptions, journals without lines, several links and odd roles, undone
journals, other currencies, pending base values, a second company — the SQL
must give exactly the same figures, for both charts and both views.
"""
from __future__ import annotations

import random
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import median

import pytest
from sqlalchemy import select

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.seed import seed_chart_if_empty
from app.db.tenant import use_company
from app.models.account import Account
from app.models.commitment import Commitment
from app.models.company import Company
from app.models.entity import Entity, TransactionEntity
from app.models.invoice import Invoice
from app.models.pay_run import PayRun
from app.models.payment import Payment
from app.models.transaction import Transaction, TransactionAttachment, TransactionLine
from app.services.calendar_periods import GREGORIAN, JALALI, month_key
from app.services.cash_forecast import HISTORY_WEEKS, _week_start, baseline
from app.services.cash_service import cash_account_predicate
from app.services.reporting.common import EXPENSE, REVENUE, classify_account_code
from app.services.reporting.dashboard_folds import _apply_reduction, _bucket_by_age, fold_dashboard
from app.services.reporting.repository import amount_columns, is_base_view

TODAY = date.today()


# --- the references: the folds as they were, line by line in Python -----------------------------

def _reference_folds(db, *, live, dr_col, cr_col, is_receivable, is_current_liability, month_of, today, before=None):
    txns = db.execute(select(Transaction.id, Transaction.date, Transaction.reference).where(*live)
                      .order_by(Transaction.date, Transaction.created_at, Transaction.id)).all()
    lines_by_txn = defaultdict(list)
    for tid, debit, credit, code, name, desc in db.execute(
        select(TransactionLine.transaction_id, dr_col, cr_col, Account.code, Account.name,
               TransactionLine.line_description)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .where(*live)
    ):
        lines_by_txn[tid].append((int(debit or 0), int(credit or 0), code, name, desc))
    links_by_txn = defaultdict(list)
    for tid, role, entity_name in db.execute(
        select(TransactionEntity.transaction_id, TransactionEntity.role, Entity.name)
        .join(Transaction, TransactionEntity.transaction_id == Transaction.id)
        .outerjoin(Entity, TransactionEntity.entity_id == Entity.id)
        .where(*live)
    ):
        links_by_txn[tid].append((role, entity_name))
    with_attachment = set(db.execute(
        select(TransactionAttachment.transaction_id).distinct()
        .join(Transaction, TransactionAttachment.transaction_id == Transaction.id)
        .where(*live)
    ).scalars())
    r = {k: defaultdict(int) for k in ("monthly_revenue", "monthly_expense", "expense_by_category",
                                        "spend_by_vendor")}
    r["profitability"] = defaultdict(lambda: {"revenue": 0, "cost": 0})
    r["ar_buckets"] = defaultdict(lambda: {"current": 0, "days_31_60": 0, "days_60_plus": 0})
    r["ap_buckets"] = defaultdict(lambda: {"current": 0, "days_31_60": 0, "days_60_plus": 0})
    n = dict.fromkeys(("receivable_due_this_week", "payable_due_this_week", "tax_and_liability_payable",
                       "expense_txn_count", "expense_txn_with_attachment", "line_count", "missing_line_desc",
                       "missing_reference", "unlinked_entities"), 0)
    if before is not None:
        # what the journals before the window leave owed, per party, starts in the oldest bucket
        old_lines, old_links = defaultdict(list), defaultdict(list)
        for tid, debit, credit, code in db.execute(
            select(TransactionLine.transaction_id, dr_col, cr_col, Account.code)
            .join(Transaction, TransactionLine.transaction_id == Transaction.id)
            .join(Account, TransactionLine.account_id == Account.id)
            .where(*before)
        ):
            old_lines[tid].append((int(debit or 0), int(credit or 0), code))
        for tid, role, entity_name in db.execute(
            select(TransactionEntity.transaction_id, TransactionEntity.role, Entity.name)
            .join(Transaction, TransactionEntity.transaction_id == Transaction.id)
            .outerjoin(Entity, TransactionEntity.entity_id == Entity.id)
            .where(*before)
        ):
            old_links[tid].append((role, entity_name))
        old_ar, old_ap = defaultdict(int), defaultdict(int)
        for tid, t_lines in old_lines.items():
            recv = sum(d - c for d, c, code in t_lines if is_receivable(code))
            liab = sum(c - d for d, c, code in t_lines if is_current_liability(code))
            n["tax_and_liability_payable"] += liab
            roles = defaultdict(list)
            for role, entity_name in old_links.get(tid, ()):
                roles[(role or "").lower()].append(entity_name if entity_name is not None else "Unknown")
            for c in roles.get("client", []) or ["Unassigned client"]:
                old_ar[c] += recv
            for v in (roles.get("payee", []) + roles.get("supplier", [])) or ["Unassigned vendor"]:
                old_ap[v] += liab
        for c, owed in old_ar.items():
            if owed > 0:
                r["ar_buckets"][c]["days_60_plus"] += owed
        for v, owed in old_ap.items():
            if owed > 0:
                r["ap_buckets"][v]["days_60_plus"] += owed
    for t_id, t_date, t_reference in txns:
        t_lines = lines_by_txn.get(t_id, ())
        t_links = links_by_txn.get(t_id, ())
        n["line_count"] += len(t_lines)
        if not (t_reference or "").strip():
            n["missing_reference"] += 1
        if not t_links:
            n["unlinked_entities"] += 1
        month = month_of(t_date)
        txn_revenue = txn_expense = receivable_delta = payable_delta = 0
        expense_seen = False
        for debit, credit, code, name, desc in t_lines:
            if not (desc or "").strip():
                n["missing_line_desc"] += 1
            kind = classify_account_code(code)
            rev = (credit - debit) if kind == REVENUE else 0
            exp = (debit - credit) if kind == EXPENSE else 0
            txn_revenue += rev
            txn_expense += exp
            if is_receivable(code):
                receivable_delta += debit - credit
            if is_current_liability(code):
                payable_delta += credit - debit
                n["tax_and_liability_payable"] += credit - debit
            if exp > 0:
                expense_seen = True
                r["expense_by_category"][name] += exp
        r["monthly_revenue"][month] += max(0, txn_revenue)
        r["monthly_expense"][month] += max(0, txn_expense)
        roles = defaultdict(list)
        for role, entity_name in t_links:
            roles[(role or "").lower()].append(entity_name if entity_name is not None else "Unknown")
        client_names = roles.get("client", []) or ["Unassigned client"]
        vendor_names = roles.get("payee", []) + roles.get("supplier", [])
        if not vendor_names and txn_expense > 0:
            vendor_names = ["Unassigned vendor"]
        for v in vendor_names:
            r["spend_by_vendor"][v] += max(0, txn_expense)
        for c in client_names:
            r["profitability"][c]["revenue"] += max(0, txn_revenue)
            r["profitability"][c]["cost"] += max(0, txn_expense)
        age_days = max(0, (today - t_date).days)
        bucket = _bucket_by_age(age_days)
        if receivable_delta > 0:
            for c in client_names:
                r["ar_buckets"][c][bucket] += receivable_delta
            if 23 <= age_days <= 30:
                n["receivable_due_this_week"] += receivable_delta
        elif receivable_delta < 0:
            for c in client_names:
                _apply_reduction(r["ar_buckets"][c], -receivable_delta)
        if payable_delta > 0:
            for v in vendor_names or ["Unassigned vendor"]:
                r["ap_buckets"][v][bucket] += payable_delta
            if 23 <= age_days <= 30:
                n["payable_due_this_week"] += payable_delta
        elif payable_delta < 0:
            for v in vendor_names or ["Unassigned vendor"]:
                _apply_reduction(r["ap_buckets"][v], -payable_delta)
        if expense_seen:
            n["expense_txn_count"] += 1
            if t_id in with_attachment:
                n["expense_txn_with_attachment"] += 1
    out = {k: {kk: (dict(vv) if isinstance(vv, dict) else vv) for kk, vv in v.items()} for k, v in r.items()}
    out.update(n, txn_count=len(txns))
    return out


def _as_dict(folds):
    out = {}
    for k, v in vars(folds).items():
        out[k] = {kk: (dict(vv) if isinstance(vv, dict) else vv) for kk, vv in v.items()} \
            if isinstance(v, dict) else v
    return out


def _reference_baseline(db, today, currency, locale):
    is_cash = cash_account_predicate(locale)
    end = _week_start(today)
    start = end - timedelta(weeks=HISTORY_WEEKS)
    explained = set(db.execute(select(Payment.transaction_id)).scalars())
    for a, b in db.execute(select(PayRun.post_transaction_id, PayRun.pay_transaction_id)):
        explained |= {a, b}
    explained |= set(db.execute(select(Commitment.settled_transaction_id)).scalars())
    explained.discard(None)
    net, when, first_seen = defaultdict(int), {}, None
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
    weekly_in, weekly_out = defaultdict(int), defaultdict(int)
    for tid, amount in net.items():
        wk = _week_start(when[tid])
        if amount > 0:
            weekly_in[wk] += amount
        elif amount < 0:
            weekly_out[wk] += -amount
    if first_seen is None:
        return {"inflow": 0, "outflow": 0, "weeks_of_history": 0}
    weeks = [start + timedelta(weeks=i) for i in range(HISTORY_WEEKS)]
    weeks = [w for w in weeks if w >= _week_start(first_seen)]
    if len(weeks) < 4:
        return {"inflow": 0, "outflow": 0, "weeks_of_history": len(weeks)}
    return {"inflow": int(median(weekly_in.get(w, 0) for w in weeks)),
            "outflow": int(median(weekly_out.get(w, 0) for w in weeks)), "weeks_of_history": len(weeks)}


# --- random books ----------------------------------------------------------------------------------------

def _company(db, locale):
    c = Company(id=uuid.uuid4(), name=f"Folds {locale}", slug=f"folds-{uuid.uuid4().hex[:8]}", locale=locale,
                base_currency="IRR" if locale == "ir" else "GBP", status="active", token_version=0)
    db.add(c)
    db.commit()
    with use_company(c.id):
        seed_chart_if_empty(db, locale=locale)
        db.commit()
        acc = {a.code: a for a in db.execute(select(Account)).scalars()}
    return c, acc


def _pools(acc, locale):
    codes = sorted(acc)
    is_recv, is_liab = _predicates(locale)
    recv = [c for c in codes if is_recv(c)]
    liab = [c for c in codes if is_liab(c)]
    cash = [c for c in codes if cash_account_predicate(locale)(c)]
    rev = [c for c in codes if classify_account_code(c) == REVENUE]
    exp = [c for c in codes if classify_account_code(c) == EXPENSE]
    return [p for p in (recv, liab, cash, rev, exp, codes) if p]


def _random_books(db, company, acc, rng, *, n=160, base="IRR"):
    locale = company.locale
    pools = _pools(acc, locale)
    with use_company(company.id):
        ents = [Entity(id=uuid.uuid4(), name=nm, type=tp, company_id=company.id) for nm, tp in (
            ("Aria", "client"), ("Behsaz", "client"), ("Caspian", "client"), ("Delta", "supplier"),
            ("Elm", "supplier"), ("Fars Payroll", "person"), ("Aria", "supplier"))]      # a name twice
        db.add_all(ents)
        db.flush()
        journals = []
        for i in range(n):
            currency = rng.choice([base] * 4 + ["USD"])
            t = Transaction(id=uuid.uuid4(), date=TODAY - timedelta(days=rng.randint(-3, 420)),
                            reference=rng.choice([None, "", "  ", "\t\n", "INV-1", "rec-rent", "REC-X"]),
                            description="j", currency=currency)
            if rng.random() < 0.06:
                t.deleted_at = datetime.now(timezone.utc)
            db.add(t)
            db.flush()                           # as every writer does before adding lines
            journals.append(t)
            for _ in range(rng.choice([0, 1, 2, 2, 2, 3, 4])):
                code = rng.choice(rng.choice(pools))
                amount = rng.randint(1, 900) * 100
                debit, credit = (amount, 0) if rng.random() < 0.5 else (0, amount)
                db.add(TransactionLine(transaction_id=t.id, account_id=acc[code].id, debit=debit, credit=credit,
                                       line_description=rng.choice([None, "", " ", "\n", "rent", "sale"])))
            for _ in range(rng.choice([0, 0, 1, 1, 1, 2, 3])):
                db.add(TransactionEntity(transaction_id=t.id, entity_id=rng.choice(ents).id,
                                         role=rng.choice(["client", "Client", "supplier", "payee", "PAYEE",
                                                          "employee", "bank"])))
            if rng.random() < 0.25:
                db.add(TransactionAttachment(transaction_id=t.id, file_name="r.pdf",
                                             file_path=f"/tmp/folds-{uuid.uuid4().hex}.pdf",
                                             content_type="application/pdf", size_bytes=1))
        db.commit()
    return journals, ents


@pytest.fixture()
def two_companies(db):
    made = []

    def make(locale):
        c, acc = _company(db, locale)
        made.append(c)
        return c, acc
    yield make
    from tests.test_admin_audit import _purge_company
    for c in made:
        _purge_company(db, str(c.id))


def _live(currency, months_back=12):
    cutoff = TODAY - timedelta(days=months_back * 31)
    return (Transaction.date >= cutoff, Transaction.deleted_at.is_(None),
            *(() if is_base_view(currency) else (Transaction.currency == currency,)))


def _before(currency, months_back=12):
    cutoff = TODAY - timedelta(days=months_back * 31)
    return (Transaction.date < cutoff, Transaction.deleted_at.is_(None),
            *(() if is_base_view(currency) else (Transaction.currency == currency,)))


def _predicates(locale):
    from app.api.reports import _current_liability_predicate, _receivable_predicate
    return _receivable_predicate(locale), _current_liability_predicate(locale)


@pytest.mark.parametrize("locale,currency,seed", [
    ("ir", "IRR", 1), ("ir", "IRR", 2), ("ir", "ALL", 3), ("ir", "USD", 4),
    ("uk", "GBP", 5), ("uk", "ALL", 6), ("uk", "GBP", 7),
])
def test_sql_folds_match_the_line_by_line_fold(db, two_companies, locale, currency, seed):
    rng = random.Random(seed)
    company, acc = two_companies(locale)
    base = "IRR" if locale == "ir" else "GBP"
    _random_books(db, company, acc, rng, base=base)
    other, other_acc = two_companies(locale)                  # must never leak into the first
    _random_books(db, other, other_acc, random.Random(seed + 100), n=40, base=base)
    is_recv, is_liab = _predicates(locale)
    cal = JALALI if locale == "ir" else GREGORIAN
    dr_col, cr_col = amount_columns(currency)
    for months_back in (12, 3):
        kw = dict(live=_live(currency, months_back), before=_before(currency, months_back), dr_col=dr_col,
                  cr_col=cr_col, is_receivable=is_recv, is_current_liability=is_liab,
                  month_of=lambda d: month_key(d, cal), today=TODAY)
        with use_company(company.id):
            want = _reference_folds(db, **kw)
            got = _as_dict(fold_dashboard(db, **kw))
        assert got == want
        assert got["txn_count"] > 0 and got["line_count"] > 0          # the books aren't trivially empty
    # and the second company's own figures are its own
    with use_company(other.id):
        kw["live"], kw["before"] = _live(currency), _before(currency)
        assert _as_dict(fold_dashboard(db, **kw)) == _reference_folds(db, **kw)


def test_the_random_books_reach_every_branch(db, two_companies):
    """Guard for the test above: the generated books do exercise the edges."""
    company, acc = two_companies("ir")
    _random_books(db, company, acc, random.Random(1))
    is_recv, is_liab = _predicates("ir")
    with use_company(company.id):
        f = fold_dashboard(db, live=_live("IRR"), dr_col=TransactionLine.debit, cr_col=TransactionLine.credit,
                           is_receivable=is_recv, is_current_liability=is_liab,
                           month_of=lambda d: month_key(d, JALALI), today=TODAY)
    assert f.missing_line_desc and f.missing_reference and f.unlinked_entities
    assert f.expense_txn_count and f.expense_txn_with_attachment
    assert "Unassigned client" in f.profitability and "Unassigned vendor" in f.spend_by_vendor
    assert f.ar_buckets and f.ap_buckets and f.tax_and_liability_payable


# --- the route: date order, whitespace ------------------------------------------------------------------

@pytest.fixture()
def owner(db, client, two_companies):
    company, acc = two_companies("ir")
    with use_company(company.id):
        aria = Entity(id=uuid.uuid4(), name="Aria", type="client", company_id=company.id)
        db.add(aria)
        db.commit()
    tok = create_session_token(user_id=str(uuid.uuid4()), username="folds-owner", is_admin=True,
                               company_id=str(company.id), role="owner")
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, generate_csrf_token())
    return {"company": company, "acc": acc, "aria": aria, "client": client}


def _post(db, o, *, days_ago, lines, entity=None, desc="x", reference="R"):
    with use_company(o["company"].id):
        t = Transaction(id=uuid.uuid4(), date=TODAY - timedelta(days=days_ago), reference=reference,
                        description="j", currency="IRR")
        db.add(t)
        db.flush()
        for code, debit, credit in lines:
            db.add(TransactionLine(transaction_id=t.id, account_id=o["acc"][code].id, debit=debit,
                                   credit=credit, line_description=desc))
        if entity is not None:
            db.add(TransactionEntity(transaction_id=t.id, entity_id=entity.id, role="client"))
        db.commit()
    return t


def _dashboard(o):
    from app.api.reports import invalidate_dashboard_cache
    invalidate_dashboard_cache()
    r = o["client"].get("/reports/owner-dashboard?currency=IRR")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_receipt_entered_before_its_invoice_still_clears_it(db, owner):
    """Aging runs in date order. The journals used to come back in no set
    order, so a receipt read before the sale it settles was lost and the sale
    stayed overdue."""
    rev = sorted(c for c in owner["acc"] if c.startswith("41") and len(c) >= 4)[0]
    _post(db, owner, days_ago=10, lines=[("1110", 500_000, 0), ("1112", 0, 500_000)], entity=owner["aria"])
    _post(db, owner, days_ago=45, lines=[("1112", 500_000, 0), (rev, 0, 500_000)], entity=owner["aria"])
    d = _dashboard(owner)
    assert d["ar_aging"] == []
    assert not any(a["title"] == "Overdue receivables" for a in d["alerts"])
    # a second, unpaid sale stays in its bucket
    _post(db, owner, days_ago=40, lines=[("1112", 200_000, 0), (rev, 0, 200_000)], entity=owner["aria"])
    assert [(r["name"], r["days_31_60"], r["total"]) for r in _dashboard(owner)["ar_aging"]] == [("Aria", 200_000, 200_000)]


def test_what_is_owed_from_over_a_year_ago_stays_on_the_dashboard(db, owner):
    """D15: the aging and "Liabilities payable" were the 12-month window's
    movements, so an unpaid sale over a year old vanished from them — exactly
    what the oldest bucket is for — and so did an old liability."""
    rev = sorted(c for c in owner["acc"] if c.startswith("41") and len(c) >= 4)[0]
    _post(db, owner, days_ago=420, lines=[("1112", 700_000, 0), (rev, 0, 700_000)], entity=owner["aria"])
    _post(db, owner, days_ago=420, lines=[("1110", 300_000, 0), ("2110", 0, 300_000)])    # owed to a supplier since
    d = _dashboard(owner)
    assert [(r["name"], r["days_60_plus"], r["total"]) for r in d["ar_aging"]] == [("Aria", 700_000, 700_000)]
    kpis = {k["key"]: k["value"] for k in d["kpis"]}
    assert kpis["tax_and_liability_payable"] == 300_000
    assert [(r["name"], r["days_60_plus"]) for r in d["ap_aging"]] == [("Unassigned vendor", 300_000)]
    # paid inside the window: it leaves the aging, and nothing negative is left behind
    _post(db, owner, days_ago=20, lines=[("1110", 700_000, 0), ("1112", 0, 700_000)], entity=owner["aria"])
    assert _dashboard(owner)["ar_aging"] == []


def test_whitespace_only_descriptions_and_references_count_as_missing(db, owner):
    rev = sorted(c for c in owner["acc"] if c.startswith("41") and len(c) >= 4)[0]
    _post(db, owner, days_ago=3, lines=[("1110", 1, 0), (rev, 0, 1)], desc=" \t\n", reference="\t ")
    _post(db, owner, days_ago=3, lines=[("1110", 1, 0), (rev, 0, 1)], desc="sale", reference="S-1")
    issues = {h["key"]: h["count"] for h in _dashboard(owner)["health_issues"]}
    assert issues["missing_line_description"] == 2 and issues["missing_reference"] == 1


def test_ties_are_listed_by_name(db, owner):
    exp = sorted(c for c in owner["acc"] if classify_account_code(c) == EXPENSE and len(c) >= 4)[:3]
    for code in reversed(exp):
        _post(db, owner, days_ago=2, lines=[(code, 1_000, 0), ("1110", 0, 1_000)])
    names = {a.code: a.name for a in owner["acc"].values()}
    got = [r["category"] for r in _dashboard(owner)["expense_by_category"]]
    assert got == sorted(names[c] for c in exp)


# --- the forecast baseline ------------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [11, 12, 13])
def test_sql_baseline_matches_the_line_by_line_one(db, two_companies, seed):
    rng = random.Random(seed)
    company, acc = two_companies("ir")
    journals, ents = _random_books(db, company, acc, rng, n=60)
    # steady takings and payments in the 26 weeks (some with a second cash
    # line, some recurring), and below, journals the schedules explain
    rev = sorted(c for c in acc if c.startswith("41") and len(c) >= 4)[0]
    exp = sorted(c for c in acc if c.startswith("6") and len(c) >= 4)[0]
    with use_company(company.id):
        for day in range(0, 190, 2):
            t = Transaction(id=uuid.uuid4(), date=TODAY - timedelta(days=day),
                            reference=rng.choice(["J", None, "", "rec-1", "REC-2"]), description="j", currency="IRR")
            db.add(t)
            db.flush()
            journals.append(t)
            amount = rng.randint(1, 99) * 1000
            cash_in = day % 4 == 0
            lines = [("1110", amount, 0), (rev, 0, amount)] if cash_in else [(exp, amount, 0), ("1110", 0, amount)]
            if rng.random() < 0.3:                                      # a fee taken out of the same receipt
                lines += [("1110", 0, 500), (exp, 500, 0)]
            for code, debit, credit in lines:
                db.add(TransactionLine(transaction_id=t.id, account_id=acc[code].id, debit=debit, credit=credit))
        db.flush()
        live = [t for t in journals if t.deleted_at is None]
        inv = Invoice(id=uuid.uuid4(), number="INV-F1", kind="sales", status="paid", issue_date=TODAY,
                      due_date=TODAY, amount=1, currency="IRR", entity_id=ents[0].id)
        db.add(inv)
        db.flush()
        for t in rng.sample(live, 6):
            db.add(Payment(invoice_id=inv.id, date=t.date, amount=1, currency="IRR", direction="in",
                           transaction_id=t.id))
        a, b = rng.sample(live, 2)
        db.add(PayRun(period_start=TODAY, period_end=TODAY, pay_date=TODAY, status="paid", total_net=1,
                      post_transaction_id=a.id, pay_transaction_id=b.id))
        db.add(PayRun(period_start=TODAY, period_end=TODAY, pay_date=TODAY, status="draft", total_net=1))
        db.add(Commitment(kind="cheque", direction="pay", title="c", amount=1, due_date=TODAY, status="settled",
                          settled_transaction_id=rng.choice(live).id))
        db.commit()
    with use_company(company.id):
        for day in (TODAY, TODAY - timedelta(days=100), TODAY - timedelta(days=170)):
            want = _reference_baseline(db, day, "IRR", "ir")
            assert baseline(db, day, "IRR", "ir") == want
        assert want["weeks_of_history"] >= 1
        here = _reference_baseline(db, TODAY, "IRR", "ir")
        assert here["inflow"] > 0 and here["outflow"] > 0                  # not trivially zero

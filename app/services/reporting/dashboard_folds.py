"""The owner dashboard's journal folds, summed in the database (roadmap §2.6).

The dashboard used to read every line, link and attachment of its window into
Python (60k lines at 20k journals) and fold them journal by journal. The same
figures now come from a handful of GROUP BY queries over one per-journal
subquery; Python only sees one row per day, per expense account, per party
and per journal that moves a receivable or a current liability (the aging
needs those in date order).

What each figure means is unchanged — ``tests/test_dashboard_folds.py`` keeps
the old line-by-line fold as a reference and checks both agree on random books:

* a journal's revenue / expense is the net of its revenue / expense lines,
  floored at zero per journal before it is added to a month, a party or a
  vendor;
* expense by category adds only the lines that increase an expense account;
* vendors are the journal's payee/supplier links ("Unassigned vendor" when an
  expense journal has none), clients its client links ("Unassigned client");
  a party linked twice counts twice, as before;
* aging adds a journal's receivable (current-liability) increase to its
  clients' (vendors') age bucket and takes a decrease from the oldest bucket
  first, journals in date order.

Which account is revenue, expense, receivable or a current liability is still
decided in Python from the account code (one query over the chart); the
queries then test ``account_id IN (…)``, so the SQL can't drift from
``classify_account_code``.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionAttachment, TransactionLine
from app.services.reporting.common import EXPENSE, REVENUE, classify_account_code

UNASSIGNED_CLIENT = "Unassigned client"
UNASSIGNED_VENDOR = "Unassigned vendor"
UNKNOWN_PARTY = "Unknown"
VENDOR_ROLES = ("payee", "supplier")
# str.strip() in the old fold; the ASCII whitespace a pasted description carries.
_BLANKS = " \t\r\n"


def _bucket_by_age(days_old: int) -> str:
    if days_old <= 30:
        return "current"
    if days_old <= 60:
        return "days_31_60"
    return "days_60_plus"


def _apply_reduction(buckets: dict[str, int], amount: int) -> None:
    for key in ("days_60_plus", "days_31_60", "current"):
        if amount <= 0:
            return
        take = min(amount, buckets[key])
        buckets[key] -= take
        amount -= take


def _empty_buckets() -> dict[str, int]:
    return {"current": 0, "days_31_60": 0, "days_60_plus": 0}


@dataclass
class DashboardFolds:
    monthly_revenue: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    monthly_expense: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    expense_by_category: dict[str, int] = field(default_factory=dict)
    spend_by_vendor: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    profitability: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: {"revenue": 0, "cost": 0}))
    ar_buckets: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(_empty_buckets))
    ap_buckets: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(_empty_buckets))
    receivable_due_this_week: int = 0
    payable_due_this_week: int = 0
    tax_and_liability_payable: int = 0
    txn_count: int = 0
    line_count: int = 0
    missing_line_desc: int = 0
    missing_reference: int = 0
    unlinked_entities: int = 0
    expense_txn_count: int = 0
    expense_txn_with_attachment: int = 0


def _blank(col):
    return func.trim(func.coalesce(col, ""), _BLANKS) == ""


def _pos(expr):
    return case((expr > 0, expr), else_=0)


def fold_dashboard(
    db: Session,
    *,
    live: tuple,
    dr_col,
    cr_col,
    is_receivable: Callable[[str], bool],
    is_current_liability: Callable[[str], bool],
    month_of: Callable[[date], str],
    today: date,
    before: tuple | None = None,
) -> DashboardFolds:
    """``live`` is the dashboard's window filter on Transaction; ``dr_col`` /
    ``cr_col`` the line columns it sums (own currency or base value).
    ``before``: the journals before the window (same currency, not undone).
    What they leave owed starts each party's aging in its oldest bucket and
    counts in the liabilities: the aging and the liabilities used to be the
    window's movements, so a receivable over a year old vanished from them."""
    out = DashboardFolds()
    # Every query below reaches lines, links and files through a journal in
    # ``live``, which leaves out undone journals itself; the global filter's
    # per-line re-check of the journal (models/transaction.py) is redundant here
    # and costs a fifth of each scan.
    live = (*live, Transaction.deleted_at.is_(None))

    def run(stmt):
        return db.execute(stmt, execution_options={"include_deleted": True})

    rev_ids, exp_ids, recv_ids, liab_ids = [], [], [], []
    for aid, code in db.execute(select(Account.id, Account.code)):
        kind = classify_account_code(code)
        if kind == REVENUE:
            rev_ids.append(aid)
        elif kind == EXPENSE:
            exp_ids.append(aid)
        if is_receivable(code):
            recv_ids.append(aid)
        if is_current_liability(code):
            liab_ids.append(aid)

    TL = TransactionLine
    dr = func.coalesce(dr_col, 0)
    cr = func.coalesce(cr_col, 0)
    is_exp = TL.account_id.in_(exp_ids)
    per_txn = (
        select(
            TL.transaction_id.label("tid"),
            func.sum(case((TL.account_id.in_(rev_ids), cr - dr), else_=0)).label("rev"),
            func.sum(case((is_exp, dr - cr), else_=0)).label("exp"),
            func.sum(case((TL.account_id.in_(recv_ids), dr - cr), else_=0)).label("recv"),
            func.sum(case((TL.account_id.in_(liab_ids), cr - dr), else_=0)).label("liab"),
            func.count().label("n_lines"),
            func.sum(case((_blank(TL.line_description), 1), else_=0)).label("n_blank"),
            func.max(case((and_(is_exp, dr - cr > 0), 1), else_=0)).label("has_exp"),
        )
        .join(Transaction, TL.transaction_id == Transaction.id)
        .where(*live)
        .group_by(TL.transaction_id)
        .subquery("per_txn")
    )
    rev = func.coalesce(per_txn.c.rev, 0)
    exp = func.coalesce(per_txn.c.exp, 0)
    has_exp = func.coalesce(per_txn.c.has_exp, 0) == 1

    # Per journal: does it link any party, a client, a vendor; has it a file.
    role = func.lower(TransactionEntity.role)
    links = (
        select(
            TransactionEntity.transaction_id.label("tid"),
            func.max(case((role == "client", 1), else_=0)).label("client"),
            func.max(case((role.in_(VENDOR_ROLES), 1), else_=0)).label("vendor"),
        )
        .join(Transaction, TransactionEntity.transaction_id == Transaction.id)
        .where(*live)
        .group_by(TransactionEntity.transaction_id)
        .subquery("links")
    )
    files = (
        select(TransactionAttachment.transaction_id.label("tid"))
        .join(Transaction, TransactionAttachment.transaction_id == Transaction.id)
        .where(*live)
        .group_by(TransactionAttachment.transaction_id)
        .subquery("files")
    )
    no_link = links.c.tid.is_(None)
    no_client = func.coalesce(links.c.client, 0) == 0
    no_vendor = func.coalesce(links.c.vendor, 0) == 0
    has_attachment = files.c.tid.is_not(None)

    # One row per day with journals: months, book-quality counts, the
    # liabilities total and the "unassigned" parties.
    for (day, n, no_ref, no_link, n_lines, n_blank, n_exp, n_exp_att, rev_d, exp_d, liab_d,
         unassigned_vendor, n_no_client, rev_no_client, exp_no_client) in run(
        select(
            Transaction.date,
            func.count(),
            func.sum(case((_blank(Transaction.reference), 1), else_=0)),
            func.sum(case((no_link, 1), else_=0)),
            func.sum(func.coalesce(per_txn.c.n_lines, 0)),
            func.sum(func.coalesce(per_txn.c.n_blank, 0)),
            func.sum(case((has_exp, 1), else_=0)),
            func.sum(case((and_(has_exp, has_attachment), 1), else_=0)),
            func.sum(_pos(rev)),
            func.sum(_pos(exp)),
            func.sum(func.coalesce(per_txn.c.liab, 0)),
            func.sum(case((and_(exp > 0, no_vendor), exp), else_=0)),
            func.sum(case((no_client, 1), else_=0)),
            func.sum(case((no_client, _pos(rev)), else_=0)),
            func.sum(case((no_client, _pos(exp)), else_=0)),
        )
        .select_from(Transaction)
        .outerjoin(per_txn, per_txn.c.tid == Transaction.id)
        .outerjoin(links, links.c.tid == Transaction.id)
        .outerjoin(files, files.c.tid == Transaction.id)
        .where(*live)
        .group_by(Transaction.date)
    ):
        month = month_of(day)
        out.monthly_revenue[month] += int(rev_d or 0)
        out.monthly_expense[month] += int(exp_d or 0)
        out.txn_count += int(n)
        out.missing_reference += int(no_ref or 0)
        out.unlinked_entities += int(no_link or 0)
        out.line_count += int(n_lines or 0)
        out.missing_line_desc += int(n_blank or 0)
        out.expense_txn_count += int(n_exp or 0)
        out.expense_txn_with_attachment += int(n_exp_att or 0)
        out.tax_and_liability_payable += int(liab_d or 0)
        if unassigned_vendor:
            out.spend_by_vendor[UNASSIGNED_VENDOR] += int(unassigned_vendor)
        if n_no_client:
            p = out.profitability[UNASSIGNED_CLIENT]
            p["revenue"] += int(rev_no_client or 0)
            p["cost"] += int(exp_no_client or 0)

    # Expense by account: only the lines that increase an expense.
    for name, amount in run(
        select(Account.name, func.sum(dr - cr))
        .select_from(TL)
        .join(Transaction, TL.transaction_id == Transaction.id)
        .join(Account, TL.account_id == Account.id)
        .where(*live, is_exp, dr - cr > 0)
        .group_by(Account.name)
    ):
        out.expense_by_category[name] = int(amount or 0)

    # Named parties: vendors get the journal's expense, clients its revenue
    # and cost. A party on a journal with neither still gets a (zero) row.
    side = case((role.in_(VENDOR_ROLES), literal("v")), (role == "client", literal("c")), else_=literal(""))
    party = func.coalesce(Entity.name, UNKNOWN_PARTY)
    for kind, name, rev_p, exp_p in run(
        select(side, party, func.sum(_pos(rev)), func.sum(_pos(exp)))
        .select_from(TransactionEntity)
        .join(Transaction, TransactionEntity.transaction_id == Transaction.id)
        .outerjoin(Entity, TransactionEntity.entity_id == Entity.id)
        .outerjoin(per_txn, per_txn.c.tid == Transaction.id)
        .where(*live, or_(role.in_(VENDOR_ROLES), role == "client"))
        .group_by(side, party)
    ):
        if kind == "v":
            out.spend_by_vendor[name] += int(exp_p or 0)
        else:
            p = out.profitability[name]
            p["revenue"] += int(rev_p or 0)
            p["cost"] += int(exp_p or 0)

    # Before the window: per journal, what it moves in receivables and current
    # liabilities and whom it names; summed per party, a balance still owed is
    # over a year old, so it starts in the oldest bucket. Journals of no party
    # are the unassigned client's / vendor's, as in the walk below.
    if before is not None:
        before = (*before, Transaction.deleted_at.is_(None))
        pre = (
            select(TL.transaction_id.label("tid"),
                   func.sum(case((TL.account_id.in_(recv_ids), dr - cr), else_=0)).label("recv"),
                   func.sum(case((TL.account_id.in_(liab_ids), cr - dr), else_=0)).label("liab"))
            .join(Transaction, TL.transaction_id == Transaction.id)
            .where(*before)
            .group_by(TL.transaction_id)
            .subquery("pre")
        )
        old_ar: dict[str, int] = defaultdict(int)
        old_ap: dict[str, int] = defaultdict(int)
        seen: dict = {}
        for tid, recv, liab, link_role, link_name, has_link in run(
            select(pre.c.tid, pre.c.recv, pre.c.liab, TransactionEntity.role, Entity.name,
                   TransactionEntity.id.is_not(None))
            .select_from(pre)
            .outerjoin(TransactionEntity, TransactionEntity.transaction_id == pre.c.tid)
            .outerjoin(Entity, TransactionEntity.entity_id == Entity.id)
            .where(or_(pre.c.recv != 0, pre.c.liab != 0))
        ):
            j = seen.get(tid)
            if j is None:
                j = seen[tid] = [int(recv or 0), int(liab or 0), [], []]
            if has_link:
                r = (link_role or "").lower()
                n = link_name if link_name is not None else UNKNOWN_PARTY
                if r == "client":
                    j[2].append(n)
                elif r in VENDOR_ROLES:
                    j[3].append(n)
        for recv, liab, clients, vendors in seen.values():
            out.tax_and_liability_payable += liab
            for c in clients or [UNASSIGNED_CLIENT]:
                old_ar[c] += recv
            for v in vendors or [UNASSIGNED_VENDOR]:
                old_ap[v] += liab
        for c, owed in old_ar.items():
            if owed > 0:
                out.ar_buckets[c]["days_60_plus"] += owed
        for v, owed in old_ap.items():
            if owed > 0:
                out.ap_buckets[v]["days_60_plus"] += owed

    # Aging: journals that move a receivable or a current liability, in date
    # order, with their links (one row per link, one with NULLs if none).
    moves = or_(per_txn.c.recv != 0, per_txn.c.liab != 0)
    # Rows come grouped by journal (the id is the last sort key), so a new id
    # starts a new journal — no dict of UUIDs to hash.
    journals: list = []
    prev = None
    for tid, day, recv, liab, link_role, link_name, has_link in run(
        select(Transaction.id, Transaction.date, per_txn.c.recv, per_txn.c.liab,
               TransactionEntity.role, Entity.name, TransactionEntity.id.is_not(None))
        .select_from(Transaction)
        .join(per_txn, per_txn.c.tid == Transaction.id)
        .outerjoin(TransactionEntity, TransactionEntity.transaction_id == Transaction.id)
        .outerjoin(Entity, TransactionEntity.entity_id == Entity.id)
        .where(*live, moves)
        .order_by(Transaction.date, Transaction.created_at, Transaction.id)
    ):
        if tid != prev:
            prev = tid
            j = (day, int(recv or 0), int(liab or 0), [], [])
            journals.append(j)
        if has_link:
            r = (link_role or "").lower()
            n = link_name if link_name is not None else UNKNOWN_PARTY
            if r == "client":
                j[3].append(n)
            elif r in VENDOR_ROLES:
                j[4].append(n)

    for day, recv, liab, clients, vendors in journals:
        clients = clients or [UNASSIGNED_CLIENT]
        vendors = vendors or [UNASSIGNED_VENDOR]
        age_days = max(0, (today - day).days)
        bucket = _bucket_by_age(age_days)
        due_this_week = 23 <= age_days <= 30
        if recv > 0:
            for c in clients:
                out.ar_buckets[c][bucket] += recv
            if due_this_week:
                out.receivable_due_this_week += recv
        elif recv < 0:
            for c in clients:
                _apply_reduction(out.ar_buckets[c], -recv)
        if liab > 0:
            for v in vendors:
                out.ap_buckets[v][bucket] += liab
            if due_this_week:
                out.payable_due_this_week += liab
        elif liab < 0:
            for v in vendors:
                _apply_reduction(out.ap_buckets[v], -liab)

    return out

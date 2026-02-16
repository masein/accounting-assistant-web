from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.account import Account
from app.models.entity import Entity, TransactionEntity
from app.models.invoice import Invoice
from app.models.recurring import RecurringRule
from app.models.transaction import Transaction, TransactionLine
from app.schemas.report import (
    AccountDetailResponse,
    AccountLineDetail,
    AlertItem,
    AgingRow,
    ExpenseCategoryRow,
    ForecastRow,
    HealthChecklistItem,
    HealthIssue,
    KpiCard,
    LedgerSummaryResponse,
    LedgerSummaryRow,
    MonthlySeriesRow,
    MissingReferenceResponse,
    MissingReferenceRow,
    OwnerDashboardResponse,
    ProfitabilityRow,
    VendorSpendRow,
)
from app.schemas.transaction import AttachmentRead, TransactionEntityLinkRead, TransactionLineRead, TransactionRead

router = APIRouter(prefix="/reports", tags=["reports"])


def _attachment_url(file_path: str) -> str:
    if not file_path:
        return ""
    normalized = str(file_path).replace("\\", "/")
    if "/uploads/" in normalized:
        tail = normalized.split("/uploads/", 1)[1].lstrip("/")
        return f"/uploads/{tail}"
    return f"/uploads/transactions/{Path(normalized).name}"


def _month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _line_revenue(line: TransactionLine) -> int:
    if line.account.code.startswith("41"):
        return line.credit - line.debit
    return 0


def _line_expense(line: TransactionLine) -> int:
    if line.account.code.startswith("61") or line.account.code.startswith("62"):
        return line.debit - line.credit
    return 0


def _line_cash_delta(line: TransactionLine) -> int:
    if line.account.code == "1110":
        return line.debit - line.credit
    return 0


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


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _week_of_month(d: date) -> int:
    return ((d.day - 1) // 7) + 1


def _next_month_same_day(d: date) -> date:
    year = d.year + (1 if d.month == 12 else 0)
    month = 1 if d.month == 12 else d.month + 1
    day = min(d.day, 28)
    while day >= 1:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, 1)


def _next_year_same_day(d: date) -> date:
    day = min(d.day, 28)
    while day >= 1:
        try:
            return date(d.year + 1, d.month, day)
        except ValueError:
            day -= 1
    return date(d.year + 1, d.month, 1)


@router.get("/ledger-summary", response_model=LedgerSummaryResponse)
def get_ledger_summary(db: Session = Depends(get_db)) -> LedgerSummaryResponse:
    """
    Aggregate all transaction lines by account: turnover (sum of debits/credits) and
    ending balance (debit_balance / credit_balance), in trial-balance style like the Excel files.
    """
    q = select(TransactionLine).options(selectinload(TransactionLine.account))
    lines = db.execute(q).scalars().all()
    # Aggregate by account_id
    by_account: dict[str, dict] = defaultdict(
        lambda: {
            "account_code": "",
            "account_name": "",
            "debit_turnover": 0,
            "credit_turnover": 0,
            "debit_balance": 0,
            "credit_balance": 0,
        }
    )
    for line in lines:
        acc = line.account
        key = str(acc.id)
        by_account[key]["account_code"] = acc.code
        by_account[key]["account_name"] = acc.name
        by_account[key]["debit_turnover"] += line.debit
        by_account[key]["credit_turnover"] += line.credit
    # Compute ending balance per account (debit balance = net debit, credit balance = net credit)
    for key, data in by_account.items():
        net = data["debit_turnover"] - data["credit_turnover"]
        if net >= 0:
            data["debit_balance"] = net
            data["credit_balance"] = 0
        else:
            data["debit_balance"] = 0
            data["credit_balance"] = -net
    rows = [
        LedgerSummaryRow(
            account_code=d["account_code"],
            account_name=d["account_name"],
            debit_turnover=d["debit_turnover"],
            credit_turnover=d["credit_turnover"],
            debit_balance=d["debit_balance"],
            credit_balance=d["credit_balance"],
        )
        for d in sorted(by_account.values(), key=lambda x: x["account_code"])
    ]
    total_debit_turnover = sum(r.debit_turnover for r in rows)
    total_credit_turnover = sum(r.credit_turnover for r in rows)
    total_debit_balance = sum(r.debit_balance for r in rows)
    total_credit_balance = sum(r.credit_balance for r in rows)
    return LedgerSummaryResponse(
        rows=rows,
        total_debit_turnover=total_debit_turnover,
        total_credit_turnover=total_credit_turnover,
        total_debit_balance=total_debit_balance,
        total_credit_balance=total_credit_balance,
    )


@router.get("/accounts/{account_code}/detail", response_model=AccountDetailResponse)
def get_account_detail(account_code: str, db: Session = Depends(get_db)) -> AccountDetailResponse:
    """
    Transaction list and summary for a single account. Used when user clicks a ledger row.
    """
    acc = db.execute(select(Account).where(Account.code == account_code.strip())).scalars().one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail=f"Account not found: {account_code}")
    q = (
        select(TransactionLine, Transaction)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .where(TransactionLine.account_id == acc.id)
        .order_by(Transaction.date, Transaction.id)
    )
    rows = db.execute(q).all()
    lines: list[AccountLineDetail] = []
    debit_turnover = credit_turnover = 0
    for line, txn in rows:
        debit_turnover += line.debit
        credit_turnover += line.credit
        lines.append(
            AccountLineDetail(
                transaction_date=txn.date,
                reference=txn.reference,
                description=txn.description,
                debit=line.debit,
                credit=line.credit,
                line_description=line.line_description,
            )
        )
    net = debit_turnover - credit_turnover
    debit_balance = net if net >= 0 else 0
    credit_balance = -net if net < 0 else 0
    return AccountDetailResponse(
        account_code=acc.code,
        account_name=acc.name,
        debit_turnover=debit_turnover,
        credit_turnover=credit_turnover,
        debit_balance=debit_balance,
        credit_balance=credit_balance,
        lines=lines,
    )


@router.get("/entities/{entity_id}/transactions", response_model=list[TransactionRead])
def get_entity_transactions(
    entity_id: UUID,
    db: Session = Depends(get_db),
) -> list[TransactionRead]:
    """
    All transactions linked to this entity (e.g. all vouchers with client Innotech).
    """
    entity = db.get(Entity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    q = (
        select(Transaction)
        .join(TransactionEntity, Transaction.id == TransactionEntity.transaction_id)
        .where(TransactionEntity.entity_id == entity_id)
        .distinct()
        .order_by(Transaction.date, Transaction.id)
        .options(
            selectinload(Transaction.lines).selectinload(TransactionLine.account),
            selectinload(Transaction.entity_links).selectinload(TransactionEntity.entity),
            selectinload(Transaction.attachments),
        )
    )
    transactions = db.execute(q).scalars().unique().all()
    out = []
    for t in transactions:
        lines_read = [
            TransactionLineRead(
                id=line.id,
                account_id=line.account_id,
                account_code=line.account.code,
                debit=line.debit,
                credit=line.credit,
                line_description=line.line_description,
            )
            for line in t.lines
        ]
        out.append(
            TransactionRead(
                id=t.id,
                date=t.date,
                reference=t.reference,
                description=t.description,
                lines=lines_read,
                entity_links=[
                    TransactionEntityLinkRead(
                        role=link.role,
                        entity_id=link.entity_id,
                        entity_name=(link.entity.name if link.entity else None),
                        entity_type=(link.entity.type if link.entity else None),
                    )
                    for link in (t.entity_links or [])
                ],
                attachments=[
                    AttachmentRead(
                        id=a.id,
                        file_name=a.file_name,
                        content_type=a.content_type,
                        size_bytes=a.size_bytes,
                        url=_attachment_url(a.file_path),
                        transaction_id=t.id,
                    )
                    for a in (t.attachments or [])
                ],
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
        )
    return out


@router.get("/owner-dashboard", response_model=OwnerDashboardResponse)
def get_owner_dashboard(db: Session = Depends(get_db)) -> OwnerDashboardResponse:
    today = date.today()
    txns = db.execute(
        select(Transaction).options(
            selectinload(Transaction.lines).selectinload(TransactionLine.account),
            selectinload(Transaction.entity_links).selectinload(TransactionEntity.entity),
            selectinload(Transaction.attachments),
        )
    ).scalars().unique().all()

    monthly_revenue: dict[str, int] = defaultdict(int)
    monthly_expense: dict[str, int] = defaultdict(int)
    weekly_cash_in: dict[date, int] = defaultdict(int)
    weekly_cash_out: dict[date, int] = defaultdict(int)
    expense_by_category: dict[str, int] = defaultdict(int)
    spend_by_vendor: dict[str, int] = defaultdict(int)
    profitability: dict[str, dict[str, int]] = defaultdict(lambda: {"revenue": 0, "cost": 0})
    ar_buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"current": 0, "days_31_60": 0, "days_60_plus": 0})
    ap_buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"current": 0, "days_31_60": 0, "days_60_plus": 0})

    cash_on_hand = 0
    receivable_due_this_week = 0
    payable_due_this_week = 0
    tax_and_liability_payable = 0
    expense_txn_count = 0
    expense_txn_with_attachment = 0

    for t in txns:
        month = _month_key(t.date)
        week_start = t.date - timedelta(days=t.date.weekday())
        txn_revenue = 0
        txn_expense = 0
        txn_cash_delta = 0
        receivable_delta = 0
        payable_delta = 0
        expense_accounts_seen: set[str] = set()
        for ln in t.lines:
            rev = _line_revenue(ln)
            exp = _line_expense(ln)
            cash_delta = _line_cash_delta(ln)
            txn_revenue += rev
            txn_expense += exp
            txn_cash_delta += cash_delta
            if ln.account.code == "1112":
                receivable_delta += ln.debit - ln.credit
            if ln.account.code.startswith("21"):
                payable_delta += ln.credit - ln.debit
                tax_and_liability_payable += ln.credit - ln.debit
            if exp > 0:
                expense_accounts_seen.add(ln.account.name)
                expense_by_category[ln.account.name] += exp
        monthly_revenue[month] += max(0, txn_revenue)
        monthly_expense[month] += max(0, txn_expense)
        if txn_cash_delta >= 0:
            weekly_cash_in[week_start] += txn_cash_delta
        else:
            weekly_cash_out[week_start] += -txn_cash_delta
        cash_on_hand += txn_cash_delta

        roles = defaultdict(list)
        for link in t.entity_links:
            roles[(link.role or "").lower()].append(link.entity.name if link.entity else "Unknown")
        client_names = roles.get("client", []) or ["Unassigned client"]
        vendor_names = roles.get("payee", []) + roles.get("supplier", [])
        if not vendor_names and txn_expense > 0:
            vendor_names = ["Unassigned vendor"]

        for n in vendor_names:
            spend_by_vendor[n] += max(0, txn_expense)
        for c in client_names:
            profitability[c]["revenue"] += max(0, txn_revenue)
            profitability[c]["cost"] += max(0, txn_expense)

        age_days = max(0, (today - t.date).days)
        bucket = _bucket_by_age(age_days)
        if receivable_delta > 0:
            for c in client_names:
                ar_buckets[c][bucket] += receivable_delta
            if age_days >= 23 and age_days <= 30:
                receivable_due_this_week += receivable_delta
        elif receivable_delta < 0:
            for c in client_names:
                _apply_reduction(ar_buckets[c], -receivable_delta)
        if payable_delta > 0:
            names = vendor_names or ["Unassigned vendor"]
            for v in names:
                ap_buckets[v][bucket] += payable_delta
            if age_days >= 23 and age_days <= 30:
                payable_due_this_week += payable_delta
        elif payable_delta < 0:
            names = vendor_names or ["Unassigned vendor"]
            for v in names:
                _apply_reduction(ap_buckets[v], -payable_delta)

        if expense_accounts_seen:
            expense_txn_count += 1
            if t.attachments:
                expense_txn_with_attachment += 1

    current_month = _month_key(today)
    monthly_net = monthly_revenue.get(current_month, 0) - monthly_expense.get(current_month, 0)

    recent_months = []
    for i in range(3):
        m = (today.replace(day=1) - timedelta(days=i * 31))
        recent_months.append(_month_key(m))
    burn_values = [monthly_expense.get(m, 0) for m in recent_months]
    burn_rate = int(mean(burn_values)) if burn_values else 0
    runway_months = round(cash_on_hand / burn_rate, 1) if burn_rate > 0 else None

    week_keys = sorted(set(weekly_cash_in.keys()) | set(weekly_cash_out.keys()))
    hist_weeks = week_keys[-24:] if week_keys else []
    avg_in = int(mean([weekly_cash_in.get(w, 0) for w in hist_weeks])) if hist_weeks else 0
    avg_out = int(mean([weekly_cash_out.get(w, 0) for w in hist_weeks])) if hist_weeks else 0

    # Weekly seasonality profile (week-of-month) from historical cash behavior.
    wom_in: dict[int, list[int]] = defaultdict(list)
    wom_out: dict[int, list[int]] = defaultdict(list)
    for w in hist_weeks:
        wom = _week_of_month(w)
        wom_in[wom].append(weekly_cash_in.get(w, 0))
        wom_out[wom].append(weekly_cash_out.get(w, 0))

    base_week = _week_start(today)
    forecast_weeks = [base_week + timedelta(days=7 * i) for i in range(1, 14)]
    forecast_start = forecast_weeks[0]
    forecast_end = forecast_weeks[-1] + timedelta(days=6)

    # Scheduled expectations from unpaid invoices due in forecast window.
    sched_in: dict[date, int] = defaultdict(int)
    sched_out: dict[date, int] = defaultdict(int)
    open_invoices = db.execute(
        select(Invoice).where(
            Invoice.status.in_(("draft", "issued")),
            Invoice.due_date >= forecast_start,
            Invoice.due_date <= forecast_end,
        )
    ).scalars().all()
    for inv in open_invoices:
        w = _week_start(inv.due_date)
        if inv.kind == "sales":
            sched_in[w] += max(0, inv.amount)
        elif inv.kind == "purchase":
            sched_out[w] += max(0, inv.amount)

    # Scheduled expectations from active recurring rules.
    active_rules = db.execute(
        select(RecurringRule).where(
            RecurringRule.status == "active",
            RecurringRule.amount.is_not(None),
            RecurringRule.amount > 0,
        )
    ).scalars().all()
    for rule in active_rules:
        run_on = rule.next_run_date
        while run_on < forecast_start:
            run_on = _next_year_same_day(run_on) if rule.frequency == "yearly" else _next_month_same_day(run_on)
        while run_on <= forecast_end:
            w = _week_start(run_on)
            amt = int(rule.amount or 0)
            if (rule.direction or "").lower() == "receipt":
                sched_in[w] += amt
            else:
                sched_out[w] += amt
            run_on = _next_year_same_day(run_on) if rule.frequency == "yearly" else _next_month_same_day(run_on)

    projected_cash = cash_on_hand
    forecast_rows: list[ForecastRow] = []
    for w in forecast_weeks:
        wom = _week_of_month(w)
        seasonal_in = int(mean(wom_in[wom])) if wom_in.get(wom) else avg_in
        seasonal_out = int(mean(wom_out[wom])) if wom_out.get(wom) else avg_out
        projected_in = max(0, seasonal_in + sched_in.get(w, 0))
        projected_out = max(0, seasonal_out + sched_out.get(w, 0))
        net = projected_in - projected_out
        projected_cash += net
        forecast_rows.append(
            ForecastRow(
                week_start=w,
                projected_inflow=projected_in,
                projected_outflow=projected_out,
                projected_net=net,
                projected_cash=projected_cash,
                risk=projected_cash < 0,
            )
        )

    ar_rows = []
    for n, b in ar_buckets.items():
        total = b["current"] + b["days_31_60"] + b["days_60_plus"]
        if total > 0:
            ar_rows.append(AgingRow(name=n, current=b["current"], days_31_60=b["days_31_60"], days_60_plus=b["days_60_plus"], total=total))
    ar_rows.sort(key=lambda r: r.total, reverse=True)

    ap_rows = []
    for n, b in ap_buckets.items():
        total = b["current"] + b["days_31_60"] + b["days_60_plus"]
        if total > 0:
            ap_rows.append(AgingRow(name=n, current=b["current"], days_31_60=b["days_31_60"], days_60_plus=b["days_60_plus"], total=total))
    ap_rows.sort(key=lambda r: r.total, reverse=True)

    expense_rows = [ExpenseCategoryRow(category=k, amount=v) for k, v in sorted(expense_by_category.items(), key=lambda x: x[1], reverse=True)[:8]]
    vendor_rows = [VendorSpendRow(vendor=k, amount=v) for k, v in sorted(spend_by_vendor.items(), key=lambda x: x[1], reverse=True)[:8]]

    series_keys = sorted(set(monthly_revenue.keys()) | set(monthly_expense.keys()))
    monthly_expense_series = [MonthlySeriesRow(period=m, value=monthly_expense.get(m, 0)) for m in series_keys[-12:]]

    profitability_rows: list[ProfitabilityRow] = []
    for client, vals in profitability.items():
        rev = vals["revenue"]
        cost = vals["cost"]
        profit = rev - cost
        margin = round((profit / rev) * 100.0, 2) if rev > 0 else None
        profitability_rows.append(ProfitabilityRow(client=client, revenue=rev, cost=cost, profit=profit, margin_pct=margin))
    profitability_rows.sort(key=lambda r: r.profit, reverse=True)

    txn_count = len(txns) or 1
    line_count = sum(len(t.lines) for t in txns) or 1
    missing_reference = sum(1 for t in txns if not (t.reference or "").strip())
    unlinked_entities = sum(1 for t in txns if not t.entity_links)
    missing_attachments_on_expense = max(0, expense_txn_count - expense_txn_with_attachment)
    missing_line_desc = sum(1 for t in txns for ln in t.lines if not (ln.line_description or "").strip())
    health_issues = [
        HealthIssue(key="missing_reference", label="Missing reference", count=missing_reference, ratio=missing_reference / txn_count),
        HealthIssue(key="unlinked_entity", label="Transactions without entity", count=unlinked_entities, ratio=unlinked_entities / txn_count),
        HealthIssue(key="expense_without_attachment", label="Expense transactions without attachment", count=missing_attachments_on_expense, ratio=(missing_attachments_on_expense / max(1, expense_txn_count))),
        HealthIssue(key="missing_line_description", label="Lines without description", count=missing_line_desc, ratio=missing_line_desc / line_count),
    ]
    weighted_penalty = int(
        (health_issues[0].ratio * 25)
        + (health_issues[1].ratio * 30)
        + (health_issues[2].ratio * 25)
        + (health_issues[3].ratio * 20)
    )
    health_score = max(0, min(100, 100 - weighted_penalty))

    overdue_ar = sum(r.days_31_60 + r.days_60_plus for r in ar_rows)
    overdue_ap = sum(r.days_31_60 + r.days_60_plus for r in ap_rows)
    alerts: list[AlertItem] = []
    if runway_months is not None and runway_months < 3:
        alerts.append(AlertItem(level="high", title="Cash runway is short", message=f"Estimated runway is {runway_months} months based on recent burn rate."))
    if overdue_ar > 0:
        alerts.append(AlertItem(level="medium", title="Overdue receivables", message=f"Overdue AR is {overdue_ar:,}. Follow up collections."))
    if overdue_ap > 0:
        alerts.append(AlertItem(level="medium", title="Overdue payables", message=f"Overdue AP is {overdue_ap:,}. Plan vendor payments."))
    if health_score < 70:
        alerts.append(AlertItem(level="medium", title="Book quality risk", message=f"Data quality score is {health_score}/100. Resolve missing references/entities/attachments."))
    if burn_rate > 0 and monthly_expense.get(current_month, 0) > int(burn_rate * 1.5):
        alerts.append(AlertItem(level="low", title="Expense spike", message="This month expenses are significantly above recent average."))

    close_checklist = [
        HealthChecklistItem(item="References captured", ok=(missing_reference / txn_count) < 0.2, detail=f"{txn_count - missing_reference}/{txn_count} transactions have reference."),
        HealthChecklistItem(item="Entities linked", ok=(unlinked_entities / txn_count) < 0.2, detail=f"{txn_count - unlinked_entities}/{txn_count} transactions have entity links."),
        HealthChecklistItem(item="Expense attachments available", ok=(missing_attachments_on_expense / max(1, expense_txn_count)) < 0.4, detail=f"{expense_txn_with_attachment}/{max(1, expense_txn_count)} expense transactions have attachments."),
        HealthChecklistItem(item="Line descriptions complete", ok=(missing_line_desc / line_count) < 0.25, detail=f"{line_count - missing_line_desc}/{line_count} lines have descriptions."),
    ]

    kpis = [
        KpiCard(key="cash_on_hand", label="Cash on hand", value=cash_on_hand, unit="IRR"),
        KpiCard(key="monthly_net_profit", label="Monthly net profit", value=monthly_net, unit="IRR"),
        KpiCard(key="burn_rate", label="Monthly burn rate", value=burn_rate, unit="IRR"),
        KpiCard(key="runway_months", label="Runway", value=(runway_months if runway_months is not None else -1), unit="months"),
        KpiCard(key="ar_due_week", label="AR due in ~7 days", value=receivable_due_this_week, unit="IRR"),
        KpiCard(key="ap_due_week", label="AP due in ~7 days", value=payable_due_this_week, unit="IRR"),
        KpiCard(key="tax_and_liability_payable", label="Liabilities payable (21xx)", value=max(0, tax_and_liability_payable), unit="IRR"),
    ]

    top_profit = profitability_rows[0] if profitability_rows else None
    owner_pack = (
        f"# Owner Weekly Pack ({today.isoformat()})\n\n"
        f"- Cash on hand: {cash_on_hand:,} IRR\n"
        f"- Net profit this month: {monthly_net:,} IRR\n"
        f"- Burn rate: {burn_rate:,} IRR/month\n"
        f"- Runway: {runway_months if runway_months is not None else 'N/A'} months\n"
        f"- Overdue AR: {overdue_ar:,} IRR\n"
        f"- Overdue AP: {overdue_ap:,} IRR\n"
        f"- Data health score: {health_score}/100\n"
        f"- Most profitable client: {(top_profit.client + ' (' + format(top_profit.profit, ',') + ' IRR)') if top_profit else 'N/A'}\n\n"
        f"## Priority Actions\n"
        f"1. Collect overdue receivables and monitor top debtor clients.\n"
        f"2. Review expense spikes and highest vendor/category spend.\n"
        f"3. Improve bookkeeping hygiene (references, entity links, attachments).\n"
    )

    return OwnerDashboardResponse(
        generated_on=today,
        kpis=kpis,
        forecast_13_weeks=forecast_rows,
        ar_aging=ar_rows[:10],
        ap_aging=ap_rows[:10],
        expense_by_category=expense_rows,
        spend_by_vendor=vendor_rows,
        monthly_expense_series=monthly_expense_series,
        profitability_by_client=profitability_rows[:10],
        health_score=health_score,
        health_issues=health_issues,
        close_checklist=close_checklist,
        alerts=alerts,
        owner_pack_markdown=owner_pack,
    )


@router.get("/missing-references", response_model=MissingReferenceResponse)
def get_missing_references(db: Session = Depends(get_db)) -> MissingReferenceResponse:
    rows = db.execute(
        select(Transaction).where((Transaction.reference.is_(None)) | (Transaction.reference == "")).order_by(Transaction.date.desc())
    ).scalars().all()
    items: list[MissingReferenceRow] = []
    for t in rows[:200]:
        suggested = None
        if t.description:
            txt = t.description.strip().upper()
            if "INVOICE" in txt:
                suggested = "INV-" + str(t.date).replace("-", "")
            elif "RENT" in txt:
                suggested = "RENT-" + str(t.date).replace("-", "")
            else:
                suggested = "REF-" + str(t.date).replace("-", "")
        items.append(
            MissingReferenceRow(
                transaction_id=str(t.id),
                date=t.date,
                description=t.description,
                suggested_reference=suggested,
            )
        )
    return MissingReferenceResponse(items=items)

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.account import Account
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionLine
from app.services.cash_service import cash_on_hand as _cash_on_hand_balance
from app.services.fx_service import get_reporting_currency
from app.services.reporting.repository import (
    _currency_filter,
    amount_columns,
    is_base_view,
    line_dr_cr,
    resolve_currency_view,
)
from app.services.locale_service import get_reporting_locale
from app.services.reporting.dashboard_folds import fold_dashboard
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
    TransactionSearchResponse,
    TransactionSearchRow,
    ProfitabilityRow,
    VendorSpendRow,
)
from app.schemas.transaction import (
    AttachmentRead,
    EntityTransactionRead,
    TransactionEntityLinkRead,
    TransactionLineRead,
    TransactionRead,
)

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


# Receivable / current-liability detection IS chart-specific: the same
# code means different things across locales (e.g. UK 1210 = bank deposit,
# Iran 1210 = property/plant). These predicates are selected by reporting
# locale inside get_owner_dashboard.
def _receivable_predicate(locale: str):
    if (locale or "").strip().lower() == "uk":
        return lambda c: c.startswith("1100")  # Trade debtors
    return lambda c: c == "1112"  # حساب‌ها و اسناد دریافتنی تجاری


def _current_liability_predicate(locale: str):
    if (locale or "").strip().lower() == "uk":
        # Creditors due within one year: 2100–2799 (2800+ is long-term).
        return lambda c: len(c) >= 2 and c[0] == "2" and c[1] in "1234567"
    return lambda c: c.startswith("21")  # Iranian current liabilities


@router.get("/ledger-summary", response_model=LedgerSummaryResponse)
def get_ledger_summary(
    currency: str | None = Query(None, description="Filter by currency (IRR, USD, etc.)"),
    db: Session = Depends(get_db),
) -> LedgerSummaryResponse:
    """
    Aggregate all transaction lines by account: turnover (sum of debits/credits) and
    ending balance (debit_balance / credit_balance), in trial-balance style like the Excel files.
    """
    # One currency per view — never sum IRR and USD face values. Default is
    # the reporting currency; the response lists the other currencies present.
    currency, other_currencies = resolve_currency_view(db, currency)
    # Summed in the database (roadmap §2.6): loading every line into Python
    # took ~1 s at 60k lines. Replaced/undone journals never count.
    # currency=ALL: every currency at its base-currency value (roadmap §4.6).
    dr, cr = amount_columns(currency)
    totals = db.execute(
        _currency_filter(
            select(TransactionLine.account_id, func.sum(dr), func.sum(cr))
            .join(Transaction, TransactionLine.transaction_id == Transaction.id)
            .where(Transaction.deleted_at.is_(None))
            .group_by(TransactionLine.account_id), currency)
    ).all()
    accounts = {a.id: a for a in db.execute(
        select(Account).where(Account.id.in_([t[0] for t in totals]))
        .options(selectinload(Account.parent))
    ).scalars().all()} if totals else {}
    by_account: dict[str, dict] = {}
    for account_id, debit, credit in totals:
        acc = accounts.get(account_id)
        if acc is None:
            # account row invisible (e.g. filtered by tenancy after a partial
            # wipe) — skip rather than 500 the whole ledger
            continue
        # کل: the parent GROUP account this معین rolls up into.
        parent = acc.parent
        by_account[str(acc.id)] = {
            "account_code": acc.code,
            "account_name": acc.name,
            "parent_code": parent.code if parent else None,
            "parent_name": parent.name if parent else None,
            "debit_turnover": int(debit or 0),
            "credit_turnover": int(credit or 0),
        }
    # Ending balance per account (debit balance = net debit, credit balance = net credit)
    for data in by_account.values():
        net = data["debit_turnover"] - data["credit_turnover"]
        data["debit_balance"] = net if net >= 0 else 0
        data["credit_balance"] = -net if net < 0 else 0
    rows = [
        LedgerSummaryRow(
            account_code=d["account_code"],
            account_name=d["account_name"],
            parent_code=d.get("parent_code"),
            parent_name=d.get("parent_name"),
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
        currency=currency,
        other_currencies=other_currencies,
        rows=rows,
        total_debit_turnover=total_debit_turnover,
        total_credit_turnover=total_credit_turnover,
        total_debit_balance=total_debit_balance,
        total_credit_balance=total_credit_balance,
    )


@router.get("/accounts/{account_code}/detail", response_model=AccountDetailResponse)
def get_account_detail(
    account_code: str,
    currency: str | None = Query(None, description="Filter by currency (IRR, USD, etc.)"),
    db: Session = Depends(get_db),
) -> AccountDetailResponse:
    """
    Transaction list and summary for a single account. Used when user clicks a ledger row.
    """
    acc = db.execute(select(Account).where(Account.code == account_code.strip())).scalars().one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail=f"Account not found: {account_code}")
    q = (
        select(TransactionLine, Transaction)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .where(TransactionLine.account_id == acc.id, Transaction.deleted_at.is_(None))
    )
    currency, other_currencies = resolve_currency_view(db, currency)
    q = _currency_filter(q, currency)
    q = q.order_by(Transaction.date, Transaction.created_at, Transaction.id)
    rows = db.execute(q).all()
    lines: list[AccountLineDetail] = []
    debit_turnover = credit_turnover = 0
    for line, txn in rows:
        debit, credit = line_dr_cr(line, currency)
        debit_turnover += debit
        credit_turnover += credit
        lines.append(
            AccountLineDetail(
                transaction_date=txn.date,
                reference=txn.reference,
                description=txn.description,
                debit=debit,
                credit=credit,
                line_description=line.line_description,
            )
        )
    net = debit_turnover - credit_turnover
    debit_balance = net if net >= 0 else 0
    credit_balance = -net if net < 0 else 0
    return AccountDetailResponse(
        currency=currency,
        other_currencies=other_currencies,
        account_code=acc.code,
        account_name=acc.name,
        parent_code=(acc.parent.code if acc.parent else None),
        parent_name=(acc.parent.name if acc.parent else None),
        debit_turnover=debit_turnover,
        credit_turnover=credit_turnover,
        debit_balance=debit_balance,
        credit_balance=credit_balance,
        lines=lines,
    )


@router.get("/entities/{entity_id}/transactions", response_model=list[EntityTransactionRead])
def get_entity_transactions(
    entity_id: UUID,
    currency: str | None = Query(None, description="Filter by currency (IRR, USD, etc.)"),
    db: Session = Depends(get_db),
) -> list[EntityTransactionRead]:
    """
    All transactions linked to this entity (e.g. all vouchers with client
    Innotech), as a statement of account: each row carries the entity's own
    Debtor / Creditor movement and the running Remaining balance.
    """
    from app.services.entity_statement import build_entity_statement, control_account_code

    entity = db.get(Entity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    # Linked transactions… and, for a BANK, every journal that touches its GL
    # cash account — an ordinary voucher moves the bank's money without ever
    # linking the bank entity, and hiding those made the list disagree with
    # the balance (the reported bug).
    bank_account_id = None
    if entity.type == "bank" and (entity.code or "").strip():
        acc = db.execute(
            select(Account).where(Account.code == entity.code.strip())
        ).scalars().first()
        bank_account_id = acc.id if acc else None
    link_exists = (
        select(TransactionEntity.id)
        .where(TransactionEntity.transaction_id == Transaction.id,
               TransactionEntity.entity_id == entity_id)
        .exists()
    )
    if bank_account_id is not None:
        line_exists = (
            select(TransactionLine.id)
            .where(TransactionLine.transaction_id == Transaction.id,
                   TransactionLine.account_id == bank_account_id)
            .exists()
        )
        cond = or_(link_exists, line_exists)
    else:
        cond = link_exists
    q = select(Transaction).where(cond, Transaction.deleted_at.is_(None))
    if currency:
        q = q.where(Transaction.currency == currency)
    q = (
        q.distinct()
        .order_by(Transaction.date, Transaction.created_at, Transaction.id)
        .options(
            selectinload(Transaction.lines).selectinload(TransactionLine.account),
            selectinload(Transaction.entity_links).selectinload(TransactionEntity.entity),
            selectinload(Transaction.attachments),
        )
    )
    transactions = db.execute(q).scalars().unique().all()
    movements = build_entity_statement(db, entity, transactions)
    control_code = control_account_code(db, entity)
    out = []
    for t, mv in zip(transactions, movements):
        lines_read = [
            TransactionLineRead(
                id=line.id,
                account_id=line.account_id,
                account_code=line.account.code,
                debit=line.debit,
                credit=line.credit,
                base_debit=line.base_debit,
                base_credit=line.base_credit,
                line_description=line.line_description,
            )
            for line in t.lines
        ]
        out.append(
            EntityTransactionRead(
                id=t.id,
                date=t.date,
                reference=t.reference,
                description=t.description,
                currency=t.currency or "IRR",
                entity_paid=mv.paid,
                entity_received=mv.received,
                entity_balance=mv.balance,
                entity_placed=mv.placed,
                entity_control_account=control_code,
                lines=lines_read,
                entity_links=(
                    [
                        TransactionEntityLinkRead(
                            role=link.role,
                            entity_id=link.entity_id,
                            entity_name=(link.entity.name if link.entity else None),
                            entity_type=(link.entity.type if link.entity else None),
                            amount=link.amount,
                        )
                        for link in (t.entity_links or [])
                    ]
                    + (
                        # bank pulled in via its GL lines (no explicit link):
                        # synthesize a link carrying the bank's own net move so
                        # the "This entity" column shows its share, not the
                        # whole journal total
                        [TransactionEntityLinkRead(
                            role="bank", entity_id=entity_id,
                            entity_name=entity.name, entity_type=entity.type,
                            amount=sum(
                                l.debit - l.credit for l in t.lines
                                if l.account_id == bank_account_id
                            ),
                        )]
                        if bank_account_id is not None
                        and not any(link.entity_id == entity_id for link in (t.entity_links or []))
                        and any(l.account_id == bank_account_id for l in t.lines)
                        else []
                    )
                ),
                attachments=[
                    AttachmentRead(
                        id=a.id,
                        file_name=a.file_name,
                        content_type=a.content_type,
                        size_bytes=a.size_bytes,
                        url=f"/transactions/attachments/{a.id}/file",
                        transaction_id=t.id,
                    )
                    for a in (t.attachments or [])
                ],
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
        )
    return out


import time as _time

_dashboard_cache: dict[str, tuple[float, OwnerDashboardResponse]] = {}
_DASHBOARD_CACHE_TTL = 60  # seconds
# Fewer entries than this in the dashboard window → no risk alerts (see get_owner_dashboard).
MIN_TXNS_FOR_ALERTS = 10


@router.get("/owner-dashboard", response_model=OwnerDashboardResponse)
def get_owner_dashboard(
    currency: str | None = Query(None, description="Filter by currency (IRR, USD, etc.)"),
    db: Session = Depends(get_db),
    months_back: int = 12,
) -> OwnerDashboardResponse:
    currency, other_currencies = resolve_currency_view(db, currency)
    # The cache is process-wide, so the key MUST carry the tenant: without it a
    # company saw another company's dashboard for up to a minute (security
    # review 2026-09-24, C2).
    # The books version is bumped in the same transaction as any ledger or
    # invoice write, so a worker that didn't see the write still misses.
    from app.core.shared_state import books_version, current_scope, platform_version
    scope = current_scope()
    from app.services.calendar_periods import company_calendar as _cal
    cache_key = (f"dashboard:{scope}:{books_version(db, scope)}:{platform_version(db)}:"
                 f"{months_back}:{currency}:{_cal(db)}")
    now = _time.time()
    cached = _dashboard_cache.get(cache_key)
    if cached and (now - cached[0]) < _DASHBOARD_CACHE_TTL:
        return cached[1]

    today = date.today()
    # Chart-of-accounts conventions differ by locale; resolve the cash /
    # receivable / current-liability predicates once up front so the KPI
    # aggregation works for the UK (and any non-Iranian) chart, not just Iran.
    # Months are the company's months: Jalali for an Iranian company (§3.5).
    from app.services.calendar_periods import company_calendar, last_n_months, month_key, month_label
    cal = company_calendar(db)
    locale = get_reporting_locale(db)
    _is_receivable = _receivable_predicate(locale)
    _is_current_liab = _current_liability_predicate(locale)

    cutoff = today - timedelta(days=months_back * 31)
    # Flat column queries instead of an object graph (roadmap §2.6): the
    # selectin loads cost ~130 round trips and 3 s at 20k journals. Every
    # query repeats the window filter as a join so the tenant criteria apply.
    # currency=ALL: every currency at its base value (roadmap §4.6).
    base_view = is_base_view(currency)
    live = (Transaction.date >= cutoff, Transaction.deleted_at.is_(None),
            *(() if base_view else (Transaction.currency == currency,)))
    # what is still owed from before the window: the aging and the liabilities
    # are balances to date, not the year's movements
    before = (Transaction.date < cutoff, Transaction.deleted_at.is_(None),
              *(() if base_view else (Transaction.currency == currency,)))
    dr_col, cr_col = amount_columns(currency)
    # Summed in the database, one row per day / account / party (§2.6); only
    # the journals that move a receivable or a liability come back one by one.
    folds = fold_dashboard(db, live=live, before=before, dr_col=dr_col, cr_col=cr_col,
                           is_receivable=_is_receivable, is_current_liability=_is_current_liab,
                           month_of=lambda d: month_key(d, cal), today=today)
    monthly_revenue, monthly_expense = folds.monthly_revenue, folds.monthly_expense
    expense_by_category, spend_by_vendor = folds.expense_by_category, folds.spend_by_vendor
    profitability, ar_buckets, ap_buckets = folds.profitability, folds.ar_buckets, folds.ap_buckets
    receivable_due_this_week = folds.receivable_due_this_week
    payable_due_this_week = folds.payable_due_this_week
    tax_and_liability_payable = folds.tax_and_liability_payable
    expense_txn_count = folds.expense_txn_count
    expense_txn_with_attachment = folds.expense_txn_with_attachment
    line_count = folds.line_count
    missing_line_desc = folds.missing_line_desc
    missing_reference = folds.missing_reference
    unlinked_entities = folds.unlinked_entities

    # True cash-on-hand: the net balance of every cash/bank account up to
    # today, not just the trailing window scanned above (which is sized for
    # the burn-rate / forecast and would otherwise understate the balance).
    # Shared with the CFO/CEO engine via cash_service so both agree (AI-6).
    cash_on_hand = _cash_on_hand_balance(db, locale=locale, currency=currency, as_of=today)

    current_month = month_key(today, cal)
    monthly_net = monthly_revenue.get(current_month, 0) - monthly_expense.get(current_month, 0)

    # this month and the two before it, in the company's calendar (§3.5)
    recent_months = [p.key for p in last_n_months(today, 3, cal)][::-1]
    burn_values = [monthly_expense.get(m, 0) for m in recent_months]
    burn_rate = int(mean(burn_values)) if burn_values else 0
    runway_months = round(cash_on_hand / burn_rate, 1) if burn_rate > 0 else None

    # The 13 weeks come from the forecast service (roadmap §5.3): open invoices
    # on each customer's learned payment habit, cheques, payroll, recurring
    # flows and the median unscheduled week — the same figures the forecast
    # page and the AI accountant show.
    from app.services.cash_forecast import forecast as _cash_forecast
    # the forecast is per currency; the combined view forecasts the base one
    fc = _cash_forecast(db, today=today, currency=(get_reporting_currency(db) if base_view else currency),
                        locale=locale, opening=cash_on_hand)
    forecast_rows = [
        ForecastRow(week_start=date.fromisoformat(w["week_start"]), projected_inflow=w["inflow"],
                    projected_outflow=w["outflow"], projected_net=w["net"], projected_cash=w["closing"],
                    risk=w["risk"])
        for w in fc["weeks"]
    ]

    ar_rows = []
    for n, b in ar_buckets.items():
        total = b["current"] + b["days_31_60"] + b["days_60_plus"]
        if total > 0:
            ar_rows.append(AgingRow(name=n, current=b["current"], days_31_60=b["days_31_60"], days_60_plus=b["days_60_plus"], total=total))
    ar_rows.sort(key=lambda r: (-r.total, r.name))

    ap_rows = []
    for n, b in ap_buckets.items():
        total = b["current"] + b["days_31_60"] + b["days_60_plus"]
        if total > 0:
            ap_rows.append(AgingRow(name=n, current=b["current"], days_31_60=b["days_31_60"], days_60_plus=b["days_60_plus"], total=total))
    ap_rows.sort(key=lambda r: (-r.total, r.name))

    expense_rows = [ExpenseCategoryRow(category=k, amount=v) for k, v in sorted(expense_by_category.items(), key=lambda x: (-x[1], x[0]))[:8]]
    vendor_rows = [VendorSpendRow(vendor=k, amount=v) for k, v in sorted(spend_by_vendor.items(), key=lambda x: (-x[1], x[0]))[:8]]

    series_keys = sorted(set(monthly_revenue.keys()) | set(monthly_expense.keys()))
    monthly_expense_series = [MonthlySeriesRow(period=m, value=monthly_expense.get(m, 0), label=month_label(m))
                              for m in series_keys[-12:]]

    profitability_rows: list[ProfitabilityRow] = []
    for client, vals in profitability.items():
        rev = vals["revenue"]
        cost = vals["cost"]
        profit = rev - cost
        margin = round((profit / rev) * 100.0, 2) if rev > 0 else None
        profitability_rows.append(ProfitabilityRow(client=client, revenue=rev, cost=cost, profit=profit, margin_pct=margin))
    profitability_rows.sort(key=lambda r: (-r.profit, r.client))

    txn_count = folds.txn_count or 1
    line_count = line_count or 1
    missing_attachments_on_expense = max(0, expense_txn_count - expense_txn_with_attachment)
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
    # Minimum activity before the alarms fire: a fresh company with three tiny
    # vouchers is empty, not "at risk" (QA 2026-09-24 6.1). Runway / spike need
    # two months of expenses and a real sample of entries; the quality score
    # needs a sample to be a score at all.
    months_with_expenses = sum(1 for m in recent_months if monthly_expense.get(m, 0) > 0)
    enough_history = folds.txn_count >= MIN_TXNS_FOR_ALERTS and months_with_expenses >= 2
    if runway_months is not None and runway_months < 3 and enough_history:
        alerts.append(AlertItem(level="high", title="Cash runway is short", message=f"Estimated runway is {runway_months} months based on recent burn rate."))
    if overdue_ar > 0:
        alerts.append(AlertItem(level="medium", title="Overdue receivables", message=f"Overdue AR is {overdue_ar:,}. Follow up collections."))
    if overdue_ap > 0:
        alerts.append(AlertItem(level="medium", title="Overdue payables", message=f"Overdue AP is {overdue_ap:,}. Plan vendor payments."))
    if health_score < 70 and folds.txn_count >= MIN_TXNS_FOR_ALERTS:
        alerts.append(AlertItem(level="medium", title="Book quality risk", message=f"Data quality score is {health_score}/100. Resolve missing references/entities/attachments."))
    if burn_rate > 0 and monthly_expense.get(current_month, 0) > int(burn_rate * 1.5) and enough_history:
        alerts.append(AlertItem(level="low", title="Expense spike", message="This month expenses are significantly above recent average."))

    close_checklist = [
        HealthChecklistItem(item="References captured", ok=(missing_reference / txn_count) < 0.2, detail=f"{txn_count - missing_reference}/{txn_count} transactions have reference."),
        HealthChecklistItem(item="Entities linked", ok=(unlinked_entities / txn_count) < 0.2, detail=f"{txn_count - unlinked_entities}/{txn_count} transactions have entity links."),
        HealthChecklistItem(item="Expense attachments available", ok=(missing_attachments_on_expense / max(1, expense_txn_count)) < 0.4, detail=f"{expense_txn_with_attachment}/{max(1, expense_txn_count)} expense transactions have attachments."),
        HealthChecklistItem(item="Line descriptions complete", ok=(missing_line_desc / line_count) < 0.25, detail=f"{line_count - missing_line_desc}/{line_count} lines have descriptions."),
    ]

    # Label monetary KPIs with the actual reporting currency (GBP for the UK
    # locale, IRR for Iran, etc.) instead of hardcoding IRR. When the caller
    # filters by an explicit currency, honour that; otherwise fall back to the
    # company's reporting-currency setting.
    display_currency = get_reporting_currency(db) if base_view else currency

    kpis = [
        KpiCard(key="cash_on_hand", label="Cash on hand", value=cash_on_hand, unit=display_currency),
        KpiCard(key="monthly_net_profit", label="Monthly net profit", value=monthly_net, unit=display_currency),
        KpiCard(key="burn_rate", label="Monthly burn rate", value=burn_rate, unit=display_currency),
        KpiCard(key="runway_months", label="Runway", value=(runway_months if runway_months is not None else -1), unit="months"),
        KpiCard(key="ar_due_week", label="AR due in ~7 days", value=receivable_due_this_week, unit=display_currency),
        KpiCard(key="ap_due_week", label="AP due in ~7 days", value=payable_due_this_week, unit=display_currency),
        KpiCard(key="tax_and_liability_payable", label="Liabilities payable (21xx)", value=max(0, tax_and_liability_payable), unit=display_currency),
    ]

    top_profit = profitability_rows[0] if profitability_rows else None
    owner_pack = (
        f"# Owner Weekly Pack ({today.isoformat()})\n\n"
        f"- Cash on hand: {cash_on_hand:,} {display_currency}\n"
        f"- Net profit this month: {monthly_net:,} {display_currency}\n"
        f"- Burn rate: {burn_rate:,} {display_currency}/month\n"
        f"- Runway: {runway_months if runway_months is not None else 'N/A'} months\n"
        f"- Overdue AR: {overdue_ar:,} {display_currency}\n"
        f"- Overdue AP: {overdue_ap:,} {display_currency}\n"
        f"- Data health score: {health_score}/100\n"
        f"- Most profitable client: {(top_profit.client + ' (' + format(top_profit.profit, ',') + ' ' + display_currency + ')') if top_profit else 'N/A'}\n\n"
        f"## Priority Actions\n"
        f"1. Collect overdue receivables and monitor top debtor clients.\n"
        f"2. Review expense spikes and highest vendor/category spend.\n"
        f"3. Improve bookkeeping hygiene (references, entity links, attachments).\n"
    )

    result = OwnerDashboardResponse(
        currency=currency,
        other_currencies=other_currencies,
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
    _dashboard_cache[cache_key] = (_time.time(), result)
    return result


def invalidate_dashboard_cache() -> None:
    """Call after transaction create/update/delete to clear cached dashboard."""
    _dashboard_cache.clear()


class _ForecastDelay(BaseModel):
    entity_id: UUID
    days: int = Field(..., ge=-90, le=365)


class _ForecastOneOff(BaseModel):
    on: date
    amount: int = Field(..., ge=-10**15, le=10**15)
    label: str | None = Field(None, max_length=120)


class CashForecastScenarioIn(BaseModel):
    currency: str | None = Field(None, max_length=8)
    weeks: int = Field(13, ge=1, le=26)
    bounce_commitments: list[UUID] = Field(default_factory=list, max_length=200)
    skip_invoices: list[UUID] = Field(default_factory=list, max_length=200)
    delays: list[_ForecastDelay] = Field(default_factory=list, max_length=100)
    one_offs: list[_ForecastOneOff] = Field(default_factory=list, max_length=50)


@router.get("/cash-forecast")
def get_cash_forecast(
    currency: str | None = Query(None, max_length=8),
    weeks: int = Query(13, ge=1, le=26),
    db: Session = Depends(get_db),
) -> dict:
    """The 13-week cash forecast (roadmap §5.3): scheduled flows on their
    learned dates plus the usual unscheduled week, and the lowest point."""
    from app.services.cash_forecast import forecast
    return forecast(db, currency=currency, weeks=weeks)


@router.post("/cash-forecast/scenario")
def post_cash_forecast_scenario(body: CashForecastScenarioIn, db: Session = Depends(get_db)) -> dict:
    """What if: cheques that bounce, invoices never paid, parties paying later,
    one-off flows. Returns the base and the scenario side by side. Read-only."""
    from app.services.cash_forecast import compare, resolve_scenario
    scenario, notes = resolve_scenario(
        db, bounce_ids=body.bounce_commitments, skip_invoice_ids=body.skip_invoices,
        delays=[{"entity_id": d.entity_id, "days": d.days} for d in body.delays],
        one_offs=[{"date": o.on.isoformat(), "amount": o.amount, "label": o.label} for o in body.one_offs],
    )
    out = compare(db, scenario, currency=body.currency, weeks=body.weeks)
    out["scenario_notes"] = notes
    return out


@router.get("/tax-summary")
def get_tax_summary(
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """VAT / sales-tax summary for a period: output tax (sales), input tax
    (purchases) and net tax = output − input, with the rate assumptions used
    and an estimate caveat. Defaults to the current calendar quarter."""
    from app.services.tax_service import compute_tax_summary

    today = date.today()
    if to_date is None:
        to_date = today
    if from_date is None:
        # Start of the current calendar quarter.
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        from_date = date(today.year, q_start_month, 1)
    return compute_tax_summary(db, from_date, to_date, currency=currency)


def _tax_rate_read(r) -> dict:
    return {
        "id": str(r.id),
        "code": r.code,
        "jurisdiction": r.jurisdiction,
        "description": r.description,
        "rate": float(r.rate or 0),
        "effective_from": r.effective_from.isoformat(),
        "effective_to": r.effective_to.isoformat() if r.effective_to else None,
    }


@router.get("/tax-rates")
def list_tax_rates_endpoint(code: str | None = Query(None), db: Session = Depends(get_db)) -> list[dict]:
    """All effective-dated tax rates (optionally filtered to one code), so the
    UI can show and manage the rate history."""
    from app.services.tax_rate_service import list_tax_rates
    return [_tax_rate_read(r) for r in list_tax_rates(db, code)]


@router.get("/tax-rates/effective")
def effective_tax_rate(code: str = Query(...), on: date = Query(...),
                       db: Session = Depends(get_db)) -> dict:
    """The rate in effect for ``code`` on date ``on`` (§7.6) — used by the
    invoice UI to auto-fill the line rate as of the invoice date."""
    from app.services.tax_rate_service import tax_rate_for
    rate = tax_rate_for(db, code, on)
    return {"code": code, "on": on.isoformat(), "rate": rate}


class TaxRateUpsert(BaseModel):
    code: str
    jurisdiction: str
    rate: float = Field(..., ge=0, le=100)
    effective_from: date
    effective_to: date | None = None
    description: str | None = None


@router.post("/tax-rates", status_code=201)
def upsert_tax_rate(payload: TaxRateUpsert, db: Session = Depends(get_db)) -> dict:
    """Add or update an effective-dated rate (admin). Matched by code +
    effective_from so re-saving a window updates it in place."""
    from app.models.tax_rate import TaxRate
    from app.services.audit_service import log_audit_event

    row = db.execute(
        select(TaxRate).where(
            TaxRate.code == payload.code.strip(),
            TaxRate.effective_from == payload.effective_from,
        )
    ).scalar_one_or_none()
    if row is None:
        row = TaxRate(code=payload.code.strip(), effective_from=payload.effective_from)
        db.add(row)
    row.jurisdiction = payload.jurisdiction.strip().upper()
    row.rate = float(payload.rate)
    row.effective_to = payload.effective_to
    row.description = (payload.description or "").strip() or None
    log_audit_event(db, action="upsert", entity_type="tax_rate", entity_id=payload.code,
                    detail=f"Tax rate {payload.code} {payload.rate}% from {payload.effective_from}")
    db.commit()
    db.refresh(row)
    return _tax_rate_read(row)


@router.get("/missing-references", response_model=MissingReferenceResponse)
def get_missing_references(
    currency: str | None = Query(None, description="Filter by currency (IRR, USD, etc.)"),
    db: Session = Depends(get_db),
) -> MissingReferenceResponse:
    q = select(Transaction).where((Transaction.reference.is_(None)) | (Transaction.reference == ""))
    if currency:
        q = q.where(Transaction.currency == currency)
    rows = db.execute(q.order_by(Transaction.date.desc())).scalars().all()
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


@router.get("/transactions/search", response_model=TransactionSearchResponse)
def search_transactions(
    account_code: str | None = Query(None, description="Exact account code"),
    account_code_prefix: str | None = Query(None, description="Comma-separated account code prefixes (e.g. 41,42,43)"),
    month: str | None = Query(None, description="YYYY-MM to filter by month"),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    search: str | None = Query(None, description="Text search in description/reference"),
    entity_name: str | None = Query(None, description="Filter by linked entity name"),
    currency: str | None = Query(None, description="Filter by currency (IRR, USD, etc.)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort_by: str = Query("date", pattern="^(date|account_code|debit|credit|reference)$"),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
) -> TransactionSearchResponse:
    """Flexible transaction search for drill-down views."""
    q = (
        select(TransactionLine, Transaction, Account)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .where(Transaction.deleted_at.is_(None))
    )

    base_view = is_base_view(currency)          # every currency, at base value
    if base_view:
        from app.services.fx_base import base_currency
        base_ccy = base_currency(db)
    q = _currency_filter(q, currency)

    if account_code:
        q = q.where(Account.code == account_code)

    if account_code_prefix:
        prefixes = [p.strip() for p in account_code_prefix.split(",") if p.strip()]
        if prefixes:
            q = q.where(or_(*[Account.code.startswith(p) for p in prefixes]))

    if month:
        try:
            y, m = month.split("-")
            month_start = date(int(y), int(m), 1)
            if int(m) == 12:
                month_end = date(int(y) + 1, 1, 1)
            else:
                month_end = date(int(y), int(m) + 1, 1)
            q = q.where(Transaction.date >= month_start, Transaction.date < month_end)
        except (ValueError, IndexError):
            pass

    if from_date:
        q = q.where(Transaction.date >= from_date)
    if to_date:
        q = q.where(Transaction.date <= to_date)

    if search:
        from app.utils.text import fold_fa, fold_sql
        term = f"%{fold_fa(search)}%"
        q = q.where(
            or_(
                fold_sql(Transaction.description).ilike(term),
                Transaction.reference.ilike(term),
                fold_sql(TransactionLine.line_description).ilike(term),
            )
        )

    # Entity name filter via subquery
    from app.utils.text import fold_fa, fold_sql
    if entity_name:
        entity_sub = (
            select(TransactionEntity.transaction_id)
            .join(Entity, TransactionEntity.entity_id == Entity.id)
            .where(fold_sql(Entity.name).ilike(f"%{fold_fa(entity_name)}%"))
        ).scalar_subquery()
        q = q.where(Transaction.id.in_(
            select(TransactionEntity.transaction_id)
            .join(Entity, TransactionEntity.entity_id == Entity.id)
            .where(fold_sql(Entity.name).ilike(f"%{fold_fa(entity_name)}%"))
        ))

    # Count total before pagination
    count_q = select(func.count()).select_from(q.subquery())
    total_count = db.execute(count_q).scalar() or 0

    # Sorting
    sort_col_map = {
        "date": Transaction.date,
        "account_code": Account.code,
        "debit": amount_columns(currency)[0],
        "credit": amount_columns(currency)[1],
        "reference": Transaction.reference,
    }
    sort_col = sort_col_map.get(sort_by, Transaction.date)
    if sort_dir == "desc":
        q = q.order_by(sort_col.desc(), Transaction.id)
    else:
        q = q.order_by(sort_col.asc(), Transaction.id)

    # Pagination
    offset = (page - 1) * page_size
    q = q.offset(offset).limit(page_size)

    results = db.execute(q).all()

    # Collect transaction IDs to batch-load entity names
    txn_ids = list({r[1].id for r in results})
    entity_map: dict[UUID, list[str]] = defaultdict(list)
    if txn_ids:
        te_rows = db.execute(
            select(TransactionEntity.transaction_id, Entity.name)
            .join(Entity, TransactionEntity.entity_id == Entity.id)
            .where(TransactionEntity.transaction_id.in_(txn_ids))
        ).all()
        for tid, ename in te_rows:
            entity_map[tid].append(ename)

    rows: list[TransactionSearchRow] = []
    total_debit = 0
    total_credit = 0
    for line, txn, acc in results:
        debit, credit = line_dr_cr(line, currency)
        total_debit += debit
        total_credit += credit
        rows.append(TransactionSearchRow(
            transaction_id=txn.id,
            date=txn.date,
            reference=txn.reference,
            description=txn.description,
            currency=(base_ccy if base_view else (getattr(txn, "currency", None) or "IRR")),
            account_code=acc.code,
            account_name=acc.name,
            debit=debit,
            credit=credit,
            line_description=line.line_description,
            entity_names=entity_map.get(txn.id, []),
        ))

    return TransactionSearchResponse(
        rows=rows,
        total_count=total_count,
        page=page,
        page_size=page_size,
        total_debit=total_debit,
        total_credit=total_credit,
    )

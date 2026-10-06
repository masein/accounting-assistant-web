from __future__ import annotations

import io
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.http_headers import content_disposition
from app.core.messages import said
from app.core.spreadsheet import CSV_MEDIA_TYPE, csv_bytes, csv_writer
from app.api.transactions import _create_transaction_from_payload
from app.db.session import get_db
from app.models.transaction import Transaction, TransactionLine
from app.schemas.iran_statement import (
    IranBalanceSheetResponse,
    IranCashFlowResponse,
    IranChangesInEquityResponse,
    IranComprehensiveIncomeResponse,
    IranIncomeStatementResponse,
)
from app.schemas.uk_statement import (
    UKBalanceSheetResponse,
    UKCashFlowResponse,
    UKChangesInEquityResponse,
    UKComprehensiveIncomeResponse,
    UKIncomeStatementResponse,
)
from app.services.reporting.uk_statement_service import UKStatementService
from app.schemas.manager_report import (
    AccountLedgerResponse,
    BalanceSheetResponse,
    CashBankStatementResponse,
    CashFlowResponse,
    DebtorCreditorResponse,
    IncomeStatementResponse,
    InventoryBalanceResponse,
    InventoryItemCreate,
    InventoryItemUpdate,
    InventoryItemRead,
    InventoryMovementCreate,
    InventoryMovementRead,
    InventoryMovementResponse,
    JournalEntryRead,
    PaginatedJournalResponse,
    PersonRunningBalanceResponse,
    SalesPurchaseReportResponse,
    TrialBalanceResponse,
)
from app.schemas.transaction import TransactionCreate, TransactionRead, TransactionUpdate
from app.services.reporting.cash_flow_service import CashFlowService
from app.services.reporting.financial_statement_service import FinancialStatementService
from app.services.reporting.iran_statement_service import IranStatementService
from app.services.reporting.inventory_report_service import InventoryReportService
from app.services.reporting.ledger_service import LedgerService
from app.services.reporting.operations_report_service import OperationsReportService
from app.services.reporting.sales_report_service import SalesReportService

router = APIRouter(prefix="/manager-reports", tags=["manager-reports"])


def _csv_response(filename: str, headers: list[str], rows: list[list[str | int | float]]) -> Response:
    buf = io.StringIO()
    w = csv_writer(buf)
    w.writerow(headers)
    for row in rows:
        w.writerow(row)
    return Response(
        content=csv_bytes(buf),
        media_type=CSV_MEDIA_TYPE,
        headers={"Content-Disposition": content_disposition(filename)},
    )


def _format_or_json(fmt: str) -> str:
    f = (fmt or "json").strip().lower()
    if f not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="format must be json or csv")
    return f


@router.get("/financial/balance-sheet", response_model=BalanceSheetResponse)
def balance_sheet(
    to_date: date | None = Query(None),
    comparative_to_date: date | None = Query(None),
    currency: str | None = Query(None, description="Filter by currency (IRR, USD, etc.)"),
    request: Request = None,
    db: Session = Depends(get_db),
) -> BalanceSheetResponse:
    svc = FinancialStatementService(db)
    # the analysis's warnings in the page's language (#53)
    return said(svc.balance_sheet(to_date=to_date, comparative_to_date=comparative_to_date, currency=currency), request)


@router.get("/financial/income-statement", response_model=IncomeStatementResponse)
def income_statement(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    currency: str | None = Query(None),
    request: Request = None,
    db: Session = Depends(get_db),
) -> IncomeStatementResponse:
    svc = FinancialStatementService(db)
    return said(svc.income_statement(from_date=from_date, to_date=to_date, currency=currency), request)


@router.get("/financial/iran/income-statement", response_model=IranIncomeStatementResponse)
def iran_income_statement(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    comparative_from_date: date | None = Query(None),
    comparative_to_date: date | None = Query(None),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> IranIncomeStatementResponse:
    """Income Statement in the Iranian standard format (صورت سود و زیان).

    The prior period defaults to the same window shifted one year earlier
    if `comparative_from_date` and `comparative_to_date` are not provided.
    """
    svc = IranStatementService(db)
    return svc.income_statement(
        from_date=from_date,
        to_date=to_date,
        comparative_from_date=comparative_from_date,
        comparative_to_date=comparative_to_date,
        currency=currency,
    )


@router.get("/financial/iran/balance-sheet", response_model=IranBalanceSheetResponse)
def iran_balance_sheet(
    as_of: date | None = Query(None),
    comparative_as_of: date | None = Query(None),
    comparative_beginning_as_of: date | None = Query(
        None,
        description="Restated opening balance date for the prior period (third date column). Defaults to one year before comparative_as_of.",
    ),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> IranBalanceSheetResponse:
    """Balance Sheet in the Iranian standard format (صورت وضعیت مالی).

    Three date columns per the audited Iranian template: current period,
    prior period, and the restated opening balance of the prior period.
    Comparative defaults: one year back; opening defaults: two years back.
    """
    svc = IranStatementService(db)
    return svc.balance_sheet(
        as_of=as_of,
        comparative_as_of=comparative_as_of,
        comparative_beginning_as_of=comparative_beginning_as_of,
        currency=currency,
    )


@router.get("/financial/iran/changes-in-equity", response_model=IranChangesInEquityResponse)
def iran_changes_in_equity(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    comparative_from_date: date | None = Query(None),
    comparative_to_date: date | None = Query(None),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> IranChangesInEquityResponse:
    """Statement of Changes in Equity (صورت تغییرات در حقوق مالکانه).

    Two-period matrix per the audited Iranian template: comparative-period
    movements are emitted first (opening, restatement rows, sub-period
    header, year's events), then the bridging opening of the current
    period and the same set of rows for the current year.

    Auto-populated rows: opening / restated-opening / period net profit /
    closing. Movement rows such as dividends, capital increases, capital
    increase in progress, treasury buy/sell, transfers, reserve
    allocations, and error/policy restatements remain zero until those
    events are tagged explicitly on transactions.
    """
    svc = IranStatementService(db)
    return svc.changes_in_equity(
        from_date=from_date,
        to_date=to_date,
        comparative_from_date=comparative_from_date,
        comparative_to_date=comparative_to_date,
        currency=currency,
    )


@router.get("/financial/iran/comprehensive-income", response_model=IranComprehensiveIncomeResponse)
def iran_comprehensive_income(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    comparative_from_date: date | None = Query(None),
    comparative_to_date: date | None = Query(None),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> IranComprehensiveIncomeResponse:
    """صورت سود و زیان جامع — starts from net profit and lists the
    non-reclassifiable and reclassifiable OCI items prescribed by the
    Iranian standard. OCI items stay zero until their underlying
    movements (revaluation, FX, …) are tagged explicitly on transactions.
    """
    svc = IranStatementService(db)
    return svc.comprehensive_income(
        from_date=from_date,
        to_date=to_date,
        comparative_from_date=comparative_from_date,
        comparative_to_date=comparative_to_date,
        currency=currency,
    )


@router.get("/financial/iran/cash-flow", response_model=IranCashFlowResponse)
def iran_cash_flow(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    comparative_from_date: date | None = Query(None),
    comparative_to_date: date | None = Query(None),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> IranCashFlowResponse:
    """صورت جریان‌های نقدی — Iranian-template cash flow. Rows are the
    prescribed operating/investing/financing line items; movements are
    classified by the primary counterparty account prefix on each
    cash-touching transaction.
    """
    svc = IranStatementService(db)
    return svc.cash_flow(
        from_date=from_date,
        to_date=to_date,
        comparative_from_date=comparative_from_date,
        comparative_to_date=comparative_to_date,
        currency=currency,
    )


# ---------------------------------------------------------------------------
# UK FRS 102 Section 1A statements
# ---------------------------------------------------------------------------


@router.get("/financial/uk/balance-sheet", response_model=UKBalanceSheetResponse)
def uk_balance_sheet(
    as_of: date | None = Query(None),
    comparative_as_of: date | None = Query(None),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> UKBalanceSheetResponse:
    """Statement of Financial Position in the FRS 102 Section 1A
    (Companies Act 2006 format 1) layout.

    Comparative date defaults to one year before `as_of` if not provided.
    """
    return UKStatementService(db).balance_sheet(
        as_of=as_of, comparative_as_of=comparative_as_of, currency=currency,
    )


@router.get("/financial/uk/profit-and-loss", response_model=UKIncomeStatementResponse)
def uk_profit_and_loss(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    comparative_from_date: date | None = Query(None),
    comparative_to_date: date | None = Query(None),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> UKIncomeStatementResponse:
    """Profit and Loss Account (FRS 102 1A, format 1, by function)."""
    return UKStatementService(db).income_statement(
        from_date=from_date, to_date=to_date,
        comparative_from_date=comparative_from_date,
        comparative_to_date=comparative_to_date,
        currency=currency,
    )


@router.get("/financial/uk/comprehensive-income", response_model=UKComprehensiveIncomeResponse)
def uk_comprehensive_income(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    comparative_from_date: date | None = Query(None),
    comparative_to_date: date | None = Query(None),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> UKComprehensiveIncomeResponse:
    """Statement of Comprehensive Income — net profit plus the OCI section."""
    return UKStatementService(db).comprehensive_income(
        from_date=from_date, to_date=to_date,
        comparative_from_date=comparative_from_date,
        comparative_to_date=comparative_to_date,
        currency=currency,
    )


@router.get("/financial/uk/changes-in-equity", response_model=UKChangesInEquityResponse)
def uk_changes_in_equity(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    comparative_from_date: date | None = Query(None),
    comparative_to_date: date | None = Query(None),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> UKChangesInEquityResponse:
    """Statement of Changes in Equity — two-period matrix per FRS 102."""
    return UKStatementService(db).changes_in_equity(
        from_date=from_date, to_date=to_date,
        comparative_from_date=comparative_from_date,
        comparative_to_date=comparative_to_date,
        currency=currency,
    )


@router.get("/financial/uk/cash-flow", response_model=UKCashFlowResponse)
def uk_cash_flow(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    comparative_from_date: date | None = Query(None),
    comparative_to_date: date | None = Query(None),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> UKCashFlowResponse:
    """Statement of Cash Flows (FRS 102 Section 7)."""
    return UKStatementService(db).cash_flow(
        from_date=from_date, to_date=to_date,
        comparative_from_date=comparative_from_date,
        comparative_to_date=comparative_to_date,
        currency=currency,
    )


@router.get("/financial/cash-flow", response_model=CashFlowResponse)
def cash_flow_statement(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    currency: str | None = Query(None),
    request: Request = None,
    db: Session = Depends(get_db),
) -> CashFlowResponse:
    svc = CashFlowService(db)
    return said(svc.statement(from_date=from_date, to_date=to_date, currency=currency), request)


@router.get("/financial/export")
def export_statements(
    format: str = Query("pdf", pattern="^(pdf|xlsx)$"),
    statements: str | None = Query(None, description="Comma-separated: balance_sheet,income_statement,comprehensive_income,changes_in_equity,cash_flow — default all the locale has"),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    currency: str | None = Query(None),
    lang: str | None = Query(None, pattern="^(fa|en)$"),
    db: Session = Depends(get_db),
):
    """The financial statements as a PDF (one per page, under the letterhead)
    or an Excel workbook (a sheet each), made on the server (roadmap §4.9)."""
    from fastapi.responses import Response

    from app.services.documents.branding import build_brand
    from app.services.reporting.statement_export import ExportError, filename, render_pdf, render_xlsx, tables
    wanted = [s.strip() for s in statements.split(",") if s.strip()] if statements else None
    try:
        tabs, meta = tables(db, statements=wanted, from_date=from_date, to_date=to_date, currency=currency, lang=lang)
    except ExportError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if format == "pdf":
        body, media = render_pdf(db, tabs, meta), "application/pdf"
    else:
        body = render_xlsx(tabs, meta, company=build_brand(db)["issuer"]["name"])
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": content_disposition(filename(meta, "financial-statements", format))})


def _close_month(db: Session, month: str | None, lang: str | None, *, documents: bool = True):
    from app.services.reporting.close_pack import ExportError, resolve_month
    try:
        return resolve_month(db, month, lang, documents=documents)
    except ExportError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/close-pack/checklist")
def close_pack_checklist(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM in the company's calendar; default last month"),
    lang: str | None = Query(None, pattern="^(fa|en|es|ar)$"),
    db: Session = Depends(get_db),
) -> dict:
    """What the month-end close still needs, before the pack is downloaded (roadmap §4.9)."""
    from app.services.reporting.close_pack import checklist, recent_months, summary
    m = _close_month(db, month, lang, documents=False)
    items = checklist(db, m)
    return {"month": m.key, "label": m.label, "from_date": m.start.isoformat(), "to_date": m.end.isoformat(),
            "lang": m.lang, "items": items, "summary": summary(items, m.lang),
            "open": sum(1 for i in items if i["state"] == "warn"), "months": recent_months(db, m.lang)}


@router.get("/close-pack")
def close_pack(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    format: str = Query("zip", pattern="^(zip|pdf|xlsx)$"),
    lang: str | None = Query(None, pattern="^(fa|en|es|ar)$"),
    db: Session = Depends(get_db),
):
    """The monthly close pack: a ZIP of the PDF (checklist, statements, trial
    balance, aging, bank reconciliation, budgets), the same as a workbook, and
    the month's journal as CSV — or just the PDF or the workbook."""
    from fastapi.responses import Response

    from app.services.reporting import close_pack as cp
    m = _close_month(db, month, lang)
    if format == "zip":
        body, media = cp.zip_pack(db, m), "application/zip"
    elif format == "pdf":
        body, media = cp.pdf(db, m), "application/pdf"
    else:
        body, media = cp.xlsx(db, m), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": content_disposition(f'close-pack-{m.key}.{format}')})


@router.get("/financial/cash-flow-periods")
def cash_flow_periods(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    granularity: str = Query("monthly", pattern="^(weekly|monthly|quarterly|seasonal)$"),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    svc = CashFlowService(db)
    return svc.cash_flow_periods(from_date=from_date, to_date=to_date, granularity=granularity, currency=currency)


@router.get("/accounts/list")
def accounts_list(db: Session = Depends(get_db)):
    """Return all non-group accounts (code + name) for search/autocomplete."""
    from app.models.account import Account
    accs = db.execute(select(Account).where(Account.level != "GROUP").order_by(Account.code)).scalars().all()
    return [{"code": a.code, "name": a.name} for a in accs]


@router.get("/books/general-journal", response_model=PaginatedJournalResponse)
def general_journal(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    format: str = Query("json"),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> PaginatedJournalResponse | Response:
    svc = LedgerService(db)
    rep = svc.general_journal(from_date=from_date, to_date=to_date, page=page, page_size=page_size, currency=currency)
    if _format_or_json(format) == "json":
        return rep
    rows: list[list[str | int | float]] = []
    for item in rep.items:
        for ln in item.lines:
            rows.append(
                [
                    str(item.transaction_id),
                    item.date.isoformat(),
                    item.reference or "",
                    item.description or "",
                    ln.account_code,
                    ln.account_name,
                    ln.debit,
                    ln.credit,
                    ln.line_description or "",
                ]
            )
    return _csv_response(
        "general-journal.csv",
        ["transaction_id", "date", "reference", "description", "account_code", "account_name", "debit", "credit", "line_description"],
        rows,
    )


@router.get("/books/general-ledger", response_model=TrialBalanceResponse)
def general_ledger(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=1000),
    format: str = Query("json"),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> TrialBalanceResponse | Response:
    svc = LedgerService(db)
    rep = svc.general_ledger(from_date=from_date, to_date=to_date, page=page, page_size=page_size, currency=currency)
    if _format_or_json(format) == "json":
        return rep
    return _csv_response(
        "general-ledger.csv",
        ["account_code", "account_name", "debit_turnover", "credit_turnover", "debit_balance", "credit_balance"],
        [
            [r.account_code, r.account_name, r.debit_turnover, r.credit_turnover, r.debit_balance, r.credit_balance]
            for r in rep.rows
        ],
    )


@router.get("/books/account-ledger/{account_code}", response_model=AccountLedgerResponse)
def account_ledger(
    account_code: str,
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    format: str = Query("json"),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> AccountLedgerResponse | Response:
    svc = LedgerService(db)
    rep = svc.account_ledger(account_code=account_code, from_date=from_date, to_date=to_date, page=page, page_size=page_size, currency=currency)
    if _format_or_json(format) == "json":
        return rep
    return _csv_response(
        f"account-ledger-{account_code}.csv",
        ["date", "transaction_id", "reference", "description", "debit", "credit", "running_balance", "line_description"],
        [
            [r.date.isoformat(), str(r.transaction_id), r.reference or "", r.description or "", r.debit, r.credit, r.running_balance, r.line_description or ""]
            for r in rep.items
        ],
    )


@router.get("/books/trial-balance", response_model=TrialBalanceResponse)
def trial_balance(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=1000),
    format: str = Query("json"),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> TrialBalanceResponse | Response:
    svc = LedgerService(db)
    rep = svc.trial_balance(from_date=from_date, to_date=to_date, page=page, page_size=page_size, currency=currency)
    if _format_or_json(format) == "json":
        return rep
    return _csv_response(
        "trial-balance.csv",
        ["account_code", "account_name", "debit_turnover", "credit_turnover", "debit_balance", "credit_balance"],
        [
            [r.account_code, r.account_name, r.debit_turnover, r.credit_turnover, r.debit_balance, r.credit_balance]
            for r in rep.rows
        ],
    )


@router.get("/operational/debtor-creditor", response_model=DebtorCreditorResponse)
def debtor_creditor(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> DebtorCreditorResponse:
    svc = OperationsReportService(db)
    return svc.debtor_creditor(from_date=from_date, to_date=to_date, currency=currency)


def _open_invoice_aging(db: Session, kind: str, from_date, to_date) -> dict:
    """AR (kind='sales') / AP (kind='purchase') aging by OPEN balance, not
    gross. balance_due = amount − payments − reduction credit notes; only
    invoices with a positive open balance appear."""
    from app.models.invoice import Invoice
    from app.models.entity import Entity
    from app.api.invoices import _invoice_totals

    # Aging is a snapshot AS OF a date: every open invoice issued on or before
    # it, however old. (It used to default to invoices issued this month, so
    # last month's overdue invoice vanished from receivables.) Drafts are not
    # owed yet and never appear. ``from_date`` narrows by issue date only when
    # given explicitly.
    as_of = to_date or date.today()
    conditions = [
        Invoice.kind == kind,
        Invoice.issue_date <= as_of,
        Invoice.status.in_(["issued", "partially_paid"]),
    ]
    if from_date is not None:
        conditions.append(Invoice.issue_date >= from_date)
    invoices = db.execute(
        select(Invoice).where(*conditions).order_by(Invoice.due_date.asc())
    ).scalars().all()

    entity_ids = [inv.entity_id for inv in invoices if inv.entity_id]
    entities = {}
    if entity_ids:
        ents = db.execute(select(Entity).where(Entity.id.in_(entity_ids))).scalars().all()
        entities = {e.id: e for e in ents}

    party_key = "vendor" if kind == "purchase" else "customer"
    items = []
    total = 0
    for inv in invoices:
        paid, credited, balance_due = _invoice_totals(db, inv)
        if balance_due <= 0:
            continue
        total += balance_due
        days_overdue = (as_of - inv.due_date).days if inv.due_date and inv.due_date < as_of else 0
        items.append({
            "invoice_id": str(inv.id),
            "invoice_number": inv.number,
            party_key: entities.get(inv.entity_id).name if inv.entity_id and entities.get(inv.entity_id) else "Unknown",
            "amount": int(inv.amount or 0),
            "amount_paid": paid,
            "credited": credited,
            "balance_due": balance_due,
            "issue_date": inv.issue_date.isoformat() if inv.issue_date else None,
            "due_date": inv.due_date.isoformat() if inv.due_date else None,
            "status": inv.status,
            "days_overdue": days_overdue,
            # Buckets by days past due (1–30 used to be labelled "31-60").
            "aging_bucket": ("current" if days_overdue <= 0 else "1-30" if days_overdue <= 30
                             else "31-60" if days_overdue <= 60 else "61-90" if days_overdue <= 90 else "90+"),
        })

    return {
        "report_type": "accounts_payable" if kind == "purchase" else "accounts_receivable",
        "period": {"from_date": from_date.isoformat() if from_date else None, "to_date": as_of.isoformat()},
        "as_of": as_of.isoformat(),
        "items": items,
        "total": total,
        "count": len(items),
    }


@router.get("/operational/accounts-payable")
def accounts_payable(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """Accounts payable aging by open balance (purchase invoices/bills)."""
    return _open_invoice_aging(db, "purchase", from_date, to_date)


@router.get("/operational/accounts-receivable")
def accounts_receivable(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """Accounts receivable aging by open balance (sales invoices)."""
    return _open_invoice_aging(db, "sales", from_date, to_date)


@router.get("/operational/person-running-balance", response_model=PersonRunningBalanceResponse)
def person_running_balance(
    entity_id: UUID = Query(...),
    role: str = Query(..., pattern="^(client|supplier|payee|bank|shareholder)$"),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> PersonRunningBalanceResponse:
    svc = OperationsReportService(db)
    return svc.person_running_balance(entity_id=entity_id, role=role, from_date=from_date, to_date=to_date)


@router.get("/operational/cash-bank-statement", response_model=CashBankStatementResponse)
def cash_bank_statement(
    account_code: str | None = Query(None, description="Default: the chart's bank account"),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> CashBankStatementResponse:
    if not account_code:
        from app.services.account_resolver import resolve_account_code
        account_code = resolve_account_code(db, "bank")
    svc = LedgerService(db)
    return svc.cash_bank_statement(account_code=account_code, from_date=from_date, to_date=to_date, page=page, page_size=page_size)


@router.post("/inventory/items", response_model=InventoryItemRead, status_code=201)
def create_inventory_item(payload: InventoryItemCreate, db: Session = Depends(get_db)) -> InventoryItemRead:
    svc = InventoryReportService(db)
    return svc.create_item(payload)


@router.get("/inventory/items", response_model=list[InventoryItemRead])
def get_inventory_items(db: Session = Depends(get_db)) -> list[InventoryItemRead]:
    svc = InventoryReportService(db)
    return svc.list_items()


@router.post("/inventory/movements", response_model=InventoryMovementRead, status_code=201)
def create_inventory_movement(payload: InventoryMovementCreate, db: Session = Depends(get_db)) -> InventoryMovementRead:
    svc = InventoryReportService(db)
    return svc.add_movement(payload)


@router.get("/inventory/movements", response_model=InventoryMovementResponse)
def inventory_movement_report(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    item_id: UUID | None = Query(None),
    db: Session = Depends(get_db),
) -> InventoryMovementResponse:
    svc = InventoryReportService(db)
    return svc.movement_report(from_date=from_date, to_date=to_date, page=page, page_size=page_size, item_id=item_id)


@router.get("/inventory/balance", response_model=InventoryBalanceResponse)
def inventory_balance_report(
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> InventoryBalanceResponse:
    svc = InventoryReportService(db)
    return svc.balance_report(to_date=to_date)


@router.patch("/inventory/items/{item_id}/price")
def update_inventory_price(
    item_id: UUID,
    list_price: int = Query(..., ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """Update the list price for an inventory item."""
    from app.models.inventory import InventoryItem
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    old_price = getattr(item, 'list_price', 0) or 0
    item.list_price = list_price
    db.commit()
    return {"item_id": str(item.id), "name": item.name, "old_price": old_price, "new_price": list_price}


# --- Inventory costing, valuation, reorder, barcode, BOM (roadmap §4.4) -------------------

class InventorySettingsIn(BaseModel):
    method: str = Field(..., pattern="^(weighted_average|fifo)$")


class BomLineIn(BaseModel):
    component_id: UUID
    quantity: float = Field(..., gt=0)


class BomIn(BaseModel):
    lines: list[BomLineIn] = Field(default_factory=list, max_length=200)


class ProductionIn(BaseModel):
    product_id: UUID
    quantity: float = Field(..., gt=0, le=10**9)
    on: date
    reference: str | None = Field(None, max_length=120)
    allow_short: bool = False


def _inventory_item(db: Session, item_id: UUID):
    from app.models.inventory import InventoryItem
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.get("/inventory/settings")
def get_inventory_settings(db: Session = Depends(get_db)) -> dict:
    from app.services.inventory_costing import METHODS, get_method
    return {"method": get_method(db), "methods": list(METHODS)}


@router.put("/inventory/settings")
def put_inventory_settings(body: InventorySettingsIn, db: Session = Depends(get_db)) -> dict:
    """Switch the costing method. Values are recomputed from the movements, so
    every report re-values history under the new method."""
    from app.services.audit_service import log_audit_event
    from app.services.inventory_costing import METHODS, get_method, set_method
    before = get_method(db)
    method = set_method(db, body.method)
    log_audit_event(db, action="update", entity_type="inventory_settings", entity_id="costing",
                    detail=f"{before} → {method}")
    db.commit()
    return {"method": method, "methods": list(METHODS)}


@router.get("/inventory/valuation")
def inventory_valuation(as_of: date | None = Query(None),
                        method: str | None = Query(None, pattern="^(weighted_average|fifo)$"),
                        db: Session = Depends(get_db)) -> dict:
    from app.services.inventory_costing import valuation
    return valuation(db, as_of=as_of, method=method)


@router.get("/inventory/low-stock")
def inventory_low_stock(db: Session = Depends(get_db)) -> dict:
    from app.services.inventory_costing import low_stock
    rows = low_stock(db)
    return {"count": len(rows), "rows": rows}


@router.get("/inventory/items/by-barcode/{barcode}", response_model=InventoryItemRead)
def inventory_item_by_barcode(barcode: str, db: Session = Depends(get_db)) -> InventoryItemRead:
    from app.models.inventory import InventoryItem
    from app.utils.digits import ascii_digits
    item = db.execute(select(InventoryItem).where(InventoryItem.barcode == ascii_digits(barcode).strip())).scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="No item has this barcode")
    return InventoryItemRead.model_validate(item)


@router.patch("/inventory/items/{item_id}", response_model=InventoryItemRead)
def update_inventory_item(item_id: UUID, body: InventoryItemUpdate, db: Session = Depends(get_db)) -> InventoryItemRead:
    item = _inventory_item(db, item_id)
    changes = body.model_dump(exclude_unset=True)
    if "barcode" in changes:
        changes["barcode"] = (changes["barcode"] or "").strip() or None
        InventoryReportService(db).assert_barcode_free(changes["barcode"], except_id=item.id)
    for key in ("name", "sku", "unit"):
        if key in changes and changes[key] is not None:
            changes[key] = changes[key].strip() or (None if key == "sku" else changes[key])
    for key, value in changes.items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return InventoryItemRead.model_validate(item)


@router.get("/inventory/items/{item_id}/bom")
def get_inventory_bom(item_id: UUID, db: Session = Depends(get_db)) -> dict:
    from app.services.inventory_costing import get_bom
    item = _inventory_item(db, item_id)
    return {"product_id": str(item.id), "name": item.name, "lines": get_bom(db, item.id)}


@router.put("/inventory/items/{item_id}/bom")
def put_inventory_bom(item_id: UUID, body: BomIn, db: Session = Depends(get_db)) -> dict:
    from app.services.inventory_costing import set_bom
    item = _inventory_item(db, item_id)
    lines = set_bom(db, item, [ln.model_dump() for ln in body.lines])
    db.commit()
    return {"product_id": str(item.id), "name": item.name, "lines": lines}


@router.post("/inventory/production", status_code=201)
def inventory_production(body: ProductionIn, db: Session = Depends(get_db)) -> dict:
    """Make ``quantity`` of a finished item from its bill of materials: the
    components go out at cost and the product comes in at their total."""
    from app.services.audit_service import log_audit_event
    from app.services.inventory_costing import produce
    item = _inventory_item(db, body.product_id)
    out = produce(db, item, body.quantity, on=body.on, reference=body.reference, allow_short=body.allow_short)
    log_audit_event(db, action="create", entity_type="production_run", entity_id=str(item.id),
                    detail=f"{out['quantity']} × {item.name} at {out['unit_cost']}")
    db.commit()
    return out


@router.get("/financial/balance-sheet-periods")
def balance_sheet_periods(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    granularity: str = Query("monthly", pattern="^(weekly|monthly|quarterly|seasonal)$"),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """Balance sheet totals (assets, liabilities, equity) at each period end."""
    import calendar
    from datetime import timedelta
    from app.services.reporting.common import default_period
    from app.services.reporting.repository import account_turnovers_upto, list_accounts, net_profit_to_date
    from app.services.reporting.common import classify_account_code, balance_from_turnovers, ASSET, LIABILITY, EQUITY

    def _add_months(d: date, n: int) -> date:
        m = d.month - 1 + n
        y = d.year + m // 12
        m = m % 12 + 1
        return date(y, m, 1)

    period = default_period(from_date, to_date)
    accounts = list_accounts(db)

    # Period ends in the company's calendar: Jalali months and seasons for an
    # Iranian company (roadmap §3.5), Gregorian otherwise.
    from app.services.calendar_periods import company_calendar, period_ends
    buckets = period_ends(period.from_date, period.to_date, granularity, company_calendar(db))

    periods = []
    for label, end_date in buckets:
        turnovers = account_turnovers_upto(db, end_date, currency=currency)
        turnover_map = {aid: (d, c) for aid, d, c in turnovers}
        totals = {ASSET: 0, LIABILITY: 0, EQUITY: 0}
        for acc in accounts:
            acc_type = classify_account_code(acc.code)
            if acc_type not in totals:
                continue
            tc = turnover_map.get(acc.id)
            if tc:
                # signed, like the statement itself (overdrafts, contra assets)
                totals[acc_type] += balance_from_turnovers(acc_type, tc[0], tc[1])
        # unclosed profit/(loss) to date is equity, so the trend balances too
        totals[EQUITY] += net_profit_to_date(db, end_date, currency=currency)

        periods.append({
            "period": label,
            "date": end_date.isoformat(),
            "assets": totals[ASSET],
            "liabilities": totals[LIABILITY],
            "equity": totals[EQUITY],
            "net_worth": totals[ASSET] - totals[LIABILITY],
        })

    return {
        "report_type": "balance_sheet_periods",
        "granularity": granularity,
        "period": {"from_date": period.from_date.isoformat(), "to_date": period.to_date.isoformat()},
        "periods": periods,
        "totals": {
            "latest_assets": periods[-1]["assets"] if periods else 0,
            "latest_liabilities": periods[-1]["liabilities"] if periods else 0,
            "latest_equity": periods[-1]["equity"] if periods else 0,
        },
    }


@router.get("/sales/trend")
def sales_trend(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    product_name: str | None = Query(None),
    granularity: str = Query("monthly", pattern="^(weekly|monthly|quarterly|seasonal)$"),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """Sales of a specific product (or all products) grouped by period."""
    from collections import defaultdict
    from app.services.reporting.common import default_period
    from app.services.reporting.repository import sales_items_between

    from app.services.calendar_periods import company_calendar, period_key
    cal = company_calendar(db)
    period = default_period(from_date, to_date)
    rows = sales_items_between(db, period.from_date, period.to_date, currency=currency)

    by_period: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"quantity": 0.0, "sales_amount": 0, "invoice_count": 0}
    )
    seen_invoices: dict[str, set] = defaultdict(set)

    for item, inv in rows:
        name = (item.product_name or "").strip()
        if product_name and product_name.lower() not in name.lower():
            continue

        key = period_key(inv.issue_date, granularity, cal)       # the company's calendar (§3.5)

        by_period[key]["quantity"] += float(item.quantity or 0)
        by_period[key]["sales_amount"] += int(item.line_total or 0)
        if inv.id not in seen_invoices[key]:
            seen_invoices[key].add(inv.id)
            by_period[key]["invoice_count"] += 1

    periods = []
    for k in sorted(by_period.keys()):
        periods.append({"period": k, **by_period[k]})

    return {
        "report_type": "sales_trend",
        "granularity": granularity,
        "product_filter": product_name,
        "period": {"from_date": period.from_date.isoformat(), "to_date": period.to_date.isoformat()},
        "periods": periods,
        "totals": {
            "total_quantity": sum(p["quantity"] for p in periods),
            "total_sales": sum(p["sales_amount"] for p in periods),
            "total_invoices": sum(p["invoice_count"] for p in periods),
        },
    }


@router.get("/entities/search")
def entities_search(
    search: str = Query(""),
    type: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Return entities for autocomplete (name + type)."""
    from app.models.entity import Entity
    q = select(Entity).order_by(Entity.name)
    if type:
        q = q.where(Entity.type == type)
    entities = db.execute(q).scalars().all()
    results = [{"name": e.name, "type": e.type} for e in entities]
    if search:
        s = search.lower()
        results = [r for r in results if s in r["name"].lower()]
    return results


@router.get("/products/names")
def product_names(db: Session = Depends(get_db)):
    """Return unique product names from invoices for autocomplete."""
    from app.models.invoice_item import InvoiceItem
    names = db.execute(
        select(InvoiceItem.product_name).where(InvoiceItem.product_name.isnot(None)).distinct().order_by(InvoiceItem.product_name)
    ).scalars().all()
    return [n for n in names if n and n.strip()]


@router.get("/sales/by-product", response_model=SalesPurchaseReportResponse)
def sales_by_product(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    product_name: str | None = Query(None),
    db: Session = Depends(get_db),
) -> SalesPurchaseReportResponse:
    return SalesReportService(db).sales_by_product(from_date=from_date, to_date=to_date, product_name=product_name)


@router.get("/sales/by-invoice", response_model=SalesPurchaseReportResponse)
def sales_by_invoice(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    product_name: str | None = Query(None),
    db: Session = Depends(get_db),
) -> SalesPurchaseReportResponse:
    return SalesReportService(db).sales_by_invoice(from_date=from_date, to_date=to_date, product_name=product_name)


@router.get("/purchases/by-product", response_model=SalesPurchaseReportResponse)
def purchase_by_product(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    product_name: str | None = Query(None),
    db: Session = Depends(get_db),
) -> SalesPurchaseReportResponse:
    return SalesReportService(db).purchase_by_product(from_date=from_date, to_date=to_date, product_name=product_name)


@router.get("/purchases/by-invoice", response_model=SalesPurchaseReportResponse)
def purchase_by_invoice(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    product_name: str | None = Query(None),
    db: Session = Depends(get_db),
) -> SalesPurchaseReportResponse:
    return SalesReportService(db).purchase_by_invoice(from_date=from_date, to_date=to_date, product_name=product_name)


@router.post("/journal/register", response_model=TransactionRead, status_code=201)
def register_journal_entry(payload: TransactionCreate, db: Session = Depends(get_db)) -> TransactionRead:
    from app.api.transactions import _load_transaction_with_lines, _transaction_to_read

    row = _create_transaction_from_payload(db, payload)
    db.commit()
    db.refresh(row)
    _load_transaction_with_lines(db, row)
    return _transaction_to_read(row)


@router.get("/journal/entries", response_model=PaginatedJournalResponse)
def list_journal_entries(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> PaginatedJournalResponse:
    return LedgerService(db).general_journal(from_date=from_date, to_date=to_date, page=page, page_size=page_size)


@router.patch("/journal/{transaction_id}", response_model=TransactionRead)
def edit_journal_entry(transaction_id: UUID, payload: TransactionUpdate, db: Session = Depends(get_db)) -> TransactionRead:
    from app.api.transactions import _load_transaction_with_lines, _transaction_to_read
    from app.services.ledger_posting import get_account_by_code as _get_account_by_code
    from app.services.ledger_posting import validate_balanced_lines as _validate_balanced_lines
    from app.models.entity import Entity, TransactionEntity

    t = db.get(Transaction, transaction_id)
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found")
    from app.services.ledger_posting import assert_transaction_mutable
    assert_transaction_mutable(db, t, new_date=payload.date)
    from app.services.learned_preferences import learn_from_edit, snapshot
    before = snapshot(t)                       # correction memory (roadmap §5.4)
    if payload.date is not None:
        t.date = payload.date
    if payload.reference is not None:
        t.reference = payload.reference.strip() or None
    if payload.description is not None:
        t.description = payload.description.strip() or None
    if payload.lines is not None:
        _validate_balanced_lines(payload.lines)
        for ln in list(t.lines or []):
            db.delete(ln)
        db.flush()
        for line in payload.lines:
            acc = _get_account_by_code(db, line.account_code)
            db.add(
                TransactionLine(
                    transaction_id=t.id,
                    account_id=acc.id,
                    debit=line.debit,
                    credit=line.credit,
                    line_description=line.line_description,
                )
            )
    if payload.entity_links is not None:
        for link in list(t.entity_links or []):
            db.delete(link)
        db.flush()
        for link in payload.entity_links:
            entity = db.get(Entity, link.entity_id) if link.entity_id else None
            if not entity:
                continue
            db.add(TransactionEntity(transaction_id=t.id, entity_id=entity.id, role=link.role.strip().lower()))
    db.flush()
    db.expire(t, ["lines", "entity_links"])
    learn_from_edit(db, t.description, before, snapshot(t))
    db.commit()
    from app.services.fx_settlement import settle_waiting    # an edited payment is settled again
    if settle_waiting(db):
        db.commit()
    db.refresh(t)
    _load_transaction_with_lines(db, t)
    return _transaction_to_read(t)


@router.post("/journal/{transaction_id}/reverse", response_model=JournalEntryRead)
def reverse_journal_entry(
    transaction_id: UUID,
    reverse_date: date | None = Query(None),
    reference: str | None = Query(None),
    description: str | None = Query(None),
    db: Session = Depends(get_db),
) -> JournalEntryRead:
    return LedgerService(db).reverse_journal_entry(
        transaction_id=transaction_id,
        reverse_date=reverse_date,
        reference=reference,
        description=description,
    )


@router.get("/books/trial-balance-by-currency")
def trial_balance_by_currency(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=1000),
    convert_to: str | None = Query(None, description="If set, also return converted totals in this currency using latest FX rates"),
    db: Session = Depends(get_db),
) -> dict:
    """Trial balance split into one block per currency.

    Returns `{ blocks: [{currency, rows[], totals}], converted_to, converted_total }`.
    When `convert_to` is provided, each block is also converted to that currency
    using the most recent FX rate on or before `to_date` (or today).
    """
    from app.services.reporting.repository import distinct_currencies
    from app.services.fx_service import convert_minor, get_rate
    import traceback, sys
    svc = LedgerService(db)
    try:
        used = distinct_currencies(db, from_date, to_date) or ["IRR"]
    except Exception:
        traceback.print_exc(file=sys.stderr); sys.stderr.flush()
        raise
    blocks = []
    converted_grand_total_debit = 0
    converted_grand_total_credit = 0
    missing_rates: list[str] = []
    on = to_date or date.today()
    for ccy in used:
        try:
            rep = svc.trial_balance(from_date=from_date, to_date=to_date, page=page, page_size=page_size, currency=ccy)
        except Exception:
            traceback.print_exc(file=sys.stderr); sys.stderr.flush()
            raise
        totals = rep.totals or {}
        total_debit_turnover = int(totals.get("debit_turnover", 0) or 0)
        total_credit_turnover = int(totals.get("credit_turnover", 0) or 0)
        total_debit_balance = int(totals.get("debit_balance", 0) or 0)
        total_credit_balance = int(totals.get("credit_balance", 0) or 0)
        block = {
            "currency": ccy,
            "rows": [r.model_dump() if hasattr(r, "model_dump") else r for r in rep.rows],
            "total_debit_turnover": total_debit_turnover,
            "total_credit_turnover": total_credit_turnover,
            "total_debit_balance": total_debit_balance,
            "total_credit_balance": total_credit_balance,
        }
        if convert_to:
            tc = convert_to.strip().upper()
            if ccy.upper() == tc:
                block["converted_rate"] = 1.0
                block["converted_debit_balance"] = total_debit_balance
                block["converted_credit_balance"] = total_credit_balance
            else:
                rate = get_rate(db, ccy, tc, on)
                if rate is None:
                    missing_rates.append(f"{ccy}->{tc}")
                    block["converted_rate"] = None
                    block["converted_debit_balance"] = None
                    block["converted_credit_balance"] = None
                else:
                    block["converted_rate"] = rate
                    block["converted_debit_balance"] = convert_minor(int(total_debit_balance), rate)
                    block["converted_credit_balance"] = convert_minor(int(total_credit_balance), rate)
            if block.get("converted_debit_balance") is not None:
                converted_grand_total_debit += block["converted_debit_balance"]
            if block.get("converted_credit_balance") is not None:
                converted_grand_total_credit += block["converted_credit_balance"]
        blocks.append(block)
    return {
        "blocks": blocks,
        "converted_to": convert_to,
        "converted_total_debit_balance": converted_grand_total_debit if convert_to else None,
        "converted_total_credit_balance": converted_grand_total_credit if convert_to else None,
        "missing_rates": missing_rates,
    }

from __future__ import annotations

import csv
import io
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.transactions import _create_transaction_from_payload
from app.db.session import get_db
from app.models.transaction import Transaction, TransactionLine
from app.schemas.manager_report import (
    AccountLedgerResponse,
    BalanceSheetResponse,
    CashBankStatementResponse,
    CashFlowResponse,
    DebtorCreditorResponse,
    IncomeStatementResponse,
    InventoryBalanceResponse,
    InventoryItemCreate,
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
from app.services.reporting.inventory_report_service import InventoryReportService
from app.services.reporting.ledger_service import LedgerService
from app.services.reporting.operations_report_service import OperationsReportService
from app.services.reporting.sales_report_service import SalesReportService

router = APIRouter(prefix="/manager-reports", tags=["manager-reports"])


def _csv_response(filename: str, headers: list[str], rows: list[list[str | int | float]]) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for row in rows:
        w.writerow(row)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
    db: Session = Depends(get_db),
) -> BalanceSheetResponse:
    svc = FinancialStatementService(db)
    return svc.balance_sheet(to_date=to_date, comparative_to_date=comparative_to_date, currency=currency)


@router.get("/financial/income-statement", response_model=IncomeStatementResponse)
def income_statement(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> IncomeStatementResponse:
    svc = FinancialStatementService(db)
    return svc.income_statement(from_date=from_date, to_date=to_date, currency=currency)


@router.get("/financial/cash-flow", response_model=CashFlowResponse)
def cash_flow_statement(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    currency: str | None = Query(None),
    db: Session = Depends(get_db),
) -> CashFlowResponse:
    svc = CashFlowService(db)
    return svc.statement(from_date=from_date, to_date=to_date, currency=currency)


@router.get("/financial/cash-flow-periods")
def cash_flow_periods(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    granularity: str = Query("monthly", regex="^(weekly|monthly|quarterly|seasonal)$"),
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


@router.get("/operational/accounts-payable")
def accounts_payable(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """Get detailed accounts payable with verification status."""
    from app.services.reporting.common import default_period
    from app.models.invoice import Invoice
    from app.models.entity import Entity
    period = default_period(from_date, to_date)

    invoices = db.execute(
        select(Invoice)
        .where(
            Invoice.kind == "purchase",
            Invoice.issue_date >= period.from_date,
            Invoice.issue_date <= period.to_date,
            Invoice.status.in_(["issued", "draft"]),
        )
        .order_by(Invoice.due_date.asc())
    ).scalars().all()

    entity_ids = [inv.entity_id for inv in invoices if inv.entity_id]
    entities = {}
    if entity_ids:
        from sqlalchemy import select as sel
        ents = db.execute(sel(Entity).where(Entity.id.in_(entity_ids))).scalars().all()
        entities = {e.id: e for e in ents}

    items = []
    total = 0
    for inv in invoices:
        amt = int(inv.amount or 0)
        total += amt
        days_overdue = (date.today() - inv.due_date).days if inv.due_date and inv.due_date < date.today() else 0
        items.append({
            "invoice_id": str(inv.id),
            "invoice_number": inv.number,
            "vendor": entities.get(inv.entity_id).name if inv.entity_id and entities.get(inv.entity_id) else "Unknown",
            "amount": amt,
            "issue_date": inv.issue_date.isoformat() if inv.issue_date else None,
            "due_date": inv.due_date.isoformat() if inv.due_date else None,
            "status": inv.status,
            "days_overdue": days_overdue,
            "aging_bucket": "current" if days_overdue <= 0 else "31-60" if days_overdue <= 60 else "60+" if days_overdue <= 90 else "90+",
        })

    return {
        "report_type": "accounts_payable",
        "period": {"from_date": period.from_date.isoformat(), "to_date": period.to_date.isoformat()},
        "items": items,
        "total": total,
        "count": len(items),
    }


@router.get("/operational/person-running-balance", response_model=PersonRunningBalanceResponse)
def person_running_balance(
    entity_id: UUID = Query(...),
    role: str = Query(..., pattern="^(client|supplier|payee|bank)$"),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> PersonRunningBalanceResponse:
    svc = OperationsReportService(db)
    return svc.person_running_balance(entity_id=entity_id, role=role, from_date=from_date, to_date=to_date)


@router.get("/operational/cash-bank-statement", response_model=CashBankStatementResponse)
def cash_bank_statement(
    account_code: str = Query("1110"),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> CashBankStatementResponse:
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


@router.get("/financial/balance-sheet-periods")
def balance_sheet_periods(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    granularity: str = Query("monthly", regex="^(weekly|monthly|quarterly|seasonal)$"),
    db: Session = Depends(get_db),
) -> dict:
    """Balance sheet totals (assets, liabilities, equity) at each period end."""
    import calendar
    from datetime import timedelta
    from app.services.reporting.common import default_period
    from app.services.reporting.repository import account_turnovers_upto, list_accounts
    from app.services.reporting.common import classify_account_code, balance_from_turnovers, statement_sign_value, ASSET, LIABILITY, EQUITY

    def _add_months(d: date, n: int) -> date:
        m = d.month - 1 + n
        y = d.year + m // 12
        m = m % 12 + 1
        return date(y, m, 1)

    period = default_period(from_date, to_date)
    accounts = list_accounts(db)

    # Build list of period-end dates
    ends: list[date] = []
    if granularity == "monthly":
        cursor = period.from_date.replace(day=1)
        while cursor <= period.to_date:
            month_end = date(cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1])
            ends.append(min(month_end, period.to_date))
            cursor = _add_months(cursor, 1)
    elif granularity == "quarterly":
        cursor = period.from_date.replace(day=1)
        while cursor <= period.to_date:
            q_end_month = _add_months(cursor, 3)
            q_end = q_end_month - timedelta(days=1)
            ends.append(min(q_end, period.to_date))
            cursor = q_end_month
    elif granularity == "weekly":
        cursor = period.from_date
        while cursor <= period.to_date:
            week_end = cursor + timedelta(days=(6 - cursor.weekday()))
            ends.append(min(week_end, period.to_date))
            cursor = week_end + timedelta(days=1)
    else:  # seasonal
        cursor = period.from_date.replace(day=1)
        while cursor <= period.to_date:
            q_end_month = _add_months(cursor, 3)
            q_end = q_end_month - timedelta(days=1)
            ends.append(min(q_end, period.to_date))
            cursor = q_end_month

    # Deduplicate and sort
    ends = sorted(set(ends))

    periods = []
    for end_date in ends:
        turnovers = account_turnovers_upto(db, end_date)
        turnover_map = {aid: (d, c) for aid, d, c in turnovers}
        totals = {ASSET: 0, LIABILITY: 0, EQUITY: 0}
        for acc in accounts:
            acc_type = classify_account_code(acc.code)
            if acc_type not in totals:
                continue
            tc = turnover_map.get(acc.id)
            if tc:
                raw = balance_from_turnovers(acc_type, tc[0], tc[1])
                totals[acc_type] += statement_sign_value(acc_type, raw)

        if granularity == "monthly":
            label = end_date.strftime("%Y-%m")
        elif granularity == "quarterly":
            q = (end_date.month - 1) // 3 + 1
            label = f"{end_date.year}-Q{q}"
        elif granularity == "weekly":
            label = end_date.strftime("%Y-W%W")
        else:
            month = end_date.month
            season = "Spring" if month in (3, 4, 5) else "Summer" if month in (6, 7, 8) else "Autumn" if month in (9, 10, 11) else "Winter"
            label = f"{end_date.year}-{season}"

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
    granularity: str = Query("monthly", regex="^(weekly|monthly|quarterly|seasonal)$"),
    db: Session = Depends(get_db),
) -> dict:
    """Sales of a specific product (or all products) grouped by period."""
    from collections import defaultdict
    from app.services.reporting.common import default_period
    from app.services.reporting.repository import sales_items_between

    period = default_period(from_date, to_date)
    rows = sales_items_between(db, period.from_date, period.to_date)

    by_period: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"quantity": 0.0, "sales_amount": 0, "invoice_count": 0}
    )
    seen_invoices: dict[str, set] = defaultdict(set)

    for item, inv in rows:
        name = (item.product_name or "").strip()
        if product_name and product_name.lower() not in name.lower():
            continue

        d = inv.issue_date
        if granularity == "weekly":
            key = d.strftime("%Y-W%W")
        elif granularity == "quarterly":
            q = (d.month - 1) // 3 + 1
            key = f"{d.year}-Q{q}"
        elif granularity == "seasonal":
            month = d.month
            season = "Spring" if month in (3, 4, 5) else "Summer" if month in (6, 7, 8) else "Autumn" if month in (9, 10, 11) else "Winter"
            key = f"{d.year}-{season}"
        else:
            key = d.strftime("%Y-%m")

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
    from app.api.transactions import _load_transaction_with_lines, _transaction_to_read, _validate_balanced_lines, _get_account_by_code
    from app.models.entity import Entity, TransactionEntity

    t = db.get(Transaction, transaction_id)
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found")
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
    from app.services.fx_service import get_rate
    svc = LedgerService(db)
    used = distinct_currencies(db, from_date, to_date) or ["IRR"]
    blocks = []
    converted_grand_total_debit = 0
    converted_grand_total_credit = 0
    missing_rates: list[str] = []
    on = to_date or date.today()
    for ccy in used:
        rep = svc.trial_balance(from_date=from_date, to_date=to_date, page=page, page_size=page_size, currency=ccy)
        block = {
            "currency": ccy,
            "rows": [r.model_dump() if hasattr(r, "model_dump") else r for r in rep.rows],
            "total_debit_turnover": rep.total_debit_turnover,
            "total_credit_turnover": rep.total_credit_turnover,
            "total_debit_balance": rep.total_debit_balance,
            "total_credit_balance": rep.total_credit_balance,
        }
        if convert_to:
            tc = convert_to.strip().upper()
            if ccy.upper() == tc:
                block["converted_rate"] = 1.0
                block["converted_debit_balance"] = rep.total_debit_balance
                block["converted_credit_balance"] = rep.total_credit_balance
            else:
                rate = get_rate(db, ccy, tc, on)
                if rate is None:
                    missing_rates.append(f"{ccy}->{tc}")
                    block["converted_rate"] = None
                    block["converted_debit_balance"] = None
                    block["converted_credit_balance"] = None
                else:
                    block["converted_rate"] = rate
                    block["converted_debit_balance"] = int(round(rep.total_debit_balance * rate))
                    block["converted_credit_balance"] = int(round(rep.total_credit_balance * rate))
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

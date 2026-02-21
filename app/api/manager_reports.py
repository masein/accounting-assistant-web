from __future__ import annotations

import csv
import io
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
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
    db: Session = Depends(get_db),
) -> BalanceSheetResponse:
    svc = FinancialStatementService(db)
    return svc.balance_sheet(to_date=to_date, comparative_to_date=comparative_to_date)


@router.get("/financial/income-statement", response_model=IncomeStatementResponse)
def income_statement(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> IncomeStatementResponse:
    svc = FinancialStatementService(db)
    return svc.income_statement(from_date=from_date, to_date=to_date)


@router.get("/financial/cash-flow", response_model=CashFlowResponse)
def cash_flow_statement(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> CashFlowResponse:
    svc = CashFlowService(db)
    return svc.statement(from_date=from_date, to_date=to_date)


@router.get("/books/general-journal", response_model=PaginatedJournalResponse)
def general_journal(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    format: str = Query("json"),
    db: Session = Depends(get_db),
) -> PaginatedJournalResponse | Response:
    svc = LedgerService(db)
    rep = svc.general_journal(from_date=from_date, to_date=to_date, page=page, page_size=page_size)
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
    db: Session = Depends(get_db),
) -> TrialBalanceResponse | Response:
    svc = LedgerService(db)
    rep = svc.general_ledger(from_date=from_date, to_date=to_date, page=page, page_size=page_size)
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
    db: Session = Depends(get_db),
) -> AccountLedgerResponse | Response:
    svc = LedgerService(db)
    rep = svc.account_ledger(account_code=account_code, from_date=from_date, to_date=to_date, page=page, page_size=page_size)
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
    db: Session = Depends(get_db),
) -> TrialBalanceResponse | Response:
    svc = LedgerService(db)
    rep = svc.trial_balance(from_date=from_date, to_date=to_date, page=page, page_size=page_size)
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
    db: Session = Depends(get_db),
) -> DebtorCreditorResponse:
    svc = OperationsReportService(db)
    return svc.debtor_creditor(from_date=from_date, to_date=to_date)


@router.get("/operational/person-running-balance", response_model=PersonRunningBalanceResponse)
def person_running_balance(
    entity_id: UUID = Query(...),
    role: str = Query(..., pattern="^(client|supplier|payee)$"),
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


@router.get("/sales/by-product", response_model=SalesPurchaseReportResponse)
def sales_by_product(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> SalesPurchaseReportResponse:
    return SalesReportService(db).sales_by_product(from_date=from_date, to_date=to_date)


@router.get("/sales/by-invoice", response_model=SalesPurchaseReportResponse)
def sales_by_invoice(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> SalesPurchaseReportResponse:
    return SalesReportService(db).sales_by_invoice(from_date=from_date, to_date=to_date)


@router.get("/purchases/by-product", response_model=SalesPurchaseReportResponse)
def purchase_by_product(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> SalesPurchaseReportResponse:
    return SalesReportService(db).purchase_by_product(from_date=from_date, to_date=to_date)


@router.get("/purchases/by-invoice", response_model=SalesPurchaseReportResponse)
def purchase_by_invoice(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> SalesPurchaseReportResponse:
    return SalesReportService(db).purchase_by_invoice(from_date=from_date, to_date=to_date)


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

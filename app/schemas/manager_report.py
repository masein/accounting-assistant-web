from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field


class ReportPeriod(BaseModel):
    from_date: date | None = Field(default=None, alias="from")
    to_date: date | None = Field(default=None, alias="to")

    model_config = {"populate_by_name": True}


class StatementAccountNode(BaseModel):
    account_id: UUID | None = None
    account_code: str
    account_name: str
    account_type: str
    label_fa: str | None = None
    balance: int = 0
    debit_turnover: int = 0
    credit_turnover: int = 0
    children: list["StatementAccountNode"] = Field(default_factory=list)


class StatementSection(BaseModel):
    key: str
    label: str
    label_fa: str
    items: list[StatementAccountNode] = Field(default_factory=list)
    total: int = 0


class BalanceSheetResponse(BaseModel):
    report_type: str = "balance_sheet"
    period: ReportPeriod
    comparative_period: ReportPeriod | None = None
    sections: dict[str, StatementSection]
    totals: dict[str, int]


class IncomeStatementResponse(BaseModel):
    report_type: str = "income_statement"
    period: ReportPeriod
    sections: dict[str, StatementSection]
    totals: dict[str, int]


class CashFlowLine(BaseModel):
    account_code: str
    account_name: str
    amount: int
    label_fa: str | None = None


class CashFlowSection(BaseModel):
    key: str
    label: str
    label_fa: str
    lines: list[CashFlowLine] = Field(default_factory=list)
    net: int = 0


class CashFlowResponse(BaseModel):
    report_type: str = "cash_flow_statement"
    period: ReportPeriod
    sections: dict[str, CashFlowSection]
    totals: dict[str, int]


class JournalLineRead(BaseModel):
    account_code: str
    account_name: str
    debit: int
    credit: int
    line_description: str | None = None


class JournalEntryRead(BaseModel):
    transaction_id: UUID
    date: date
    reference: str | None = None
    description: str | None = None
    lines: list[JournalLineRead]
    total_debit: int
    total_credit: int


class PaginatedJournalResponse(BaseModel):
    report_type: str
    page: int
    page_size: int
    total: int
    items: list[JournalEntryRead]


class LedgerAccountSummary(BaseModel):
    account_code: str
    account_name: str
    debit_turnover: int
    credit_turnover: int
    balance: int


class LedgerDetailRow(BaseModel):
    date: date
    transaction_id: UUID
    reference: str | None = None
    description: str | None = None
    debit: int
    credit: int
    running_balance: int
    line_description: str | None = None


class AccountLedgerResponse(BaseModel):
    report_type: str
    period: ReportPeriod
    account: LedgerAccountSummary
    page: int
    page_size: int
    total: int
    items: list[LedgerDetailRow]


class TrialBalanceRow(BaseModel):
    account_code: str
    account_name: str
    debit_turnover: int
    credit_turnover: int
    debit_balance: int
    credit_balance: int


class TrialBalanceResponse(BaseModel):
    report_type: str = "trial_balance"
    period: ReportPeriod
    page: int
    page_size: int
    total: int
    rows: list[TrialBalanceRow]
    totals: dict[str, int]


class DebtorCreditorRow(BaseModel):
    entity_id: UUID | None = None
    entity_name: str
    entity_type: str
    current: int
    days_31_60: int
    days_61_90: int
    days_90_plus: int
    total: int


class DebtorCreditorResponse(BaseModel):
    report_type: str = "debtor_creditor"
    period: ReportPeriod
    debtors: list[DebtorCreditorRow]
    creditors: list[DebtorCreditorRow]
    totals: dict[str, int]


class PersonRunningBalanceRow(BaseModel):
    date: date
    transaction_id: UUID
    reference: str | None = None
    description: str | None = None
    debit_effect: int
    credit_effect: int
    running_balance: int


class PersonRunningBalanceResponse(BaseModel):
    report_type: str = "person_running_balance"
    period: ReportPeriod
    entity_id: UUID
    entity_name: str
    role: str
    rows: list[PersonRunningBalanceRow]
    closing_balance: int


class CashBankStatementRow(BaseModel):
    date: date
    transaction_id: UUID
    reference: str | None = None
    description: str | None = None
    debit: int
    credit: int
    running_balance: int


class CashBankStatementResponse(BaseModel):
    report_type: str = "cash_bank_statement"
    period: ReportPeriod
    account: LedgerAccountSummary
    page: int
    page_size: int
    total: int
    rows: list[CashBankStatementRow]


class InventoryItemCreate(BaseModel):
    sku: str | None = None
    name: str
    unit: str = "unit"


class InventoryItemRead(BaseModel):
    id: UUID
    sku: str | None = None
    name: str
    unit: str
    is_active: bool

    model_config = {"from_attributes": True}


class InventoryMovementCreate(BaseModel):
    item_id: UUID
    movement_date: date
    movement_type: str = Field(..., description="IN | OUT | ADJUSTMENT")
    quantity: float = Field(..., gt=0)
    unit_cost: int = Field(default=0, ge=0)
    reference: str | None = None
    description: str | None = None
    invoice_id: UUID | None = None
    transaction_id: UUID | None = None


class InventoryMovementRead(BaseModel):
    id: UUID
    item_id: UUID
    item_name: str
    movement_date: date
    movement_type: str
    quantity: float
    unit_cost: int
    movement_value: int
    reference: str | None = None
    description: str | None = None


class InventoryBalanceRow(BaseModel):
    item_id: UUID
    sku: str | None = None
    item_name: str
    unit: str
    qty_in: float
    qty_out: float
    on_hand_qty: float
    average_cost: int
    inventory_value: int
    cogs: int


class InventoryBalanceResponse(BaseModel):
    report_type: str = "inventory_balance"
    period: ReportPeriod
    rows: list[InventoryBalanceRow]
    totals: dict[str, int | float]


class InventoryMovementResponse(BaseModel):
    report_type: str = "inventory_movement"
    period: ReportPeriod
    page: int
    page_size: int
    total: int
    rows: list[InventoryMovementRead]


class SalesByProductRow(BaseModel):
    product_name: str
    quantity: float
    sales_amount: int
    estimated_cost: int
    profit: int
    margin_pct: float | None = None


class SalesByInvoiceRow(BaseModel):
    invoice_id: UUID
    invoice_number: str
    issue_date: date
    due_date: date
    status: str
    entity_name: str | None = None
    amount: int


class SalesPurchaseReportResponse(BaseModel):
    report_type: str
    period: ReportPeriod
    rows: list[SalesByProductRow | SalesByInvoiceRow]
    totals: dict[str, int | float]


class ReportExportEnvelope(BaseModel):
    report_type: str
    format: str = "json"
    generated_at: str
    data: dict

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.utils.digits import ascii_digits
from app.core.money import Money


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
    balance: Money = 0
    debit_turnover: Money = 0
    credit_turnover: Money = 0
    children: list["StatementAccountNode"] = Field(default_factory=list)
    # True for lines the statement derives rather than reads from an account
    # (e.g. unclosed current-period earnings shown under equity).
    is_computed: bool = False


class StatementSection(BaseModel):
    key: str
    label: str
    label_fa: str
    items: list[StatementAccountNode] = Field(default_factory=list)
    total: Money = 0


class FinancialAnalysis(BaseModel):
    """Computed financial ratios and insights appended to statements."""
    ratios: dict[str, float | None] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    summary: str | None = None


class BalanceSheetResponse(BaseModel):
    report_type: str = "balance_sheet"
    period: ReportPeriod
    comparative_period: ReportPeriod | None = None
    sections: dict[str, StatementSection]
    totals: dict[str, Money]
    analysis: FinancialAnalysis | None = None


class IncomeStatementResponse(BaseModel):
    report_type: str = "income_statement"
    period: ReportPeriod
    sections: dict[str, StatementSection]
    totals: dict[str, Money]
    analysis: FinancialAnalysis | None = None


class CashFlowLine(BaseModel):
    account_code: str
    account_name: str
    amount: Money
    label_fa: str | None = None


class CashFlowSection(BaseModel):
    key: str
    label: str
    label_fa: str
    lines: list[CashFlowLine] = Field(default_factory=list)
    net: Money = 0


class CashFlowResponse(BaseModel):
    report_type: str = "cash_flow_statement"
    period: ReportPeriod
    sections: dict[str, CashFlowSection]
    totals: dict[str, Money]
    analysis: FinancialAnalysis | None = None


class JournalLineRead(BaseModel):
    account_code: str
    account_name: str
    debit: Money
    credit: Money
    line_description: str | None = None


class JournalEntryRead(BaseModel):
    transaction_id: UUID
    date: date
    reference: str | None = None
    description: str | None = None
    lines: list[JournalLineRead]
    total_debit: Money
    total_credit: Money


class PaginatedJournalResponse(BaseModel):
    report_type: str
    page: int
    page_size: int
    total: Money
    items: list[JournalEntryRead]


class LedgerAccountSummary(BaseModel):
    account_code: str
    account_name: str
    debit_turnover: Money
    credit_turnover: Money
    balance: Money


class LedgerDetailRow(BaseModel):
    date: date
    transaction_id: UUID
    reference: str | None = None
    description: str | None = None
    debit: Money
    credit: Money
    running_balance: Money
    line_description: str | None = None


class AccountLedgerResponse(BaseModel):
    report_type: str
    period: ReportPeriod
    account: LedgerAccountSummary
    page: int
    page_size: int
    total: Money
    items: list[LedgerDetailRow]


class TrialBalanceRow(BaseModel):
    account_code: str
    account_name: str
    debit_turnover: Money
    credit_turnover: Money
    debit_balance: Money
    credit_balance: Money


class TrialBalanceResponse(BaseModel):
    report_type: str = "trial_balance"
    # Single-currency view: which currency the figures are in, and which other
    # currencies the books hold (never summed into these figures).
    currency: str | None = None
    other_currencies: list[str] = []
    period: ReportPeriod
    page: int
    page_size: int
    total: Money
    rows: list[TrialBalanceRow]
    totals: dict[str, Money]


class DebtorCreditorRow(BaseModel):
    entity_id: UUID | None = None
    entity_name: str
    entity_type: str
    current: Money
    days_31_60: Money
    days_61_90: Money
    days_90_plus: Money
    total: Money


class DebtorCreditorResponse(BaseModel):
    report_type: str = "debtor_creditor"
    period: ReportPeriod
    debtors: list[DebtorCreditorRow]
    creditors: list[DebtorCreditorRow]
    totals: dict[str, Money]


class PersonRunningBalanceRow(BaseModel):
    date: date
    transaction_id: UUID
    reference: str | None = None
    description: str | None = None
    debit_effect: Money
    credit_effect: Money
    running_balance: Money


class PersonRunningBalanceResponse(BaseModel):
    report_type: str = "person_running_balance"
    period: ReportPeriod
    entity_id: UUID
    entity_name: str
    role: str
    rows: list[PersonRunningBalanceRow]
    closing_balance: Money


class CashBankStatementRow(BaseModel):
    date: date
    transaction_id: UUID
    reference: str | None = None
    description: str | None = None
    debit: Money
    credit: Money
    running_balance: Money


class CashBankStatementResponse(BaseModel):
    report_type: str = "cash_bank_statement"
    period: ReportPeriod
    account: LedgerAccountSummary
    page: int
    page_size: int
    total: Money
    rows: list[CashBankStatementRow]


class _LatinCodes(BaseModel):
    """A SKU or barcode typed on a Persian keyboard is stored in 0–9: a
    scanner sends 0–9, so «۶۲۹۱…» never matched a scan."""

    @field_validator("sku", "barcode", mode="before", check_fields=False)
    @classmethod
    def _ascii(cls, v):
        return ascii_digits(v)


class InventoryItemCreate(_LatinCodes):
    sku: str | None = None
    name: str = Field(..., min_length=1)
    unit: str = "unit"
    list_price: int = Field(0, ge=0)
    barcode: str | None = Field(None, max_length=64)
    reorder_level: float | None = Field(None, ge=0)
    reorder_qty: float | None = Field(None, gt=0)


class InventoryItemUpdate(_LatinCodes):
    name: str | None = Field(None, min_length=1, max_length=256)
    sku: str | None = Field(None, max_length=64)
    unit: str | None = Field(None, min_length=1, max_length=32)
    barcode: str | None = Field(None, max_length=64)
    reorder_level: float | None = Field(None, ge=0)
    reorder_qty: float | None = Field(None, gt=0)
    is_active: bool | None = None


class InventoryItemRead(BaseModel):
    id: UUID
    sku: str | None = None
    name: str
    unit: str
    is_active: bool
    list_price: Money = 0
    barcode: str | None = None
    reorder_level: float | None = None
    reorder_qty: float | None = None

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
    unit_cost: Money
    movement_value: Money
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
    average_cost: Money
    inventory_value: Money
    cogs: Money


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
    total: Money
    rows: list[InventoryMovementRead]


class SalesByProductRow(BaseModel):
    product_name: str
    quantity: float
    sales_amount: Money
    estimated_cost: Money
    profit: Money
    margin_pct: float | None = None


class SalesByInvoiceRow(BaseModel):
    invoice_id: UUID
    invoice_number: str
    issue_date: date
    due_date: date
    status: str
    entity_name: str | None = None
    amount: Money


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

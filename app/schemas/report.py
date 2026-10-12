from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel
from app.core.money import Money


class LedgerSummaryRow(BaseModel):
    account_code: str
    account_name: str
    # کل — the parent GROUP account this (معین) account rolls up into.
    parent_code: str | None = None
    parent_name: str | None = None
    debit_turnover: Money = 0
    credit_turnover: Money = 0
    debit_balance: Money = 0
    credit_balance: Money = 0


class LedgerSummaryResponse(BaseModel):
    # Single-currency view: which currency the figures are in, and which other
    # currencies the books hold (never summed into these figures).
    currency: str | None = None
    other_currencies: list[str] = []
    rows: list[LedgerSummaryRow]
    total_debit_turnover: Money
    total_credit_turnover: Money
    total_debit_balance: Money
    total_credit_balance: Money


class AccountLineDetail(BaseModel):
    transaction_date: date
    reference: str | None
    description: str | None
    debit: Money = 0
    credit: Money = 0
    line_description: str | None


class AccountDetailResponse(BaseModel):
    # Single-currency view: which currency the figures are in, and which other
    # currencies the books hold (never summed into these figures).
    currency: str | None = None
    other_currencies: list[str] = []
    account_code: str
    account_name: str
    # کل — parent GROUP account (this account is the معین level).
    parent_code: str | None = None
    parent_name: str | None = None
    debit_turnover: Money = 0
    credit_turnover: Money = 0
    debit_balance: Money = 0
    credit_balance: Money = 0
    lines: list[AccountLineDetail]


class KpiCard(BaseModel):
    key: str
    label: str
    value: float | int
    unit: str | None = None
    trend: float | None = None


class ForecastRow(BaseModel):
    week_start: date
    projected_inflow: Money
    projected_outflow: Money
    projected_net: Money
    projected_cash: Money
    risk: bool = False


class AgingRow(BaseModel):
    name: str
    current: Money = 0
    days_31_60: Money = 0
    days_60_plus: Money = 0
    total: Money = 0


class ExpenseCategoryRow(BaseModel):
    category: str
    amount: Money


class VendorSpendRow(BaseModel):
    vendor: str
    amount: Money


class MonthlySeriesRow(BaseModel):
    period: str          # "YYYY-MM" in the company's calendar ("1405-07" = Mehr 1405)
    value: Money
    label: str | None = None


class ProfitabilityRow(BaseModel):
    client: str
    revenue: Money
    cost: Money
    profit: Money
    margin_pct: float | None = None


class HealthIssue(BaseModel):
    key: str
    label: str
    count: int
    ratio: float


class HealthChecklistItem(BaseModel):
    item: str
    ok: bool
    detail: str


class AlertItem(BaseModel):
    level: str
    title: str
    message: str


class OwnerDashboardResponse(BaseModel):
    generated_on: date
    # Single-currency view: which currency the figures are in, and which other
    # currencies the books hold (never summed into these figures).
    currency: str | None = None
    other_currencies: list[str] = []
    kpis: list[KpiCard]
    forecast_13_weeks: list[ForecastRow]
    ar_aging: list[AgingRow]
    ap_aging: list[AgingRow]
    expense_by_category: list[ExpenseCategoryRow]
    spend_by_vendor: list[VendorSpendRow]
    monthly_expense_series: list[MonthlySeriesRow]
    profitability_by_client: list[ProfitabilityRow]
    health_score: int
    health_issues: list[HealthIssue]
    close_checklist: list[HealthChecklistItem]
    alerts: list[AlertItem]
    owner_pack_markdown: str


class TransactionSearchRow(BaseModel):
    transaction_id: UUID
    date: date
    reference: str | None = None
    description: str | None = None
    currency: str = "IRR"
    account_code: str
    account_name: str
    debit: Money = 0
    credit: Money = 0
    line_description: str | None = None
    entity_names: list[str] = []


class TransactionSearchResponse(BaseModel):
    rows: list[TransactionSearchRow]
    total_count: int
    page: int
    page_size: int
    total_debit: Money = 0
    total_credit: Money = 0


class MissingReferenceRow(BaseModel):
    transaction_id: str
    date: date
    description: str | None
    suggested_reference: str | None = None


class MissingReferenceResponse(BaseModel):
    items: list[MissingReferenceRow]

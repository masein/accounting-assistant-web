from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class LedgerSummaryRow(BaseModel):
    account_code: str
    account_name: str
    debit_turnover: int = 0
    credit_turnover: int = 0
    debit_balance: int = 0
    credit_balance: int = 0


class LedgerSummaryResponse(BaseModel):
    rows: list[LedgerSummaryRow]
    total_debit_turnover: int
    total_credit_turnover: int
    total_debit_balance: int
    total_credit_balance: int


class AccountLineDetail(BaseModel):
    transaction_date: date
    reference: str | None
    description: str | None
    debit: int = 0
    credit: int = 0
    line_description: str | None


class AccountDetailResponse(BaseModel):
    account_code: str
    account_name: str
    debit_turnover: int = 0
    credit_turnover: int = 0
    debit_balance: int = 0
    credit_balance: int = 0
    lines: list[AccountLineDetail]


class KpiCard(BaseModel):
    key: str
    label: str
    value: float | int
    unit: str | None = None
    trend: float | None = None


class ForecastRow(BaseModel):
    week_start: date
    projected_inflow: int
    projected_outflow: int
    projected_net: int
    projected_cash: int
    risk: bool = False


class AgingRow(BaseModel):
    name: str
    current: int = 0
    days_31_60: int = 0
    days_60_plus: int = 0
    total: int = 0


class ExpenseCategoryRow(BaseModel):
    category: str
    amount: int


class VendorSpendRow(BaseModel):
    vendor: str
    amount: int


class MonthlySeriesRow(BaseModel):
    period: str
    value: int


class ProfitabilityRow(BaseModel):
    client: str
    revenue: int
    cost: int
    profit: int
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


class MissingReferenceRow(BaseModel):
    transaction_id: str
    date: date
    description: str | None
    suggested_reference: str | None = None


class MissingReferenceResponse(BaseModel):
    items: list[MissingReferenceRow]

"""Schemas for Iranian-standard financial statements.

These differ from the generic (`manager_report.py`) schemas because the Iranian
template is presentation-ordered: rows must be emitted in a fixed sequence with
subtotal rows interleaved (سود ناخالص, سود عملیاتی, سود قبل از مالیات, ...).
A dict keyed by section is not enough to express that ordering, so we return
a flat list of `IranStatementRow` with `row_type` and `indent_level` for the UI
to render the correct hierarchy.

Amounts are in full rials (integer). The UI layer scales to millions for display.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.manager_report import ReportPeriod


class IranStatementRow(BaseModel):
    key: str
    label_fa: str
    label_en: str | None = None
    row_type: str = Field(
        "line",
        description="line | subtotal | total | header | spacer",
    )
    indent_level: int = 0
    amount_current: int | None = 0
    amount_prior: int | None = 0
    amount_prior_beginning: int | None = Field(
        default=None,
        description="Restated opening balance of the prior period (third column on the Iranian Balance Sheet). Null when not applicable (e.g. flow statements).",
    )
    change_pct: float | None = Field(
        default=None,
        description="(current - prior) / |prior| * 100, or null when prior is 0/null.",
    )
    is_negative_presentation: bool = Field(
        default=False,
        description="True for expense/deduction lines that the UI renders in parentheses.",
    )


class IranIncomeStatementResponse(BaseModel):
    report_type: str = "iran_income_statement"
    locale: str = "ir"
    period: ReportPeriod
    comparative_period: ReportPeriod | None = None
    scale: str = Field(
        default="rial",
        description="'rial' = full rials (as integers). UI may display as millions.",
    )
    rows: list[IranStatementRow]
    metadata: dict = Field(
        default_factory=dict,
        description="Extra info such as shares_outstanding for EPS (null if unknown).",
    )


class IranBalanceSheetResponse(BaseModel):
    report_type: str = "iran_balance_sheet"
    locale: str = "ir"
    as_of: str  # ISO date string of the current snapshot
    comparative_as_of: str | None = None
    comparative_beginning_as_of: str | None = Field(
        default=None,
        description="Restated opening balance date for the prior period (third column). Defaults to one year before comparative_as_of.",
    )
    scale: str = "rial"
    rows: list[IranStatementRow]
    metadata: dict = Field(default_factory=dict)


class IranEquityMovementCell(BaseModel):
    """One cell in the Statement of Changes in Equity matrix."""
    component: str = Field(description="Equity component column key (e.g. 'capital', 'retained_earnings').")
    amount: int | None = 0


class IranEquityMovementRow(BaseModel):
    key: str
    label_fa: str
    label_en: str | None = None
    row_type: str = "line"  # line | subtotal | total | header
    cells: list[IranEquityMovementCell]
    total: int | None = 0


class IranEquityComponent(BaseModel):
    key: str
    label_fa: str
    label_en: str | None = None


class IranChangesInEquityResponse(BaseModel):
    report_type: str = "iran_changes_in_equity"
    locale: str = "ir"
    period: ReportPeriod
    scale: str = "rial"
    components: list[IranEquityComponent]
    rows: list[IranEquityMovementRow]
    metadata: dict = Field(default_factory=dict)


class IranComprehensiveIncomeResponse(BaseModel):
    """صورت سود و زیان جامع — flows net profit into
    'other comprehensive income' buckets (revaluation surplus, FX translation, …)
    which are placeholders until those movements are tagged explicitly.
    """
    report_type: str = "iran_comprehensive_income"
    locale: str = "ir"
    period: ReportPeriod
    comparative_period: ReportPeriod | None = None
    scale: str = "rial"
    rows: list[IranStatementRow]
    metadata: dict = Field(default_factory=dict)


class IranCashFlowResponse(BaseModel):
    """صورت جریان‌های نقدی — ordered rows following the Iranian template with
    fixed section headers (عملیاتی / سرمایه‌گذاری / تامین مالی / …) and
    prescribed line items. Amounts follow the same signed convention as
    the Income Statement: inflows positive, outflows negative.
    """
    report_type: str = "iran_cash_flow"
    locale: str = "ir"
    period: ReportPeriod
    comparative_period: ReportPeriod | None = None
    scale: str = "rial"
    rows: list[IranStatementRow]
    metadata: dict = Field(default_factory=dict)

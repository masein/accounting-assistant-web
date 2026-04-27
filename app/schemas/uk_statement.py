"""Schemas for UK FRS 102 Section 1A statements (Companies Act 2006 formats).

Mirrors the Iranian-statement schemas in shape but with English-only labels
and the Companies Act / FRS 102 naming conventions. Amounts are integers in
pounds (whole-£). The UI scales as needed.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.manager_report import ReportPeriod


class UKStatementRow(BaseModel):
    key: str
    label: str
    row_type: str = Field(
        "line",
        description="line | subtotal | total | header | spacer",
    )
    indent_level: int = 0
    amount_current: int | None = 0
    amount_prior: int | None = 0
    is_negative_presentation: bool = Field(
        default=False,
        description="True for cost / deduction lines that the UI renders in parentheses.",
    )


class UKBalanceSheetResponse(BaseModel):
    report_type: str = "uk_balance_sheet"
    locale: str = "uk"
    as_of: str
    comparative_as_of: str | None = None
    scale: str = "gbp"
    rows: list[UKStatementRow]
    metadata: dict = Field(default_factory=dict)


class UKIncomeStatementResponse(BaseModel):
    report_type: str = "uk_profit_and_loss"
    locale: str = "uk"
    period: ReportPeriod
    comparative_period: ReportPeriod | None = None
    scale: str = "gbp"
    rows: list[UKStatementRow]
    metadata: dict = Field(default_factory=dict)


class UKComprehensiveIncomeResponse(BaseModel):
    report_type: str = "uk_comprehensive_income"
    locale: str = "uk"
    period: ReportPeriod
    comparative_period: ReportPeriod | None = None
    scale: str = "gbp"
    rows: list[UKStatementRow]
    metadata: dict = Field(default_factory=dict)


class UKEquityComponent(BaseModel):
    key: str
    label: str


class UKEquityMovementCell(BaseModel):
    component: str
    amount: int | None = 0


class UKEquityMovementRow(BaseModel):
    key: str
    label: str
    row_type: str = "line"
    cells: list[UKEquityMovementCell]
    total: int | None = 0


class UKChangesInEquityResponse(BaseModel):
    report_type: str = "uk_changes_in_equity"
    locale: str = "uk"
    period: ReportPeriod
    scale: str = "gbp"
    components: list[UKEquityComponent]
    rows: list[UKEquityMovementRow]
    metadata: dict = Field(default_factory=dict)


class UKCashFlowResponse(BaseModel):
    report_type: str = "uk_cash_flow"
    locale: str = "uk"
    period: ReportPeriod
    comparative_period: ReportPeriod | None = None
    scale: str = "gbp"
    rows: list[UKStatementRow]
    metadata: dict = Field(default_factory=dict)

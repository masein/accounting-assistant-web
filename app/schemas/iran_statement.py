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

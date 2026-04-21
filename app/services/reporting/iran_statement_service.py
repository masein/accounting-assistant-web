"""Iranian-standard financial statements (رهنمودهای استاندارد ایران).

Currently implements:
  * صورت سود و زیان (Income Statement)

Design:
  - Returns an ordered list of rows (see `IranStatementRow`), not a dict.
    The Iranian template depends on row order and interleaved subtotals
    (سود ناخالص, سود عملیاتی, سود قبل از مالیات, …) that a dict cannot
    express naturally.
  - Amounts are signed full rials. Revenue/income rows are positive;
    deduction rows (COGS, expenses, tax) are negative with
    `is_negative_presentation=True` so the UI can render them in parentheses.
  - Subtotals are the signed sum of the preceding rows they summarize.
  - Prior-period comparative column defaults to the same length window,
    one year earlier (Gregorian shift — callers can override with
    explicit `comparative_*` dates).

COA mapping (code prefixes, Iranian chart convention):
  41, 42         → درآمدهای عملیاتی (operating revenue)
  43             → سایر درآمدها (other operating income)
  51, 52         → بهای تمام شده درآمدهای عملیاتی (COGS)
  61 (not 6115)  → هزینه‌های فروش، اداری و عمومی (SG&A)
  6115           → هزینه کاهش ارزش دریافتنی‌ها (receivable impairment)
  621            → هزینه‌های مالی (financial expenses)
  62 (not 621)   → سایر هزینه‌ها (other operating expenses)
  63             → سایر درآمدها و هزینه‌های غیرعملیاتی (non-operating, net)
  641            → هزینه مالیات سال جاری (current-year tax)
  642            → هزینه مالیات سال‌های قبل (prior-years tax)
  68             → عملیات متوقف شده (discontinued operations)

Codes that don't exist in the chart simply contribute 0 to their row.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.account import Account
from app.schemas.iran_statement import IranIncomeStatementResponse, IranStatementRow
from app.schemas.manager_report import ReportPeriod
from app.services.reporting.common import (
    balance_from_turnovers,
    classify_account_code,
    default_period,
)
from app.services.reporting.repository import account_turnovers_between, list_accounts


def _shift_one_year(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        # Feb 29 -> Feb 28 on non-leap years
        return d.replace(year=d.year - 1, day=28)


def _bucket_for_code(code: str) -> str | None:
    """Map an account code to its Iranian-standard income-statement bucket.

    Returns None if the account does not belong on the income statement.
    Order matters: 6115 must be checked before 61, and 621 before 62.
    """
    c = (code or "").strip()
    if not c:
        return None
    if c.startswith("6115"):
        return "impairment_receivables"
    if c.startswith("621"):
        return "financial_expenses"
    if c.startswith("641"):
        return "tax_current_year"
    if c.startswith("642"):
        return "tax_prior_years"
    if c.startswith("68"):
        return "discontinued_ops"
    if c.startswith("41") or c.startswith("42"):
        return "revenue_operating"
    if c.startswith("43"):
        return "other_operating_income"
    if c.startswith("51") or c.startswith("52"):
        return "cogs"
    if c.startswith("61"):
        return "opex_sga"
    if c.startswith("62"):
        return "other_operating_expenses"
    if c.startswith("63"):
        return "non_operating_net"
    return None


def _bucket_totals(
    db: Session,
    accounts: list[Account],
    from_d: date,
    to_d: date,
    currency: str | None,
) -> dict[str, int]:
    """Sum positive (sign-corrected) balances per Iranian-standard bucket for a period."""
    turnovers = {
        account_id: (debit, credit)
        for account_id, debit, credit in account_turnovers_between(db, from_d, to_d, currency=currency)
    }
    buckets: dict[str, int] = {}
    for acc in accounts:
        bucket = _bucket_for_code(acc.code)
        if not bucket:
            continue
        debit, credit = turnovers.get(acc.id, (0, 0))
        acc_type = classify_account_code(acc.code)
        raw = balance_from_turnovers(acc_type, debit, credit)
        # Positive magnitude of the period's net movement on this account.
        # Sign is applied at the row level (below) based on bucket semantics.
        buckets[bucket] = buckets.get(bucket, 0) + max(0, raw)
    return buckets


# Buckets whose values are presented as negatives (deductions) on the statement.
_NEGATIVE_BUCKETS = frozenset({
    "cogs",
    "opex_sga",
    "impairment_receivables",
    "other_operating_expenses",
    "financial_expenses",
    "tax_current_year",
    "tax_prior_years",
})


def _signed(bucket: str, amount: int) -> int:
    return -amount if bucket in _NEGATIVE_BUCKETS else amount


def _pct_change(current: int | None, prior: int | None) -> float | None:
    if current is None or prior is None or prior == 0:
        return None
    return round((current - prior) / abs(prior) * 100, 2)


def _row(
    key: str,
    label_fa: str,
    current_buckets: dict[str, int],
    prior_buckets: dict[str, int],
    *,
    label_en: str | None = None,
    indent_level: int = 1,
) -> IranStatementRow:
    """Build a line row for a single bucket."""
    cur_raw = current_buckets.get(key, 0)
    prior_raw = prior_buckets.get(key, 0)
    cur = _signed(key, cur_raw)
    prior = _signed(key, prior_raw)
    return IranStatementRow(
        key=key,
        label_fa=label_fa,
        label_en=label_en,
        row_type="line",
        indent_level=indent_level,
        amount_current=cur,
        amount_prior=prior,
        change_pct=_pct_change(cur, prior),
        is_negative_presentation=key in _NEGATIVE_BUCKETS,
    )


def _subtotal(
    key: str,
    label_fa: str,
    current_value: int,
    prior_value: int,
    *,
    label_en: str | None = None,
    indent_level: int = 1,
    row_type: str = "subtotal",
) -> IranStatementRow:
    return IranStatementRow(
        key=key,
        label_fa=label_fa,
        label_en=label_en,
        row_type=row_type,
        indent_level=indent_level,
        amount_current=current_value,
        amount_prior=prior_value,
        change_pct=_pct_change(current_value, prior_value),
        is_negative_presentation=False,
    )


def _header(key: str, label_fa: str, *, label_en: str | None = None) -> IranStatementRow:
    return IranStatementRow(
        key=key,
        label_fa=label_fa,
        label_en=label_en,
        row_type="header",
        indent_level=0,
        amount_current=None,
        amount_prior=None,
        change_pct=None,
    )


def _null_row(key: str, label_fa: str, *, label_en: str | None = None, indent_level: int = 1) -> IranStatementRow:
    return IranStatementRow(
        key=key,
        label_fa=label_fa,
        label_en=label_en,
        row_type="line",
        indent_level=indent_level,
        amount_current=None,
        amount_prior=None,
        change_pct=None,
    )


def build_iran_income_statement(
    db: Session,
    from_date: date | None = None,
    to_date: date | None = None,
    comparative_from_date: date | None = None,
    comparative_to_date: date | None = None,
    currency: str | None = None,
) -> IranIncomeStatementResponse:
    period = default_period(from_date, to_date)

    # Default prior period: same window shifted one year earlier.
    if comparative_to_date is None:
        comparative_to_date = _shift_one_year(period.to_date)
    if comparative_from_date is None:
        comparative_from_date = _shift_one_year(period.from_date)

    accounts = list_accounts(db)
    current = _bucket_totals(db, accounts, period.from_date, period.to_date, currency)
    prior = _bucket_totals(db, accounts, comparative_from_date, comparative_to_date, currency)

    def s(bucket: str, side: dict[str, int]) -> int:
        return _signed(bucket, side.get(bucket, 0))

    # Subtotals — signed arithmetic across buckets.
    cur_revenue = s("revenue_operating", current)
    cur_cogs = s("cogs", current)
    gross_cur = cur_revenue + cur_cogs

    cur_sga = s("opex_sga", current)
    cur_impair = s("impairment_receivables", current)
    cur_other_inc = s("other_operating_income", current)
    cur_other_exp = s("other_operating_expenses", current)
    operating_cur = gross_cur + cur_sga + cur_impair + cur_other_inc + cur_other_exp

    cur_fin = s("financial_expenses", current)
    cur_nonop = s("non_operating_net", current)
    before_tax_cur = operating_cur + cur_fin + cur_nonop

    cur_tax_cy = s("tax_current_year", current)
    cur_tax_py = s("tax_prior_years", current)
    cont_net_cur = before_tax_cur + cur_tax_cy + cur_tax_py

    cur_disc = s("discontinued_ops", current)
    net_cur = cont_net_cur + cur_disc

    prior_revenue = s("revenue_operating", prior)
    prior_cogs = s("cogs", prior)
    gross_prior = prior_revenue + prior_cogs

    prior_sga = s("opex_sga", prior)
    prior_impair = s("impairment_receivables", prior)
    prior_other_inc = s("other_operating_income", prior)
    prior_other_exp = s("other_operating_expenses", prior)
    operating_prior = gross_prior + prior_sga + prior_impair + prior_other_inc + prior_other_exp

    prior_fin = s("financial_expenses", prior)
    prior_nonop = s("non_operating_net", prior)
    before_tax_prior = operating_prior + prior_fin + prior_nonop

    prior_tax_cy = s("tax_current_year", prior)
    prior_tax_py = s("tax_prior_years", prior)
    cont_net_prior = before_tax_prior + prior_tax_cy + prior_tax_py

    prior_disc = s("discontinued_ops", prior)
    net_prior = cont_net_prior + prior_disc

    rows: list[IranStatementRow] = [
        _header("continuing_ops", "عملیات در حال تداوم:", label_en="Continuing operations:"),
        _row("revenue_operating", "درآمدهای عملیاتی", current, prior, label_en="Operating revenue"),
        _row("cogs", "بهای تمام شده درآمدهای عملیاتی", current, prior, label_en="Cost of operating revenue"),
        _subtotal("gross_profit", "سود (زیان) ناخالص", gross_cur, gross_prior, label_en="Gross profit (loss)"),
        _row("opex_sga", "هزینه‌های فروش، اداری و عمومی", current, prior, label_en="Selling, general and administrative expenses"),
        _row("impairment_receivables", "هزینه کاهش ارزش دریافتنی‌ها (هزینه استثنایی)", current, prior, label_en="Impairment of receivables"),
        _row("other_operating_income", "سایر درآمدها", current, prior, label_en="Other income"),
        _row("other_operating_expenses", "سایر هزینه‌ها", current, prior, label_en="Other expenses"),
        _subtotal("operating_profit", "سود (زیان) عملیاتی", operating_cur, operating_prior, label_en="Operating profit (loss)"),
        _row("financial_expenses", "هزینه‌های مالی", current, prior, label_en="Finance costs"),
        _row("non_operating_net", "سایر درآمدها و هزینه‌های غیرعملیاتی", current, prior, label_en="Other non-operating income/expenses"),
        _subtotal("profit_before_tax", "سود (زیان) عملیات در حال تداوم قبل از مالیات", before_tax_cur, before_tax_prior, label_en="Profit before tax from continuing operations"),
        _header("tax_section", "هزینه مالیات بر درآمد:", label_en="Income tax expense:"),
        _row("tax_current_year", "سال جاری", current, prior, label_en="Current year", indent_level=2),
        _row("tax_prior_years", "سال‌های قبل", current, prior, label_en="Prior years", indent_level=2),
        _subtotal("continuing_net", "سود (زیان) خالص عملیات در حال تداوم", cont_net_cur, cont_net_prior, label_en="Net profit (loss) from continuing operations"),
        _header("discontinued_ops_section", "عملیات متوقف شده:", label_en="Discontinued operations:"),
        _row("discontinued_ops", "سود (زیان) خالص عملیات متوقف شده", current, prior, label_en="Net profit (loss) from discontinued operations"),
        _subtotal("net_profit", "سود (زیان) خالص", net_cur, net_prior, label_en="Net profit (loss)", row_type="total"),
        _header("eps_section", "سود (زیان) پایه هر سهم:", label_en="Basic earnings per share:"),
        _null_row("eps_operating", "عملیاتی (ریال)", label_en="Operating (rial)", indent_level=2),
        _null_row("eps_non_operating", "غیرعملیاتی (ریال)", label_en="Non-operating (rial)", indent_level=2),
        _null_row("eps_continuing", "ناشی از عملیات در حال تداوم", label_en="From continuing operations", indent_level=2),
        _null_row("eps_discontinued", "ناشی از عملیات متوقف شده", label_en="From discontinued operations", indent_level=2),
        _null_row("eps_basic", "سود (زیان) پایه هر سهم", label_en="Basic EPS"),
        _null_row("eps_net_per_share", "سود (زیان) خالص هر سهم - ریال", label_en="Net EPS (rial)"),
    ]

    return IranIncomeStatementResponse(
        period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
        comparative_period=ReportPeriod(from_date=comparative_from_date, to_date=comparative_to_date),
        rows=rows,
        metadata={
            "shares_outstanding": None,  # not tracked yet — future AppSetting
            "currency": currency,
        },
    )


class IranStatementService:
    def __init__(self, db: Session):
        self.db = db

    def income_statement(
        self,
        from_date: date | None = None,
        to_date: date | None = None,
        comparative_from_date: date | None = None,
        comparative_to_date: date | None = None,
        currency: str | None = None,
    ) -> IranIncomeStatementResponse:
        return build_iran_income_statement(
            self.db,
            from_date=from_date,
            to_date=to_date,
            comparative_from_date=comparative_from_date,
            comparative_to_date=comparative_to_date,
            currency=currency,
        )

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

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.account import Account
from app.schemas.iran_statement import (
    IranBalanceSheetResponse,
    IranChangesInEquityResponse,
    IranEquityComponent,
    IranEquityMovementCell,
    IranEquityMovementRow,
    IranIncomeStatementResponse,
    IranStatementRow,
)
from app.schemas.manager_report import ReportPeriod
from app.services.reporting.common import (
    balance_from_turnovers,
    classify_account_code,
    default_period,
)
from app.services.reporting.repository import (
    account_turnovers_between,
    account_turnovers_upto,
    list_accounts,
)


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


# ---------------------------------------------------------------------------
# Balance Sheet (صورت وضعیت مالی)
# ---------------------------------------------------------------------------
# Prefix → Iranian balance-sheet line mapping. Longer prefixes must appear
# before shorter ones so more-specific matches win (see `_bs_bucket_for_code`).
_BS_CURRENT_ASSET_MAP: list[tuple[str, str]] = [
    # Specific 4-digit codes used by the seeded chart MUST come before the
    # 3-digit Iranian-spec prefixes, because `_match_prefix_bucket` returns the
    # first hit — otherwise `1112` (receivables in the seed) would be swallowed
    # by the 3-digit `111` cash prefix.
    ("1112", "ca_trade_receivables"),    # seed: حساب‌ها و اسناد دریافتنی تجاری
    ("1110", "ca_cash"),                 # seed: موجودی نقد و بانک
    # Iranian-standard 3-digit groupings.
    ("111", "ca_cash"),                  # موجودی نقد
    ("112", "ca_trade_receivables"),     # دریافتنی‌های تجاری و سایر دریافتنی‌ها
    ("113", "ca_st_investments"),        # سرمایه‌گذاری‌های کوتاه‌مدت
    ("114", "ca_inventory"),             # موجودی مواد و کالا
    ("115", "ca_held_for_sale"),         # دارایی‌های نگهداری شده برای فروش
    ("116", "ca_prepayments"),           # سفارشات و پیش‌پرداخت‌ها
]
_BS_NON_CURRENT_ASSET_MAP: list[tuple[str, str]] = [
    ("121", "nca_ppe"),                  # دارایی‌های ثابت مشهود
    ("122", "nca_investment_property"),  # سرمایه‌گذاری در املاک
    ("123", "nca_intangibles"),          # دارایی‌های نامشهود
    ("124", "nca_lt_investments"),       # سرمایه‌گذاری‌های بلندمدت
    ("125", "nca_lt_receivables"),       # دریافتنی‌های بلندمدت
    ("127", "nca_deferred_tax"),         # دارایی مالیات انتقالی
]
_BS_CURRENT_LIAB_MAP: list[tuple[str, str]] = [
    ("211", "cl_trade_payables"),        # پرداختنی‌های تجاری و سایر پرداختنی‌ها
    ("213", "cl_tax_payable"),           # مالیات پرداختنی
    ("214", "cl_dividends_payable"),     # سود سهام پرداختنی
    ("215", "cl_st_loans"),              # تسهیلات مالی
    ("216", "cl_provisions"),            # ذخایر
    ("217", "cl_advances"),              # پیش‌دریافت‌ها
    ("218", "cl_held_for_sale_liab"),    # بدهی‌های مرتبط با دارایی‌های نگهداری‌شده برای فروش
]
_BS_NON_CURRENT_LIAB_MAP: list[tuple[str, str]] = [
    ("221", "ncl_lt_payables"),          # پرداختنی‌های بلندمدت
    ("222", "ncl_lt_loans"),             # تسهیلات مالی بلندمدت
    ("224", "ncl_deferred_tax"),         # بدهی مالیات انتقالی
    ("227", "ncl_employee_benefits"),    # ذخیره مزایای پایان خدمت کارکنان
]
_BS_EQUITY_MAP: list[tuple[str, str]] = [
    ("311", "eq_capital"),               # سرمایه
    ("312", "eq_capital_increase"),      # افزایش سرمایه در جریان
    ("313", "eq_share_premium"),         # صرف سهام
    ("314", "eq_treasury_premium"),      # صرف سهام خزانه
    ("321", "eq_legal_reserve"),         # اندوخته قانونی
    ("322", "eq_other_reserves"),        # سایر اندوخته‌ها
    ("323", "eq_revaluation_surplus"),   # مازاد تجدید ارزیابی دارایی‌ها
    ("324", "eq_fx_translation"),        # تفاوت تسعیر ارز عملیات خارجی
    ("33", "eq_retained_earnings"),      # سود (زیان) انباشته
    ("34", "eq_treasury_stock"),         # سهام خزانه
]


def _match_prefix_bucket(code: str, table: list[tuple[str, str]]) -> str | None:
    for prefix, bucket in table:
        if code.startswith(prefix):
            return bucket
    return None


def _bs_bucket_for_code(code: str) -> tuple[str, str] | None:
    """Map an account code to (section_key, bucket_key) for the balance sheet.

    section_key ∈ {current_assets, non_current_assets, current_liabilities,
                   non_current_liabilities, equity}
    Returns None if the account is not a balance-sheet line.
    """
    c = (code or "").strip()
    if not c:
        return None
    bucket = _match_prefix_bucket(c, _BS_CURRENT_ASSET_MAP)
    if bucket:
        return ("current_assets", bucket)
    # Any remaining 11xx falls into "other current assets".
    if c.startswith("11"):
        return ("current_assets", "ca_other")
    bucket = _match_prefix_bucket(c, _BS_NON_CURRENT_ASSET_MAP)
    if bucket:
        return ("non_current_assets", bucket)
    if c.startswith("12"):
        return ("non_current_assets", "nca_other")
    bucket = _match_prefix_bucket(c, _BS_CURRENT_LIAB_MAP)
    if bucket:
        return ("current_liabilities", bucket)
    if c.startswith("21"):
        return ("current_liabilities", "cl_other")
    bucket = _match_prefix_bucket(c, _BS_NON_CURRENT_LIAB_MAP)
    if bucket:
        return ("non_current_liabilities", bucket)
    if c.startswith("22"):
        return ("non_current_liabilities", "ncl_other")
    bucket = _match_prefix_bucket(c, _BS_EQUITY_MAP)
    if bucket:
        return ("equity", bucket)
    if c.startswith("3"):
        return ("equity", "eq_other")
    return None


def _balance_sheet_buckets(
    db: Session,
    accounts: list[Account],
    as_of: date,
    currency: str | None,
) -> dict[tuple[str, str], int]:
    """Sum sign-corrected balances as-of a date, grouped by (section, bucket)."""
    turnovers = {
        account_id: (debit, credit)
        for account_id, debit, credit in account_turnovers_upto(db, as_of, currency=currency)
    }
    buckets: dict[tuple[str, str], int] = {}
    for acc in accounts:
        key = _bs_bucket_for_code(acc.code)
        if not key:
            continue
        debit, credit = turnovers.get(acc.id, (0, 0))
        acc_type = classify_account_code(acc.code)
        balance = balance_from_turnovers(acc_type, debit, credit)
        # Present as positive magnitude — section context determines interpretation.
        buckets[key] = buckets.get(key, 0) + max(0, balance)
    return buckets


# Ordered list of (section, bucket, label_fa, label_en) that defines the row
# order on the Iranian balance sheet. Every row is always emitted, so the UI
# gets the full prescribed template even when a bucket has no data.
_BS_ROW_ORDER: list[tuple[str, str, str, str]] = [
    ("non_current_assets", "nca_ppe", "دارایی‌های ثابت مشهود", "Property, plant and equipment"),
    ("non_current_assets", "nca_investment_property", "سرمایه‌گذاری در املاک", "Investment property"),
    ("non_current_assets", "nca_intangibles", "دارایی‌های نامشهود", "Intangible assets"),
    ("non_current_assets", "nca_lt_investments", "سرمایه‌گذاری‌های بلندمدت", "Long-term investments"),
    ("non_current_assets", "nca_lt_receivables", "دریافتنی‌های بلندمدت", "Long-term receivables"),
    ("non_current_assets", "nca_deferred_tax", "دارایی مالیات انتقالی", "Deferred tax assets"),
    ("non_current_assets", "nca_other", "سایر دارایی‌ها", "Other non-current assets"),
    ("current_assets", "ca_held_for_sale", "دارایی‌های نگهداری شده برای فروش", "Assets held for sale"),
    ("current_assets", "ca_prepayments", "سفارشات و پیش‌پرداخت‌ها", "Orders and prepayments"),
    ("current_assets", "ca_inventory", "موجودی مواد و کالا", "Inventories"),
    ("current_assets", "ca_trade_receivables", "دریافتنی‌های تجاری و سایر دریافتنی‌ها", "Trade and other receivables"),
    ("current_assets", "ca_st_investments", "سرمایه‌گذاری‌های کوتاه‌مدت", "Short-term investments"),
    ("current_assets", "ca_cash", "موجودی نقد", "Cash and cash equivalents"),
    ("current_assets", "ca_other", "سایر دارایی‌های جاری", "Other current assets"),
    ("equity", "eq_capital", "سرمایه", "Share capital"),
    ("equity", "eq_capital_increase", "افزایش سرمایه در جریان", "Capital increase in progress"),
    ("equity", "eq_share_premium", "صرف سهام", "Share premium"),
    ("equity", "eq_treasury_premium", "صرف سهام خزانه", "Treasury share premium"),
    ("equity", "eq_legal_reserve", "اندوخته قانونی", "Legal reserve"),
    ("equity", "eq_other_reserves", "سایر اندوخته‌ها", "Other reserves"),
    ("equity", "eq_revaluation_surplus", "مازاد تجدید ارزیابی دارایی‌ها", "Revaluation surplus"),
    ("equity", "eq_fx_translation", "تفاوت تسعیر ارز عملیات خارجی", "Foreign operations FX translation"),
    ("equity", "eq_retained_earnings", "سود (زیان) انباشته", "Retained earnings"),
    ("equity", "eq_treasury_stock", "سهام خزانه", "Treasury stock"),
    ("equity", "eq_other", "سایر اقلام حقوق مالکانه", "Other equity"),
    ("non_current_liabilities", "ncl_lt_payables", "پرداختنی‌های بلندمدت", "Long-term payables"),
    ("non_current_liabilities", "ncl_lt_loans", "تسهیلات مالی بلندمدت", "Long-term borrowings"),
    ("non_current_liabilities", "ncl_deferred_tax", "بدهی مالیات انتقالی", "Deferred tax liabilities"),
    ("non_current_liabilities", "ncl_employee_benefits", "ذخیره مزایای پایان خدمت کارکنان", "Employee end-of-service benefits"),
    ("non_current_liabilities", "ncl_other", "سایر بدهی‌های غیرجاری", "Other non-current liabilities"),
    ("current_liabilities", "cl_trade_payables", "پرداختنی‌های تجاری و سایر پرداختنی‌ها", "Trade and other payables"),
    ("current_liabilities", "cl_tax_payable", "مالیات پرداختنی", "Tax payable"),
    ("current_liabilities", "cl_dividends_payable", "سود سهام پرداختنی", "Dividends payable"),
    ("current_liabilities", "cl_st_loans", "تسهیلات مالی", "Short-term borrowings"),
    ("current_liabilities", "cl_provisions", "ذخایر", "Provisions"),
    ("current_liabilities", "cl_advances", "پیش‌دریافت‌ها", "Advances received"),
    ("current_liabilities", "cl_held_for_sale_liab", "بدهی‌های مرتبط با دارایی‌های نگهداری‌شده برای فروش", "Liabilities related to assets held for sale"),
    ("current_liabilities", "cl_other", "سایر بدهی‌های جاری", "Other current liabilities"),
]


def _bs_pct_change(current: int, prior: int) -> float | None:
    if prior == 0:
        return None
    return round((current - prior) / abs(prior) * 100, 2)


def _bs_line(
    section: str, bucket: str, label_fa: str, label_en: str,
    current: dict[tuple[str, str], int], prior: dict[tuple[str, str], int],
    *, indent: int = 1,
) -> IranStatementRow:
    cur = current.get((section, bucket), 0)
    pri = prior.get((section, bucket), 0)
    return IranStatementRow(
        key=bucket,
        label_fa=label_fa,
        label_en=label_en,
        row_type="line",
        indent_level=indent,
        amount_current=cur,
        amount_prior=pri,
        change_pct=_bs_pct_change(cur, pri),
        is_negative_presentation=False,
    )


def _section_total(section: str, buckets: dict[tuple[str, str], int]) -> int:
    return sum(v for (sec, _), v in buckets.items() if sec == section)


def _bs_subtotal(key: str, label_fa: str, label_en: str, cur: int, pri: int, *, row_type: str = "subtotal", indent: int = 0) -> IranStatementRow:
    return IranStatementRow(
        key=key,
        label_fa=label_fa,
        label_en=label_en,
        row_type=row_type,
        indent_level=indent,
        amount_current=cur,
        amount_prior=pri,
        change_pct=_bs_pct_change(cur, pri),
        is_negative_presentation=False,
    )


def _bs_header(key: str, label_fa: str, label_en: str) -> IranStatementRow:
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


def build_iran_balance_sheet(
    db: Session,
    as_of: date | None = None,
    comparative_as_of: date | None = None,
    currency: str | None = None,
) -> IranBalanceSheetResponse:
    today = date.today()
    if as_of is None:
        as_of = today
    if comparative_as_of is None:
        comparative_as_of = _shift_one_year(as_of)

    accounts = list_accounts(db)
    current = _balance_sheet_buckets(db, accounts, as_of, currency)
    prior = _balance_sheet_buckets(db, accounts, comparative_as_of, currency)

    def _lines(section: str) -> list[IranStatementRow]:
        return [
            _bs_line(sec, bkt, label_fa, label_en, current, prior)
            for (sec, bkt, label_fa, label_en) in _BS_ROW_ORDER
            if sec == section
        ]

    total_nca_cur = _section_total("non_current_assets", current)
    total_nca_pri = _section_total("non_current_assets", prior)
    total_ca_cur = _section_total("current_assets", current)
    total_ca_pri = _section_total("current_assets", prior)
    total_eq_cur = _section_total("equity", current)
    total_eq_pri = _section_total("equity", prior)
    total_ncl_cur = _section_total("non_current_liabilities", current)
    total_ncl_pri = _section_total("non_current_liabilities", prior)
    total_cl_cur = _section_total("current_liabilities", current)
    total_cl_pri = _section_total("current_liabilities", prior)

    total_assets_cur = total_nca_cur + total_ca_cur
    total_assets_pri = total_nca_pri + total_ca_pri
    total_liab_cur = total_ncl_cur + total_cl_cur
    total_liab_pri = total_ncl_pri + total_cl_pri
    total_eq_liab_cur = total_eq_cur + total_liab_cur
    total_eq_liab_pri = total_eq_pri + total_liab_pri

    rows: list[IranStatementRow] = []
    rows.append(_bs_header("assets_section", "دارایی‌ها", "Assets"))
    rows.append(_bs_header("nca_section", "دارایی‌های غیرجاری", "Non-current assets"))
    rows.extend(_lines("non_current_assets"))
    rows.append(_bs_subtotal("total_nca", "جمع دارایی‌های غیرجاری", "Total non-current assets", total_nca_cur, total_nca_pri))
    rows.append(_bs_header("ca_section", "دارایی‌های جاری", "Current assets"))
    rows.extend(_lines("current_assets"))
    rows.append(_bs_subtotal("total_ca", "جمع دارایی‌های جاری", "Total current assets", total_ca_cur, total_ca_pri))
    rows.append(_bs_subtotal("total_assets", "جمع دارایی‌ها", "Total assets", total_assets_cur, total_assets_pri, row_type="total"))

    rows.append(_bs_header("eq_liab_section", "حقوق مالکانه و بدهی‌ها", "Equity and liabilities"))
    rows.append(_bs_header("equity_section", "حقوق مالکانه", "Equity"))
    rows.extend(_lines("equity"))
    rows.append(_bs_subtotal("total_equity", "جمع حقوق مالکانه", "Total equity", total_eq_cur, total_eq_pri))
    rows.append(_bs_header("ncl_section", "بدهی‌های غیرجاری", "Non-current liabilities"))
    rows.extend(_lines("non_current_liabilities"))
    rows.append(_bs_subtotal("total_ncl", "جمع بدهی‌های غیرجاری", "Total non-current liabilities", total_ncl_cur, total_ncl_pri))
    rows.append(_bs_header("cl_section", "بدهی‌های جاری", "Current liabilities"))
    rows.extend(_lines("current_liabilities"))
    rows.append(_bs_subtotal("total_cl", "جمع بدهی‌های جاری", "Total current liabilities", total_cl_cur, total_cl_pri))
    rows.append(_bs_subtotal("total_liabilities", "جمع بدهی‌ها", "Total liabilities", total_liab_cur, total_liab_pri))
    rows.append(_bs_subtotal("total_equity_and_liabilities", "جمع حقوق مالکانه و بدهی‌ها", "Total equity and liabilities", total_eq_liab_cur, total_eq_liab_pri, row_type="total"))

    return IranBalanceSheetResponse(
        as_of=as_of.isoformat(),
        comparative_as_of=comparative_as_of.isoformat(),
        rows=rows,
        metadata={
            "currency": currency,
            "balances": {
                "assets_equal_equity_plus_liabilities": total_assets_cur == total_eq_liab_cur,
            },
        },
    )


# ---------------------------------------------------------------------------
# Statement of Changes in Equity (صورت تغییرات در حقوق مالکانه)
# ---------------------------------------------------------------------------
# The matrix uses the same equity component keys as the balance sheet, plus a
# "total" column computed as the sum of all component columns per row. Columns
# left unfilled for a given movement are 0 (not null).

_EQUITY_COMPONENTS: list[tuple[str, str, str]] = [
    ("eq_capital", "سرمایه", "Share capital"),
    ("eq_capital_increase", "افزایش سرمایه در جریان", "Capital increase in progress"),
    ("eq_share_premium", "صرف سهام", "Share premium"),
    ("eq_treasury_premium", "صرف سهام خزانه", "Treasury share premium"),
    ("eq_legal_reserve", "اندوخته قانونی", "Legal reserve"),
    ("eq_other_reserves", "سایر اندوخته‌ها", "Other reserves"),
    ("eq_revaluation_surplus", "مازاد تجدید ارزیابی دارایی‌ها", "Revaluation surplus"),
    ("eq_fx_translation", "تفاوت تسعیر ارز", "FX translation"),
    ("eq_retained_earnings", "سود (زیان) انباشته", "Retained earnings"),
    ("eq_treasury_stock", "سهام خزانه", "Treasury stock"),
]


def _equity_matrix_row(
    key: str,
    label_fa: str,
    values: dict[str, int],
    *,
    label_en: str | None = None,
    row_type: str = "line",
) -> IranEquityMovementRow:
    cells = [IranEquityMovementCell(component=k, amount=int(values.get(k, 0))) for k, _, _ in _EQUITY_COMPONENTS]
    total = sum(c.amount or 0 for c in cells)
    return IranEquityMovementRow(key=key, label_fa=label_fa, label_en=label_en, row_type=row_type, cells=cells, total=total)


def _equity_empty_row(key: str, label_fa: str, *, label_en: str | None = None) -> IranEquityMovementRow:
    return _equity_matrix_row(key, label_fa, {}, label_en=label_en)


def _opening_equity_balances(
    db: Session, accounts: list[Account], as_of: date, currency: str | None
) -> dict[str, int]:
    """Equity component balances at end-of-day `as_of` (used for both opening and closing)."""
    buckets = _balance_sheet_buckets(db, accounts, as_of, currency)
    return {bucket: buckets.get(("equity", bucket), 0) for _, bucket in _BS_EQUITY_MAP}


def _period_net_profit(
    db: Session, from_d: date, to_d: date, currency: str | None
) -> int:
    """Compute period net profit by reusing the income-statement bucket sums."""
    accounts = list_accounts(db)
    cur = _bucket_totals(db, accounts, from_d, to_d, currency)

    def s(bucket: str) -> int:
        return _signed(bucket, cur.get(bucket, 0))

    gross = s("revenue_operating") + s("cogs")
    operating = gross + s("opex_sga") + s("impairment_receivables") + s("other_operating_income") + s("other_operating_expenses")
    before_tax = operating + s("financial_expenses") + s("non_operating_net")
    cont_net = before_tax + s("tax_current_year") + s("tax_prior_years")
    return cont_net + s("discontinued_ops")


def build_iran_changes_in_equity(
    db: Session,
    from_date: date | None = None,
    to_date: date | None = None,
    currency: str | None = None,
) -> IranChangesInEquityResponse:
    period = default_period(from_date, to_date)
    accounts = list_accounts(db)

    # Opening balance = end-of-day equity-account balances the day before period start.
    opening_as_of = period.from_date - timedelta(days=1)
    opening = _opening_equity_balances(db, accounts, opening_as_of, currency)

    net_profit = _period_net_profit(db, period.from_date, period.to_date, currency)

    # Closing balance = opening + sum of explicit movement rows. Computing it
    # arithmetically (rather than re-querying equity accounts at period.to_date)
    # guarantees the matrix reconciles even when P&L hasn't been closed into
    # retained earnings yet. The only non-zero movement today is net_profit
    # flowing into retained_earnings; other movement rows stay zero until
    # capital-increase / dividend / buyback events are tagged explicitly.
    closing = dict(opening)
    closing["eq_retained_earnings"] = closing.get("eq_retained_earnings", 0) + net_profit

    rows: list[IranEquityMovementRow] = [
        _equity_matrix_row("opening_balance", "مانده در ابتدای دوره", opening, label_en="Opening balance"),
        _equity_empty_row("error_corrections", "اصلاح اشتباهات", label_en="Error corrections"),
        _equity_empty_row("policy_changes", "تغییر در رویه‌های حسابداری", label_en="Accounting policy changes"),
        _equity_matrix_row("restated_opening", "مانده تجدید ارائه شده در ابتدای دوره", opening, label_en="Restated opening balance", row_type="subtotal"),
        _equity_matrix_row(
            "net_profit_reported",
            "سود (زیان) خالص گزارش شده در صورت‌های مالی",
            {"eq_retained_earnings": net_profit},
            label_en="Net profit (loss) reported",
        ),
        _equity_empty_row("other_comprehensive_income", "سایر اقلام سود و زیان جامع پس از کسر مالیات", label_en="Other comprehensive income (net of tax)"),
        _equity_matrix_row(
            "total_comprehensive_income",
            "سود (زیان) جامع سال",
            {"eq_retained_earnings": net_profit},
            label_en="Total comprehensive income",
            row_type="subtotal",
        ),
        _equity_empty_row("approved_dividends", "سود سهام مصوب", label_en="Approved dividends"),
        _equity_empty_row("capital_increase", "افزایش سرمایه", label_en="Capital increase"),
        _equity_empty_row("treasury_buyback", "خرید سهام خزانه", label_en="Treasury stock buyback"),
        _equity_empty_row("treasury_sale", "فروش سهام خزانه", label_en="Treasury stock sale"),
        _equity_empty_row("transfer_to_retained", "انتقال از سایر اقلام حقوق مالکانه به سود و زیان انباشته", label_en="Transfers to retained earnings"),
        _equity_empty_row("allocate_legal_reserve", "تخصیص به اندوخته قانونی", label_en="Allocation to legal reserve"),
        _equity_empty_row("allocate_other_reserves", "تخصیص به سایر اندوخته‌ها", label_en="Allocation to other reserves"),
        _equity_matrix_row("closing_balance", "مانده در پایان دوره", closing, label_en="Closing balance", row_type="total"),
    ]

    components = [
        IranEquityComponent(key=k, label_fa=label_fa, label_en=label_en)
        for k, label_fa, label_en in _EQUITY_COMPONENTS
    ]

    return IranChangesInEquityResponse(
        period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
        components=components,
        rows=rows,
        metadata={
            "currency": currency,
            "note": "Specific movements (dividends, capital raises, buybacks, reserve allocations) are placeholders until those events are tagged explicitly on transactions.",
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

    def balance_sheet(
        self,
        as_of: date | None = None,
        comparative_as_of: date | None = None,
        currency: str | None = None,
    ) -> IranBalanceSheetResponse:
        return build_iran_balance_sheet(
            self.db,
            as_of=as_of,
            comparative_as_of=comparative_as_of,
            currency=currency,
        )

    def changes_in_equity(
        self,
        from_date: date | None = None,
        to_date: date | None = None,
        currency: str | None = None,
    ) -> IranChangesInEquityResponse:
        return build_iran_changes_in_equity(
            self.db,
            from_date=from_date,
            to_date=to_date,
            currency=currency,
        )

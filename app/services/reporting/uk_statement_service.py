"""UK FRS 102 Section 1A statements (Companies Act 2006 formats).

Implements the five small-company statements:

* Statement of Financial Position (Companies Act format 1)
* Profit and Loss Account (format 1, by function)
* Statement of Comprehensive Income
* Statement of Changes in Equity
* Statement of Cash Flows (FRS 102 Section 7)

All amounts are stored as whole pounds (integers). Line items are derived
from the seeded UK chart of accounts via prefix mapping. Lines that need
metadata we don't yet capture (e.g. depreciation/amortisation as separate
P&L lines, OCI items) are emitted as zero placeholders so the statement
structure is always complete.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.account import Account
from app.schemas.manager_report import ReportPeriod
from app.schemas.uk_statement import (
    UKBalanceSheetResponse,
    UKCashFlowResponse,
    UKChangesInEquityResponse,
    UKComprehensiveIncomeResponse,
    UKEquityComponent,
    UKEquityMovementCell,
    UKEquityMovementRow,
    UKIncomeStatementResponse,
    UKStatementRow,
)
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
        return d.replace(year=d.year - 1, day=28)


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------


def _line(
    key: str, label: str, cur: int, prior: int,
    *, indent: int = 1, negative_presentation: bool = False,
) -> UKStatementRow:
    return UKStatementRow(
        key=key, label=label, row_type="line", indent_level=indent,
        amount_current=cur, amount_prior=prior,
        is_negative_presentation=negative_presentation,
    )


def _subtotal(
    key: str, label: str, cur: int, prior: int,
    *, row_type: str = "subtotal", indent: int = 0,
) -> UKStatementRow:
    return UKStatementRow(
        key=key, label=label, row_type=row_type, indent_level=indent,
        amount_current=cur, amount_prior=prior,
    )


def _header(key: str, label: str, *, indent: int = 0) -> UKStatementRow:
    return UKStatementRow(
        key=key, label=label, row_type="header", indent_level=indent,
        amount_current=None, amount_prior=None,
    )


# ---------------------------------------------------------------------------
# Balance Sheet (Companies Act format 1)
# ---------------------------------------------------------------------------
# Prefix → BS bucket. Order matters: longer prefixes first.
_UK_BS_MAP: list[tuple[str, str]] = [
    # Intangible assets (NBV = cost − accum amort, both classified ASSET so
    # the contra-amort accounts naturally subtract via signed sum).
    ("01", "fa_intangibles"),
    # Tangible fixed assets — NBV
    ("00", "fa_tangibles"),
    # Investments held as fixed assets
    ("02", "fa_investments"),
    # Current assets
    ("1000", "ca_stocks"),
    ("1100", "ca_debtors"),
    ("1300", "ca_debtors"),
    ("1400", "ca_debtors"),
    ("1200", "ca_cash"),
    ("1210", "ca_cash"),
    ("1220", "ca_cash"),
    # Creditors due within one year (current liabilities)
    ("21", "cl_creditors"),
    ("22", "cl_creditors"),
    ("23", "cl_creditors"),
    ("24", "cl_creditors"),
    ("25", "cl_creditors"),
    ("26", "cl_creditors"),
    ("27", "cl_creditors"),
    # Provisions for liabilities
    ("295", "ncl_provisions"),
    # Creditors due after more than one year
    ("28", "ncl_creditors"),
    ("29", "ncl_creditors"),
    # Capital and reserves
    ("3000", "eq_share_capital"),
    ("3010", "eq_share_premium"),
    ("3020", "eq_revaluation_reserve"),
    ("3030", "eq_other_reserves"),
    ("3100", "eq_pl_account"),
]


def _bs_section_for_bucket(bucket: str) -> str:
    if bucket.startswith("fa_"):
        return "fixed_assets"
    if bucket.startswith("ca_"):
        return "current_assets"
    if bucket.startswith("cl_"):
        return "current_liabilities"
    if bucket.startswith("ncl_"):
        return "non_current_liabilities"
    if bucket.startswith("eq_"):
        return "equity"
    return "other"


def _bs_bucket_for_code(code: str) -> tuple[str, str] | None:
    c = (code or "").strip()
    if not c:
        return None
    for prefix, bucket in _UK_BS_MAP:
        if c.startswith(prefix):
            return (_bs_section_for_bucket(bucket), bucket)
    return None


def _uk_pl_to_date(
    db: Session, accounts: list[Account], as_of: date, currency: str | None,
) -> int:
    """Net profit/(loss) since inception, computed directly from the P&L
    accounts. Used to fold un-closed P&L into retained earnings on the BS."""
    from app.services.reporting.common import REVENUE, EXPENSE

    turnovers = {
        account_id: (debit, credit)
        for account_id, debit, credit in account_turnovers_upto(db, as_of, currency=currency)
    }
    total = 0
    for acc in accounts:
        acc_type = classify_account_code(acc.code)
        if acc_type not in (REVENUE, EXPENSE):
            continue
        d, c = turnovers.get(acc.id, (0, 0))
        balance = balance_from_turnovers(acc_type, d, c)
        if acc_type == REVENUE:
            total += int(balance)
        else:
            total -= int(balance)
    return total


def _uk_balance_sheet_buckets(
    db: Session, accounts: list[Account], as_of: date, currency: str | None,
) -> dict[tuple[str, str], int]:
    """Sum signed balances by (section, bucket).

    Unlike the Iranian variant, the UK chart includes contra accounts
    (accumulated depreciation, accumulated amortisation) under the same
    bucket as the cost account, so we keep the sign — credits on a contra
    asset naturally reduce the bucket total to NBV.

    Also folds the net P&L since inception into ``eq_pl_account`` so the
    Balance Sheet always balances, even when no closing entries have been
    posted (typical for small-company demos).
    """
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
        balance = balance_from_turnovers(classify_account_code(acc.code), debit, credit)
        buckets[key] = buckets.get(key, 0) + int(balance)
    # Implicit closing: add un-closed P&L to retained earnings.
    pl_to_date = _uk_pl_to_date(db, accounts, as_of, currency)
    if pl_to_date:
        buckets[("equity", "eq_pl_account")] = buckets.get(("equity", "eq_pl_account"), 0) + pl_to_date
    return buckets


# Ordered row template (label_en for each prescribed FRS 102 1A line).
_UK_BS_ROW_ORDER: list[tuple[str, str, str]] = [
    ("fixed_assets", "fa_intangibles", "Intangible assets"),
    ("fixed_assets", "fa_tangibles", "Tangible assets"),
    ("fixed_assets", "fa_investments", "Investments"),
    ("current_assets", "ca_stocks", "Stocks"),
    ("current_assets", "ca_debtors", "Debtors: amounts falling due within one year"),
    ("current_assets", "ca_cash", "Cash at bank and in hand"),
    ("current_liabilities", "cl_creditors", "Creditors: amounts falling due within one year"),
    ("non_current_liabilities", "ncl_creditors", "Creditors: amounts falling due after more than one year"),
    ("non_current_liabilities", "ncl_provisions", "Provisions for liabilities"),
    ("equity", "eq_share_capital", "Called up share capital"),
    ("equity", "eq_share_premium", "Share premium account"),
    ("equity", "eq_revaluation_reserve", "Revaluation reserve"),
    ("equity", "eq_other_reserves", "Other reserves"),
    ("equity", "eq_pl_account", "Profit and loss account"),
]


def _section_total(section: str, buckets: dict[tuple[str, str], int]) -> int:
    return sum(v for (sec, _), v in buckets.items() if sec == section)


def build_uk_balance_sheet(
    db: Session,
    as_of: date | None = None,
    comparative_as_of: date | None = None,
    currency: str | None = None,
) -> UKBalanceSheetResponse:
    today = date.today()
    if as_of is None:
        as_of = today
    if comparative_as_of is None:
        comparative_as_of = _shift_one_year(as_of)

    accounts = list_accounts(db)
    current = _uk_balance_sheet_buckets(db, accounts, as_of, currency)
    prior = _uk_balance_sheet_buckets(db, accounts, comparative_as_of, currency)

    def _emit(section: str) -> list[UKStatementRow]:
        return [
            _line(bkt, label, current.get((sec, bkt), 0), prior.get((sec, bkt), 0))
            for (sec, bkt, label) in _UK_BS_ROW_ORDER
            if sec == section
        ]

    fa_cur = _section_total("fixed_assets", current)
    fa_pri = _section_total("fixed_assets", prior)
    ca_cur = _section_total("current_assets", current)
    ca_pri = _section_total("current_assets", prior)
    cl_cur = _section_total("current_liabilities", current)
    cl_pri = _section_total("current_liabilities", prior)
    ncl_cur = _section_total("non_current_liabilities", current)
    ncl_pri = _section_total("non_current_liabilities", prior)
    eq_cur = _section_total("equity", current)
    eq_pri = _section_total("equity", prior)

    net_current_cur = ca_cur - cl_cur
    net_current_pri = ca_pri - cl_pri
    total_assets_less_cl_cur = fa_cur + net_current_cur
    total_assets_less_cl_pri = fa_pri + net_current_pri
    net_assets_cur = total_assets_less_cl_cur - ncl_cur
    net_assets_pri = total_assets_less_cl_pri - ncl_pri

    rows: list[UKStatementRow] = [
        _header("fixed_assets_section", "Fixed assets"),
        *_emit("fixed_assets"),
        _subtotal("total_fixed_assets", "Total fixed assets", fa_cur, fa_pri),
        _header("current_assets_section", "Current assets"),
        *_emit("current_assets"),
        _subtotal("total_current_assets", "Total current assets", ca_cur, ca_pri),
        _line("creditors_within_one_year", "Creditors: amounts falling due within one year", -cl_cur, -cl_pri, indent=0, negative_presentation=True),
        _subtotal("net_current_assets", "Net current assets/(liabilities)", net_current_cur, net_current_pri),
        _subtotal("total_assets_less_cl", "Total assets less current liabilities", total_assets_less_cl_cur, total_assets_less_cl_pri, row_type="subtotal"),
        _line("creditors_after_one_year", "Creditors: amounts falling due after more than one year", -(_section_total("non_current_liabilities", current) - current.get(("non_current_liabilities", "ncl_provisions"), 0)), -(_section_total("non_current_liabilities", prior) - prior.get(("non_current_liabilities", "ncl_provisions"), 0)), indent=0, negative_presentation=True),
        _line("provisions_for_liabilities", "Provisions for liabilities", -current.get(("non_current_liabilities", "ncl_provisions"), 0), -prior.get(("non_current_liabilities", "ncl_provisions"), 0), indent=0, negative_presentation=True),
        _subtotal("net_assets", "Net assets", net_assets_cur, net_assets_pri, row_type="total"),
        _header("capital_reserves_section", "Capital and reserves"),
        *_emit("equity"),
        _subtotal("total_capital_reserves", "Total capital and reserves", eq_cur, eq_pri, row_type="total"),
    ]

    return UKBalanceSheetResponse(
        as_of=as_of.isoformat(),
        comparative_as_of=comparative_as_of.isoformat(),
        rows=rows,
        metadata={
            "currency": currency or "GBP",
            "balances": {
                "net_assets_equals_capital_reserves": net_assets_cur == eq_cur,
            },
        },
    )


# ---------------------------------------------------------------------------
# Profit and Loss Account (FRS 102 1A, format 1, by function)
# ---------------------------------------------------------------------------
# Prefix → P&L bucket.
_UK_PL_MAP: list[tuple[str, str]] = [
    ("4000", "turnover"),
    ("4100", "turnover"),         # sales returns — sign is captured by ledger direction
    ("4200", "other_operating_income"),
    ("5", "cost_of_sales"),
    ("70", "distribution_costs"),
    ("71", "admin_expenses"),
    ("72", "admin_expenses"),
    ("73", "admin_expenses"),
    ("74", "admin_expenses"),
    ("75", "admin_expenses"),
    ("76", "admin_expenses"),
    ("77", "admin_expenses"),
    ("78", "admin_expenses"),
    ("79", "admin_expenses"),
    ("8000", "admin_expenses"),    # bank charges
    ("8500", "admin_expenses"),    # depreciation
    ("8600", "admin_expenses"),    # amortisation
    ("8100", "interest_payable"),
    ("8200", "interest_payable"),
    ("8300", "interest_receivable"),
    ("8400", "investment_income"),
    ("9", "tax_on_profit"),
]


def _pl_bucket_for_code(code: str) -> str | None:
    c = (code or "").strip()
    if not c:
        return None
    for prefix, bucket in _UK_PL_MAP:
        if c.startswith(prefix):
            return bucket
    return None


_UK_PL_NEGATIVE_BUCKETS = frozenset({
    "cost_of_sales",
    "distribution_costs",
    "admin_expenses",
    "interest_payable",
    "tax_on_profit",
})


def _pl_signed(bucket: str, amount: int) -> int:
    return -amount if bucket in _UK_PL_NEGATIVE_BUCKETS else amount


def _uk_pl_buckets(
    db: Session, accounts: list[Account], from_d: date, to_d: date, currency: str | None,
) -> dict[str, int]:
    turnovers = {
        account_id: (debit, credit)
        for account_id, debit, credit in account_turnovers_between(db, from_d, to_d, currency=currency)
    }
    buckets: dict[str, int] = {}
    for acc in accounts:
        bucket = _pl_bucket_for_code(acc.code)
        if not bucket:
            continue
        debit, credit = turnovers.get(acc.id, (0, 0))
        raw = balance_from_turnovers(classify_account_code(acc.code), debit, credit)
        buckets[bucket] = buckets.get(bucket, 0) + max(0, int(raw))
    return buckets


def build_uk_income_statement(
    db: Session,
    from_date: date | None = None,
    to_date: date | None = None,
    comparative_from_date: date | None = None,
    comparative_to_date: date | None = None,
    currency: str | None = None,
) -> UKIncomeStatementResponse:
    period = default_period(from_date, to_date)
    if comparative_to_date is None:
        comparative_to_date = _shift_one_year(period.to_date)
    if comparative_from_date is None:
        comparative_from_date = _shift_one_year(period.from_date)

    accounts = list_accounts(db)
    current = _uk_pl_buckets(db, accounts, period.from_date, period.to_date, currency)
    prior = _uk_pl_buckets(db, accounts, comparative_from_date, comparative_to_date, currency)

    def s(bucket: str, side: dict[str, int]) -> int:
        return _pl_signed(bucket, side.get(bucket, 0))

    turnover_cur = s("turnover", current)
    turnover_pri = s("turnover", prior)
    cogs_cur = s("cost_of_sales", current)
    cogs_pri = s("cost_of_sales", prior)
    gross_cur = turnover_cur + cogs_cur
    gross_pri = turnover_pri + cogs_pri

    dist_cur = s("distribution_costs", current)
    dist_pri = s("distribution_costs", prior)
    admin_cur = s("admin_expenses", current)
    admin_pri = s("admin_expenses", prior)
    other_inc_cur = s("other_operating_income", current)
    other_inc_pri = s("other_operating_income", prior)
    operating_cur = gross_cur + dist_cur + admin_cur + other_inc_cur
    operating_pri = gross_pri + dist_pri + admin_pri + other_inc_pri

    inv_inc_cur = s("investment_income", current)
    inv_inc_pri = s("investment_income", prior)
    int_recv_cur = s("interest_receivable", current)
    int_recv_pri = s("interest_receivable", prior)
    int_pay_cur = s("interest_payable", current)
    int_pay_pri = s("interest_payable", prior)
    before_tax_cur = operating_cur + inv_inc_cur + int_recv_cur + int_pay_cur
    before_tax_pri = operating_pri + inv_inc_pri + int_recv_pri + int_pay_pri

    tax_cur = s("tax_on_profit", current)
    tax_pri = s("tax_on_profit", prior)
    net_cur = before_tax_cur + tax_cur
    net_pri = before_tax_pri + tax_pri

    rows: list[UKStatementRow] = [
        _line("turnover", "Turnover", turnover_cur, turnover_pri),
        _line("cost_of_sales", "Cost of sales", cogs_cur, cogs_pri, negative_presentation=True),
        _subtotal("gross_profit", "Gross profit/(loss)", gross_cur, gross_pri),
        _line("distribution_costs", "Distribution costs", dist_cur, dist_pri, negative_presentation=True),
        _line("admin_expenses", "Administrative expenses", admin_cur, admin_pri, negative_presentation=True),
        _line("other_operating_income", "Other operating income", other_inc_cur, other_inc_pri),
        _subtotal("operating_profit", "Operating profit/(loss)", operating_cur, operating_pri),
        _line("investment_income", "Income from fixed asset investments", inv_inc_cur, inv_inc_pri),
        _line("interest_receivable", "Interest receivable and similar income", int_recv_cur, int_recv_pri),
        _line("interest_payable", "Interest payable and similar charges", int_pay_cur, int_pay_pri, negative_presentation=True),
        _subtotal("profit_before_tax", "Profit/(loss) before taxation", before_tax_cur, before_tax_pri),
        _line("tax_on_profit", "Tax on profit/(loss)", tax_cur, tax_pri, negative_presentation=True),
        _subtotal("profit_for_year", "Profit/(loss) for the financial year", net_cur, net_pri, row_type="total"),
    ]

    return UKIncomeStatementResponse(
        period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
        comparative_period=ReportPeriod(from_date=comparative_from_date, to_date=comparative_to_date),
        rows=rows,
        metadata={"currency": currency or "GBP"},
    )


# ---------------------------------------------------------------------------
# Statement of Comprehensive Income
# ---------------------------------------------------------------------------


def _uk_period_net_profit(
    db: Session, from_d: date, to_d: date, currency: str | None,
) -> int:
    accounts = list_accounts(db)
    cur = _uk_pl_buckets(db, accounts, from_d, to_d, currency)

    def s(b: str) -> int:
        return _pl_signed(b, cur.get(b, 0))

    gross = s("turnover") + s("cost_of_sales")
    operating = gross + s("distribution_costs") + s("admin_expenses") + s("other_operating_income")
    before_tax = operating + s("investment_income") + s("interest_receivable") + s("interest_payable")
    return before_tax + s("tax_on_profit")


def build_uk_comprehensive_income(
    db: Session,
    from_date: date | None = None,
    to_date: date | None = None,
    comparative_from_date: date | None = None,
    comparative_to_date: date | None = None,
    currency: str | None = None,
) -> UKComprehensiveIncomeResponse:
    period = default_period(from_date, to_date)
    if comparative_to_date is None:
        comparative_to_date = _shift_one_year(period.to_date)
    if comparative_from_date is None:
        comparative_from_date = _shift_one_year(period.from_date)

    np_cur = _uk_period_net_profit(db, period.from_date, period.to_date, currency)
    np_pri = _uk_period_net_profit(db, comparative_from_date, comparative_to_date, currency)
    # OCI lines remain zero placeholders until tagged.
    oci_cur = 0
    oci_pri = 0

    rows: list[UKStatementRow] = [
        _subtotal("profit_for_year", "Profit/(loss) for the financial year", np_cur, np_pri, indent=0),
        _header("oci_section", "Other comprehensive income (net of tax):"),
        _line("oci_revaluation", "Revaluation gains/(losses) on tangible assets", 0, 0, indent=2),
        _line("oci_fx_translation", "Foreign currency translation differences", 0, 0, indent=2),
        _line("oci_other", "Other comprehensive income items", 0, 0, indent=2),
        _line("oci_tax", "Tax on other comprehensive income", 0, 0, indent=2, negative_presentation=True),
        _subtotal("oci_total", "Total other comprehensive income, net of tax", oci_cur, oci_pri),
        _subtotal("total_comprehensive_income", "Total comprehensive income for the year", np_cur + oci_cur, np_pri + oci_pri, row_type="total"),
    ]

    return UKComprehensiveIncomeResponse(
        period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
        comparative_period=ReportPeriod(from_date=comparative_from_date, to_date=comparative_to_date),
        rows=rows,
        metadata={
            "currency": currency or "GBP",
            "note": "OCI lines remain zero until revaluation / FX translation / other OCI movements are tagged explicitly on transactions.",
        },
    )


# ---------------------------------------------------------------------------
# Statement of Changes in Equity
# ---------------------------------------------------------------------------

_UK_EQUITY_COMPONENTS: list[tuple[str, str]] = [
    ("eq_share_capital", "Share capital"),
    ("eq_share_premium", "Share premium account"),
    ("eq_revaluation_reserve", "Revaluation reserve"),
    ("eq_other_reserves", "Other reserves"),
    ("eq_pl_account", "Profit and loss account"),
]


def _uk_equity_balances(
    db: Session, accounts: list[Account], as_of: date, currency: str | None,
) -> dict[str, int]:
    buckets = _uk_balance_sheet_buckets(db, accounts, as_of, currency)
    return {bucket: buckets.get(("equity", bucket), 0) for _, bucket in _UK_EQUITY_COMPONENTS}


def _uk_equity_row(
    key: str, label: str, values: dict[str, int], *, row_type: str = "line",
) -> UKEquityMovementRow:
    cells = [UKEquityMovementCell(component=k, amount=int(values.get(k, 0))) for k, _ in _UK_EQUITY_COMPONENTS]
    total = sum(c.amount or 0 for c in cells)
    return UKEquityMovementRow(key=key, label=label, row_type=row_type, cells=cells, total=total)


def _uk_equity_empty(key: str, label: str) -> UKEquityMovementRow:
    return _uk_equity_row(key, label, {})


def _uk_equity_header(key: str, label: str) -> UKEquityMovementRow:
    return _uk_equity_row(key, label, {}, row_type="header")


def build_uk_changes_in_equity(
    db: Session,
    from_date: date | None = None,
    to_date: date | None = None,
    comparative_from_date: date | None = None,
    comparative_to_date: date | None = None,
    currency: str | None = None,
) -> UKChangesInEquityResponse:
    period = default_period(from_date, to_date)
    if comparative_to_date is None:
        comparative_to_date = _shift_one_year(period.to_date)
    if comparative_from_date is None:
        comparative_from_date = _shift_one_year(period.from_date)

    accounts = list_accounts(db)
    comparative_opening = _uk_equity_balances(db, accounts, comparative_from_date - timedelta(days=1), currency)
    opening = _uk_equity_balances(db, accounts, period.from_date - timedelta(days=1), currency)
    comparative_np = _uk_period_net_profit(db, comparative_from_date, comparative_to_date, currency)
    net_profit = _uk_period_net_profit(db, period.from_date, period.to_date, currency)

    closing = dict(opening)
    closing["eq_pl_account"] = closing.get("eq_pl_account", 0) + net_profit

    rows: list[UKEquityMovementRow] = [
        _uk_equity_row("comparative_opening", f"At {comparative_from_date.isoformat()}", comparative_opening),
        _uk_equity_header("comparative_period", f"Movements in the year ended {comparative_to_date.isoformat()}"),
        _uk_equity_row("comparative_profit", "Profit for the year", {"eq_pl_account": comparative_np}),
        _uk_equity_empty("comparative_oci", "Other comprehensive income"),
        _uk_equity_row("comparative_total_ci", "Total comprehensive income", {"eq_pl_account": comparative_np}, row_type="subtotal"),
        _uk_equity_empty("comparative_shares_issued", "Shares issued in the year"),
        _uk_equity_empty("comparative_dividends", "Dividends declared and paid"),
        _uk_equity_empty("comparative_transfer_reserves", "Transfers between reserves"),
        _uk_equity_row("opening", f"At {(period.from_date - timedelta(days=1)).isoformat()}", opening, row_type="subtotal"),
        _uk_equity_header("current_period", f"Movements in the year ended {period.to_date.isoformat()}"),
        _uk_equity_row("profit_for_year", "Profit for the year", {"eq_pl_account": net_profit}),
        _uk_equity_empty("oci", "Other comprehensive income"),
        _uk_equity_row("total_ci", "Total comprehensive income", {"eq_pl_account": net_profit}, row_type="subtotal"),
        _uk_equity_empty("shares_issued", "Shares issued in the year"),
        _uk_equity_empty("dividends", "Dividends declared and paid"),
        _uk_equity_empty("transfer_reserves", "Transfers between reserves"),
        _uk_equity_row("closing", f"At {period.to_date.isoformat()}", closing, row_type="total"),
    ]

    components = [UKEquityComponent(key=k, label=label) for k, label in _UK_EQUITY_COMPONENTS]

    return UKChangesInEquityResponse(
        period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
        components=components,
        rows=rows,
        metadata={
            "currency": currency or "GBP",
            "comparative_period": {
                "from_date": comparative_from_date.isoformat(),
                "to_date": comparative_to_date.isoformat(),
            },
            "note": "Shares-issued / dividends / inter-reserve transfers are placeholders until those events are tagged explicitly on transactions.",
        },
    )


# ---------------------------------------------------------------------------
# Cash Flow Statement (FRS 102 Section 7, indirect method skeleton)
# ---------------------------------------------------------------------------
#
# The UK indirect-method cash flow ideally reconciles operating profit to
# operating cash via working-capital movements. We don't yet capture
# dep/amort separately on transactions, so this implementation uses the
# direct counterparty-prefix classification (same approach as the Iranian
# template) and shows the prescribed FRS 102 line skeleton.

_UK_CF_CATEGORY_MAP: list[tuple[str, str]] = [
    # Investing — fixed-asset categories
    ("00", "inv_ppe"),
    ("01", "inv_intangibles"),
    ("02", "inv_investments"),
    # Financing
    ("3000", "fin_share_capital"),
    ("3010", "fin_share_premium"),
    ("2600", "fin_borrowings"),     # bank loan ST current portion
    ("2800", "fin_borrowings"),     # bank loan LT
    ("2810", "fin_lease"),
    # Tax
    ("2300", "op_tax_paid"),
    # Interest receipts / payments — captured under operating in FRS 102 1A
    ("8100", "op_interest_paid"),
    ("8200", "op_interest_paid"),
    ("8300", "op_interest_received"),
    ("8400", "op_investment_income"),
]


def _cf_section_for_uk(category: str) -> str:
    if category.startswith("inv_"):
        return "investing"
    if category.startswith("fin_"):
        return "financing"
    return "operating"


def _uk_cf_directional(code: str, cash_delta: int) -> tuple[str, str]:
    for prefix, category in _UK_CF_CATEGORY_MAP:
        if code.startswith(prefix):
            section = _cf_section_for_uk(category)
            if category in {"op_tax_paid", "op_interest_paid", "op_interest_received", "op_investment_income"}:
                return (section, category)
            suffix = "_inflow" if cash_delta > 0 else "_outflow"
            return (section, category + suffix)
    return ("operating", "op_other")


def _uk_cash_flow_buckets(
    db: Session, from_d: date, to_d: date, currency: str | None,
) -> dict[tuple[str, str], int]:
    from app.services.reporting.repository import transactions_with_lines_between

    txns = transactions_with_lines_between(db, from_d, to_d, currency=currency)
    buckets: dict[tuple[str, str], int] = {}
    for txn in txns:
        cash_lines = [ln for ln in txn.lines if (ln.account.code or "").startswith("12")]
        if not cash_lines:
            continue
        cash_delta = int(sum((ln.debit or 0) - (ln.credit or 0) for ln in cash_lines))
        if cash_delta == 0:
            continue
        counters = [ln for ln in txn.lines if not (ln.account.code or "").startswith("12")]
        if not counters:
            key = ("operating", "op_other")
        else:
            counters.sort(key=lambda ln: abs((ln.debit or 0) - (ln.credit or 0)), reverse=True)
            key = _uk_cf_directional(counters[0].account.code or "", cash_delta)
        buckets[key] = buckets.get(key, 0) + cash_delta
    return buckets


_UK_CF_ROW_TEMPLATE: list[tuple[str, str, str]] = [
    # Operating
    ("operating", "op_other", "Cash generated from operations"),
    ("operating", "op_interest_received", "Interest received"),
    ("operating", "op_investment_income", "Dividends received"),
    ("operating", "op_interest_paid", "Interest paid"),
    ("operating", "op_tax_paid", "Corporation tax paid"),
    # Investing
    ("investing", "inv_ppe_inflow", "Proceeds from sale of tangible fixed assets"),
    ("investing", "inv_ppe_outflow", "Purchase of tangible fixed assets"),
    ("investing", "inv_intangibles_inflow", "Proceeds from sale of intangible assets"),
    ("investing", "inv_intangibles_outflow", "Purchase of intangible assets"),
    ("investing", "inv_investments_inflow", "Proceeds from sale of investments"),
    ("investing", "inv_investments_outflow", "Purchase of investments"),
    # Financing
    ("financing", "fin_share_capital_inflow", "Proceeds from issue of share capital"),
    ("financing", "fin_share_premium_inflow", "Share premium received"),
    ("financing", "fin_borrowings_inflow", "New bank loans drawn"),
    ("financing", "fin_borrowings_outflow", "Repayment of bank loans"),
    ("financing", "fin_lease_outflow", "Capital element of finance-lease payments"),
    ("financing", "fin_dividends_outflow", "Dividends paid (placeholder)"),
]


def _uk_section_sum(section: str, buckets: dict[tuple[str, str], int]) -> int:
    return sum(v for (sec, _), v in buckets.items() if sec == section)


def _uk_opening_cash(
    db: Session, accounts: list[Account], as_of: date, currency: str | None,
) -> int:
    return _uk_balance_sheet_buckets(db, accounts, as_of, currency).get(("current_assets", "ca_cash"), 0)


def _cf_row(
    section: str, bucket: str, label: str,
    current: dict, prior: dict, *, indent: int = 2,
) -> UKStatementRow:
    cur = current.get((section, bucket), 0)
    pri = prior.get((section, bucket), 0)
    return UKStatementRow(
        key=bucket, label=label, row_type="line", indent_level=indent,
        amount_current=cur, amount_prior=pri,
        is_negative_presentation=(cur < 0 or pri < 0),
    )


def build_uk_cash_flow(
    db: Session,
    from_date: date | None = None,
    to_date: date | None = None,
    comparative_from_date: date | None = None,
    comparative_to_date: date | None = None,
    currency: str | None = None,
) -> UKCashFlowResponse:
    period = default_period(from_date, to_date)
    if comparative_to_date is None:
        comparative_to_date = _shift_one_year(period.to_date)
    if comparative_from_date is None:
        comparative_from_date = _shift_one_year(period.from_date)

    current = _uk_cash_flow_buckets(db, period.from_date, period.to_date, currency)
    prior = _uk_cash_flow_buckets(db, comparative_from_date, comparative_to_date, currency)

    op_cur = _uk_section_sum("operating", current)
    op_pri = _uk_section_sum("operating", prior)
    inv_cur = _uk_section_sum("investing", current)
    inv_pri = _uk_section_sum("investing", prior)
    fin_cur = _uk_section_sum("financing", current)
    fin_pri = _uk_section_sum("financing", prior)
    net_cur = op_cur + inv_cur + fin_cur
    net_pri = op_pri + inv_pri + fin_pri

    accounts = list_accounts(db)
    opening_cash_cur = _uk_opening_cash(db, accounts, period.from_date - timedelta(days=1), currency)
    opening_cash_pri = _uk_opening_cash(db, accounts, comparative_from_date - timedelta(days=1), currency)
    closing_cash_cur = _uk_opening_cash(db, accounts, period.to_date, currency)
    closing_cash_pri = _uk_opening_cash(db, accounts, comparative_to_date, currency)
    fx_cur = closing_cash_cur - (opening_cash_cur + net_cur)
    fx_pri = closing_cash_pri - (opening_cash_pri + net_pri)

    def _emit(section: str) -> list[UKStatementRow]:
        return [
            _cf_row(sec, bkt, label, current, prior)
            for (sec, bkt, label) in _UK_CF_ROW_TEMPLATE
            if sec == section
        ]

    rows: list[UKStatementRow] = [
        _header("operating_section", "Cash flows from operating activities"),
        *_emit("operating"),
        _subtotal("operating_net", "Net cash from operating activities", op_cur, op_pri),
        _header("investing_section", "Cash flows from investing activities"),
        *_emit("investing"),
        _subtotal("investing_net", "Net cash used in investing activities", inv_cur, inv_pri),
        _header("financing_section", "Cash flows from financing activities"),
        *_emit("financing"),
        _subtotal("financing_net", "Net cash from financing activities", fin_cur, fin_pri),
        _subtotal("net_cash_change", "Net increase/(decrease) in cash and cash equivalents", net_cur, net_pri),
        _subtotal("opening_cash", "Cash at the beginning of the year", opening_cash_cur, opening_cash_pri),
        _subtotal("fx_effect", "Effect of exchange-rate changes", fx_cur, fx_pri),
        _subtotal("closing_cash", "Cash at the end of the year", closing_cash_cur, closing_cash_pri, row_type="total"),
    ]

    return UKCashFlowResponse(
        period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
        comparative_period=ReportPeriod(from_date=comparative_from_date, to_date=comparative_to_date),
        rows=rows,
        metadata={
            "currency": currency or "GBP",
            "fx_effect_method": (
                "Effect of exchange-rate changes is the residual that closes the "
                "reconciliation: closing − (opening + net change in cash)."
            ),
        },
    )


# ---------------------------------------------------------------------------
# Service wrapper
# ---------------------------------------------------------------------------


class UKStatementService:
    def __init__(self, db: Session):
        self.db = db

    def balance_sheet(self, **kw) -> UKBalanceSheetResponse:
        return build_uk_balance_sheet(self.db, **kw)

    def income_statement(self, **kw) -> UKIncomeStatementResponse:
        return build_uk_income_statement(self.db, **kw)

    def comprehensive_income(self, **kw) -> UKComprehensiveIncomeResponse:
        return build_uk_comprehensive_income(self.db, **kw)

    def changes_in_equity(self, **kw) -> UKChangesInEquityResponse:
        return build_uk_changes_in_equity(self.db, **kw)

    def cash_flow(self, **kw) -> UKCashFlowResponse:
        return build_uk_cash_flow(self.db, **kw)

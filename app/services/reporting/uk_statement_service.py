"""UK FRS 102 Section 1A statements (Companies Act 2006 formats).

Implements the five small-company statements:

* Statement of Financial Position (Companies Act format 1)
* Profit and Loss Account (format 1, by function)
* Statement of Comprehensive Income
* Statement of Changes in Equity
* Statement of Cash Flows (FRS 102 Section 7)

All amounts are stored as whole pounds (integers). Line items are derived
from the seeded UK chart of accounts via prefix mapping; every seeded code is
placed on purpose, and codes a user adds under a group fall into that group's
line (roadmap 2026-09 §3.7).

From the ledger, not placeholders: depreciation and amortisation charged
(stated after operating profit), other comprehensive income (revaluation
reserve movements against the assets), every changes-in-equity movement for
both years, dividends paid and directors' loans in the cash flow, and the
reconciliation of operating profit to cash generated from operations.
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
# Prefix → BS bucket. Order matters: longer prefixes first; each group ends
# with a catch-all so an account a user adds (chart_service) lands on its
# group's line instead of dropping off the statement — which used to happen to
# accrued income (1410), supplier prepayments (1500) and the opening-balance
# adjustment (3999), so the sheet stopped balancing.
_UK_BS_MAP: list[tuple[str, str]] = [
    # Intangible assets (NBV = cost − accum amort, both classified ASSET so
    # the contra-amort accounts naturally subtract via signed sum).
    ("01", "fa_intangibles"),
    # Investments held as fixed assets
    ("02", "fa_investments"),
    # Tangible fixed assets — NBV (00xx and any other fixed asset)
    ("0", "fa_tangibles"),
    # Current assets: stocks 10xx, cash 12xx, everything else a debtor
    # (trade debtors, prepayments, accrued income, VAT, supplier advances)
    ("10", "ca_stocks"),
    ("12", "ca_cash"),
    ("1", "ca_debtors"),
    # Provisions for liabilities
    ("295", "ncl_provisions"),
    # Creditors due after more than one year
    ("28", "ncl_creditors"),
    ("29", "ncl_creditors"),
    # Creditors due within one year (everything else in group 2)
    ("2", "cl_creditors"),
    # Capital and reserves
    ("3000", "eq_share_capital"),
    ("3010", "eq_share_premium"),
    ("3020", "eq_revaluation_reserve"),
    ("31", "eq_pl_account"),
    ("3999", "eq_pl_account"),          # opening balance adjustments: earnings from before the books
    ("3", "eq_other_reserves"),
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
# Prefix → P&L bucket, longer prefixes first, a catch-all per group.
_UK_PL_MAP: list[tuple[str, str]] = [
    # other operating income: 4200, FX gains 4210, disposal gains
    ("42", "other_operating_income"),
    ("4", "turnover"),            # sales 4000 less returns 4100 (signed)
    ("5", "cost_of_sales"),
    ("70", "distribution_costs"),
    ("7", "admin_expenses"),      # 71xx–79xx incl. FX losses 7950, disposal losses 7860
    ("8100", "interest_payable"),
    ("8200", "interest_payable"),
    ("8300", "interest_receivable"),
    ("8400", "investment_income"),
    ("8", "admin_expenses"),      # bank charges 8000, depreciation 8500, amortisation 8600
    ("9", "tax_on_profit"),
]

# Charged within the lines above, stated after operating profit (FRS 102 1A notes).
_UK_DEPRECIATION = "8500"
_UK_AMORTISATION = "8600"


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
        # signed: sales returns reduce turnover and a refund reduces a cost
        # (clamping each account at zero dropped them and left the P&L out of
        # step with the balance sheet's profit)
        raw = balance_from_turnovers(classify_account_code(acc.code), debit, credit)
        buckets[bucket] = buckets.get(bucket, 0) + int(raw)
    return buckets


def _charged(db: Session, accounts: list[Account], from_d: date, to_d: date, currency: str | None,
             prefix: str) -> int:
    """An expense charged in the period on accounts under ``prefix``."""
    turnovers = {a: (d, c) for a, d, c in account_turnovers_between(db, from_d, to_d, currency=currency)}
    return sum(int(d - c) for acc in accounts if (acc.code or "").startswith(prefix)
               for d, c in [turnovers.get(acc.id, (0, 0))])


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
        _header("stated_after_section", "Operating profit is stated after charging:"),
        _line("depreciation_charged", "Depreciation of tangible fixed assets",
              _charged(db, accounts, period.from_date, period.to_date, currency, _UK_DEPRECIATION),
              _charged(db, accounts, comparative_from_date, comparative_to_date, currency, _UK_DEPRECIATION)),
        _line("amortisation_charged", "Amortisation of intangible assets",
              _charged(db, accounts, period.from_date, period.to_date, currency, _UK_AMORTISATION),
              _charged(db, accounts, comparative_from_date, comparative_to_date, currency, _UK_AMORTISATION)),
    ]

    return UKIncomeStatementResponse(
        period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
        comparative_period=ReportPeriod(from_date=comparative_from_date, to_date=comparative_to_date),
        rows=rows,
        metadata={"currency": currency or "GBP"},
    )


# ---------------------------------------------------------------------------
# Equity movements from the ledger (OCI, changes in equity)
# ---------------------------------------------------------------------------
_EQ_MOVEMENTS = ("closed_profit", "oci", "shares_issued", "dividends", "transfers")


def _equity_movements(db: Session, from_d: date, to_d: date, currency: str | None) -> dict[str, dict[str, int]]:
    """Each equity movement in [from_d, to_d] by what is on the other side of
    the entry, per equity component (increase = +):

    * ``shares_issued`` — share capital / premium moved (a bonus issue out of
      the P&L reserve shows the reserve going down in the same row);
    * ``transfers`` — equity-only entries between reserves;
    * ``dividends`` — the P&L reserve debited against dividends payable, a
      shareholder's account, cash or another liability;
    * ``oci`` — the revaluation reserve moved against the assets;
    * ``closed_profit`` — a closing entry moving P&L balances into the reserve.

    Anything else (opening-balance adjustments, corrections) is left for the
    changes-in-equity "Other movements" row, which makes each column tie to
    the balance sheet."""
    from app.services.reporting.repository import line_net, transactions_with_lines_between

    from app.services.chart_service import OPENING_REFERENCE
    from app.services.migration_import import OPENING_REFERENCE as MIGRATION_OPENING

    out: dict[str, dict[str, int]] = {k: {} for k in _EQ_MOVEMENTS}
    for txn in transactions_with_lines_between(db, from_d, to_d, currency=currency):
        if (txn.reference or "") in (OPENING_REFERENCE, MIGRATION_OPENING):
            continue                           # opening balances are not the year's movements
        eq = [(ln, _bs_bucket_for_code(ln.account.code or "")) for ln in txn.lines
              if (ln.account.code or "").startswith("3")]
        if not eq:
            continue
        others = [(ln.account.code or "") for ln in txn.lines if not (ln.account.code or "").startswith("3")]
        comps = {b[1] for _, b in eq if b}
        reserve_debited = any((ln.account.code or "").startswith("31") and line_net(ln, currency) > 0 for ln, _ in eq)
        if comps & {"eq_share_capital", "eq_share_premium"}:
            kind = "shares_issued"
        elif not others:
            kind = "transfers"
        elif all(c[:1] in "456789" for c in others):
            kind = "closed_profit"
        elif reserve_debited and any(c.startswith(("2", "12")) for c in others):
            kind = "dividends"
        elif comps == {"eq_revaluation_reserve"}:
            kind = "oci"
        else:
            continue
        for ln, bucket in eq:
            if bucket:
                comp = bucket[1]
                out[kind][comp] = out[kind].get(comp, 0) - int(line_net(ln, currency))   # credit = increase
    return out


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
    # Revaluation gains/(losses): the revaluation reserve moved against the
    # assets. No foreign operations are consolidated, so no translation line;
    # a deferred tax charged to the reserve shows net in the revaluation line.
    reval_cur = _equity_movements(db, period.from_date, period.to_date, currency)["oci"].get("eq_revaluation_reserve", 0)
    reval_pri = _equity_movements(db, comparative_from_date, comparative_to_date, currency)["oci"].get("eq_revaluation_reserve", 0)
    oci_cur = reval_cur
    oci_pri = reval_pri

    rows: list[UKStatementRow] = [
        _subtotal("profit_for_year", "Profit/(loss) for the financial year", np_cur, np_pri, indent=0),
        _header("oci_section", "Other comprehensive income (net of tax):"),
        _line("oci_revaluation", "Revaluation gains/(losses) on tangible assets", reval_cur, reval_pri, indent=2),
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
            "note": "Other comprehensive income is the revaluation reserve's movement against the assets in the period; transfers between reserves are not OCI.",
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
    # (key, label) pairs — this read the label as the key, so every opening and
    # closing balance in the statement came out as zero
    return {bucket: buckets.get(("equity", bucket), 0) for bucket, _label in _UK_EQUITY_COMPONENTS}


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
    opening_date = period.from_date - timedelta(days=1)
    comparative_opening = _uk_equity_balances(db, accounts, comparative_from_date - timedelta(days=1), currency)
    opening = _uk_equity_balances(db, accounts, opening_date, currency)
    closing = _uk_equity_balances(db, accounts, period.to_date, currency)

    def movements(from_d: date, to_d: date, start: dict, end: dict) -> dict[str, dict[str, int]]:
        """One year's rows, per component; "other" makes the year tie to the
        balance sheet (opening + movements = closing)."""
        mv = _equity_movements(db, from_d, to_d, currency)
        profit = _uk_period_net_profit(db, from_d, to_d, currency)
        rows = {
            "profit": {"eq_pl_account": profit + mv["closed_profit"].get("eq_pl_account", 0)},
            "oci": dict(mv["oci"]),
            "shares_issued": dict(mv["shares_issued"]),
            "dividends": dict(mv["dividends"]),
            "transfers": dict(mv["transfers"]),
        }
        other = {}
        for comp, _label in _UK_EQUITY_COMPONENTS:
            explained = sum(r.get(comp, 0) for r in rows.values())
            left = end.get(comp, 0) - start.get(comp, 0) - explained
            if left:
                other[comp] = left
        rows["other"] = other
        rows["total_ci"] = {k: rows["profit"].get(k, 0) + rows["oci"].get(k, 0)
                            for k in set(rows["profit"]) | set(rows["oci"])}
        return rows

    # the comparative year runs up to the current year's opening, so the two chain
    prev = movements(comparative_from_date, opening_date, comparative_opening, opening)
    cur = movements(period.from_date, period.to_date, opening, closing)

    def block(prefix: str, mv: dict) -> list[UKEquityMovementRow]:
        p = f"{prefix}_" if prefix else ""
        out = [
            _uk_equity_row(f"{p}profit" if prefix else "profit_for_year", "Profit for the year", mv["profit"]),
            _uk_equity_row(f"{p}oci", "Other comprehensive income", mv["oci"]),
            _uk_equity_row(f"{p}total_ci", "Total comprehensive income", mv["total_ci"], row_type="subtotal"),
            _uk_equity_row(f"{p}shares_issued", "Shares issued in the year", mv["shares_issued"]),
            _uk_equity_row(f"{p}dividends", "Dividends declared and paid", mv["dividends"]),
            _uk_equity_row(f"{p}transfer_reserves", "Transfers between reserves", mv["transfers"]),
        ]
        if mv["other"]:
            out.append(_uk_equity_row(f"{p}other", "Other movements (opening balances, corrections)", mv["other"]))
        return out

    rows: list[UKEquityMovementRow] = [
        _uk_equity_row("comparative_opening", f"At {comparative_from_date.isoformat()}", comparative_opening),
        # the block runs to the current opening so the two years chain
        _uk_equity_header("comparative_period", f"Movements in the year ended {opening_date.isoformat()}"),
        *block("comparative", prev),
        _uk_equity_row("opening", f"At {opening_date.isoformat()}", opening, row_type="subtotal"),
        _uk_equity_header("current_period", f"Movements in the year ended {period.to_date.isoformat()}"),
        *block("", cur),
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
            "note": "Movements come from the ledger: shares issued (share capital / premium), dividends (the P&L reserve against dividends payable or cash), transfers between reserves, revaluations (OCI). Anything else is under other movements, so each column ties to the balance sheet.",
        },
    )


# ---------------------------------------------------------------------------
# Cash Flow Statement (FRS 102 Section 7, indirect method skeleton)
# ---------------------------------------------------------------------------
#
# The statement uses the direct method (FRS 102 7.7 allows it): each cash
# movement is classified by its largest counterparty. Below it, the note that
# reconciles operating profit to cash generated from operations — depreciation,
# amortisation, fixed-asset gains and losses, and the working-capital
# movements — with any difference left visible.

_UK_CF_CATEGORY_MAP: list[tuple[str, str]] = [
    # Investing — fixed-asset categories
    ("00", "inv_ppe"),
    ("01", "inv_intangibles"),
    ("02", "inv_investments"),
    # Financing
    # an issue credits capital and premium together: one line, not all of it
    # on whichever of the two is larger
    ("3000", "fin_share_capital"),
    ("3010", "fin_share_capital"),
    ("2600", "fin_borrowings"),     # bank loan ST current portion
    ("2800", "fin_borrowings"),     # bank loan LT
    ("2500", "fin_borrowings"),     # bank overdraft drawn / repaid
    ("2810", "fin_lease"),
    ("2750", "fin_dividends"),      # paying a declared dividend
    ("31", "fin_dividends"),        # a dividend paid straight out of the reserve
    ("2350", "fin_director_loans"), # directors' / shareholders' loan account
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
    from app.services.reporting.repository import line_net, transactions_with_lines_between

    txns = transactions_with_lines_between(db, from_d, to_d, currency=currency)
    buckets: dict[tuple[str, str], int] = {}
    for txn in txns:
        cash_lines = [ln for ln in txn.lines if (ln.account.code or "").startswith("12")]
        if not cash_lines:
            continue
        cash_delta = int(sum(line_net(ln, currency) for ln in cash_lines))
        if cash_delta == 0:
            continue
        counters = [ln for ln in txn.lines if not (ln.account.code or "").startswith("12")]
        if not counters:
            key = ("operating", "op_other")
        else:
            counters.sort(key=lambda ln: abs(line_net(ln, currency)), reverse=True)
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
    ("financing", "fin_share_capital_inflow", "Proceeds from issue of shares (including premium)"),
    ("financing", "fin_borrowings_inflow", "New bank loans drawn"),
    ("financing", "fin_borrowings_outflow", "Repayment of bank loans"),
    ("financing", "fin_lease_outflow", "Capital element of finance-lease payments"),
    ("financing", "fin_director_loans_inflow", "Advances from directors"),
    ("financing", "fin_director_loans_outflow", "Repayments to directors"),
    ("financing", "fin_dividends_outflow", "Equity dividends paid"),
]


# Creditors that are not working capital: tax (its own line), dividends,
# directors' loans, borrowings and long-term creditors (financing).
_UK_NON_WC_CREDITORS = ("2300", "2350", "2500", "2600", "2750", "28", "29")


def _uk_cash_reconciliation(db: Session, accounts: list[Account], from_d: date, to_d: date,
                            currency: str | None) -> dict[str, int]:
    """Operating profit → cash generated from operations (indirect method)."""
    from app.services.reporting.repository import line_net, transactions_with_lines_between

    turn = {a: (d, c) for a, d, c in account_turnovers_between(db, from_d, to_d, currency=currency)}

    def movement(pred) -> int:            # debit − credit over the period
        return sum(int(d - c) for acc in accounts if pred(acc.code or "")
                   for d, c in [turn.get(acc.id, (0, 0))])

    pl = _uk_pl_buckets(db, accounts, from_d, to_d, currency)
    operating = sum(_pl_signed(b, pl.get(b, 0)) for b in
                    ("turnover", "cost_of_sales", "distribution_costs", "admin_expenses", "other_operating_income"))
    # gains and losses in entries that touch fixed assets (disposals, write-downs),
    # beyond depreciation and amortisation
    fixed_asset_pl = 0
    for txn in transactions_with_lines_between(db, from_d, to_d, currency=currency):
        codes = [(ln.account.code or "") for ln in txn.lines]
        if not any(c.startswith("0") for c in codes):
            continue
        for ln in txn.lines:
            c = ln.account.code or ""
            if c[:1] in "4567" or (c.startswith("8") and not c.startswith((_UK_DEPRECIATION, _UK_AMORTISATION))):
                fixed_asset_pl += int(line_net(ln, currency))          # a loss (debit) is added back
    return {
        "operating_profit": operating,
        "depreciation": movement(lambda c: c.startswith(_UK_DEPRECIATION)),
        "amortisation": movement(lambda c: c.startswith(_UK_AMORTISATION)),
        "fixed_asset_pl": fixed_asset_pl,
        "stocks": -movement(lambda c: c.startswith("10")),
        "debtors": -movement(lambda c: c.startswith("1") and not c.startswith(("10", "12"))),
        "creditors": -movement(lambda c: c.startswith("2") and not c.startswith(_UK_NON_WC_CREDITORS + ("295",))),
        "provisions": -movement(lambda c: c.startswith("295")),
    }


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


_UK_RECON_LINES = (
    ("operating_profit", "Operating profit/(loss)"),
    ("depreciation", "Depreciation of tangible fixed assets"),
    ("amortisation", "Amortisation of intangible assets"),
    ("fixed_asset_pl", "(Profit)/loss on disposal and write-down of fixed assets"),
    ("stocks", "(Increase)/decrease in stocks"),
    ("debtors", "(Increase)/decrease in debtors"),
    ("creditors", "Increase/(decrease) in creditors"),
    ("provisions", "Increase/(decrease) in provisions"),
)


def _reconciliation_rows(cur: dict, pri: dict, generated_cur: int, generated_pri: int) -> list[UKStatementRow]:
    """The note, ending on the statement's cash generated from operations. A
    difference (non-cash investing or financing through working capital,
    entries classified by their largest line) is shown, never hidden."""
    rows = [_header("recon_section", "Reconciliation of operating profit to cash generated from operations")]
    rows += [_line(f"recon_{k}", label, cur[k], pri[k], indent=1,
                   negative_presentation=False) for k, label in _UK_RECON_LINES]
    diff_cur = generated_cur - sum(cur.values())
    diff_pri = generated_pri - sum(pri.values())
    if diff_cur or diff_pri:
        rows.append(_line("recon_other", "Other non-cash items and reclassifications", diff_cur, diff_pri, indent=1))
    rows.append(_subtotal("recon_cash_generated", "Cash generated from operations", generated_cur, generated_pri))
    return rows


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
        *_reconciliation_rows(
            _uk_cash_reconciliation(db, accounts, period.from_date, period.to_date, currency),
            _uk_cash_reconciliation(db, accounts, comparative_from_date, comparative_to_date, currency),
            current.get(("operating", "op_other"), 0), prior.get(("operating", "op_other"), 0)),
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

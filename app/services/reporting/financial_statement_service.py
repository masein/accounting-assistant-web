from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.account import Account
from app.schemas.manager_report import (
    BalanceSheetResponse,
    CashFlowLine,
    CashFlowResponse,
    CashFlowSection,
    FinancialAnalysis,
    IncomeStatementResponse,
    ReportPeriod,
    StatementAccountNode,
    StatementSection,
)
from app.services.reporting.common import (
    ACCOUNT_TYPE_FA,
    ASSET,
    EQUITY,
    EXPENSE,
    LIABILITY,
    REVENUE,
    balance_from_turnovers,
    classify_account_code,
    default_period,
    statement_sign_value,
)
from app.services.reporting.repository import account_turnovers_between, account_turnovers_upto, list_accounts


@dataclass
class AccountBalance:
    debit: int = 0
    credit: int = 0


def _build_balance_map(turnovers: list[tuple[UUID, int, int]]) -> dict[UUID, AccountBalance]:
    out: dict[UUID, AccountBalance] = {}
    for account_id, debit, credit in turnovers:
        out[account_id] = AccountBalance(debit=debit, credit=credit)
    return out


def _rollup_account_tree(accounts: list[Account], amounts: dict[UUID, int]) -> dict[UUID, int]:
    children: dict[UUID | None, list[Account]] = defaultdict(list)
    for acc in accounts:
        children[acc.parent_id].append(acc)

    totals: dict[UUID, int] = {}

    def walk(node: Account) -> int:
        total = int(amounts.get(node.id, 0))
        for ch in children.get(node.id, []):
            total += walk(ch)
        totals[node.id] = total
        return total

    for root in children.get(None, []):
        walk(root)
    return totals


def _node_from_account(account: Account, total_balance: int, type_override: str | None = None) -> StatementAccountNode:
    acc_type = type_override or classify_account_code(account.code)
    return StatementAccountNode(
        account_id=account.id,
        account_code=account.code,
        account_name=account.name,
        account_type=acc_type,
        label_fa=ACCOUNT_TYPE_FA.get(acc_type),
        balance=total_balance,
        debit_turnover=0,
        credit_turnover=0,
        children=[],
    )


def _build_section_tree(accounts: list[Account], totals: dict[UUID, int], section_type: str) -> list[StatementAccountNode]:
    by_parent: dict[UUID | None, list[Account]] = defaultdict(list)
    for acc in accounts:
        by_parent[acc.parent_id].append(acc)
    for _, arr in by_parent.items():
        arr.sort(key=lambda a: a.code)

    def build(acc: Account) -> StatementAccountNode | None:
        acc_type = classify_account_code(acc.code)
        children_nodes = [n for n in (build(ch) for ch in by_parent.get(acc.id, [])) if n is not None]
        own_total = int(totals.get(acc.id, 0))
        include_here = acc_type == section_type
        if not include_here and not children_nodes:
            return None
        node = _node_from_account(acc, own_total, type_override=acc_type)
        node.children = children_nodes
        return node

    out: list[StatementAccountNode] = []
    for root in by_parent.get(None, []):
        node = build(root)
        if node is not None:
            out.append(node)
    return out


def _sum_nodes(items: list[StatementAccountNode]) -> int:
    return int(sum(n.balance for n in items))


def _safe_ratio(numerator: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _analyze_balance_sheet(
    totals: dict[str, int],
    asset_nodes: list[StatementAccountNode],
) -> FinancialAnalysis:
    total_assets = totals.get("assets", 0)
    total_liabilities = totals.get("liabilities", 0)
    total_equity = totals.get("equity", 0)

    current_ratio = _safe_ratio(total_assets, total_liabilities) if total_liabilities else None
    debt_to_equity = _safe_ratio(total_liabilities, total_equity) if total_equity else None
    working_capital = total_assets - total_liabilities

    ratios: dict[str, float | None] = {
        "current_ratio": current_ratio,
        "debt_to_equity": debt_to_equity,
        "working_capital": float(working_capital),
    }

    warnings: list[str] = []
    if total_assets > 0:
        for node in asset_nodes:
            concentration = abs(node.balance) / total_assets if total_assets else 0
            if concentration > 0.6:
                warnings.append(
                    f"Asset concentration: {node.account_name} ({node.account_code}) represents "
                    f"{concentration:.0%} of total assets."
                )
    if current_ratio is not None and current_ratio < 1.0:
        warnings.append(f"Low liquidity: current ratio is {current_ratio:.2f} (below 1.0).")
    if debt_to_equity is not None and debt_to_equity > 2.0:
        warnings.append(f"High leverage: debt-to-equity ratio is {debt_to_equity:.2f}.")

    parts: list[str] = []
    parts.append(f"Total assets: {total_assets:,}")
    if working_capital >= 0:
        parts.append(f"working capital is positive at {working_capital:,}")
    else:
        parts.append(f"working capital is negative at {working_capital:,}")
    if current_ratio is not None:
        parts.append(f"current ratio {current_ratio:.2f}")
    summary = ". ".join(parts) + "."

    return FinancialAnalysis(ratios=ratios, warnings=warnings, summary=summary)


def _analyze_income_statement(totals: dict[str, int]) -> FinancialAnalysis:
    revenue = totals.get("revenue", 0)
    cogs = totals.get("cogs", 0)
    gross_profit = totals.get("gross_profit", 0)
    opex = totals.get("operating_expenses", 0)
    net_profit = totals.get("net_profit", 0)

    gross_margin = _safe_ratio(gross_profit * 100, revenue) if revenue else None
    operating_margin = _safe_ratio((gross_profit - opex) * 100, revenue) if revenue else None
    net_margin = _safe_ratio(net_profit * 100, revenue) if revenue else None

    ratios: dict[str, float | None] = {
        "gross_margin_pct": gross_margin,
        "operating_margin_pct": operating_margin,
        "net_margin_pct": net_margin,
    }

    warnings: list[str] = []
    if net_margin is not None and net_margin < 0:
        warnings.append(f"Net loss: margin is {net_margin:.1f}%.")
    if gross_margin is not None and gross_margin < 20:
        warnings.append(f"Low gross margin at {gross_margin:.1f}%.")

    parts: list[str] = []
    parts.append(f"Revenue: {revenue:,}")
    if gross_margin is not None:
        parts.append(f"gross margin {gross_margin:.1f}%")
    if net_margin is not None:
        parts.append(f"net margin {net_margin:.1f}%")
    parts.append(f"net profit: {net_profit:,}")
    summary = ". ".join(parts) + "."

    return FinancialAnalysis(ratios=ratios, warnings=warnings, summary=summary)


def _analyze_cash_flow(totals: dict[str, int]) -> FinancialAnalysis:
    operating = totals.get("operating", 0)
    investing = totals.get("investing", 0)
    financing = totals.get("financing", 0)
    net = totals.get("net_cash_change", 0)

    ratios: dict[str, float | None] = {
        "operating_cash_flow": float(operating),
        "free_cash_flow": float(operating + investing),
        "net_cash_change": float(net),
    }

    warnings: list[str] = []
    if operating < 0:
        warnings.append("Negative operating cash flow — the business is consuming cash from operations.")
    if net < 0:
        warnings.append(f"Net cash decreased by {abs(net):,}.")

    parts: list[str] = []
    parts.append(f"Operating cash flow: {operating:,}")
    fcf = operating + investing
    parts.append(f"free cash flow: {fcf:,}")
    parts.append(f"net cash change: {net:,}")
    summary = ". ".join(parts) + "."

    return FinancialAnalysis(ratios=ratios, warnings=warnings, summary=summary)


def build_balance_sheet(
    db: Session,
    to_date: date | None = None,
    comparative_to_date: date | None = None,
) -> BalanceSheetResponse:
    period = default_period(None, to_date)
    accounts = list_accounts(db)
    turnover = _build_balance_map(account_turnovers_upto(db, period.to_date))

    own_balances: dict[UUID, int] = {}
    for acc in accounts:
        t = turnover.get(acc.id, AccountBalance())
        acc_type = classify_account_code(acc.code)
        own_balances[acc.id] = statement_sign_value(acc_type, balance_from_turnovers(acc_type, t.debit, t.credit))
    rolled = _rollup_account_tree(accounts, own_balances)

    assets = _build_section_tree(accounts, rolled, ASSET)
    liabilities = _build_section_tree(accounts, rolled, LIABILITY)
    equity = _build_section_tree(accounts, rolled, EQUITY)

    sections = {
        "assets": StatementSection(key="assets", label="Assets", label_fa="دارایی‌ها", items=assets, total=_sum_nodes(assets)),
        "liabilities": StatementSection(
            key="liabilities",
            label="Liabilities",
            label_fa="بدهی‌ها",
            items=liabilities,
            total=_sum_nodes(liabilities),
        ),
        "equity": StatementSection(key="equity", label="Equity", label_fa="حقوق مالکانه", items=equity, total=_sum_nodes(equity)),
    }
    totals = {
        "assets": sections["assets"].total,
        "liabilities": sections["liabilities"].total,
        "equity": sections["equity"].total,
    }

    comparative = None
    if comparative_to_date:
        cp = default_period(None, comparative_to_date)
        comparative = ReportPeriod(from_date=cp.from_date, to_date=cp.to_date)

    analysis = _analyze_balance_sheet(totals, assets)

    return BalanceSheetResponse(
        period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
        comparative_period=comparative,
        sections=sections,
        totals=totals,
        analysis=analysis,
    )


def build_income_statement(
    db: Session,
    from_date: date | None = None,
    to_date: date | None = None,
) -> IncomeStatementResponse:
    period = default_period(from_date, to_date)
    accounts = list_accounts(db)
    turnover = _build_balance_map(account_turnovers_between(db, period.from_date, period.to_date))

    revenues: list[StatementAccountNode] = []
    cogs: list[StatementAccountNode] = []
    operating_expenses: list[StatementAccountNode] = []
    other_expenses: list[StatementAccountNode] = []

    for acc in accounts:
        tb = turnover.get(acc.id)
        if tb is None:
            continue
        acc_type = classify_account_code(acc.code)
        amount = max(0, balance_from_turnovers(acc_type, tb.debit, tb.credit))
        if amount == 0:
            continue
        node = StatementAccountNode(
            account_id=acc.id,
            account_code=acc.code,
            account_name=acc.name,
            account_type=acc_type,
            label_fa=ACCOUNT_TYPE_FA.get(acc_type),
            balance=amount,
            debit_turnover=tb.debit,
            credit_turnover=tb.credit,
            children=[],
        )
        if acc_type == REVENUE:
            revenues.append(node)
        elif acc.code.startswith("51"):
            cogs.append(node)
        elif acc.code.startswith("61"):
            operating_expenses.append(node)
        elif acc.code.startswith("62") or acc_type == EXPENSE:
            other_expenses.append(node)

    revenues.sort(key=lambda x: x.account_code)
    cogs.sort(key=lambda x: x.account_code)
    operating_expenses.sort(key=lambda x: x.account_code)
    other_expenses.sort(key=lambda x: x.account_code)

    total_revenue = _sum_nodes(revenues)
    total_cogs = _sum_nodes(cogs)
    total_opex = _sum_nodes(operating_expenses)
    total_other_exp = _sum_nodes(other_expenses)
    gross_profit = total_revenue - total_cogs
    net_profit = gross_profit - total_opex - total_other_exp

    sections = {
        "revenues": StatementSection(
            key="revenues",
            label="Revenues",
            label_fa="درآمدها",
            items=revenues,
            total=total_revenue,
        ),
        "cogs": StatementSection(
            key="cogs",
            label="Cost of Goods Sold",
            label_fa="بهای تمام‌شده کالای فروش‌رفته",
            items=cogs,
            total=total_cogs,
        ),
        "operating_expenses": StatementSection(
            key="operating_expenses",
            label="Operating Expenses",
            label_fa="هزینه‌های عملیاتی",
            items=operating_expenses,
            total=total_opex,
        ),
        "other_expenses": StatementSection(
            key="other_expenses",
            label="Other Expenses",
            label_fa="سایر هزینه‌ها",
            items=other_expenses,
            total=total_other_exp,
        ),
    }

    totals_dict = {
        "revenue": total_revenue,
        "cogs": total_cogs,
        "gross_profit": gross_profit,
        "operating_expenses": total_opex,
        "other_expenses": total_other_exp,
        "net_profit": net_profit,
    }

    analysis = _analyze_income_statement(totals_dict)

    return IncomeStatementResponse(
        period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
        sections=sections,
        totals=totals_dict,
        analysis=analysis,
    )


def classify_cash_flow_activity(account_codes: list[str], account_types: list[str]) -> str:
    """
    Unit-testable cash flow classifier.
    Returns: operating | investing | financing
    """
    if any(code.startswith("12") for code in account_codes):
        return "investing"
    if any(code.startswith("31") for code in account_codes) or any(t in (EQUITY, LIABILITY) for t in account_types):
        return "financing"
    return "operating"


def build_cash_flow_statement(
    db: Session,
    from_date: date | None = None,
    to_date: date | None = None,
) -> CashFlowResponse:
    from app.services.reporting.repository import transactions_with_lines_between

    period = default_period(from_date, to_date)
    txns = transactions_with_lines_between(db, period.from_date, period.to_date)
    section_sums = {"operating": 0, "investing": 0, "financing": 0}
    line_buckets: dict[str, list[CashFlowLine]] = {"operating": [], "investing": [], "financing": []}

    for txn in txns:
        cash_lines = [ln for ln in txn.lines if (ln.account.code or "").startswith("1110")]
        if not cash_lines:
            continue
        cash_delta = int(sum((ln.debit or 0) - (ln.credit or 0) for ln in cash_lines))
        if cash_delta == 0:
            continue
        counter = [ln for ln in txn.lines if not (ln.account.code or "").startswith("1110")]
        counter_codes = [ln.account.code for ln in counter]
        counter_types = [classify_account_code(ln.account.code) for ln in counter]
        bucket = classify_cash_flow_activity(counter_codes, counter_types)
        section_sums[bucket] += cash_delta
        label = txn.description or txn.reference or f"Transaction {txn.id}"
        line_buckets[bucket].append(
            CashFlowLine(
                account_code=(counter_codes[0] if counter_codes else "1110"),
                account_name=label[:128],
                amount=cash_delta,
                label_fa="جریان نقدی",
            )
        )

    sections = {
        "operating": CashFlowSection(
            key="operating",
            label="Operating Activities",
            label_fa="فعالیت‌های عملیاتی",
            lines=line_buckets["operating"],
            net=section_sums["operating"],
        ),
        "investing": CashFlowSection(
            key="investing",
            label="Investing Activities",
            label_fa="فعالیت‌های سرمایه‌گذاری",
            lines=line_buckets["investing"],
            net=section_sums["investing"],
        ),
        "financing": CashFlowSection(
            key="financing",
            label="Financing Activities",
            label_fa="فعالیت‌های تامین مالی",
            lines=line_buckets["financing"],
            net=section_sums["financing"],
        ),
    }
    totals_dict = {
        "operating": section_sums["operating"],
        "investing": section_sums["investing"],
        "financing": section_sums["financing"],
        "net_cash_change": section_sums["operating"] + section_sums["investing"] + section_sums["financing"],
    }

    analysis = _analyze_cash_flow(totals_dict)

    return CashFlowResponse(
        period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
        sections=sections,
        totals=totals_dict,
        analysis=analysis,
    )


class FinancialStatementService:
    def __init__(self, db: Session):
        self.db = db

    def balance_sheet(self, to_date: date | None = None, comparative_to_date: date | None = None) -> BalanceSheetResponse:
        return build_balance_sheet(self.db, to_date=to_date, comparative_to_date=comparative_to_date)

    def income_statement(self, from_date: date | None = None, to_date: date | None = None) -> IncomeStatementResponse:
        return build_income_statement(self.db, from_date=from_date, to_date=to_date)

    def cash_flow_statement(self, from_date: date | None = None, to_date: date | None = None) -> CashFlowResponse:
        return build_cash_flow_statement(self.db, from_date=from_date, to_date=to_date)

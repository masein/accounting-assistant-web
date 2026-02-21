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

    return BalanceSheetResponse(
        period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
        comparative_period=comparative,
        sections=sections,
        totals=totals,
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

    return IncomeStatementResponse(
        period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
        sections=sections,
        totals={
            "revenue": total_revenue,
            "cogs": total_cogs,
            "gross_profit": gross_profit,
            "operating_expenses": total_opex,
            "other_expenses": total_other_exp,
            "net_profit": net_profit,
        },
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
    return CashFlowResponse(
        period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
        sections=sections,
        totals={
            "operating": section_sums["operating"],
            "investing": section_sums["investing"],
            "financing": section_sums["financing"],
            "net_cash_change": section_sums["operating"] + section_sums["investing"] + section_sums["financing"],
        },
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

"""
CFO Intelligence Layer: answers high-level financial questions with
KPI calculations, cross-report correlation, narrative generation,
and risk scoring. Pure computation — no LLM dependency.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.account import Account
from app.models.transaction import Transaction, TransactionLine
from app.models.entity import Entity, TransactionEntity
from app.services.reporting.common import classify_account_code, ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE

logger = logging.getLogger(__name__)


@dataclass
class KPI:
    key: str
    label: str
    value: float | int
    unit: str = ""  # IRR, %, months, ratio
    trend: str = ""  # up, down, flat
    trend_pct: float = 0.0
    risk_level: str = "normal"  # normal, caution, danger


@dataclass
class Insight:
    priority: int  # 1=highest
    category: str  # revenue, expense, cash, risk, growth
    title: str
    body: str
    severity: str = "info"  # info, warning, critical


@dataclass
class CFOReport:
    kpis: list[KPI] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)
    narrative: str = ""
    risk_score: int = 0  # 0-100 (0=no risk, 100=extreme risk)
    runway_months: float = 0.0
    burn_rate: int = 0
    health_grade: str = "A"  # A, B, C, D, F


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def _load_monthly_data(db: Session, months_back: int = 12) -> dict:
    cutoff = date.today() - timedelta(days=months_back * 31)
    txns = db.execute(
        select(Transaction)
        .where(Transaction.date >= cutoff)
        .where(Transaction.deleted_at.is_(None))
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
    ).scalars().unique().all()

    monthly_revenue: dict[str, int] = defaultdict(int)
    monthly_expense: dict[str, int] = defaultdict(int)
    monthly_cash_in: dict[str, int] = defaultdict(int)
    monthly_cash_out: dict[str, int] = defaultdict(int)
    expense_by_cat: dict[str, int] = defaultdict(int)
    revenue_by_client: dict[str, int] = defaultdict(int)
    total_cash = 0
    total_receivable = 0
    total_payable = 0

    for txn in txns:
        month = _month_key(txn.date)
        for ln in txn.lines:
            acc_type = classify_account_code(ln.account.code)
            if acc_type == REVENUE:
                monthly_revenue[month] += ln.credit - ln.debit
            elif acc_type == EXPENSE:
                monthly_expense[month] += ln.debit - ln.credit
                expense_by_cat[ln.account.name] += ln.debit - ln.credit

            if (ln.account.code or "").startswith("1110"):
                delta = ln.debit - ln.credit
                total_cash += delta
                if delta > 0:
                    monthly_cash_in[month] += delta
                else:
                    monthly_cash_out[month] += abs(delta)

            if ln.account.code == "1112":
                total_receivable += ln.debit - ln.credit
            if (ln.account.code or "").startswith("21"):
                total_payable += ln.credit - ln.debit

    return {
        "monthly_revenue": monthly_revenue,
        "monthly_expense": monthly_expense,
        "monthly_cash_in": monthly_cash_in,
        "monthly_cash_out": monthly_cash_out,
        "expense_by_cat": expense_by_cat,
        "total_cash": total_cash,
        "total_receivable": total_receivable,
        "total_payable": total_payable,
        "transaction_count": len(txns),
    }


def build_cfo_report(db: Session) -> CFOReport:
    report = CFOReport()
    data = _load_monthly_data(db, months_back=12)

    rev_vals = list(data["monthly_revenue"].values())
    exp_vals = list(data["monthly_expense"].values())
    cash_in_vals = list(data["monthly_cash_in"].values())
    cash_out_vals = list(data["monthly_cash_out"].values())

    current_month = _month_key(date.today())
    prev_month = _month_key(date.today().replace(day=1) - timedelta(days=1))

    cur_rev = data["monthly_revenue"].get(current_month, 0)
    prev_rev = data["monthly_revenue"].get(prev_month, 0)
    cur_exp = data["monthly_expense"].get(current_month, 0)
    prev_exp = data["monthly_expense"].get(prev_month, 0)

    # KPI: Total Revenue
    total_rev = sum(rev_vals) if rev_vals else 0
    rev_trend = ((cur_rev - prev_rev) / prev_rev * 100) if prev_rev else 0
    report.kpis.append(KPI(
        key="total_revenue", label="Total Revenue (12m)", value=total_rev,
        unit="IRR", trend="up" if rev_trend > 0 else "down" if rev_trend < 0 else "flat",
        trend_pct=round(rev_trend, 1),
    ))

    # KPI: Monthly Avg Revenue
    avg_rev = int(mean(rev_vals)) if rev_vals else 0
    report.kpis.append(KPI(key="avg_monthly_revenue", label="Avg Monthly Revenue", value=avg_rev, unit="IRR"))

    # KPI: Net Profit
    total_exp = sum(exp_vals) if exp_vals else 0
    net_profit = total_rev - total_exp
    margin = (net_profit / total_rev * 100) if total_rev > 0 else 0
    report.kpis.append(KPI(
        key="net_profit", label="Net Profit (12m)", value=net_profit,
        unit="IRR", risk_level="danger" if net_profit < 0 else "normal",
    ))
    report.kpis.append(KPI(key="net_margin", label="Net Margin", value=round(margin, 1), unit="%"))

    # KPI: Cash on Hand
    report.kpis.append(KPI(
        key="cash_on_hand", label="Cash on Hand", value=data["total_cash"],
        unit="IRR", risk_level="danger" if data["total_cash"] < 0 else "caution" if data["total_cash"] < avg_rev else "normal",
    ))

    # KPI: Burn Rate
    recent_exp = exp_vals[-3:] if len(exp_vals) >= 3 else exp_vals
    burn_rate = int(mean(recent_exp)) if recent_exp else 0
    report.burn_rate = burn_rate
    report.kpis.append(KPI(key="burn_rate", label="Monthly Burn Rate", value=burn_rate, unit="IRR"))

    # KPI: Runway
    runway = data["total_cash"] / burn_rate if burn_rate > 0 else 999
    report.runway_months = round(runway, 1)
    report.kpis.append(KPI(
        key="runway_months", label="Cash Runway", value=round(runway, 1),
        unit="months", risk_level="danger" if runway < 3 else "caution" if runway < 6 else "normal",
    ))

    # KPI: Receivables & Payables
    report.kpis.append(KPI(key="accounts_receivable", label="Accounts Receivable", value=data["total_receivable"], unit="IRR"))
    report.kpis.append(KPI(key="accounts_payable", label="Accounts Payable", value=data["total_payable"], unit="IRR"))

    # Expense trend
    exp_trend = ((cur_exp - prev_exp) / prev_exp * 100) if prev_exp else 0
    report.kpis.append(KPI(
        key="expense_trend", label="Expense MoM Change", value=round(exp_trend, 1),
        unit="%", trend="up" if exp_trend > 0 else "down",
        risk_level="caution" if exp_trend > 20 else "normal",
    ))

    # --- INSIGHTS ---
    priority = 1

    if net_profit < 0:
        report.insights.append(Insight(
            priority=priority, category="revenue", severity="critical",
            title="Business is unprofitable",
            body=f"Net loss of {abs(net_profit):,} IRR over the past 12 months. Revenue: {total_rev:,}, Expenses: {total_exp:,}.",
        ))
        priority += 1

    if runway < 3:
        report.insights.append(Insight(
            priority=priority, category="cash", severity="critical",
            title=f"Cash runway critical: {runway:.1f} months",
            body=f"At current burn rate ({burn_rate:,}/month), cash ({data['total_cash']:,}) will run out in {runway:.1f} months.",
        ))
        priority += 1
    elif runway < 6:
        report.insights.append(Insight(
            priority=priority, category="cash", severity="warning",
            title=f"Cash runway is limited: {runway:.1f} months",
            body=f"Consider reducing expenses or accelerating collections.",
        ))
        priority += 1

    if rev_trend < -10 and prev_rev > 0:
        report.insights.append(Insight(
            priority=priority, category="revenue", severity="warning",
            title=f"Revenue declined {abs(rev_trend):.0f}% month-over-month",
            body=f"This month: {cur_rev:,} vs last month: {prev_rev:,}.",
        ))
        priority += 1

    if exp_trend > 30 and prev_exp > 0:
        report.insights.append(Insight(
            priority=priority, category="expense", severity="warning",
            title=f"Expenses spiked {exp_trend:.0f}% this month",
            body=f"This month: {cur_exp:,} vs last month: {prev_exp:,}.",
        ))
        priority += 1

    # Top cost driver
    if data["expense_by_cat"]:
        top_cat = max(data["expense_by_cat"].items(), key=lambda x: x[1])
        top_pct = top_cat[1] / total_exp * 100 if total_exp > 0 else 0
        report.insights.append(Insight(
            priority=priority, category="expense", severity="info",
            title=f"Top cost driver: {top_cat[0]}",
            body=f"{top_cat[0]} accounts for {top_pct:.0f}% of total expenses ({top_cat[1]:,} IRR).",
        ))
        priority += 1

    # Receivable risk
    if data["total_receivable"] > avg_rev * 2 and avg_rev > 0:
        report.insights.append(Insight(
            priority=priority, category="risk", severity="warning",
            title="High receivables",
            body=f"Outstanding receivables ({data['total_receivable']:,}) exceed 2 months of revenue. Collection may be lagging.",
        ))
        priority += 1

    # --- RISK SCORE ---
    risk = 0
    if net_profit < 0:
        risk += 30
    if runway < 3:
        risk += 35
    elif runway < 6:
        risk += 15
    if rev_trend < -20:
        risk += 15
    if exp_trend > 30:
        risk += 10
    if data["total_receivable"] > avg_rev * 3:
        risk += 10
    report.risk_score = min(100, risk)

    # Health Grade
    if report.risk_score <= 15:
        report.health_grade = "A"
    elif report.risk_score <= 30:
        report.health_grade = "B"
    elif report.risk_score <= 50:
        report.health_grade = "C"
    elif report.risk_score <= 70:
        report.health_grade = "D"
    else:
        report.health_grade = "F"

    # --- NARRATIVE ---
    parts = []
    parts.append(f"Over the past 12 months, total revenue was {total_rev:,} IRR with total expenses of {total_exp:,} IRR.")
    if net_profit >= 0:
        parts.append(f"The business is profitable with a net margin of {margin:.1f}%.")
    else:
        parts.append(f"The business is running at a loss of {abs(net_profit):,} IRR.")
    parts.append(f"Cash on hand is {data['total_cash']:,} IRR, providing approximately {runway:.1f} months of runway at the current burn rate of {burn_rate:,}/month.")
    if report.insights:
        top = report.insights[0]
        parts.append(f"Key concern: {top.title}.")
    parts.append(f"Overall financial health grade: {report.health_grade}.")
    report.narrative = " ".join(parts)

    return report


def answer_cfo_question(db: Session, question: str) -> str:
    """
    Answer a natural language CFO question using computed KPIs.
    Returns a plain-text narrative answer.
    """
    report = build_cfo_report(db)
    low = question.lower()

    kpi_map = {k.key: k for k in report.kpis}

    if any(w in low for w in ("healthy", "health", "سلامت", "وضعیت")):
        return (
            f"Financial health grade: {report.health_grade} (risk score: {report.risk_score}/100). "
            f"{report.narrative}"
        )

    if any(w in low for w in ("survive", "runway", "last", "بقا", "دوام")):
        r = kpi_map.get("runway_months")
        return (
            f"At the current burn rate of {report.burn_rate:,} IRR/month, "
            f"with {kpi_map.get('cash_on_hand', KPI(key='', label='', value=0)).value:,} IRR cash on hand, "
            f"the business can sustain operations for approximately {report.runway_months:.1f} months."
        )

    if any(w in low for w in ("profit drop", "profit fell", "profit declin", "سود کاهش", "چرا سود")):
        exp_trend = kpi_map.get("expense_trend")
        top_costs = sorted(report.insights, key=lambda i: i.priority)
        expense_insights = [i for i in top_costs if i.category == "expense"]
        parts = [f"Net margin is {kpi_map.get('net_margin', KPI(key='', label='', value=0)).value}%."]
        if expense_insights:
            parts.append(expense_insights[0].body)
        if exp_trend and exp_trend.value > 0:
            parts.append(f"Expenses increased {exp_trend.value}% month-over-month.")
        return " ".join(parts) if parts else report.narrative

    if any(w in low for w in ("cost driver", "main cost", "biggest expense", "هزینه اصلی", "بیشترین هزینه")):
        cost_insights = [i for i in report.insights if i.category == "expense"]
        if cost_insights:
            return cost_insights[0].body
        return "No significant cost concentration detected."

    if any(w in low for w in ("cash leak", "where.*cash", "نشت نقدینگی", "کجا.*پول")):
        parts = [f"Cash on hand: {kpi_map.get('cash_on_hand', KPI(key='', label='', value=0)).value:,} IRR."]
        parts.append(f"Burn rate: {report.burn_rate:,}/month.")
        ar = kpi_map.get("accounts_receivable")
        if ar and ar.value > 0:
            parts.append(f"Outstanding receivables: {ar.value:,} IRR — consider accelerating collections.")
        return " ".join(parts)

    if any(w in low for w in ("burn rate", "نرخ سوخت", "هزینه ماهانه")):
        return f"Monthly burn rate: {report.burn_rate:,} IRR (3-month average of expenses)."

    # Default: return full narrative
    return report.narrative


@dataclass
class CEOReport:
    revenue_total: int = 0
    revenue_trend: float = 0.0
    profit_total: int = 0
    profit_margin: float = 0.0
    cash_position: int = 0
    cash_runway_months: float = 0.0
    burn_rate: int = 0
    health_grade: str = "A"
    risk_score: int = 0
    total_assets: int = 0
    total_liabilities: int = 0
    total_equity: int = 0
    assets_breakdown: list = field(default_factory=list)
    liabilities_breakdown: list = field(default_factory=list)
    equity_breakdown: list = field(default_factory=list)
    monthly_revenue: list = field(default_factory=list)
    monthly_expenses: list = field(default_factory=list)
    monthly_profit: list = field(default_factory=list)
    top_expenses: list = field(default_factory=list)
    alerts: list = field(default_factory=list)
    accounts_receivable: int = 0
    accounts_payable: int = 0
    liability_ratio: float = 0.0


def build_ceo_report(db: Session) -> CEOReport:
    """Build a high-level CEO executive summary report."""
    cfo = build_cfo_report(db)
    data = _load_monthly_data(db, months_back=12)

    report = CEOReport()

    kpi_map = {k.key: k for k in cfo.kpis}

    # Revenue & Profit
    report.revenue_total = int(kpi_map.get("total_revenue", KPI(key="", label="", value=0)).value)
    report.profit_total = int(kpi_map.get("net_profit", KPI(key="", label="", value=0)).value)
    report.profit_margin = float(kpi_map.get("net_margin", KPI(key="", label="", value=0)).value)
    report.cash_position = int(kpi_map.get("cash_on_hand", KPI(key="", label="", value=0)).value)
    report.cash_runway_months = cfo.runway_months
    report.burn_rate = cfo.burn_rate
    report.health_grade = cfo.health_grade
    report.risk_score = cfo.risk_score
    report.accounts_receivable = int(kpi_map.get("accounts_receivable", KPI(key="", label="", value=0)).value)
    report.accounts_payable = int(kpi_map.get("accounts_payable", KPI(key="", label="", value=0)).value)

    # Revenue trend
    rev_trend_kpi = kpi_map.get("total_revenue")
    report.revenue_trend = float(rev_trend_kpi.trend_pct) if rev_trend_kpi else 0.0

    # Monthly series
    months_sorted = sorted(data["monthly_revenue"].keys())
    for m in months_sorted:
        rev = data["monthly_revenue"].get(m, 0)
        exp = data["monthly_expense"].get(m, 0)
        report.monthly_revenue.append({"month": m, "amount": rev})
        report.monthly_expenses.append({"month": m, "amount": exp})
        report.monthly_profit.append({"month": m, "amount": rev - exp})

    # Top expenses
    sorted_expenses = sorted(data["expense_by_cat"].items(), key=lambda x: x[1], reverse=True)[:8]
    total_exp = sum(data["expense_by_cat"].values()) or 1
    for cat, amt in sorted_expenses:
        report.top_expenses.append({"category": cat, "amount": amt, "pct": round(amt / total_exp * 100, 1)})

    # Balance sheet totals (from accounts)
    from app.models.account import Account as AccountModel
    from app.services.reporting.common import classify_account_code, ASSET, LIABILITY, EQUITY
    accounts = db.execute(select(AccountModel)).scalars().all()
    lines_q = select(
        TransactionLine.account_id,
        func.coalesce(func.sum(TransactionLine.debit), 0),
        func.coalesce(func.sum(TransactionLine.credit), 0),
    ).group_by(TransactionLine.account_id)
    acc_by_id = {a.id: a for a in accounts}
    assets_map: dict[str, dict] = {}
    liabilities_map: dict[str, dict] = {}
    equity_map: dict[str, dict] = {}
    for account_id, td, tc in db.execute(lines_q).all():
        acc = acc_by_id.get(account_id)
        if not acc:
            continue
        acc_type = classify_account_code(acc.code)
        if acc_type == ASSET:
            bal = (td or 0) - (tc or 0)
            report.total_assets += bal
            if bal != 0:
                assets_map[acc.code] = {"code": acc.code, "name": acc.name, "balance": bal}
        elif acc_type == LIABILITY:
            bal = (tc or 0) - (td or 0)
            report.total_liabilities += bal
            if bal != 0:
                liabilities_map[acc.code] = {"code": acc.code, "name": acc.name, "balance": bal}
        elif acc_type == EQUITY:
            bal = (tc or 0) - (td or 0)
            report.total_equity += bal
            if bal != 0:
                equity_map[acc.code] = {"code": acc.code, "name": acc.name, "balance": bal}

    report.assets_breakdown = sorted(assets_map.values(), key=lambda x: abs(x["balance"]), reverse=True)
    report.liabilities_breakdown = sorted(liabilities_map.values(), key=lambda x: abs(x["balance"]), reverse=True)
    report.equity_breakdown = sorted(equity_map.values(), key=lambda x: abs(x["balance"]), reverse=True)
    report.liability_ratio = round(report.total_liabilities / report.total_assets, 4) if report.total_assets > 0 else 0.0

    # Alerts
    for insight in cfo.insights:
        if insight.severity in ("critical", "warning"):
            report.alerts.append({"severity": insight.severity, "title": insight.title, "body": insight.body})

    return report

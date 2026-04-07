"""
Self-auditing accounting service: continuous integrity checks,
anomaly detection, fraud signal detection, and financial health scoring.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean, stdev
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.audit_log import AuditLog, IntegrityCheck
from app.models.transaction import Transaction, TransactionLine
from app.models.account import Account
from app.services.reporting.common import classify_account_code, ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE

logger = logging.getLogger(__name__)


@dataclass
class AuditFinding:
    severity: str  # critical, warning, info
    category: str  # equation, duplicate, anomaly, backdated, negative_balance, fraud_signal
    title: str
    detail: str
    entity_id: str | None = None
    amount: int | None = None


@dataclass
class AuditReport:
    integrity_score: int = 100  # 0-100
    health_score: int = 100
    findings: list[AuditFinding] = field(default_factory=list)
    checks_passed: int = 0
    checks_failed: int = 0
    total_transactions: int = 0
    date_range: tuple[date | None, date | None] = (None, None)


def check_accounting_equation(db: Session) -> list[AuditFinding]:
    """Verify Assets = Liabilities + Equity across all accounts."""
    findings: list[AuditFinding] = []
    accounts = db.execute(select(Account)).scalars().all()
    code_to_acc = {a.code: a for a in accounts}

    totals = {ASSET: 0, LIABILITY: 0, EQUITY: 0, REVENUE: 0, EXPENSE: 0}

    lines = db.execute(
        select(
            TransactionLine.account_id,
            func.sum(TransactionLine.debit).label("total_debit"),
            func.sum(TransactionLine.credit).label("total_credit"),
        ).group_by(TransactionLine.account_id)
    ).all()

    acc_by_id = {a.id: a for a in accounts}
    for account_id, total_debit, total_credit in lines:
        acc = acc_by_id.get(account_id)
        if not acc:
            continue
        acc_type = classify_account_code(acc.code)
        if acc_type in (ASSET, EXPENSE):
            totals[acc_type] += (total_debit or 0) - (total_credit or 0)
        else:
            totals[acc_type] += (total_credit or 0) - (total_debit or 0)

    assets = totals[ASSET]
    liabilities = totals[LIABILITY]
    equity = totals[EQUITY]
    revenue = totals[REVENUE]
    expenses = totals[EXPENSE]
    retained = revenue - expenses

    diff = assets - (liabilities + equity + retained)
    if abs(diff) > 0:
        findings.append(AuditFinding(
            severity="critical",
            category="equation",
            title="Accounting equation imbalance",
            detail=f"Assets ({assets:,}) ≠ Liabilities ({liabilities:,}) + Equity ({equity:,}) + Retained ({retained:,}). Diff: {diff:,}",
            amount=diff,
        ))

    return findings


def check_debit_credit_balance(db: Session) -> list[AuditFinding]:
    """Verify that every transaction has debits = credits."""
    findings: list[AuditFinding] = []

    unbalanced = db.execute(
        select(
            TransactionLine.transaction_id,
            func.sum(TransactionLine.debit).label("td"),
            func.sum(TransactionLine.credit).label("tc"),
        )
        .group_by(TransactionLine.transaction_id)
        .having(func.sum(TransactionLine.debit) != func.sum(TransactionLine.credit))
    ).all()

    for txn_id, td, tc in unbalanced:
        findings.append(AuditFinding(
            severity="critical",
            category="equation",
            title="Unbalanced transaction",
            detail=f"Transaction {txn_id}: debit={td:,}, credit={tc:,}, diff={abs(td - tc):,}",
            entity_id=str(txn_id),
            amount=abs(td - tc),
        ))

    return findings


def detect_duplicate_payments(db: Session, lookback_days: int = 90) -> list[AuditFinding]:
    """Detect potential duplicate payments: same amount, same day, similar description."""
    findings: list[AuditFinding] = []
    cutoff = date.today() - timedelta(days=lookback_days)

    txns = db.execute(
        select(Transaction)
        .where(Transaction.date >= cutoff, Transaction.deleted_at.is_(None))
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
    ).scalars().unique().all()

    by_date_amount: dict[str, list[Transaction]] = defaultdict(list)
    for txn in txns:
        total_debit = sum(ln.debit for ln in txn.lines)
        key = f"{txn.date.isoformat()}:{total_debit}"
        by_date_amount[key].append(txn)

    for key, group in by_date_amount.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                desc_a = (a.description or "").lower().strip()
                desc_b = (b.description or "").lower().strip()
                from difflib import SequenceMatcher
                sim = SequenceMatcher(None, desc_a, desc_b).ratio()
                if sim > 0.7:
                    amt = sum(ln.debit for ln in a.lines)
                    findings.append(AuditFinding(
                        severity="warning",
                        category="duplicate",
                        title="Potential duplicate payment",
                        detail=f"Transactions {a.id} and {b.id} on {a.date}: same amount ({amt:,}), description similarity {sim:.0%}",
                        entity_id=str(a.id),
                        amount=amt,
                    ))

    return findings


def detect_anomalies(db: Session, lookback_days: int = 180) -> list[AuditFinding]:
    """Detect spending anomalies: sudden spikes in expense categories."""
    findings: list[AuditFinding] = []
    cutoff = date.today() - timedelta(days=lookback_days)

    txns = db.execute(
        select(Transaction)
        .where(Transaction.date >= cutoff, Transaction.deleted_at.is_(None))
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
    ).scalars().unique().all()

    monthly_category: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for txn in txns:
        month = txn.date.strftime("%Y-%m")
        for ln in txn.lines:
            if classify_account_code(ln.account.code) == EXPENSE and ln.debit > 0:
                monthly_category[ln.account.name][month] += ln.debit

    for category, months in monthly_category.items():
        if len(months) < 3:
            continue
        values = list(months.values())
        avg = mean(values)
        if avg == 0:
            continue
        sd = stdev(values) if len(values) > 1 else 0
        threshold = avg + 2 * sd if sd > 0 else avg * 1.5

        current_month = date.today().strftime("%Y-%m")
        current = months.get(current_month, 0)
        if current > threshold and current > avg * 1.5:
            findings.append(AuditFinding(
                severity="warning",
                category="anomaly",
                title=f"Expense spike: {category}",
                detail=f"{category} this month: {current:,} vs average {avg:,.0f} (threshold {threshold:,.0f})",
                amount=current,
            ))

    return findings


def detect_negative_balances(db: Session) -> list[AuditFinding]:
    """Detect accounts with unexpected negative balances (assets going negative)."""
    findings: list[AuditFinding] = []

    balances = db.execute(
        select(
            Account.id,
            Account.code,
            Account.name,
            func.coalesce(func.sum(TransactionLine.debit), 0).label("td"),
            func.coalesce(func.sum(TransactionLine.credit), 0).label("tc"),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .group_by(Account.id, Account.code, Account.name)
    ).all()

    for acc_id, code, name, td, tc in balances:
        acc_type = classify_account_code(code)
        if acc_type == ASSET:
            balance = td - tc
            if balance < 0:
                findings.append(AuditFinding(
                    severity="warning",
                    category="negative_balance",
                    title=f"Negative asset balance: {name}",
                    detail=f"Account {code} ({name}) has negative balance: {balance:,}",
                    entity_id=str(acc_id),
                    amount=balance,
                ))

    return findings


def detect_backdated_entries(db: Session, days_threshold: int = 30) -> list[AuditFinding]:
    """Detect entries created significantly after their stated date."""
    findings: list[AuditFinding] = []

    txns = db.execute(
        select(Transaction)
        .where(Transaction.created_at.isnot(None), Transaction.deleted_at.is_(None))
    ).scalars().all()

    for txn in txns:
        if not txn.created_at or not txn.date:
            continue
        created_date = txn.created_at.date() if hasattr(txn.created_at, "date") else txn.created_at
        gap = (created_date - txn.date).days
        if gap > days_threshold:
            findings.append(AuditFinding(
                severity="warning",
                category="backdated",
                title="Backdated entry",
                detail=f"Transaction {txn.id} dated {txn.date} was created {gap} days later on {created_date}",
                entity_id=str(txn.id),
            ))

    return findings


def run_full_audit(db: Session) -> AuditReport:
    """Run all audit checks and produce a comprehensive report."""
    report = AuditReport()

    txn_count = db.execute(select(func.count(Transaction.id))).scalar() or 0
    report.total_transactions = txn_count

    if txn_count > 0:
        date_range = db.execute(
            select(func.min(Transaction.date), func.max(Transaction.date))
        ).one()
        report.date_range = (date_range[0], date_range[1])

    checks = [
        ("Accounting equation", check_accounting_equation),
        ("Debit=Credit per transaction", check_debit_credit_balance),
        ("Duplicate payments", detect_duplicate_payments),
        ("Expense anomalies", detect_anomalies),
        ("Negative balances", detect_negative_balances),
        ("Backdated entries", detect_backdated_entries),
    ]

    for name, check_fn in checks:
        try:
            findings = check_fn(db)
            report.findings.extend(findings)
            if findings:
                report.checks_failed += 1
            else:
                report.checks_passed += 1
        except Exception:
            logger.exception("Audit check failed: %s", name)
            report.checks_failed += 1
            report.findings.append(AuditFinding(
                severity="warning",
                category="system",
                title=f"Check failed: {name}",
                detail=f"The {name} check encountered an error",
            ))

    # Calculate integrity score
    critical_count = sum(1 for f in report.findings if f.severity == "critical")
    warning_count = sum(1 for f in report.findings if f.severity == "warning")
    report.integrity_score = max(0, 100 - critical_count * 25 - warning_count * 5)

    # Calculate health score
    report.health_score = max(0, 100 - critical_count * 20 - warning_count * 3)

    # Persist check results
    for name, _ in checks:
        check_findings = [f for f in report.findings if f.title.startswith(name) or name.lower() in f.title.lower()]
        status = "fail" if any(f.severity == "critical" for f in check_findings) else "warning" if check_findings else "pass"
        score = max(0, 100 - len(check_findings) * 10)
        ic = IntegrityCheck(
            check_type=name.lower().replace(" ", "_").replace("=", "_"),
            status=status,
            score=score,
            detail=json.dumps([{"title": f.title, "detail": f.detail, "severity": f.severity} for f in check_findings]) if check_findings else None,
        )
        db.add(ic)

    db.commit()
    return report


def log_audit_event(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    user_id: str | None = None,
    username: str | None = None,
    detail: str | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Write an immutable audit log entry."""
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        username=username,
        detail=detail,
        ip_address=ip_address,
    )
    db.add(entry)
    db.flush()
    return entry

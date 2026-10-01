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
    domain: str = "financial"  # treasury, managerial, financial
    # what the finding says, for the reader's language: a FINDING_TEXT key and
    # its values (title/detail above stay the English wording)
    key: str = ""
    params: dict = field(default_factory=dict)

    def localized(self, lang: str) -> tuple[str, str]:
        """(title, detail) in ``lang`` — English for an unknown key or language."""
        text = FINDING_TEXT.get(self.key)
        if not text or lang not in text["title"]:
            return self.title, self.detail
        params = self.params
        if lang in ("fa", "ar"):   # a name or a signed number keeps its own direction in a right-to-left line
            params = {k: f"\u2068{v}\u2069" for k, v in params.items()}
        return text["title"][lang].format(**params), text["detail"][lang].format(**params)


# The findings' wording in each language the app speaks; {placeholders} are the
# finding's params (tests/test_audit_service_i18n.py keeps the four in step).
FINDING_TEXT: dict[str, dict[str, dict[str, str]]] = {
    "equation_imbalance": {
        "title": {"en": "Accounting equation imbalance", "fa": "عدم توازن معادله حسابداری",
                  "es": "Desequilibrio de la ecuación contable", "ar": "اختلال معادلة المحاسبة"},
        "detail": {"en": "Assets ({assets}) ≠ liabilities ({liabilities}) + equity ({equity}) + retained earnings ({retained}). Difference: {diff}",
                   "fa": "دارایی‌ها ({assets}) ≠ بدهی‌ها ({liabilities}) + حقوق مالکانه ({equity}) + سود انباشته ({retained}). اختلاف: {diff}",
                   "es": "Activos ({assets}) ≠ pasivos ({liabilities}) + patrimonio ({equity}) + resultados acumulados ({retained}). Diferencia: {diff}",
                   "ar": "الأصول ({assets}) ≠ الالتزامات ({liabilities}) + حقوق الملكية ({equity}) + الأرباح المحتجزة ({retained}). الفرق: {diff}"},
    },
    "unbalanced_journal": {
        "title": {"en": "Unbalanced journal", "fa": "سند ناتراز", "es": "Asiento descuadrado", "ar": "قيد غير متوازن"},
        "detail": {"en": "Journal {ref}: debit {debit}, credit {credit}, difference {diff}",
                   "fa": "سند {ref}: بدهکار {debit}، بستانکار {credit}، اختلاف {diff}",
                   "es": "Asiento {ref}: debe {debit}, haber {credit}, diferencia {diff}",
                   "ar": "القيد {ref}: مدين {debit}، دائن {credit}، الفرق {diff}"},
    },
    "duplicate_payment": {
        "title": {"en": "Possible duplicate payment", "fa": "احتمال پرداخت تکراری", "es": "Posible pago duplicado",
                  "ar": "دفعة مكررة محتملة"},
        "detail": {"en": "Journals {a} and {b} on {date}: the same amount ({amount}), descriptions {similarity} alike",
                   "fa": "اسناد {a} و {b} در تاریخ {date}: مبلغ یکسان ({amount})، شباهت شرح {similarity}",
                   "es": "Asientos {a} y {b} del {date}: el mismo importe ({amount}), descripciones parecidas en un {similarity}",
                   "ar": "القيدان {a} و{b} بتاريخ {date}: المبلغ نفسه ({amount})، وتشابه الوصف {similarity}"},
    },
    "expense_spike": {
        "title": {"en": "Expense spike: {category}", "fa": "جهش هزینه: {category}", "es": "Pico de gasto: {category}",
                  "ar": "ارتفاع حاد في المصروف: {category}"},
        "detail": {"en": "{category} this month: {current} against an average of {average} (threshold {threshold})",
                   "fa": "{category} در این ماه: {current} در برابر میانگین {average} (آستانه {threshold})",
                   "es": "{category} este mes: {current} frente a una media de {average} (umbral {threshold})",
                   "ar": "{category} هذا الشهر: {current} مقابل متوسط {average} (الحد {threshold})"},
    },
    "negative_asset": {
        "title": {"en": "Negative asset balance: {name}", "fa": "مانده منفی دارایی: {name}",
                  "es": "Saldo negativo de activo: {name}", "ar": "رصيد أصل سالب: {name}"},
        "detail": {"en": "Account {code} ({name}) has a negative balance: {balance}",
                   "fa": "حساب {code} ({name}) مانده منفی دارد: {balance}",
                   "es": "La cuenta {code} ({name}) tiene saldo negativo: {balance}",
                   "ar": "الحساب {code} ({name}) رصيده سالب: {balance}"},
    },
    "backdated": {
        "title": {"en": "Backdated entry", "fa": "سند با تاریخ گذشته", "es": "Asiento con fecha atrasada", "ar": "قيد بتاريخ سابق"},
        "detail": {"en": "Journal {ref} dated {date} was entered {days} days later, on {created}",
                   "fa": "سند {ref} به تاریخ {date}، {days} روز بعد در {created} ثبت شد",
                   "es": "El asiento {ref} con fecha {date} se registró {days} días después, el {created}",
                   "ar": "القيد {ref} المؤرخ {date} أُدخل بعد {days} يوماً، في {created}"},
    },
    "liability_threshold": {
        "title": {"en": "Liabilities exceed the threshold", "fa": "بدهی‌ها از آستانه بیشتر است",
                  "es": "Los pasivos superan el umbral", "ar": "الالتزامات تتجاوز الحد"},
        "detail": {"en": "Total liabilities ({total}) exceed the threshold ({threshold}) by {excess}",
                   "fa": "جمع بدهی‌ها ({total}) به اندازه {excess} از آستانه ({threshold}) بیشتر است",
                   "es": "El total de pasivos ({total}) supera el umbral ({threshold}) en {excess}",
                   "ar": "إجمالي الالتزامات ({total}) يتجاوز الحد ({threshold}) بمقدار {excess}"},
    },
    "check_failed": {
        "title": {"en": "Check failed: {name}", "fa": "بررسی انجام نشد: {name}", "es": "Falló la comprobación: {name}",
                  "ar": "فشل الفحص: {name}"},
        "detail": {"en": "The {name} check ran into an error", "fa": "بررسی {name} با خطا روبه‌رو شد",
                   "es": "La comprobación {name} encontró un error", "ar": "واجه فحص {name} خطأً"},
    },
}


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
            key="equation_imbalance",
            params={"assets": f"{assets:,}", "liabilities": f"{liabilities:,}", "equity": f"{equity:,}",
                    "retained": f"{retained:,}", "diff": f"{diff:,}"},
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
            key="unbalanced_journal",
            params={"ref": str(txn_id)[:8], "debit": f"{td:,}", "credit": f"{tc:,}", "diff": f"{abs(td - tc):,}"},
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
                        key="duplicate_payment",
                        params={"a": str(a.id)[:8], "b": str(b.id)[:8], "date": str(a.date), "amount": f"{amt:,}",
                                "similarity": f"{sim:.0%}"},
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
                key="expense_spike",
                params={"category": str(category), "current": f"{current:,}", "average": f"{avg:,.0f}",
                        "threshold": f"{threshold:,.0f}"},
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
                    key="negative_asset",
                    params={"code": str(code), "name": str(name), "balance": f"{balance:,}"},
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
                key="backdated",
                params={"ref": str(txn.id)[:8], "date": str(txn.date), "days": str(gap), "created": str(created_date)},
            ))

    return findings


def check_liability_threshold(db: Session, threshold: int = 0) -> list[AuditFinding]:
    """Check if total liabilities exceed a defined threshold."""
    findings: list[AuditFinding] = []
    if threshold <= 0:
        # Try to get threshold from app settings
        from app.models.app_setting import AppSetting
        setting = db.execute(
            select(AppSetting).where(AppSetting.key == "liability_threshold")
        ).scalar_one_or_none()
        if setting:
            try:
                threshold = int(setting.value)
            except (ValueError, TypeError):
                threshold = 0

    if threshold <= 0:
        return findings

    balances = db.execute(
        select(
            func.coalesce(func.sum(TransactionLine.credit), 0).label("tc"),
            func.coalesce(func.sum(TransactionLine.debit), 0).label("td"),
        )
        .join(Account, Account.id == TransactionLine.account_id)
        .where(Account.code.like("2%"))
    ).one()
    total_liabilities = int((balances[0] or 0) - (balances[1] or 0))

    if total_liabilities > threshold:
        findings.append(AuditFinding(
            severity="critical",
            category="liability_threshold",
            title="Liabilities exceed threshold",
            detail=f"Total liabilities ({total_liabilities:,}) exceed the defined threshold ({threshold:,}). Excess: {total_liabilities - threshold:,}",
            amount=total_liabilities,
            key="liability_threshold",
            params={"total": f"{total_liabilities:,}", "threshold": f"{threshold:,}",
                    "excess": f"{total_liabilities - threshold:,}"},
            domain="treasury",
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
        ("Liability threshold", check_liability_threshold),
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
                key="check_failed",
                params={"name": name},
            ))

    # Assign domains to findings
    for f in report.findings:
        if f.category in ("equation", "negative_balance"):
            f.domain = "financial"
        elif f.category in ("duplicate", "anomaly", "liability_threshold"):
            f.domain = "treasury"
        elif f.category in ("backdated", "fraud_signal"):
            f.domain = "managerial"
        # default is "financial"

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
    role: str | None = None,
) -> AuditLog:
    """Write an immutable audit log entry. The acting user (id/username/role)
    defaults to the request's current user when not supplied."""
    from app.core.audit import _default_actor
    user_id, username, role = _default_actor(user_id, username, role)
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        username=username,
        actor_role=role,
        detail=detail,
        ip_address=ip_address,
    )
    db.add(entry)
    db.flush()
    return entry

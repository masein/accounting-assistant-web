"""
API router for the AI Financial Brain: bank statement ingestion,
reconciliation, self-auditing, and CFO intelligence.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from datetime import date
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.account import Account
from app.models.audit_log import AuditLog, IntegrityCheck, TransactionVersion
from app.models.bank_statement import BankStatement, BankStatementRow
from app.models.transaction import Transaction, TransactionLine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/brain", tags=["financial-brain"])


# ─── Schemas ────────────────────────────────────────────────────────

class BankStatementUploadResponse(BaseModel):
    id: UUID
    status: str
    total_rows: int
    bank_name: str
    source_type: str
    errors: list[str] = Field(default_factory=list)


class BankStatementRowRead(BaseModel):
    id: UUID
    row_index: int
    tx_date: date
    description: str | None
    reference: str | None
    debit: int
    credit: int
    balance: int | None
    counterparty: str | None
    confidence: float
    category: str | None
    suggested_account_code: str | None
    recon_status: str
    matched_transaction_id: UUID | None
    user_approved: bool


class BankStatementRead(BaseModel):
    id: UUID
    bank_name: str
    account_number: str | None
    source_type: str
    source_filename: str
    currency: str
    from_date: date | None
    to_date: date | None
    status: str
    total_rows: int
    matched_rows: int
    new_rows: int
    rows: list[BankStatementRowRead] = Field(default_factory=list)


class ReconcileResponse(BaseModel):
    total_rows: int
    matched: int
    partial: int
    unmatched: int
    duplicates: int
    auto_matched: int
    missing_in_bank: int


class RowApproval(BaseModel):
    row_id: UUID
    action: str = Field(..., description="approve, reject, skip, create")
    account_code: str | None = None


class BatchApprovalRequest(BaseModel):
    approvals: list[RowApproval]


class BatchApprovalResponse(BaseModel):
    approved: int
    rejected: int
    skipped: int
    created: int
    errors: list[str] = Field(default_factory=list)


class AuditFindingRead(BaseModel):
    severity: str
    category: str
    title: str
    detail: str
    entity_id: str | None = None
    amount: int | None = None
    domain: str = "financial"
    verification_status: str = "pending"


class AuditReportResponse(BaseModel):
    integrity_score: int
    health_score: int
    findings: list[AuditFindingRead]
    checks_passed: int
    checks_failed: int
    total_transactions: int
    liability_total: int = 0
    liability_threshold: int = 0


class AuditLogRead(BaseModel):
    id: UUID
    timestamp: str
    action: str
    entity_type: str
    entity_id: str | None
    username: str | None
    detail: str | None


class CFOKpiRead(BaseModel):
    key: str
    label: str
    value: float | int
    unit: str
    trend: str
    trend_pct: float
    risk_level: str


class CFOInsightRead(BaseModel):
    priority: int
    category: str
    title: str
    body: str
    severity: str


class CFOReportResponse(BaseModel):
    kpis: list[CFOKpiRead]
    insights: list[CFOInsightRead]
    narrative: str
    risk_score: int
    runway_months: float
    burn_rate: int
    health_grade: str


class CEOReportResponse(BaseModel):
    revenue_total: int
    revenue_trend: float
    profit_total: int
    profit_margin: float
    cash_position: int
    cash_runway_months: float
    burn_rate: int
    health_grade: str
    risk_score: int
    total_assets: int
    total_liabilities: int
    total_equity: int
    monthly_revenue: list[dict]
    monthly_expenses: list[dict]
    monthly_profit: list[dict]
    top_expenses: list[dict]
    alerts: list[dict]
    accounts_receivable: int
    accounts_payable: int
    liability_ratio: float


class CFOQuestionRequest(BaseModel):
    question: str = Field(..., min_length=3)


class CFOQuestionResponse(BaseModel):
    question: str
    answer: str
    health_grade: str
    risk_score: int


class TransactionVersionRead(BaseModel):
    id: UUID
    transaction_id: str
    version: int
    action: str
    snapshot: str
    created_at: str


# ─── Bank Statement Endpoints ──────────────────────────────────────

@router.post("/bank-statements/upload", response_model=BankStatementUploadResponse)
async def upload_bank_statement(
    file: UploadFile = File(...),
    bank_name: str = Query("Unknown"),
    db: Session = Depends(get_db),
) -> BankStatementUploadResponse:
    """Upload a CSV, Excel, or image/PDF bank statement for parsing and reconciliation."""
    content = await file.read()
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower()

    from app.services.bank_statement_parser import parse_csv, parse_excel, parse_ocr_rows, classify_transaction

    if ext in (".csv", ".tsv"):
        result = parse_csv(content, bank_name=bank_name)
    elif ext in (".xlsx", ".xls"):
        tmp_path = Path("/tmp") / f"bs_{uuid.uuid4().hex}{ext}"
        tmp_path.write_bytes(content)
        result = parse_excel(str(tmp_path), bank_name=bank_name)
        tmp_path.unlink(missing_ok=True)
    elif ext in (".jpg", ".jpeg", ".png", ".webp", ".pdf"):
        from app.services.ocr_extract import extract_from_attachment
        tmp_path = Path("/tmp") / f"bs_{uuid.uuid4().hex}{ext}"
        tmp_path.write_bytes(content)
        try:
            ocr_result = await extract_from_attachment(str(tmp_path), file.content_type or "image/jpeg")
            raw_text = ocr_result.get("raw_text", "")
            result = parse_ocr_rows(raw_text, bank_name=bank_name)
            result.source_type = "ocr_pdf" if ext == ".pdf" else "ocr_image"
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    stmt = BankStatement(
        bank_name=bank_name,
        source_type=result.source_type,
        source_filename=filename,
        currency=result.currency,
        from_date=result.from_date,
        to_date=result.to_date,
        status="parsed",
        total_rows=len(result.rows),
    )
    db.add(stmt)
    db.flush()

    for row in result.rows:
        cat, code = classify_transaction(row.description)
        db_row = BankStatementRow(
            statement_id=stmt.id,
            row_index=row.row_index,
            tx_date=row.tx_date,
            description=row.description,
            reference=row.reference,
            debit=row.debit,
            credit=row.credit,
            balance=row.balance,
            counterparty=row.counterparty,
            raw_text=row.raw_text,
            confidence=row.confidence,
            category=cat,
            suggested_account_code=code,
        )
        db.add(db_row)

    db.commit()
    return BankStatementUploadResponse(
        id=stmt.id,
        status=stmt.status,
        total_rows=len(result.rows),
        bank_name=bank_name,
        source_type=result.source_type,
        errors=result.errors,
    )


@router.get("/bank-statements", response_model=list[BankStatementRead])
def list_bank_statements(
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
) -> list[BankStatementRead]:
    stmts = db.execute(
        select(BankStatement).order_by(BankStatement.created_at.desc()).limit(limit)
    ).scalars().all()
    out = []
    for s in stmts:
        rows = db.execute(
            select(BankStatementRow).where(BankStatementRow.statement_id == s.id)
            .order_by(BankStatementRow.row_index)
        ).scalars().all()
        out.append(BankStatementRead(
            id=s.id, bank_name=s.bank_name, account_number=s.account_number,
            source_type=s.source_type, source_filename=s.source_filename,
            currency=s.currency, from_date=s.from_date, to_date=s.to_date,
            status=s.status, total_rows=s.total_rows,
            matched_rows=s.matched_rows, new_rows=s.new_rows,
            rows=[BankStatementRowRead(
                id=r.id, row_index=r.row_index, tx_date=r.tx_date,
                description=r.description, reference=r.reference,
                debit=r.debit, credit=r.credit, balance=r.balance,
                counterparty=r.counterparty, confidence=r.confidence,
                category=r.category, suggested_account_code=r.suggested_account_code,
                recon_status=r.recon_status,
                matched_transaction_id=r.matched_transaction_id,
                user_approved=r.user_approved,
            ) for r in rows],
        ))
    return out


@router.get("/bank-statements/{statement_id}", response_model=BankStatementRead)
def get_bank_statement(statement_id: UUID, db: Session = Depends(get_db)) -> BankStatementRead:
    s = db.get(BankStatement, statement_id)
    if not s:
        raise HTTPException(status_code=404, detail="Statement not found")
    rows = db.execute(
        select(BankStatementRow).where(BankStatementRow.statement_id == s.id)
        .order_by(BankStatementRow.row_index)
    ).scalars().all()
    return BankStatementRead(
        id=s.id, bank_name=s.bank_name, account_number=s.account_number,
        source_type=s.source_type, source_filename=s.source_filename,
        currency=s.currency, from_date=s.from_date, to_date=s.to_date,
        status=s.status, total_rows=s.total_rows,
        matched_rows=s.matched_rows, new_rows=s.new_rows,
        rows=[BankStatementRowRead(
            id=r.id, row_index=r.row_index, tx_date=r.tx_date,
            description=r.description, reference=r.reference,
            debit=r.debit, credit=r.credit, balance=r.balance,
            counterparty=r.counterparty, confidence=r.confidence,
            category=r.category, suggested_account_code=r.suggested_account_code,
            recon_status=r.recon_status,
            matched_transaction_id=r.matched_transaction_id,
            user_approved=r.user_approved,
        ) for r in rows],
    )


# ─── Reconciliation Endpoints ──────────────────────────────────────

@router.post("/bank-statements/{statement_id}/reconcile", response_model=ReconcileResponse)
def reconcile_statement(statement_id: UUID, db: Session = Depends(get_db)) -> ReconcileResponse:
    """Run automatic reconciliation on a parsed bank statement."""
    s = db.get(BankStatement, statement_id)
    if not s:
        raise HTTPException(status_code=404, detail="Statement not found")

    rows = db.execute(
        select(BankStatementRow).where(BankStatementRow.statement_id == s.id)
        .order_by(BankStatementRow.row_index)
    ).scalars().all()

    from app.services.reconciliation import reconcile_statement as _reconcile, detect_missing_entries

    results = _reconcile(db, rows)

    matched = partial = unmatched = duplicates = auto_matched = 0
    for row, result in zip(rows, results):
        row.recon_status = result.status
        if result.best_match:
            row.matched_transaction_id = result.best_match.transaction_id
        if result.auto_match:
            auto_matched += 1
        if result.status == "matched":
            matched += 1
        elif result.status == "partial":
            partial += 1
        elif result.status == "duplicate":
            duplicates += 1
        else:
            unmatched += 1

    # Detect missing entries
    matched_ids = {r.matched_transaction_id for r in rows if r.matched_transaction_id}
    missing = detect_missing_entries(db, s.from_date or rows[0].tx_date, s.to_date or rows[-1].tx_date, matched_ids) if rows else []

    s.status = "reviewing"
    s.matched_rows = matched
    s.new_rows = unmatched
    db.commit()

    return ReconcileResponse(
        total_rows=len(rows),
        matched=matched,
        partial=partial,
        unmatched=unmatched,
        duplicates=duplicates,
        auto_matched=auto_matched,
        missing_in_bank=len(missing),
    )


@router.post("/bank-statements/{statement_id}/approve", response_model=BatchApprovalResponse)
def batch_approve_rows(
    statement_id: UUID,
    payload: BatchApprovalRequest,
    db: Session = Depends(get_db),
) -> BatchApprovalResponse:
    """Approve, reject, or create transactions from bank statement rows."""
    s = db.get(BankStatement, statement_id)
    if not s:
        raise HTTPException(status_code=404, detail="Statement not found")

    approved = rejected = skipped = created = 0
    errors: list[str] = []

    for approval in payload.approvals:
        row = db.get(BankStatementRow, approval.row_id)
        if not row or row.statement_id != s.id:
            errors.append(f"Row {approval.row_id} not found in this statement")
            continue

        if approval.action == "approve":
            row.user_approved = True
            row.recon_status = "matched"
            approved += 1

        elif approval.action == "reject":
            row.user_approved = False
            row.recon_status = "unmatched"
            row.matched_transaction_id = None
            rejected += 1

        elif approval.action == "skip":
            row.recon_status = "skipped"
            skipped += 1

        elif approval.action == "create":
            acc_code = approval.account_code or row.suggested_account_code or "6190"
            acc = db.execute(select(Account).where(Account.code == acc_code)).scalar_one_or_none()
            cash_acc = db.execute(select(Account).where(Account.code == "1110")).scalar_one_or_none()
            if not acc or not cash_acc:
                errors.append(f"Row {row.row_index}: account code '{acc_code}' or cash account not found")
                continue

            txn = Transaction(
                date=row.tx_date,
                reference=row.reference,
                description=row.description or f"Bank statement row #{row.row_index}",
            )
            db.add(txn)
            db.flush()

            if row.debit > 0:
                db.add(TransactionLine(transaction_id=txn.id, account_id=acc.id, debit=row.debit, credit=0))
                db.add(TransactionLine(transaction_id=txn.id, account_id=cash_acc.id, debit=0, credit=row.debit))
            else:
                db.add(TransactionLine(transaction_id=txn.id, account_id=cash_acc.id, debit=row.credit, credit=0))
                db.add(TransactionLine(transaction_id=txn.id, account_id=acc.id, debit=0, credit=row.credit))

            row.created_transaction_id = txn.id
            row.recon_status = "matched"
            row.user_approved = True
            created += 1

    s.status = "approved" if all(
        r.recon_status in ("matched", "skipped", "duplicate") for r in db.execute(
            select(BankStatementRow).where(BankStatementRow.statement_id == s.id)
        ).scalars().all()
    ) else "reviewing"

    db.commit()
    return BatchApprovalResponse(
        approved=approved, rejected=rejected, skipped=skipped,
        created=created, errors=errors,
    )


# ─── Audit Endpoints ───────────────────────────────────────────────

@router.get("/audit/report", response_model=AuditReportResponse)
def get_audit_report(db: Session = Depends(get_db)) -> AuditReportResponse:
    """Run a full self-audit and return the report."""
    from app.services.audit_service import run_full_audit
    report = run_full_audit(db)
    return AuditReportResponse(
        integrity_score=report.integrity_score,
        health_score=report.health_score,
        findings=[AuditFindingRead(
            severity=f.severity, category=f.category,
            title=f.title, detail=f.detail,
            entity_id=f.entity_id, amount=f.amount,
            domain=f.domain,
        ) for f in report.findings],
        checks_passed=report.checks_passed,
        checks_failed=report.checks_failed,
        total_transactions=report.total_transactions,
    )


@router.get("/audit/logs", response_model=list[AuditLogRead])
def get_audit_logs(
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=500),
    entity_type: str | None = Query(None),
    action: str | None = Query(None),
) -> list[AuditLogRead]:
    """Retrieve audit log entries."""
    q = select(AuditLog).order_by(AuditLog.timestamp.desc())
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if action:
        q = q.where(AuditLog.action == action)
    q = q.limit(limit)
    logs = db.execute(q).scalars().all()
    return [AuditLogRead(
        id=l.id, timestamp=l.timestamp.isoformat() if l.timestamp else "",
        action=l.action, entity_type=l.entity_type,
        entity_id=l.entity_id, username=l.username,
        detail=l.detail,
    ) for l in logs]


@router.get("/audit/integrity-history", response_model=list[dict])
def get_integrity_history(
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
) -> list[dict]:
    checks = db.execute(
        select(IntegrityCheck).order_by(IntegrityCheck.checked_at.desc()).limit(limit)
    ).scalars().all()
    return [
        {
            "id": str(c.id),
            "check_type": c.check_type,
            "status": c.status,
            "score": c.score,
            "detail": c.detail,
            "checked_at": c.checked_at.isoformat() if c.checked_at else "",
        }
        for c in checks
    ]


@router.get("/audit/versions/{transaction_id}", response_model=list[TransactionVersionRead])
def get_transaction_versions(
    transaction_id: UUID,
    db: Session = Depends(get_db),
) -> list[TransactionVersionRead]:
    versions = db.execute(
        select(TransactionVersion)
        .where(TransactionVersion.transaction_id == str(transaction_id))
        .order_by(TransactionVersion.version.desc())
    ).scalars().all()
    return [TransactionVersionRead(
        id=v.id, transaction_id=v.transaction_id,
        version=v.version, action=v.action,
        snapshot=v.snapshot,
        created_at=v.created_at.isoformat() if v.created_at else "",
    ) for v in versions]


# ─── Settings Endpoint ────────────────────────────────────────────

class SettingPayload(BaseModel):
    key: str
    value: str


@router.post("/settings")
def save_setting(payload: SettingPayload, db: Session = Depends(get_db)) -> dict:
    """Save an application setting (key-value pair)."""
    from app.models.app_setting import AppSetting
    existing = db.execute(select(AppSetting).where(AppSetting.key == payload.key)).scalar_one_or_none()
    if existing:
        existing.value = payload.value
    else:
        db.add(AppSetting(key=payload.key, value=payload.value))
    db.commit()
    return {"key": payload.key, "value": payload.value, "status": "saved"}


@router.get("/settings/{key}")
def get_setting(key: str, db: Session = Depends(get_db)) -> dict:
    """Get an application setting by key."""
    from app.models.app_setting import AppSetting
    setting = db.execute(select(AppSetting).where(AppSetting.key == key)).scalar_one_or_none()
    return {"key": key, "value": setting.value if setting else None}


# ─── CFO Intelligence Endpoints ────────────────────────────────────

@router.get("/cfo/report", response_model=CFOReportResponse)
def get_cfo_report(db: Session = Depends(get_db)) -> CFOReportResponse:
    """Get the CFO-level financial intelligence report."""
    from app.services.cfo_intelligence import build_cfo_report
    report = build_cfo_report(db)
    return CFOReportResponse(
        kpis=[CFOKpiRead(
            key=k.key, label=k.label, value=k.value, unit=k.unit,
            trend=k.trend, trend_pct=k.trend_pct, risk_level=k.risk_level,
        ) for k in report.kpis],
        insights=[CFOInsightRead(
            priority=i.priority, category=i.category,
            title=i.title, body=i.body, severity=i.severity,
        ) for i in report.insights],
        narrative=report.narrative,
        risk_score=report.risk_score,
        runway_months=report.runway_months,
        burn_rate=report.burn_rate,
        health_grade=report.health_grade,
    )


@router.get("/ceo/report", response_model=CEOReportResponse)
def get_ceo_report(db: Session = Depends(get_db)) -> CEOReportResponse:
    """Get the CEO-level executive summary report."""
    from app.services.cfo_intelligence import build_ceo_report
    report = build_ceo_report(db)
    return CEOReportResponse(
        revenue_total=report.revenue_total,
        revenue_trend=report.revenue_trend,
        profit_total=report.profit_total,
        profit_margin=report.profit_margin,
        cash_position=report.cash_position,
        cash_runway_months=report.cash_runway_months,
        burn_rate=report.burn_rate,
        health_grade=report.health_grade,
        risk_score=report.risk_score,
        total_assets=report.total_assets,
        total_liabilities=report.total_liabilities,
        total_equity=report.total_equity,
        monthly_revenue=report.monthly_revenue,
        monthly_expenses=report.monthly_expenses,
        monthly_profit=report.monthly_profit,
        top_expenses=report.top_expenses,
        alerts=report.alerts,
        accounts_receivable=report.accounts_receivable,
        accounts_payable=report.accounts_payable,
        liability_ratio=report.liability_ratio,
    )


class SeedDataResponse(BaseModel):
    transactions_created: int
    invoices_created: int
    entities_created: int
    inventory_items_created: int
    recurring_rules_created: int
    budget_limits_created: int
    bank_statement_rows: int
    message: str


@router.post("/cfo/seed-sample-data", response_model=SeedDataResponse)
def seed_sample_financial_data(db: Session = Depends(get_db)) -> SeedDataResponse:
    """
    Seed the database with 6 months of comprehensive financial data
    covering ALL app sections: transactions, invoices, entities, inventory,
    recurring rules, budgets, and a sample bank statement.
    """
    from datetime import timedelta
    from app.models.entity import Entity, TransactionEntity
    from app.models.invoice import Invoice
    from app.models.invoice_item import InvoiceItem
    from app.models.recurring import RecurringRule
    from app.models.budget import BudgetLimit
    from app.models.inventory import InventoryItem, InventoryMovement

    today = date.today()
    counters = {"txn": 0, "inv": 0, "ent": 0, "item": 0, "rec": 0, "bud": 0, "bs_rows": 0}

    # --- Accounts ---
    accounts = {a.code: a for a in db.execute(select(Account).where(Account.level != "GROUP")).scalars().all()}
    cash = accounts.get("1110")
    receivable = accounts.get("1112")
    payable = accounts.get("2110")
    sales = accounts.get("4110")
    payroll = accounts.get("6110")
    operating = accounts.get("6112")
    financial_exp = accounts.get("6210")
    fixed_assets = accounts.get("1210")
    capital = accounts.get("3110")

    if not all([cash, receivable, payable, sales, payroll]):
        raise HTTPException(status_code=400, detail="Required accounts not found. Seed chart of accounts first.")

    # ===== ENTITIES =====
    entity_specs = [
        ("client", "Innotech Solutions", "CLI-001"),
        ("client", "DataFlow Corp", "CLI-002"),
        ("client", "Parsian Trading", "CLI-003"),
        ("supplier", "Office Supplies Co", "SUP-001"),
        ("supplier", "Cloud Hosting Inc", "SUP-002"),
        ("supplier", "Pars Stationery", "SUP-003"),
        ("employee", "Ali Rezaei", "EMP-001"),
        ("employee", "Sara Mohammadi", "EMP-002"),
        ("employee", "Reza Karimi", "EMP-003"),
        ("bank", "Mellat Bank", "BNK-001"),
        ("bank", "Saderat Bank", "BNK-002"),
    ]
    entity_map = {}
    for etype, name, code in entity_specs:
        existing = db.execute(select(Entity).where(Entity.name == name, Entity.type == etype)).scalars().first()
        if existing:
            entity_map[name] = existing
        else:
            e = Entity(type=etype, name=name, code=code)
            db.add(e)
            db.flush()
            entity_map[name] = e
            counters["ent"] += 1

    # Helper to link entity to transaction
    def link_entity(txn_id, entity_name, role):
        ent = entity_map.get(entity_name)
        if ent:
            db.add(TransactionEntity(transaction_id=txn_id, entity_id=ent.id, role=role))

    # ===== TRANSACTIONS (6 months) =====
    for months_ago in range(6, 0, -1):
        month_date = today - timedelta(days=months_ago * 30)
        month_label = 7 - months_ago
        base_rev = 50_000_000 + (6 - months_ago) * 5_000_000

        # Cash sale
        t = Transaction(date=month_date, description=f"Cash sale to Parsian Trading - month {month_label}", reference=f"REV-{month_label:03d}")
        db.add(t); db.flush()
        db.add(TransactionLine(transaction_id=t.id, account_id=cash.id, debit=base_rev, credit=0, line_description="Cash received from client"))
        db.add(TransactionLine(transaction_id=t.id, account_id=sales.id, debit=0, credit=base_rev, line_description="Sales revenue"))
        link_entity(t.id, "Parsian Trading", "client")
        counters["txn"] += 1

        # Credit sale → receivable
        credit_rev = base_rev // 2
        t = Transaction(date=month_date + timedelta(days=5), description=f"Credit sale to Innotech Solutions - month {month_label}", reference=f"INV-{month_label:03d}")
        db.add(t); db.flush()
        db.add(TransactionLine(transaction_id=t.id, account_id=receivable.id, debit=credit_rev, credit=0, line_description="Accounts receivable"))
        db.add(TransactionLine(transaction_id=t.id, account_id=sales.id, debit=0, credit=credit_rev, line_description="Credit sales"))
        link_entity(t.id, "Innotech Solutions", "client")
        counters["txn"] += 1

        # Collect receivable (except last 2 months)
        if months_ago > 2:
            t = Transaction(date=month_date + timedelta(days=20), description=f"Collection from Innotech Solutions - month {month_label}", reference=f"COL-{month_label:03d}")
            db.add(t); db.flush()
            db.add(TransactionLine(transaction_id=t.id, account_id=cash.id, debit=credit_rev, credit=0, line_description="Cash received"))
            db.add(TransactionLine(transaction_id=t.id, account_id=receivable.id, debit=0, credit=credit_rev, line_description="Receivable cleared"))
            link_entity(t.id, "Innotech Solutions", "client")
            counters["txn"] += 1

        # DataFlow sale (smaller)
        df_rev = 15_000_000 + months_ago * 2_000_000
        t = Transaction(date=month_date + timedelta(days=8), description=f"Service fee from DataFlow Corp - month {month_label}", reference=f"DF-{month_label:03d}")
        db.add(t); db.flush()
        db.add(TransactionLine(transaction_id=t.id, account_id=cash.id, debit=df_rev, credit=0, line_description="Cash from DataFlow"))
        db.add(TransactionLine(transaction_id=t.id, account_id=sales.id, debit=0, credit=df_rev, line_description="Service revenue"))
        link_entity(t.id, "DataFlow Corp", "client")
        counters["txn"] += 1

        # Payroll
        payroll_amt = 30_000_000 + (month_label - 1) * 500_000
        t = Transaction(date=month_date + timedelta(days=25), description=f"Payroll payment - month {month_label}", reference=f"PAY-{month_label:03d}")
        db.add(t); db.flush()
        db.add(TransactionLine(transaction_id=t.id, account_id=payroll.id, debit=payroll_amt, credit=0, line_description="Salary expense"))
        db.add(TransactionLine(transaction_id=t.id, account_id=cash.id, debit=0, credit=payroll_amt, line_description="Salary payment"))
        link_entity(t.id, "Ali Rezaei", "payee")
        counters["txn"] += 1

        # Operating expenses
        op_amt = 10_000_000 + months_ago * 1_000_000
        t = Transaction(date=month_date + timedelta(days=10), description=f"Office supplies from Pars Stationery - month {month_label}", reference=f"OPX-{month_label:03d}")
        db.add(t); db.flush()
        if operating:
            db.add(TransactionLine(transaction_id=t.id, account_id=operating.id, debit=op_amt, credit=0, line_description="Operating costs"))
            db.add(TransactionLine(transaction_id=t.id, account_id=cash.id, debit=0, credit=op_amt, line_description="Cash payment"))
        link_entity(t.id, "Pars Stationery", "supplier")
        counters["txn"] += 1

        # Cloud hosting (via payable)
        cloud_amt = 8_000_000
        t = Transaction(date=month_date + timedelta(days=15), description=f"Cloud hosting subscription - month {month_label}", reference=f"CLD-{month_label:03d}")
        db.add(t); db.flush()
        if operating:
            db.add(TransactionLine(transaction_id=t.id, account_id=operating.id, debit=cloud_amt, credit=0, line_description="Cloud hosting"))
        db.add(TransactionLine(transaction_id=t.id, account_id=payable.id, debit=0, credit=cloud_amt, line_description="Payable to Cloud Hosting Inc"))
        link_entity(t.id, "Cloud Hosting Inc", "supplier")
        counters["txn"] += 1

        # Pay off cloud hosting (except last 2 months)
        if months_ago > 2:
            t = Transaction(date=month_date + timedelta(days=28), description=f"Pay Cloud Hosting Inc - month {month_label}", reference=f"PMT-{month_label:03d}")
            db.add(t); db.flush()
            db.add(TransactionLine(transaction_id=t.id, account_id=payable.id, debit=cloud_amt, credit=0, line_description="Clear payable"))
            db.add(TransactionLine(transaction_id=t.id, account_id=cash.id, debit=0, credit=cloud_amt, line_description="Cash payment"))
            link_entity(t.id, "Cloud Hosting Inc", "supplier")
            counters["txn"] += 1

        # Bank fee
        if financial_exp:
            fee = 500_000
            t = Transaction(date=month_date + timedelta(days=29), description=f"Bank fees - month {month_label}", reference=f"FEE-{month_label:03d}")
            db.add(t); db.flush()
            db.add(TransactionLine(transaction_id=t.id, account_id=financial_exp.id, debit=fee, credit=0, line_description="Bank service charge"))
            db.add(TransactionLine(transaction_id=t.id, account_id=cash.id, debit=0, credit=fee, line_description="Fee deducted from account"))
            link_entity(t.id, "Mellat Bank", "bank")
            counters["txn"] += 1

    # Capital injection (one-time)
    if capital:
        t = Transaction(date=today - timedelta(days=200), description="Initial capital investment", reference="CAP-001")
        db.add(t); db.flush()
        db.add(TransactionLine(transaction_id=t.id, account_id=cash.id, debit=500_000_000, credit=0, line_description="Cash injected"))
        db.add(TransactionLine(transaction_id=t.id, account_id=capital.id, debit=0, credit=500_000_000, line_description="Owner's capital"))
        counters["txn"] += 1

    # Fixed asset purchase
    if fixed_assets:
        t = Transaction(date=today - timedelta(days=150), description="Purchased office equipment", reference="AST-001")
        db.add(t); db.flush()
        db.add(TransactionLine(transaction_id=t.id, account_id=fixed_assets.id, debit=25_000_000, credit=0, line_description="Office equipment"))
        db.add(TransactionLine(transaction_id=t.id, account_id=cash.id, debit=0, credit=25_000_000, line_description="Equipment payment"))
        link_entity(t.id, "Office Supplies Co", "supplier")
        counters["txn"] += 1

    # ===== INVOICES =====
    invoice_specs = [
        ("INV-2026-001", "sales", "issued", -15, 15, 75_000_000, "Consulting services Q1", "Innotech Solutions", [("Consulting – 40 hours", 40, 1_875_000, 75_000_000)]),
        ("INV-2026-002", "purchase", "issued", -10, 20, 8_000_000, "Cloud hosting Q1", "Cloud Hosting Inc", [("Cloud Server (3 mo)", 3, 2_666_667, 8_000_000)]),
        ("INV-2026-003", "sales", "paid", -45, -15, 50_000_000, "Software development", "DataFlow Corp", [("Frontend dev", 1, 30_000_000, 30_000_000), ("Backend dev", 1, 20_000_000, 20_000_000)]),
        ("INV-2026-004", "sales", "draft", -5, 25, 120_000_000, "Platform integration project", "Parsian Trading", [("Integration", 1, 80_000_000, 80_000_000), ("Testing & QA", 1, 40_000_000, 40_000_000)]),
        ("INV-2026-005", "purchase", "paid", -60, -30, 25_000_000, "Office equipment", "Office Supplies Co", [("Desk (x5)", 5, 3_000_000, 15_000_000), ("Chair (x5)", 5, 2_000_000, 10_000_000)]),
        ("INV-2026-006", "sales", "issued", -3, 27, 35_000_000, "Monthly support retainer", "Innotech Solutions", [("Support retainer – April", 1, 35_000_000, 35_000_000)]),
    ]
    for inv_spec in invoice_specs:
        number, kind, status, issue_offset, due_offset, amount, desc, entity_name, items = inv_spec
        existing = db.execute(select(Invoice).where(Invoice.number == number)).scalars().first()
        if existing:
            continue
        inv = Invoice(
            number=number, kind=kind, status=status,
            issue_date=today + timedelta(days=issue_offset),
            due_date=today + timedelta(days=due_offset),
            amount=amount, description=desc,
            entity_id=entity_map.get(entity_name, entity_map["Innotech Solutions"]).id,
        )
        db.add(inv); db.flush()
        for product, qty, price, total in items:
            db.add(InvoiceItem(invoice_id=inv.id, product_name=product, quantity=qty, unit_price=price, line_total=total))
        counters["inv"] += 1

    # ===== RECURRING RULES =====
    recurring_specs = [
        ("Monthly payroll", "payment", "monthly", 30_000_000, "Ali Rezaei", "PAY"),
        ("Cloud hosting", "payment", "monthly", 8_000_000, "Cloud Hosting Inc", "CLD"),
        ("Innotech retainer", "receipt", "monthly", 35_000_000, "Innotech Solutions", "RET"),
    ]
    for rname, direction, freq, amount, entity_name, prefix in recurring_specs:
        existing = db.execute(select(RecurringRule).where(RecurringRule.name == rname)).scalars().first()
        if existing:
            continue
        r = RecurringRule(
            name=rname, direction=direction, frequency=freq, amount=amount,
            start_date=today - timedelta(days=180),
            next_run_date=today + timedelta(days=1),
            entity_id=entity_map.get(entity_name, entity_map["Innotech Solutions"]).id,
            reference_prefix=prefix,
            note=f"Auto-generated recurring rule for {rname.lower()}",
        )
        db.add(r)
        counters["rec"] += 1

    # ===== BUDGET LIMITS =====
    current_month = today.strftime("%Y-%m")
    prev_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    next_month = (today.replace(day=28) + timedelta(days=4)).strftime("%Y-%m")
    budget_specs = [
        (current_month, "هزینه\u200cهای حقوق", 35_000_000),
        (current_month, "سایر هزینه\u200cهای عملیاتی", 15_000_000),
        (current_month, "هزینه\u200cهای مالی", 2_000_000),
        (next_month, "هزینه\u200cهای حقوق", 36_000_000),
        (next_month, "سایر هزینه\u200cهای عملیاتی", 14_000_000),
    ]
    for month, cat, limit_amt in budget_specs:
        existing = db.execute(select(BudgetLimit).where(BudgetLimit.month == month, BudgetLimit.category == cat)).scalars().first()
        if existing:
            continue
        db.add(BudgetLimit(month=month, category=cat, limit_amount=limit_amt))
        counters["bud"] += 1

    # ===== INVENTORY =====
    inv_items_specs = [
        ("A4 Paper (ream)", "SKU-001", "ream"),
        ("Printer Toner", "SKU-002", "unit"),
        ("USB Flash Drive 64GB", "SKU-003", "unit"),
        ("Desk Lamp", "SKU-004", "unit"),
        ("Whiteboard Marker Set", "SKU-005", "pack"),
    ]
    inv_item_map = {}
    for iname, sku, unit in inv_items_specs:
        existing = db.execute(select(InventoryItem).where(InventoryItem.sku == sku)).scalars().first()
        if existing:
            inv_item_map[sku] = existing
            continue
        item = InventoryItem(sku=sku, name=iname, unit=unit)
        db.add(item); db.flush()
        inv_item_map[sku] = item
        counters["item"] += 1

    # Inventory movements
    movements = [
        ("SKU-001", -120, "IN", 50, 150_000, "Initial stock"),
        ("SKU-001", -90, "OUT", 10, 0, "Used by office"),
        ("SKU-001", -60, "IN", 30, 160_000, "Restock from Pars Stationery"),
        ("SKU-001", -30, "OUT", 15, 0, "Used by office"),
        ("SKU-002", -120, "IN", 5, 2_500_000, "Initial toner stock"),
        ("SKU-002", -60, "OUT", 2, 0, "Replaced printer toners"),
        ("SKU-002", -10, "IN", 3, 2_700_000, "Reorder toner"),
        ("SKU-003", -100, "IN", 20, 350_000, "Bulk USB purchase"),
        ("SKU-003", -50, "OUT", 8, 0, "Distributed to employees"),
        ("SKU-004", -90, "IN", 10, 800_000, "Desk lamps for new office"),
        ("SKU-004", -80, "OUT", 5, 0, "Installed in workstations"),
        ("SKU-005", -110, "IN", 30, 120_000, "Marker sets"),
        ("SKU-005", -40, "OUT", 12, 0, "Meeting room supplies"),
    ]
    for sku, day_offset, mtype, qty, cost, desc in movements:
        item = inv_item_map.get(sku)
        if not item:
            continue
        db.add(InventoryMovement(
            item_id=item.id,
            movement_date=today + timedelta(days=day_offset),
            movement_type=mtype,
            quantity=qty,
            unit_cost=cost,
            reference=f"MOV-{sku}-{abs(day_offset)}",
            description=desc,
        ))

    # ===== BANK STATEMENT (CSV-like) =====
    bs = BankStatement(
        bank_name="Mellat Bank",
        source_type="csv",
        source_filename="mellat_demo_statement.csv",
        currency="IRR",
        from_date=today - timedelta(days=90),
        to_date=today,
        status="parsed",
        total_rows=0,
    )
    db.add(bs); db.flush()

    balance = 200_000_000
    bs_row_idx = 0
    for days_ago in range(90, 0, -3):
        d = today - timedelta(days=days_ago)
        # Alternate between deposits and withdrawals
        if days_ago % 6 == 0:
            amt = 15_000_000 + (days_ago % 10) * 1_000_000
            balance += amt
            db.add(BankStatementRow(
                statement_id=bs.id, row_index=bs_row_idx, tx_date=d,
                description=f"Transfer in from client", reference=f"TRF-{bs_row_idx:04d}",
                debit=0, credit=amt, balance=balance, counterparty="Innotech Solutions",
                confidence=0.9, category="revenue", suggested_account_code="4110",
            ))
        else:
            amt = 5_000_000 + (days_ago % 7) * 500_000
            balance -= amt
            db.add(BankStatementRow(
                statement_id=bs.id, row_index=bs_row_idx, tx_date=d,
                description=f"Payment - {'salary' if days_ago % 9 == 0 else 'operating expense'}",
                reference=f"PMT-{bs_row_idx:04d}",
                debit=amt, credit=0, balance=balance,
                counterparty="Various" if days_ago % 9 != 0 else "Ali Rezaei",
                confidence=0.85, category="expense",
                suggested_account_code="6110" if days_ago % 9 == 0 else "6112",
            ))
        bs_row_idx += 1

    bs.total_rows = bs_row_idx
    counters["bs_rows"] = bs_row_idx

    db.commit()

    msg_parts = []
    if counters["txn"]: msg_parts.append(f"{counters['txn']} transactions")
    if counters["inv"]: msg_parts.append(f"{counters['inv']} invoices")
    if counters["ent"]: msg_parts.append(f"{counters['ent']} entities")
    if counters["item"]: msg_parts.append(f"{counters['item']} inventory items")
    if counters["rec"]: msg_parts.append(f"{counters['rec']} recurring rules")
    if counters["bud"]: msg_parts.append(f"{counters['bud']} budget limits")
    if counters["bs_rows"]: msg_parts.append(f"{counters['bs_rows']} bank statement rows")

    return SeedDataResponse(
        transactions_created=counters["txn"],
        invoices_created=counters["inv"],
        entities_created=counters["ent"],
        inventory_items_created=counters["item"],
        recurring_rules_created=counters["rec"],
        budget_limits_created=counters["bud"],
        bank_statement_rows=counters["bs_rows"],
        message=f"Demo data seeded: {', '.join(msg_parts)}." if msg_parts else "No new data needed — demo data already present.",
    )


@router.post("/cfo/ask", response_model=CFOQuestionResponse)
def ask_cfo_question(
    payload: CFOQuestionRequest,
    db: Session = Depends(get_db),
) -> CFOQuestionResponse:
    """Ask a natural language financial question to the CFO AI."""
    from app.services.cfo_intelligence import answer_cfo_question, build_cfo_report
    answer = answer_cfo_question(db, payload.question)
    report = build_cfo_report(db)
    return CFOQuestionResponse(
        question=payload.question,
        answer=answer,
        health_grade=report.health_grade,
        risk_score=report.risk_score,
    )

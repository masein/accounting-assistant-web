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
    assets_breakdown: list[dict] = []
    liabilities_breakdown: list[dict] = []
    equity_breakdown: list[dict] = []
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
    Seed the database with 12 months of rich, diverse financial data
    covering ALL app sections: transactions, invoices, entities, inventory,
    recurring rules, budgets, and bank statements.
    """
    import random
    from datetime import timedelta
    from app.models.entity import Entity, TransactionEntity
    from app.models.invoice import Invoice
    from app.models.invoice_item import InvoiceItem
    from app.models.recurring import RecurringRule
    from app.models.budget import BudgetLimit
    from app.models.inventory import InventoryItem, InventoryMovement

    random.seed(42)  # deterministic for reproducibility
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

    # ===== ENTITIES (20+ diverse entities) =====
    entity_specs = [
        # Clients — diverse industries
        ("client", "Innotech Solutions", "CLI-001"),
        ("client", "DataFlow Corp", "CLI-002"),
        ("client", "Parsian Trading", "CLI-003"),
        ("client", "Golestan Food Industries", "CLI-004"),
        ("client", "Tehran Web Agency", "CLI-005"),
        ("client", "Sepahan Steel Co", "CLI-006"),
        ("client", "Novin Pharma", "CLI-007"),
        ("client", "Aria Construction", "CLI-008"),
        # Suppliers — various categories
        ("supplier", "Office Supplies Co", "SUP-001"),
        ("supplier", "Cloud Hosting Inc", "SUP-002"),
        ("supplier", "Pars Stationery", "SUP-003"),
        ("supplier", "Iran Server Co", "SUP-004"),
        ("supplier", "Kaveh Electronics", "SUP-005"),
        ("supplier", "Aban Logistics", "SUP-006"),
        ("supplier", "Sharif IT Services", "SUP-007"),
        # Employees
        ("employee", "Ali Rezaei", "EMP-001"),
        ("employee", "Sara Mohammadi", "EMP-002"),
        ("employee", "Reza Karimi", "EMP-003"),
        ("employee", "Maryam Hosseini", "EMP-004"),
        ("employee", "Hossein Ahmadi", "EMP-005"),
        ("employee", "Zahra Moradi", "EMP-006"),
        # Banks
        ("bank", "Mellat Bank", "BNK-001"),
        ("bank", "Saderat Bank", "BNK-002"),
        ("bank", "Tejarat Bank", "BNK-003"),
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

    def make_txn(d, desc, ref, lines, entities=None):
        t = Transaction(date=d, description=desc, reference=ref)
        db.add(t); db.flush()
        for acc, debit, credit, ldesc in lines:
            if acc:
                db.add(TransactionLine(transaction_id=t.id, account_id=acc.id, debit=debit, credit=credit, line_description=ldesc))
        for ename, role in (entities or []):
            link_entity(t.id, ename, role)
        counters["txn"] += 1
        return t

    # ===== TRANSACTIONS (12 months of rich diverse data) =====
    clients = ["Innotech Solutions", "DataFlow Corp", "Parsian Trading", "Golestan Food Industries",
               "Tehran Web Agency", "Sepahan Steel Co", "Novin Pharma", "Aria Construction"]
    suppliers = ["Office Supplies Co", "Cloud Hosting Inc", "Pars Stationery", "Iran Server Co",
                 "Kaveh Electronics", "Aban Logistics", "Sharif IT Services"]
    employees = ["Ali Rezaei", "Sara Mohammadi", "Reza Karimi", "Maryam Hosseini", "Hossein Ahmadi", "Zahra Moradi"]

    # Capital injection (one-time, 12 months ago)
    if capital:
        make_txn(today - timedelta(days=365), "Initial capital investment by founders", "CAP-001",
                 [(cash, 800_000_000, 0, "Cash injected by founder A"),
                  (capital, 0, 800_000_000, "Owner's capital")])

    # Additional capital (6 months ago)
    if capital:
        make_txn(today - timedelta(days=180), "Additional capital from partner B", "CAP-002",
                 [(cash, 300_000_000, 0, "Partner B investment"),
                  (capital, 0, 300_000_000, "Partner capital increase")])

    # Fixed assets across multiple dates
    if fixed_assets:
        asset_purchases = [
            (-330, 45_000_000, "Server rack and networking equipment", "AST-001", "Kaveh Electronics"),
            (-270, 25_000_000, "Office furniture (desks, chairs, shelves)", "AST-002", "Office Supplies Co"),
            (-180, 35_000_000, "Development workstations (x6)", "AST-003", "Kaveh Electronics"),
            (-90, 15_000_000, "Conference room AV equipment", "AST-004", "Kaveh Electronics"),
            (-30, 20_000_000, "Standing desks and ergonomic chairs", "AST-005", "Office Supplies Co"),
        ]
        for offset, amt, desc, ref, supplier in asset_purchases:
            make_txn(today + timedelta(days=offset), desc, ref,
                     [(fixed_assets, amt, 0, desc), (cash, 0, amt, "Payment for " + desc)],
                     [(supplier, "supplier")])

    # 12 months of diverse monthly transactions
    for months_ago in range(12, 0, -1):
        month_date = today - timedelta(days=months_ago * 30)
        m = 13 - months_ago  # month label 1-12
        # Seasonal variation: higher revenue in spring/autumn, lower in summer/winter
        seasonal_mult = 1.0
        month_num = month_date.month
        if month_num in (3, 4, 5, 9, 10, 11):
            seasonal_mult = 1.3
        elif month_num in (6, 7, 8):
            seasonal_mult = 0.8
        elif month_num in (12, 1, 2):
            seasonal_mult = 0.9

        # --- Revenue from multiple clients ---
        # Big client: Innotech Solutions (credit sales, consulting)
        inno_rev = int((45_000_000 + m * 3_000_000) * seasonal_mult + random.randint(-5_000_000, 5_000_000))
        make_txn(month_date + timedelta(days=2), f"Consulting services for Innotech Solutions - month {m}", f"INO-{m:03d}",
                 [(receivable, inno_rev, 0, "Receivable from Innotech"),
                  (sales, 0, inno_rev, "Consulting revenue")],
                 [("Innotech Solutions", "client")])

        # Collect Innotech receivable (with 30-45 day delay, skip last 2 months)
        if months_ago > 2:
            collect_date = month_date + timedelta(days=random.randint(28, 42))
            if collect_date < today:
                make_txn(collect_date, f"Collection from Innotech Solutions", f"COL-INO-{m:03d}",
                         [(cash, inno_rev, 0, "Cash received from Innotech"),
                          (receivable, 0, inno_rev, "Receivable cleared")],
                         [("Innotech Solutions", "client")])

        # DataFlow Corp (cash sales, software dev)
        df_rev = int((20_000_000 + m * 2_500_000) * seasonal_mult + random.randint(-3_000_000, 3_000_000))
        make_txn(month_date + timedelta(days=5), f"Software development fee from DataFlow Corp - month {m}", f"DF-{m:03d}",
                 [(cash, df_rev, 0, "Cash from DataFlow"),
                  (sales, 0, df_rev, "Software dev revenue")],
                 [("DataFlow Corp", "client")])

        # Parsian Trading (cash sales, product delivery)
        pars_rev = int((35_000_000 + random.randint(0, 15_000_000)) * seasonal_mult)
        make_txn(month_date + timedelta(days=8), f"Product delivery to Parsian Trading - month {m}", f"PRS-{m:03d}",
                 [(cash, pars_rev, 0, "Cash from Parsian Trading"),
                  (sales, 0, pars_rev, "Product sales")],
                 [("Parsian Trading", "client")])

        # Golestan Food (credit sales, quarterly — months 3, 6, 9, 12)
        if m % 3 == 0:
            gol_rev = int(80_000_000 * seasonal_mult + random.randint(-10_000_000, 10_000_000))
            make_txn(month_date + timedelta(days=10), f"Quarterly supply to Golestan Food Industries", f"GOL-Q{m//3}",
                     [(receivable, gol_rev, 0, "Receivable from Golestan"),
                      (sales, 0, gol_rev, "Quarterly supply revenue")],
                     [("Golestan Food Industries", "client")])
            if months_ago > 3:
                make_txn(month_date + timedelta(days=40), f"Collection from Golestan Food", f"COL-GOL-Q{m//3}",
                         [(cash, gol_rev, 0, "Cash from Golestan"),
                          (receivable, 0, gol_rev, "Golestan receivable cleared")],
                         [("Golestan Food Industries", "client")])

        # Tehran Web Agency (small monthly, cash)
        twa_rev = int(12_000_000 + random.randint(-2_000_000, 5_000_000))
        make_txn(month_date + timedelta(days=12), f"Website maintenance for Tehran Web Agency - month {m}", f"TWA-{m:03d}",
                 [(cash, twa_rev, 0, "Cash from Tehran Web Agency"),
                  (sales, 0, twa_rev, "Maintenance revenue")],
                 [("Tehran Web Agency", "client")])

        # Sepahan Steel (large occasional — months 2, 5, 8, 11)
        if m % 3 == 2:
            sep_rev = int(120_000_000 + random.randint(-20_000_000, 30_000_000))
            make_txn(month_date + timedelta(days=6), f"ERP module delivery to Sepahan Steel", f"SEP-{m:03d}",
                     [(receivable, sep_rev, 0, "Receivable from Sepahan Steel"),
                      (sales, 0, sep_rev, "ERP module revenue")],
                     [("Sepahan Steel Co", "client")])
            if months_ago > 2:
                make_txn(month_date + timedelta(days=35), f"Collection from Sepahan Steel", f"COL-SEP-{m:03d}",
                         [(cash, sep_rev, 0, "Cash from Sepahan"),
                          (receivable, 0, sep_rev, "Sepahan receivable cleared")],
                         [("Sepahan Steel Co", "client")])

        # Novin Pharma (credit sales, every other month)
        if m % 2 == 0:
            np_rev = int(28_000_000 + random.randint(-5_000_000, 8_000_000))
            make_txn(month_date + timedelta(days=15), f"Lab software license to Novin Pharma", f"NP-{m:03d}",
                     [(receivable, np_rev, 0, "Receivable from Novin Pharma"),
                      (sales, 0, np_rev, "License revenue")],
                     [("Novin Pharma", "client")])
            if months_ago > 2:
                make_txn(month_date + timedelta(days=45), f"Collection from Novin Pharma", f"COL-NP-{m:03d}",
                         [(cash, np_rev, 0, "Cash from Novin Pharma"),
                          (receivable, 0, np_rev, "Novin Pharma receivable cleared")],
                         [("Novin Pharma", "client")])

        # Aria Construction (bimonthly project milestones)
        if m % 2 == 1:
            aria_rev = int(55_000_000 + random.randint(-10_000_000, 15_000_000))
            make_txn(month_date + timedelta(days=18), f"Project milestone payment from Aria Construction", f"ARI-{m:03d}",
                     [(cash, aria_rev, 0, "Cash from Aria Construction"),
                      (sales, 0, aria_rev, "Project milestone revenue")],
                     [("Aria Construction", "client")])

        # --- Expenses ---
        # Payroll (multiple employees, growing team)
        base_payroll = 18_000_000 + m * 200_000
        for emp_idx, emp in enumerate(employees[:min(3 + m // 4, len(employees))]):
            emp_salary = base_payroll + emp_idx * 2_000_000 + random.randint(-500_000, 500_000)
            make_txn(month_date + timedelta(days=25), f"Salary payment to {emp} - month {m}", f"PAY-{emp_idx+1}-{m:03d}",
                     [(payroll, emp_salary, 0, f"Salary expense - {emp}"),
                      (cash, 0, emp_salary, f"Salary paid to {emp}")],
                     [(emp, "payee")])

        # Cloud hosting (increasing with growth)
        cloud_amt = 6_000_000 + m * 500_000
        make_txn(month_date + timedelta(days=3), f"Cloud hosting - {['Basic', 'Pro', 'Enterprise'][min(m//4, 2)]} plan - month {m}", f"CLD-{m:03d}",
                 [(operating, cloud_amt, 0, "Cloud hosting subscription"),
                  (payable, 0, cloud_amt, "Payable to Cloud Hosting Inc")],
                 [("Cloud Hosting Inc", "supplier")])
        # Pay cloud hosting (except last 2 months)
        if months_ago > 2:
            make_txn(month_date + timedelta(days=20), f"Pay Cloud Hosting Inc - month {m}", f"PMT-CLD-{m:03d}",
                     [(payable, cloud_amt, 0, "Clear cloud hosting payable"),
                      (cash, 0, cloud_amt, "Cloud hosting payment")],
                     [("Cloud Hosting Inc", "supplier")])

        # Office supplies (seasonal — higher in spring/autumn)
        office_amt = int((5_000_000 + random.randint(0, 5_000_000)) * seasonal_mult)
        make_txn(month_date + timedelta(days=10), f"Office supplies from Pars Stationery - month {m}", f"OFS-{m:03d}",
                 [(operating, office_amt, 0, "Office supplies"),
                  (cash, 0, office_amt, "Cash payment for supplies")],
                 [("Pars Stationery", "supplier")])

        # Server/IT infrastructure (via Iran Server Co, on payable)
        if m >= 3:
            server_amt = 4_000_000 + random.randint(0, 3_000_000)
            make_txn(month_date + timedelta(days=7), f"Dedicated server rental - month {m}", f"SRV-{m:03d}",
                     [(operating, server_amt, 0, "Server rental"),
                      (payable, 0, server_amt, "Payable to Iran Server Co")],
                     [("Iran Server Co", "supplier")])
            if months_ago > 2:
                make_txn(month_date + timedelta(days=22), f"Pay Iran Server Co - month {m}", f"PMT-SRV-{m:03d}",
                         [(payable, server_amt, 0, "Clear server payable"),
                          (cash, 0, server_amt, "Server payment")],
                         [("Iran Server Co", "supplier")])

        # Logistics/shipping (via Aban Logistics, occasional)
        if m % 2 == 0:
            log_amt = 3_000_000 + random.randint(0, 4_000_000)
            make_txn(month_date + timedelta(days=14), f"Shipping & logistics - month {m}", f"LOG-{m:03d}",
                     [(operating, log_amt, 0, "Logistics expense"),
                      (cash, 0, log_amt, "Logistics payment")],
                     [("Aban Logistics", "supplier")])

        # IT consulting/outsourcing (Sharif IT, quarterly)
        if m % 3 == 0:
            it_amt = 15_000_000 + random.randint(0, 10_000_000)
            make_txn(month_date + timedelta(days=16), f"IT consulting - Q{m//3}", f"ITC-Q{m//3}",
                     [(operating, it_amt, 0, "IT consulting services"),
                      (payable, 0, it_amt, "Payable to Sharif IT")],
                     [("Sharif IT Services", "supplier")])
            if months_ago > 3:
                make_txn(month_date + timedelta(days=40), f"Pay Sharif IT Services", f"PMT-ITC-Q{m//3}",
                         [(payable, it_amt, 0, "Clear IT consulting payable"),
                          (cash, 0, it_amt, "IT consulting payment")],
                         [("Sharif IT Services", "supplier")])

        # Electronics purchases (Kaveh Electronics, occasional)
        if m % 4 == 0:
            elec_amt = 8_000_000 + random.randint(0, 7_000_000)
            make_txn(month_date + timedelta(days=11), f"Electronics & peripherals purchase", f"ELC-{m:03d}",
                     [(operating, elec_amt, 0, "Electronics purchase"),
                      (cash, 0, elec_amt, "Electronics payment")],
                     [("Kaveh Electronics", "supplier")])

        # Bank fees (multiple banks)
        if financial_exp:
            for bank_name, fee in [("Mellat Bank", 400_000 + random.randint(0, 300_000)),
                                   ("Saderat Bank", 250_000 + random.randint(0, 200_000)),
                                   ("Tejarat Bank", 350_000 + random.randint(0, 250_000))]:
                make_txn(month_date + timedelta(days=28 + random.randint(0, 2)), f"Bank fees - {bank_name} - month {m}", f"FEE-{bank_name[:3].upper()}-{m:03d}",
                         [(financial_exp, fee, 0, f"Service charge - {bank_name}"),
                          (cash, 0, fee, f"Fee deducted by {bank_name}")],
                         [(bank_name, "bank")])

    # ===== INVOICES (20+ diverse invoices) =====
    invoice_specs = [
        # Sales invoices — diverse products and statuses
        ("INV-2025-001", "sales", "paid", -350, -320, 45_000_000, "Annual software license", "Innotech Solutions",
         [("Enterprise License (annual)", 1, 45_000_000, 45_000_000, 20_000_000)]),
        ("INV-2025-002", "sales", "paid", -300, -270, 85_000_000, "ERP customization project", "Sepahan Steel Co",
         [("ERP Module - Inventory", 1, 50_000_000, 50_000_000, 25_000_000), ("ERP Module - HR", 1, 35_000_000, 35_000_000, 18_000_000)]),
        ("INV-2025-003", "sales", "paid", -250, -220, 30_000_000, "Website redesign", "Tehran Web Agency",
         [("UI/UX Design", 80, 200_000, 16_000_000, 8_000_000), ("Frontend Development", 70, 200_000, 14_000_000, 7_000_000)]),
        ("INV-2025-004", "sales", "paid", -200, -170, 120_000_000, "Quarterly supply - Q3", "Golestan Food Industries",
         [("Packaging Software", 12, 5_000_000, 60_000_000, 30_000_000), ("Label Printer Integration", 4, 15_000_000, 60_000_000, 25_000_000)]),
        ("INV-2026-001", "sales", "paid", -150, -120, 75_000_000, "Consulting services Q4", "Innotech Solutions",
         [("Consulting – 40 hours", 40, 1_875_000, 75_000_000, 35_000_000)]),
        ("INV-2026-002", "sales", "issued", -90, -60, 95_000_000, "Lab management system", "Novin Pharma",
         [("Lab LIMS Module", 1, 60_000_000, 60_000_000, 30_000_000), ("Training (5 days)", 5, 7_000_000, 35_000_000, 10_000_000)]),
        ("INV-2026-003", "sales", "paid", -60, -30, 50_000_000, "Software development sprint", "DataFlow Corp",
         [("Frontend dev (React)", 1, 30_000_000, 30_000_000, 15_000_000), ("Backend dev (Python)", 1, 20_000_000, 20_000_000, 10_000_000)]),
        ("INV-2026-004", "sales", "issued", -30, 0, 65_000_000, "Construction project tracker", "Aria Construction",
         [("Project Management Module", 1, 40_000_000, 40_000_000, 20_000_000), ("Mobile App", 1, 25_000_000, 25_000_000, 12_000_000)]),
        ("INV-2026-005", "sales", "draft", -15, 15, 120_000_000, "Platform integration project", "Parsian Trading",
         [("API Integration", 1, 50_000_000, 50_000_000, 25_000_000), ("Data Migration", 1, 30_000_000, 30_000_000, 15_000_000), ("Testing & QA", 200, 200_000, 40_000_000, 20_000_000)]),
        ("INV-2026-006", "sales", "issued", -5, 25, 35_000_000, "Monthly support retainer - April", "Innotech Solutions",
         [("Support retainer – April", 1, 35_000_000, 35_000_000, 10_000_000)]),
        ("INV-2026-007", "sales", "draft", -2, 28, 180_000_000, "Annual ERP renewal + expansion", "Sepahan Steel Co",
         [("ERP License Renewal", 1, 80_000_000, 80_000_000, 30_000_000), ("New Finance Module", 1, 60_000_000, 60_000_000, 35_000_000), ("On-site Training (10 days)", 10, 4_000_000, 40_000_000, 15_000_000)]),
        # Purchase invoices — diverse suppliers
        ("PUR-2025-001", "purchase", "paid", -320, -290, 45_000_000, "Server hardware", "Kaveh Electronics",
         [("Rack Server", 2, 18_000_000, 36_000_000, 0), ("UPS System", 1, 9_000_000, 9_000_000, 0)]),
        ("PUR-2025-002", "purchase", "paid", -270, -240, 25_000_000, "Office furniture", "Office Supplies Co",
         [("Standing Desk", 5, 3_000_000, 15_000_000, 0), ("Ergonomic Chair", 5, 2_000_000, 10_000_000, 0)]),
        ("PUR-2026-001", "purchase", "paid", -180, -150, 12_000_000, "Cloud hosting (6 months)", "Cloud Hosting Inc",
         [("Cloud Server Pro (6 mo)", 6, 2_000_000, 12_000_000, 0)]),
        ("PUR-2026-002", "purchase", "issued", -90, -60, 18_000_000, "IT consulting (Q1)", "Sharif IT Services",
         [("Security Audit", 1, 10_000_000, 10_000_000, 0), ("Code Review", 40, 200_000, 8_000_000, 0)]),
        ("PUR-2026-003", "purchase", "issued", -45, -15, 8_500_000, "Office supplies bulk", "Pars Stationery",
         [("A4 Paper (500 reams)", 500, 7_000, 3_500_000, 0), ("Printer Toner", 5, 1_000_000, 5_000_000, 0)]),
        ("PUR-2026-004", "purchase", "draft", -10, 20, 35_000_000, "Network equipment upgrade", "Kaveh Electronics",
         [("Managed Switch 48-port", 3, 8_000_000, 24_000_000, 0), ("WiFi 6 Access Point", 5, 2_200_000, 11_000_000, 0)]),
        ("PUR-2026-005", "purchase", "issued", -5, 25, 6_000_000, "Shipping & packaging materials", "Aban Logistics",
         [("Shipping Labels (10000)", 10000, 200, 2_000_000, 0), ("Packaging Boxes (500)", 500, 8_000, 4_000_000, 0)]),
        ("PUR-2026-006", "purchase", "issued", -3, 27, 15_000_000, "Dedicated servers (Q2)", "Iran Server Co",
         [("Dedicated Server (3 mo)", 3, 5_000_000, 15_000_000, 0)]),
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
        for item_data in items:
            product, qty, price, total, unit_cost = item_data
            db.add(InvoiceItem(invoice_id=inv.id, product_name=product, quantity=qty, unit_price=price,
                               unit_cost=unit_cost if unit_cost else None, line_total=total))
        counters["inv"] += 1

    # ===== RECURRING RULES =====
    recurring_specs = [
        ("Monthly payroll - Ali Rezaei", "payment", "monthly", 20_000_000, "Ali Rezaei", "PAY-AR"),
        ("Monthly payroll - Sara Mohammadi", "payment", "monthly", 22_000_000, "Sara Mohammadi", "PAY-SM"),
        ("Monthly payroll - Reza Karimi", "payment", "monthly", 18_000_000, "Reza Karimi", "PAY-RK"),
        ("Cloud hosting subscription", "payment", "monthly", 8_000_000, "Cloud Hosting Inc", "CLD"),
        ("Server rental", "payment", "monthly", 5_000_000, "Iran Server Co", "SRV"),
        ("Innotech retainer", "receipt", "monthly", 35_000_000, "Innotech Solutions", "RET"),
        ("Tehran Web Agency maintenance", "receipt", "monthly", 12_000_000, "Tehran Web Agency", "TWA"),
    ]
    for rname, direction, freq, amount, entity_name, prefix in recurring_specs:
        existing = db.execute(select(RecurringRule).where(RecurringRule.name == rname)).scalars().first()
        if existing:
            continue
        r = RecurringRule(
            name=rname, direction=direction, frequency=freq, amount=amount,
            start_date=today - timedelta(days=365),
            next_run_date=today + timedelta(days=1),
            entity_id=entity_map.get(entity_name, entity_map["Innotech Solutions"]).id,
            reference_prefix=prefix,
            note=f"Auto-generated recurring rule for {rname.lower()}",
        )
        db.add(r)
        counters["rec"] += 1

    # ===== BUDGET LIMITS (3 months) =====
    for month_offset in [-1, 0, 1]:
        d = today.replace(day=1) + timedelta(days=32 * month_offset)
        month_str = d.strftime("%Y-%m")
        budget_specs_local = [
            (month_str, "هزینه\u200cهای حقوق", 120_000_000 + month_offset * 2_000_000),
            (month_str, "سایر هزینه\u200cهای عملیاتی", 25_000_000 + month_offset * 1_000_000),
            (month_str, "هزینه\u200cهای مالی", 3_000_000),
        ]
        for month, cat, limit_amt in budget_specs_local:
            existing = db.execute(select(BudgetLimit).where(BudgetLimit.month == month, BudgetLimit.category == cat)).scalars().first()
            if existing:
                continue
            db.add(BudgetLimit(month=month, category=cat, limit_amount=limit_amt))
            counters["bud"] += 1

    # ===== INVENTORY (12 diverse items with list prices) =====
    inv_items_specs = [
        ("A4 Paper (ream)", "SKU-001", "ream", 180_000),
        ("Printer Toner - Black", "SKU-002", "unit", 3_200_000),
        ("Printer Toner - Color", "SKU-003", "unit", 4_500_000),
        ("USB Flash Drive 64GB", "SKU-004", "unit", 450_000),
        ("USB Flash Drive 128GB", "SKU-005", "unit", 750_000),
        ("Desk Lamp LED", "SKU-006", "unit", 950_000),
        ("Whiteboard Marker Set (12pc)", "SKU-007", "pack", 150_000),
        ("External SSD 1TB", "SKU-008", "unit", 4_800_000),
        ("Wireless Mouse", "SKU-009", "unit", 650_000),
        ("Mechanical Keyboard", "SKU-010", "unit", 2_800_000),
        ("Webcam HD 1080p", "SKU-011", "unit", 1_500_000),
        ("Ethernet Cable Cat6 (3m)", "SKU-012", "unit", 120_000),
    ]
    inv_item_map = {}
    for iname, sku, unit, price in inv_items_specs:
        existing = db.execute(select(InventoryItem).where(InventoryItem.sku == sku)).scalars().first()
        if existing:
            inv_item_map[sku] = existing
            continue
        item = InventoryItem(sku=sku, name=iname, unit=unit, list_price=price)
        db.add(item); db.flush()
        inv_item_map[sku] = item
        counters["item"] += 1

    # Inventory movements (richer: multiple IN/OUT across months)
    movements = [
        # A4 Paper — high volume
        ("SKU-001", -330, "IN", 200, 150_000, "Initial bulk stock from Pars Stationery"),
        ("SKU-001", -300, "OUT", 40, 0, "Used by office - Q1"),
        ("SKU-001", -270, "OUT", 35, 0, "Used by office - Q1"),
        ("SKU-001", -240, "IN", 100, 155_000, "Restock"),
        ("SKU-001", -210, "OUT", 50, 0, "Monthly consumption"),
        ("SKU-001", -180, "OUT", 45, 0, "Monthly consumption"),
        ("SKU-001", -150, "IN", 150, 160_000, "Bulk restock from Pars Stationery"),
        ("SKU-001", -120, "OUT", 55, 0, "Monthly consumption"),
        ("SKU-001", -90, "OUT", 40, 0, "Monthly consumption"),
        ("SKU-001", -60, "IN", 80, 165_000, "Restock"),
        ("SKU-001", -30, "OUT", 50, 0, "Monthly consumption"),
        # Toner — moderate
        ("SKU-002", -300, "IN", 10, 2_800_000, "Initial toner stock"),
        ("SKU-002", -240, "OUT", 3, 0, "Replaced office printer toners"),
        ("SKU-002", -180, "OUT", 2, 0, "Replaced printer toners"),
        ("SKU-002", -120, "IN", 8, 3_000_000, "Reorder from supplier"),
        ("SKU-002", -60, "OUT", 3, 0, "Quarterly replacement"),
        ("SKU-003", -280, "IN", 6, 4_000_000, "Color toner initial"),
        ("SKU-003", -200, "OUT", 2, 0, "Color printer replacement"),
        ("SKU-003", -100, "OUT", 1, 0, "Color printer replacement"),
        # USB drives
        ("SKU-004", -300, "IN", 30, 350_000, "Bulk USB purchase"),
        ("SKU-004", -250, "OUT", 10, 0, "Distributed to employees"),
        ("SKU-004", -150, "OUT", 8, 0, "Client deliverables"),
        ("SKU-004", -60, "IN", 20, 380_000, "Restock"),
        ("SKU-005", -200, "IN", 15, 650_000, "128GB drives for dev team"),
        ("SKU-005", -120, "OUT", 6, 0, "Dev team distribution"),
        # Desk lamps
        ("SKU-006", -270, "IN", 15, 800_000, "Desk lamps for new office"),
        ("SKU-006", -260, "OUT", 8, 0, "Installed in workstations"),
        ("SKU-006", -90, "OUT", 3, 0, "New hires setup"),
        # Whiteboard markers
        ("SKU-007", -330, "IN", 50, 100_000, "Marker sets bulk"),
        ("SKU-007", -270, "OUT", 15, 0, "Meeting room supplies"),
        ("SKU-007", -180, "OUT", 12, 0, "Meeting room supplies"),
        ("SKU-007", -90, "IN", 30, 120_000, "Restock"),
        ("SKU-007", -30, "OUT", 10, 0, "Office consumption"),
        # External SSDs
        ("SKU-008", -200, "IN", 5, 4_200_000, "SSDs for backup"),
        ("SKU-008", -150, "OUT", 2, 0, "Issued to devs"),
        ("SKU-008", -60, "IN", 3, 4_500_000, "Additional SSDs"),
        # Peripherals
        ("SKU-009", -250, "IN", 12, 550_000, "Wireless mice"),
        ("SKU-009", -200, "OUT", 6, 0, "New hire setup"),
        ("SKU-009", -100, "OUT", 3, 0, "Replacements"),
        ("SKU-010", -250, "IN", 8, 2_500_000, "Mechanical keyboards for devs"),
        ("SKU-010", -240, "OUT", 6, 0, "Dev team distribution"),
        ("SKU-010", -60, "IN", 4, 2_600_000, "Additional keyboards"),
        ("SKU-011", -180, "IN", 10, 1_300_000, "Webcams for remote meetings"),
        ("SKU-011", -170, "OUT", 6, 0, "Distributed to team"),
        ("SKU-011", -30, "OUT", 2, 0, "New hire setup"),
        # Cables
        ("SKU-012", -330, "IN", 50, 90_000, "Ethernet cables bulk"),
        ("SKU-012", -270, "OUT", 20, 0, "Office wiring"),
        ("SKU-012", -120, "OUT", 10, 0, "Server room"),
        ("SKU-012", -60, "IN", 30, 100_000, "Restock"),
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

    # ===== BANK STATEMENTS (2 banks, richer data) =====
    bank_configs = [
        ("Mellat Bank", "mellat_demo_statement.csv", 300_000_000, [
            ("Innotech Solutions", "DataFlow Corp", "Parsian Trading", "Golestan Food Industries"),
            ("Ali Rezaei", "Cloud Hosting Inc", "Pars Stationery", "Iran Server Co"),
        ]),
        ("Saderat Bank", "saderat_demo_statement.csv", 150_000_000, [
            ("Tehran Web Agency", "Sepahan Steel Co", "Novin Pharma", "Aria Construction"),
            ("Sara Mohammadi", "Kaveh Electronics", "Aban Logistics", "Sharif IT Services"),
        ]),
    ]
    for bank_name, filename, start_balance, (deposit_parties, withdrawal_parties) in bank_configs:
        bs = BankStatement(
            bank_name=bank_name,
            source_type="csv",
            source_filename=filename,
            currency="IRR",
            from_date=today - timedelta(days=180),
            to_date=today,
            status="parsed",
            total_rows=0,
        )
        db.add(bs); db.flush()

        balance = start_balance
        bs_row_idx = 0
        for days_ago in range(180, 0, -2):
            d = today - timedelta(days=days_ago)
            if days_ago % 5 == 0:
                # Deposit
                party = deposit_parties[bs_row_idx % len(deposit_parties)]
                amt = random.randint(10_000_000, 60_000_000)
                balance += amt
                desc_options = [f"Transfer from {party}", f"Invoice payment - {party}", f"Wire transfer - {party}"]
                db.add(BankStatementRow(
                    statement_id=bs.id, row_index=bs_row_idx, tx_date=d,
                    description=desc_options[bs_row_idx % len(desc_options)],
                    reference=f"TRF-{bank_name[:3].upper()}-{bs_row_idx:04d}",
                    debit=0, credit=amt, balance=balance, counterparty=party,
                    confidence=random.uniform(0.82, 0.98), category="revenue", suggested_account_code="4110",
                ))
            else:
                # Withdrawal
                party = withdrawal_parties[bs_row_idx % len(withdrawal_parties)]
                amt = random.randint(3_000_000, 25_000_000)
                balance -= amt
                cats = ["salary", "operating expense", "vendor payment", "utilities", "rent"]
                cat = cats[bs_row_idx % len(cats)]
                acct_map = {"salary": "6110", "operating expense": "6112", "vendor payment": "2110",
                            "utilities": "6112", "rent": "6112"}
                db.add(BankStatementRow(
                    statement_id=bs.id, row_index=bs_row_idx, tx_date=d,
                    description=f"Payment - {cat} - {party}",
                    reference=f"PMT-{bank_name[:3].upper()}-{bs_row_idx:04d}",
                    debit=amt, credit=0, balance=balance, counterparty=party,
                    confidence=random.uniform(0.78, 0.95), category="expense",
                    suggested_account_code=acct_map.get(cat, "6112"),
                ))
            bs_row_idx += 1

        bs.total_rows = bs_row_idx
        counters["bs_rows"] += bs_row_idx

    # ===== LIABILITY THRESHOLD SETTING =====
    from app.models.app_setting import AppSetting
    existing_threshold = db.execute(select(AppSetting).where(AppSetting.key == "liability_threshold")).scalars().first()
    if not existing_threshold:
        db.add(AppSetting(key="liability_threshold", value="500000000"))

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

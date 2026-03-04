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


class AuditReportResponse(BaseModel):
    integrity_score: int
    health_score: int
    findings: list[AuditFindingRead]
    checks_passed: int
    checks_failed: int
    total_transactions: int


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

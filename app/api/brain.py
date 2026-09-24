"""
API router for the AI Financial Brain: bank statement ingestion,
reconciliation, self-auditing, and CFO intelligence.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import SessionUser, get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.account import Account
from app.models.audit_log import AuditLog, IntegrityCheck, TransactionVersion
from app.models.bank_statement import BankStatement, BankStatementRow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/brain", tags=["financial-brain"])

MAX_STATEMENT_UPLOAD_BYTES = 20 * 1024 * 1024  # the only upload route that had no cap (review M7)


# Models live in app/schemas/brain; imported here so the handlers below and
# the modules/tests that import them from this router keep working.
from app.schemas.brain import (  # noqa: E402
    StatementReviewResponse,
    AuditFindingRead,
    AuditLogRead,
    AuditReportResponse,
    BankStatementRead,
    BankStatementRowRead,
    BankStatementUploadResponse,
    BatchApprovalRequest,
    BatchApprovalResponse,
    CEOReportResponse,
    CFOInsightRead,
    CFOKpiRead,
    CFOQuestionRequest,
    CFOQuestionResponse,
    CFOReportResponse,
    FeeSuggestion,
    ReconcileResponse,
    RowApproval,
    SeedDataResponse,
    SettingPayload,
    TransactionVersionRead,
)


def _preferred_report_language(db: Session, current: SessionUser) -> str:
    """The user's UI language, used to localize the prose (narrative,
    insights, Q&A answers, CEO alerts) of the CFO/CEO reports."""
    from app.models.user import User
    from app.services.cfo_intelligence import SUPPORTED_REPORT_LANGUAGES

    try:
        row = db.get(User, current.user_id)
        lang = (row.preferred_language or "en") if row else "en"
    except Exception:
        lang = "en"
    lang = lang.strip().lower()
    return lang if lang in SUPPORTED_REPORT_LANGUAGES else "en"


# ─── Schemas ────────────────────────────────────────────────────────





































# ─── Bank Statement Endpoints ──────────────────────────────────────

@router.get("/ocr-health")
def ocr_health() -> dict:
    """Report whether the vision-OCR engine (PyMuPDF) is installed so the UI
    can warn that PDF/image scanning is unavailable until the image is
    rebuilt. ``model`` is the configured OCR model for reference."""
    from app.core.config import settings
    from app.services.ocr_extract import ocr_engine_available

    return {
        "ocr_available": ocr_engine_available(),
        "model": settings.ocr_model,
        "fallback_model": settings.ocr_fallback_model,
    }


@router.post("/bank-statements/upload", response_model=BankStatementUploadResponse)
async def upload_bank_statement(
    file: UploadFile = File(...),
    bank_name: str = Query("Unknown"),
    column_map: str | None = Query(
        None,
        description='JSON object mapping roles to 0-based column indexes, e.g. {"date":0,"amount":2,"description":1}. Supplied on re-upload after a needs_mapping response.',
    ),
    confirm_duplicate: bool = Query(
        False, description="Import even if an identical file was already uploaded."
    ),
    db: Session = Depends(get_db),
) -> BankStatementUploadResponse:
    """Upload a CSV, Excel, or image/PDF bank statement for parsing and reconciliation."""
    from app.services.statement_import import import_statement_bytes

    content = await file.read()
    if len(content) > MAX_STATEMENT_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Statement file too large (max 20 MB).")
    return await import_statement_bytes(
        db,
        content=content,
        filename=file.filename or "unknown",
        content_type=file.content_type,
        bank_name=bank_name,
        column_map=column_map,
        confirm_duplicate=confirm_duplicate,
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
    from app.services.statement_import import reconcile_statement_rows

    return reconcile_statement_rows(db, s)


@router.post("/bank-statements/{statement_id}/review", response_model=StatementReviewResponse)
def review_statement(statement_id: UUID, db: Session = Depends(get_db)) -> StatementReviewResponse:
    """Check a statement against the books and list every contradiction with
    a suggested fix: bank rows with no entry, entries the bank never saw,
    amount / date mismatches, rows already imported, and the gap between the
    statement's closing balance and the book balance. Reconciles first so
    the findings reflect the current ledger."""
    s = db.get(BankStatement, statement_id)
    if not s:
        raise HTTPException(status_code=404, detail="Statement not found")
    from app.services.statement_review import build_statement_review

    return build_statement_review(db, s)


# Kept importable from this module for callers/tests that used to reach the
# helpers here.
from app.services.statement_import import account_name as _account_name  # noqa: E402,F401
from app.services.statement_import import classify_fee_row as _classify_fee_row  # noqa: E402,F401


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
            # Refuse to post a row already recognised as previously imported.
            # The UI doesn't offer Create on these, but double-posting money is
            # bad enough to guard at the API too; a genuine one-off can still be
            # keyed as a normal voucher.
            if row.recon_status == "duplicate":
                errors.append(
                    f"Row {row.row_index}: already imported previously — not posted again"
                )
                continue

            # Post through the canonical builder so a statement row gets the
            # same guards as a hand-keyed voucher: balanced legs, no future
            # date, no posting into a closed period, and the statement's own
            # currency (the old inline builder skipped all four).
            from app.services.ledger_posting import create_transaction_from_payload as _create_transaction_from_payload
            from app.schemas.transaction import TransactionCreate, TransactionLineCreate
            from app.services.account_resolver import AccountResolutionError, resolve_account_code

            try:
                # Counter leg: the user's choice, else the import's guess, else
                # the locale's generic expense account (never a hardcoded code —
                # 6190 is Iran-only and 404s on a UK chart).
                acc_code = (
                    approval.account_code
                    or row.suggested_account_code
                    or resolve_account_code(db, "expense")
                )
                # The statement's own bank account, not the generic chart
                # bank — the chat card already posts there (QA 2026-09-24).
                from app.services.statement_import import bank_account_for_statement
                cash_code = bank_account_for_statement(db, s) or resolve_account_code(db, "bank")
            except AccountResolutionError as e:
                errors.append(f"Row {row.row_index}: {e}")
                continue

            amount = row.debit if row.debit > 0 else row.credit
            # debit on the statement = money leaving the bank.
            legs = (
                [(acc_code, amount, 0), (cash_code, 0, amount)]
                if row.debit > 0
                else [(cash_code, amount, 0), (acc_code, 0, amount)]
            )
            payload = TransactionCreate(
                date=row.tx_date,
                reference=row.reference,
                description=row.description or f"Bank statement row #{row.row_index}",
                currency=s.currency or "IRR",
                lines=[
                    TransactionLineCreate(account_code=code, debit=dr, credit=cr)
                    for code, dr, cr in legs
                ],
            )
            try:
                txn = _create_transaction_from_payload(db, payload)
            except HTTPException as e:
                # One unpostable row must not sink the whole batch.
                errors.append(f"Row {row.row_index}: {e.detail}")
                continue

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
        actor_role=getattr(l, "actor_role", None),
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
def get_cfo_report(
    currency: str | None = Query(None, description="Filter by currency (IRR, USD, etc.)"),
    db: Session = Depends(get_db),
    current: SessionUser = Depends(get_current_user),
) -> CFOReportResponse:
    """Get the CFO-level financial intelligence report."""
    from app.services.cfo_intelligence import build_cfo_report, _resolve_currency_unit
    report = build_cfo_report(db, currency=currency, lang=_preferred_report_language(db, current))
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
        currency=_resolve_currency_unit(db, currency),
    )


@router.get("/ceo/report", response_model=CEOReportResponse)
def get_ceo_report(
    currency: str | None = Query(None, description="Filter by currency (IRR, USD, etc.)"),
    db: Session = Depends(get_db),
    current: SessionUser = Depends(get_current_user),
) -> CEOReportResponse:
    """Get the CEO-level executive summary report."""
    from app.services.cfo_intelligence import build_ceo_report, _resolve_currency_unit
    report = build_ceo_report(db, currency=currency, lang=_preferred_report_language(db, current))
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
        currency=_resolve_currency_unit(db, currency),
    )




@router.post("/cfo/seed-sample-data", response_model=SeedDataResponse)
def seed_sample_financial_data(db: Session = Depends(get_db)) -> SeedDataResponse:
    """Seed twelve months of demo data. Idempotent — see the service."""
    from app.services.demo_data import seed_sample_financial_data as _seed

    return SeedDataResponse(**_seed(db))


@router.post("/cfo/ask", response_model=CFOQuestionResponse)
def ask_cfo_question(
    payload: CFOQuestionRequest,
    db: Session = Depends(get_db),
    current: SessionUser = Depends(get_current_user),
) -> CFOQuestionResponse:
    """Ask a natural language financial question to the CFO AI."""
    from app.services.cfo_intelligence import answer_cfo_question, build_cfo_report
    lang = _preferred_report_language(db, current)
    answer = answer_cfo_question(db, payload.question, lang=lang)
    report = build_cfo_report(db, lang=lang)
    return CFOQuestionResponse(
        question=payload.question,
        answer=answer,
        health_grade=report.health_grade,
        risk_score=report.risk_score,
    )

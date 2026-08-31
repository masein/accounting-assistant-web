"""Petty cash (حساب تنخواه) REST API — per-user imprest accounts.

User panel (Perm.PETTY_OWN): see own account (balance + full history), record
expenses with a receipt attachment — they start ``pending``.
Admin panel (Perm.PETTY_MANAGE — Owner/CFO/Accountant): create accounts,
charge (deposit) and adjust them, approve/reject expenses, see everything.

GL postings happen at decision time through the same balanced-entry path as
every other write (period lock + audit apply):

    deposit:          DR petty_cash        / CR bank
    expense approved: DR expense category  / CR petty_cash
    adjustment:       signed move between petty_cash and a counter account

Subledger balance = Σ signed_amount of APPROVED rows.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import SessionUser, get_current_user
from app.core.permissions import Perm, role_can
from app.db.session import get_db
from app.models.petty_cash import PettyCashAccount, PettyCashTransaction
from app.models.transaction import TransactionAttachment
from app.models.user import User
from app.services.account_resolver import resolve_account_code
from app.services.audit_service import log_audit_event

router = APIRouter(prefix="/petty-cash", tags=["petty-cash"])

MAX_AMOUNT = 100_000_000_000_000


class AccountCreate(BaseModel):
    username: str | None = None       # resolve holder by login
    user_id: str | None = None        # …or directly by id
    holder_name: str | None = None    # display name (defaults to username)


class ExpenseCreate(BaseModel):
    amount: int = Field(..., gt=0, le=MAX_AMOUNT)
    description: str = Field(..., min_length=1, max_length=512)
    category_account_code: str | None = Field(None, max_length=16)
    attachment_id: uuid.UUID | None = None
    expense_date: date | None = None


class DepositCreate(BaseModel):
    amount: int = Field(..., gt=0, le=MAX_AMOUNT)
    bank_account_code: str = Field(..., max_length=16)
    description: str = Field("", max_length=512)


class AdjustCreate(BaseModel):
    signed_amount: int = Field(..., description="+ adds to the float, − takes from it")
    counter_account_code: str = Field(..., max_length=16)
    description: str = Field(..., min_length=1, max_length=512)


def _is_manager(user: SessionUser) -> bool:
    return role_can(user.role, Perm.PETTY_MANAGE)


def _balance(db: Session, account_id: uuid.UUID) -> int:
    total = db.execute(
        select(func.coalesce(func.sum(PettyCashTransaction.signed_amount), 0)).where(
            PettyCashTransaction.account_id == account_id,
            PettyCashTransaction.status == "approved",
        )
    ).scalar()
    return int(total or 0)


def _txn_read(t: PettyCashTransaction) -> dict:
    return {
        "id": str(t.id), "kind": t.kind, "amount": t.amount,
        "signed_amount": t.signed_amount, "description": t.description,
        "counter_account_code": t.counter_account_code,
        "attachment_id": str(t.attachment_id) if t.attachment_id else None,
        "status": t.status,
        "transaction_id": str(t.transaction_id) if t.transaction_id else None,
        "created_by": t.created_by, "decided_by": t.decided_by,
        "decided_at": t.decided_at.isoformat() if t.decided_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _account_read(db: Session, acc: PettyCashAccount, *, with_txns: bool = False) -> dict:
    out = {
        "id": str(acc.id), "user_id": acc.user_id, "holder_name": acc.holder_name,
        "status": acc.status, "balance": _balance(db, acc.id),
        "pending_expenses": len([t for t in acc.transactions
                                 if t.status == "pending" and t.kind == "expense"]),
        "created_at": acc.created_at.isoformat() if acc.created_at else None,
    }
    if with_txns:
        out["transactions"] = [_txn_read(t) for t in reversed(acc.transactions)]
    return out


def _get_account(db: Session, account_id: uuid.UUID, user: SessionUser,
                 *, manage: bool = False) -> PettyCashAccount:
    acc = db.get(PettyCashAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="Petty cash account not found")
    if manage and not _is_manager(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    if not manage and not _is_manager(user) and acc.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="Not your petty cash account")
    return acc


def _post_gl(db: Session, *, debit_code: str, credit_code: str, amount: int,
             description: str, entry_date: date | None = None):
    from app.services.ledger_posting import create_transaction_from_payload as _create_transaction_from_payload
    from app.schemas.transaction import TransactionCreate

    payload = TransactionCreate(
        date=entry_date or date.today(),
        reference=None,
        description=description[:2000],
        currency="IRR",
        lines=[
            {"account_code": debit_code, "debit": amount, "credit": 0,
             "line_description": description[:512]},
            {"account_code": credit_code, "debit": 0, "credit": amount,
             "line_description": description[:512]},
        ],
    )
    return _create_transaction_from_payload(db, payload)


@router.get("/accounts")
def list_accounts(
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> list[dict]:
    """Admins see every account; everyone else sees their own."""
    q = select(PettyCashAccount).order_by(PettyCashAccount.created_at)
    if not _is_manager(user):
        q = q.where(PettyCashAccount.user_id == user.user_id)
    rows = db.execute(q).scalars().all()
    return [_account_read(db, a) for a in rows]


@router.post("/accounts", status_code=201)
def create_account(
    payload: AccountCreate,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> dict:
    if not _is_manager(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    target_id, holder = payload.user_id, (payload.holder_name or "").strip()
    if not target_id and payload.username:
        row = db.execute(
            select(User).where(func.lower(User.username) == payload.username.strip().lower())
        ).scalars().first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"User not found: {payload.username}")
        target_id = str(row.id)
        holder = holder or row.username
    if not target_id:
        raise HTTPException(status_code=400, detail="Give username or user_id for the holder")
    existing = db.execute(
        select(PettyCashAccount).where(
            PettyCashAccount.user_id == target_id,
            PettyCashAccount.status == "active",
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=400, detail="This user already has an active petty cash account")
    acc = PettyCashAccount(user_id=target_id, holder_name=holder or target_id[:8])
    db.add(acc)
    db.commit()
    db.refresh(acc)
    log_audit_event(db, "create", "petty_cash_account", entity_id=str(acc.id),
                    detail=f"holder={acc.holder_name}")
    db.commit()
    return _account_read(db, acc)


@router.get("/accounts/{account_id}")
def account_detail(
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> dict:
    acc = _get_account(db, account_id, user)
    return _account_read(db, acc, with_txns=True)


@router.post("/accounts/{account_id}/deposit")
def deposit(
    account_id: uuid.UUID,
    payload: DepositCreate,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> dict:
    """Admin charges the float: DR petty_cash / CR bank — approved immediately."""
    acc = _get_account(db, account_id, user, manage=True)
    petty_code = resolve_account_code(db, "petty_cash")
    desc = payload.description or f"Petty cash deposit — {acc.holder_name}"
    txn = _post_gl(db, debit_code=petty_code, credit_code=payload.bank_account_code.strip(),
                   amount=payload.amount, description=desc)
    row = PettyCashTransaction(
        account_id=acc.id, kind="deposit", amount=payload.amount,
        signed_amount=payload.amount, description=desc,
        counter_account_code=payload.bank_account_code.strip(),
        status="approved", transaction_id=txn.id,
        created_by=user.user_id, decided_by=user.user_id,
        decided_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    log_audit_event(db, "petty_cash_deposit", "petty_cash_account", entity_id=str(acc.id),
                    detail=f"amount={payload.amount}")
    db.commit()
    return {"ok": True, "balance": _balance(db, acc.id), "transaction": _txn_read(row)}


@router.post("/accounts/{account_id}/adjust")
def adjust(
    account_id: uuid.UUID,
    payload: AdjustCreate,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> dict:
    """Admin balance correction (e.g. return unused float to the bank)."""
    acc = _get_account(db, account_id, user, manage=True)
    if payload.signed_amount == 0:
        raise HTTPException(status_code=400, detail="signed_amount must be non-zero")
    if abs(payload.signed_amount) > MAX_AMOUNT:
        raise HTTPException(status_code=400, detail="Amount too large")
    petty_code = resolve_account_code(db, "petty_cash")
    counter = payload.counter_account_code.strip()
    amount = abs(payload.signed_amount)
    if payload.signed_amount > 0:
        txn = _post_gl(db, debit_code=petty_code, credit_code=counter,
                       amount=amount, description=payload.description)
    else:
        txn = _post_gl(db, debit_code=counter, credit_code=petty_code,
                       amount=amount, description=payload.description)
    row = PettyCashTransaction(
        account_id=acc.id, kind="adjustment", amount=amount,
        signed_amount=payload.signed_amount, description=payload.description,
        counter_account_code=counter, status="approved", transaction_id=txn.id,
        created_by=user.user_id, decided_by=user.user_id,
        decided_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    log_audit_event(db, "petty_cash_adjust", "petty_cash_account", entity_id=str(acc.id),
                    detail=f"signed_amount={payload.signed_amount}")
    db.commit()
    return {"ok": True, "balance": _balance(db, acc.id), "transaction": _txn_read(row)}


@router.post("/accounts/{account_id}/expenses", status_code=201)
def record_expense(
    account_id: uuid.UUID,
    payload: ExpenseCreate,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> dict:
    """The holder records a spend (receipt optional) — pending until approved.
    Nothing posts to the GL yet."""
    acc = _get_account(db, account_id, user)  # own or admin
    if payload.attachment_id is not None and db.get(TransactionAttachment, payload.attachment_id) is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    row = PettyCashTransaction(
        account_id=acc.id, kind="expense", amount=payload.amount,
        signed_amount=-payload.amount, description=payload.description,
        counter_account_code=(payload.category_account_code or "").strip() or None,
        attachment_id=payload.attachment_id, status="pending",
        created_by=user.user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log_audit_event(db, "petty_cash_expense", "petty_cash_account", entity_id=str(acc.id),
                    detail=f"amount={payload.amount} pending")
    db.commit()
    return _txn_read(row)


def _get_pending_expense(db: Session, txn_id: uuid.UUID) -> PettyCashTransaction:
    row = db.get(PettyCashTransaction, txn_id)
    if row is None or row.kind != "expense":
        raise HTTPException(status_code=404, detail="Petty cash expense not found")
    if row.status != "pending":
        raise HTTPException(status_code=400, detail=f"Expense is already {row.status}")
    return row


@router.post("/expenses/{txn_id}/approve")
def approve_expense(
    txn_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> dict:
    """Approve → DR expense category / CR petty_cash; receipt rides onto the
    posted journal entry."""
    if not _is_manager(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    row = _get_pending_expense(db, txn_id)
    petty_code = resolve_account_code(db, "petty_cash")
    category = row.counter_account_code or resolve_account_code(db, "expense")
    txn = _post_gl(db, debit_code=category, credit_code=petty_code,
                   amount=row.amount,
                   description=f"Petty cash: {row.description}")
    if row.attachment_id is not None:
        att = db.get(TransactionAttachment, row.attachment_id)
        if att is not None and att.transaction_id is None:
            att.transaction_id = txn.id
    row.status = "approved"
    row.counter_account_code = category
    row.transaction_id = txn.id
    row.decided_by = user.user_id
    row.decided_at = datetime.now(timezone.utc)
    db.commit()
    log_audit_event(db, "petty_cash_approve", "petty_cash_transaction", entity_id=str(row.id),
                    detail=f"amount={row.amount}")
    db.commit()
    return {"ok": True, "balance": _balance(db, row.account_id), "transaction": _txn_read(row)}


@router.post("/expenses/{txn_id}/reject")
def reject_expense(
    txn_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> dict:
    if not _is_manager(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    row = _get_pending_expense(db, txn_id)
    row.status = "rejected"
    row.decided_by = user.user_id
    row.decided_at = datetime.now(timezone.utc)
    db.commit()
    log_audit_event(db, "petty_cash_reject", "petty_cash_transaction", entity_id=str(row.id),
                    detail=f"amount={row.amount}")
    db.commit()
    return {"ok": True, "transaction": _txn_read(row)}

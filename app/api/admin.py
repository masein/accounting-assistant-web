"""Admin endpoints: AI settings, reset database, user management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from uuid import UUID
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.auth import hash_password, require_admin
from app.core.ai_runtime import get_ai_config_public, update_ai_config
from app.db.session import get_db
from app.models.account import Account
from app.models.budget import BudgetLimit
from app.models.entity import Entity, TransactionEntity
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.inventory import InventoryItem, InventoryMovement
from app.models.recurring import RecurringRule
from app.models.transaction import Transaction, TransactionAttachment, TransactionLine
from app.models.transaction_fee import PaymentMethod, TransactionFee, TransactionFeeApplication
from app.models.trial_balance import TrialBalance, TrialBalanceLine
from app.models.user import User
from app.db.seed import seed_admin_user_if_missing, seed_chart_if_empty, seed_payment_methods_if_empty

router = APIRouter(prefix="/admin", tags=["admin"])


class AIConfigPatch(BaseModel):
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_key_header: str | None = None
    api_key_prefix: str | None = None


class UserCreatePayload(BaseModel):
    username: str
    password: str
    preferred_language: str = "en"
    is_admin: bool = False
    is_active: bool = True


class UserUpdatePayload(BaseModel):
    password: str | None = None
    preferred_language: str | None = None
    is_admin: bool | None = None
    is_active: bool | None = None


@router.get("/ai-config")
def get_ai_config(_=Depends(require_admin)) -> dict:
    return get_ai_config_public()


@router.patch("/ai-config")
def patch_ai_config(payload: AIConfigPatch, _=Depends(require_admin)) -> dict:
    return update_ai_config(
        provider=payload.provider,
        model=payload.model,
        base_url=payload.base_url,
        api_key=payload.api_key,
        api_key_header=payload.api_key_header,
        api_key_prefix=payload.api_key_prefix,
    )


@router.post("/reset-db")
def reset_db(db: Session = Depends(get_db), _=Depends(require_admin)) -> dict:
    """
    Delete all data and re-seed the chart of accounts. Entities, transactions, and trial balances are cleared.
    """
    # Delete in strict dependency order (children before parents).
    try:
        db.execute(delete(TransactionEntity))
        db.execute(delete(TransactionAttachment))
        db.execute(delete(TransactionLine))
        db.execute(delete(TransactionFeeApplication))
        db.execute(delete(TransactionFee))
        db.execute(delete(PaymentMethod))
        db.execute(delete(InventoryMovement))
        db.execute(delete(InvoiceItem))
        db.execute(delete(InventoryItem))
        db.execute(delete(Invoice))  # can reference Transaction via transaction_id
        db.execute(delete(Transaction))
        db.execute(delete(RecurringRule))
        db.execute(delete(BudgetLimit))
        db.execute(delete(TrialBalanceLine))
        db.execute(delete(TrialBalance))
        db.execute(delete(Entity))
        db.execute(delete(Account))
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Reset failed: {str(e)}") from e

    n = seed_chart_if_empty(db)
    seed_payment_methods_if_empty(db)
    seed_admin_user_if_missing(db)
    return {
        "ok": True,
        "message": "Database reset. Chart of accounts re-seeded.",
        "accounts_created": n,
    }


@router.get("/users")
def list_users(db: Session = Depends(get_db), _=Depends(require_admin)) -> list[dict]:
    users = db.query(User).order_by(User.username.asc()).all()
    return [
        {
            "id": str(u.id),
            "username": u.username,
            "preferred_language": u.preferred_language or "en",
            "is_admin": bool(u.is_admin),
            "is_active": bool(u.is_active),
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.post("/users", status_code=201)
def create_user(payload: UserCreatePayload, db: Session = Depends(get_db), _=Depends(require_admin)) -> dict:
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    lang = (payload.preferred_language or "en").strip().lower()
    if lang not in {"en", "fa", "es", "ar"}:
        raise HTTPException(status_code=400, detail="Unsupported language")
    password_hash, password_salt = hash_password(payload.password)
    user = User(
        username=username,
        password_hash=password_hash,
        password_salt=password_salt,
        preferred_language=lang,
        is_admin=bool(payload.is_admin),
        is_active=bool(payload.is_active),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {
        "id": str(user.id),
        "username": user.username,
        "preferred_language": user.preferred_language or "en",
        "is_admin": bool(user.is_admin),
        "is_active": bool(user.is_active),
    }


@router.patch("/users/{user_id}")
def update_user(user_id: UUID, payload: UserUpdatePayload, db: Session = Depends(get_db), _=Depends(require_admin)) -> dict:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.password is not None:
        password_hash, password_salt = hash_password(payload.password)
        user.password_hash = password_hash
        user.password_salt = password_salt
    if payload.preferred_language is not None:
        lang = payload.preferred_language.strip().lower()
        if lang not in {"en", "fa", "es", "ar"}:
            raise HTTPException(status_code=400, detail="Unsupported language")
        user.preferred_language = lang
    if payload.is_admin is not None:
        user.is_admin = bool(payload.is_admin)
    if payload.is_active is not None:
        user.is_active = bool(payload.is_active)
    db.commit()
    db.refresh(user)
    return {
        "id": str(user.id),
        "username": user.username,
        "preferred_language": user.preferred_language or "en",
        "is_admin": bool(user.is_admin),
        "is_active": bool(user.is_active),
    }


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: UUID, db: Session = Depends(get_db), _=Depends(require_admin)) -> None:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.username.lower() == "admin":
        raise HTTPException(status_code=400, detail="Default admin user cannot be deleted")
    db.delete(user)
    db.commit()

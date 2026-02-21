"""Admin endpoints: reset database, etc."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

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
from app.db.seed import seed_chart_if_empty, seed_payment_methods_if_empty

router = APIRouter(prefix="/admin", tags=["admin"])


class AIConfigPatch(BaseModel):
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_key_header: str | None = None
    api_key_prefix: str | None = None


@router.get("/ai-config")
def get_ai_config() -> dict:
    return get_ai_config_public()


@router.patch("/ai-config")
def patch_ai_config(payload: AIConfigPatch) -> dict:
    return update_ai_config(
        provider=payload.provider,
        model=payload.model,
        base_url=payload.base_url,
        api_key=payload.api_key,
        api_key_header=payload.api_key_header,
        api_key_prefix=payload.api_key_prefix,
    )


@router.post("/reset-db")
def reset_db(db: Session = Depends(get_db)) -> dict:
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
    return {
        "ok": True,
        "message": "Database reset. Chart of accounts re-seeded.",
        "accounts_created": n,
    }

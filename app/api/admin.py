"""Admin endpoints: AI settings, reset database, user management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from uuid import UUID
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.auth import hash_password, require_admin, validate_password_strength
from app.core.ai_runtime import get_ai_config_public, update_ai_config
from app.db.session import get_db
from app.services.locale_service import (
    SUPPORTED_CALENDARS,
    SUPPORTED_LOCALES,
    get_display_calendar,
    get_reporting_locale,
    set_display_calendar,
    set_reporting_locale,
)
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


class ReportingLocaleRead(BaseModel):
    locale: str
    supported: list[str]


class ReportingLocaleUpdate(BaseModel):
    locale: str


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
def reset_db(
    db: Session = Depends(get_db),
    locale: str = "ir",
    with_demo_data: bool = False,
    _=Depends(require_admin),
) -> dict:
    """
    Delete all data and re-seed the chart of accounts. Entities, transactions,
    and trial balances are cleared.

    Optional query parameters:
    - ``locale`` — ``"ir"`` (default, Iranian standard chart) or ``"uk"``
      (Sage-style FRS 102 1A chart). Also updates the reporting-locale
      AppSetting so reports default to the matching template.
    - ``with_demo_data`` — when ``true``, posts a curated set of journal
      entries spanning two fiscal years so the user can demo populated
      statements immediately.
    """
    locale_norm = (locale or "ir").strip().lower()
    if locale_norm not in SUPPORTED_LOCALES:
        raise HTTPException(status_code=400, detail=f"Unsupported locale '{locale}'. Supported: {sorted(SUPPORTED_LOCALES)}")

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

    n = seed_chart_if_empty(db, locale=locale_norm)
    seed_payment_methods_if_empty(db)
    seed_admin_user_if_missing(db)

    # Align the reporting-locale AppSetting with the chart we just seeded.
    set_reporting_locale(db, "uk" if locale_norm == "uk" else "ir")
    db.commit()

    demo_entries = 0
    if with_demo_data:
        from app.db.demo_data import seed_iran_demo, seed_uk_demo
        if locale_norm == "uk":
            demo_entries = seed_uk_demo(db)
        else:
            demo_entries = seed_iran_demo(db)

    return {
        "ok": True,
        "message": "Database reset. Chart of accounts re-seeded.",
        "locale": locale_norm,
        "accounts_created": n,
        "demo_entries": demo_entries,
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
    try:
        validate_password_strength(payload.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
        try:
            validate_password_strength(payload.password)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
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


@router.get("/reporting-locale", response_model=ReportingLocaleRead)
def read_reporting_locale(db: Session = Depends(get_db)) -> ReportingLocaleRead:
    return ReportingLocaleRead(locale=get_reporting_locale(db), supported=sorted(SUPPORTED_LOCALES))


@router.put("/reporting-locale", response_model=ReportingLocaleRead)
def update_reporting_locale(
    payload: ReportingLocaleUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
) -> ReportingLocaleRead:
    try:
        locale = set_reporting_locale(db, payload.locale)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.commit()
    return ReportingLocaleRead(locale=locale, supported=sorted(SUPPORTED_LOCALES))


class DisplayCalendarRead(BaseModel):
    calendar: str
    supported: list[str]


class DisplayCalendarUpdate(BaseModel):
    calendar: str


@router.get("/display-calendar", response_model=DisplayCalendarRead)
def read_display_calendar(db: Session = Depends(get_db)) -> DisplayCalendarRead:
    return DisplayCalendarRead(calendar=get_display_calendar(db), supported=sorted(SUPPORTED_CALENDARS))


@router.put("/display-calendar", response_model=DisplayCalendarRead)
def update_display_calendar(
    payload: DisplayCalendarUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
) -> DisplayCalendarRead:
    try:
        cal = set_display_calendar(db, payload.calendar)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.commit()
    return DisplayCalendarRead(calendar=cal, supported=sorted(SUPPORTED_CALENDARS))


class SharesOutstandingPayload(BaseModel):
    shares: int


@router.get("/iran-shares-outstanding")
def read_iran_shares_outstanding(db: Session = Depends(get_db)) -> dict:
    from app.services.reporting.iran_statement_service import _get_shares_outstanding

    return {"shares": _get_shares_outstanding(db)}


@router.put("/iran-shares-outstanding")
def update_iran_shares_outstanding(
    payload: SharesOutstandingPayload,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
) -> dict:
    """Set the issued-share count used to compute basic EPS on the Iranian
    Income Statement. Set to 0 to clear (EPS rows will return null)."""
    from sqlalchemy import select
    from app.models.app_setting import AppSetting
    from app.services.reporting.iran_statement_service import SHARES_OUTSTANDING_KEY

    if payload.shares < 0:
        raise HTTPException(status_code=400, detail="shares must be >= 0")
    row = db.execute(
        select(AppSetting).where(AppSetting.key == SHARES_OUTSTANDING_KEY)
    ).scalar_one_or_none()
    value = str(int(payload.shares))
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=SHARES_OUTSTANDING_KEY, value=value))
    db.commit()
    return {"shares": payload.shares if payload.shares > 0 else None}

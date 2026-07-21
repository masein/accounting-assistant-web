"""Admin endpoints: AI settings, reset database, user management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from uuid import UUID
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.auth import (
    SessionUser,
    get_current_user,
    hash_password,
    require_admin,
    validate_password_strength,
)
from app.core.permissions import ALL_ROLES, Role
from app.core.ai_runtime import (
    get_ai_config_public,
    resolve_anthropic_config,
    update_ai_config,
    update_anthropic_config,
)
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
from app.models.adjustment import Adjustment
from app.models.bank_statement import BankStatement, BankStatementRow
from app.models.budget import BudgetLimit
from app.models.credit_note import CreditNote
from app.models.employee_pay import EmployeePayProfile
from app.models.entity import Entity, TransactionEntity
from app.models.equity import EquityEvent, Shareholding
from app.models.goods_receipt import GoodsReceipt, GoodsReceiptLine
from app.models.pending_time_entry import PendingTimeEntry
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.mileage_claim import MileageClaim
from app.models.pay_run import PayRun, PayRunLine
from app.models.payment import Payment
from app.models.purchase_order import PurchaseOrder, PurchaseOrderLine
from app.models.inventory import InventoryItem, InventoryMovement
from app.models.recurring import RecurringRule
from app.models.tax_rate import TaxRate
from app.models.time_billing import BillingRateOverride, Project, TimeEntry
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
    role: str = Role.EMPLOYEE
    preferred_language: str = "en"
    is_active: bool = True
    entity_id: UUID | None = None  # link to an employee Entity (self-service)


class UserUpdatePayload(BaseModel):
    password: str | None = None
    role: str | None = None
    preferred_language: str | None = None
    is_active: bool | None = None
    entity_id: UUID | None = None
    unlink_entity: bool = False  # explicit unlink (entity_id=None is "no change")


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


SUPPORTED_CHAT_SHAPES = ("anthropic", "openai")


class AnthropicConfigPatch(BaseModel):
    """Settings specific to the Anthropic provider used by the AI accountant.
    Editing these does NOT change the active OpenAI-compatible provider."""
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class ChatShapeRead(BaseModel):
    """The current AI Chat provider-shape setting + the active default.

    ``shape`` is the persisted user choice (or empty string when not yet
    set). ``effective`` is what the chat will actually use right now —
    auto-detected when ``shape`` is empty (Anthropic when the Anthropic
    key is configured, OpenAI otherwise)."""
    shape: str
    effective: str
    supported: list[str]


class ChatShapeUpdate(BaseModel):
    """``shape`` ∈ ``{"anthropic", "openai", ""}``. Empty string clears
    the explicit override and re-enables auto-detection."""
    shape: str


def _resolve_effective_shape(db: Session) -> str:
    """Mirror of ``orchestrator._resolve_chat_shape`` for the admin GET."""
    from sqlalchemy import select as _sel
    from app.core.ai_runtime import resolve_anthropic_config
    from app.models.app_setting import AppSetting

    row = db.execute(
        _sel(AppSetting).where(AppSetting.key == "ai_chat_provider_shape")
    ).scalar_one_or_none()
    if row and (row.value or "").strip().lower() in SUPPORTED_CHAT_SHAPES:
        return row.value.strip().lower()
    return "anthropic" if resolve_anthropic_config().get("api_key") else "openai"


@router.get("/chat-provider-shape", response_model=ChatShapeRead)
def read_chat_provider_shape(db: Session = Depends(get_db)) -> ChatShapeRead:
    """Return the AI Chat provider-shape selector for the Settings UI."""
    from sqlalchemy import select as _sel
    from app.models.app_setting import AppSetting

    row = db.execute(
        _sel(AppSetting).where(AppSetting.key == "ai_chat_provider_shape")
    ).scalar_one_or_none()
    stored = (row.value or "").strip().lower() if row else ""
    if stored not in SUPPORTED_CHAT_SHAPES:
        stored = ""
    return ChatShapeRead(
        shape=stored,
        effective=_resolve_effective_shape(db),
        supported=list(SUPPORTED_CHAT_SHAPES),
    )


@router.put("/chat-provider-shape", response_model=ChatShapeRead)
def update_chat_provider_shape(
    payload: ChatShapeUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
) -> ChatShapeRead:
    """Persist the user's explicit chat-shape choice (or clear it to
    re-enable auto-detection by sending an empty string)."""
    from sqlalchemy import select as _sel
    from app.models.app_setting import AppSetting

    value = (payload.shape or "").strip().lower()
    if value and value not in SUPPORTED_CHAT_SHAPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported shape {value!r}. Supported: {list(SUPPORTED_CHAT_SHAPES)} or '' (auto).",
        )
    row = db.execute(
        _sel(AppSetting).where(AppSetting.key == "ai_chat_provider_shape")
    ).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key="ai_chat_provider_shape", value=value))
    db.commit()
    return read_chat_provider_shape(db)


class ClosedPeriodRead(BaseModel):
    closed_period: str | None = None


class ClosedPeriodUpdate(BaseModel):
    closed_period: str | None = None  # ISO date, or empty/null to clear the lock


@router.get("/closed-period", response_model=ClosedPeriodRead)
def read_closed_period(db: Session = Depends(get_db)) -> ClosedPeriodRead:
    """The date the books are locked through (inclusive), or null if open."""
    from app.services.period_service import get_closed_period

    cp = get_closed_period(db)
    return ClosedPeriodRead(closed_period=cp.isoformat() if cp else None)


@router.put("/closed-period", response_model=ClosedPeriodRead)
def update_closed_period(
    payload: ClosedPeriodUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
) -> ClosedPeriodRead:
    """Lock the books through a date (inclusive), or clear with empty/null."""
    from datetime import date as _date

    from app.services.period_service import set_closed_period

    raw = (payload.closed_period or "").strip()
    value: _date | None = None
    if raw:
        try:
            value = _date.fromisoformat(raw)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="closed_period must be an ISO date (YYYY-MM-DD) or empty.") from e
    set_closed_period(db, value)
    db.commit()
    return read_closed_period(db)


@router.get("/anthropic-config")
def get_anthropic_config(_=Depends(require_admin)) -> dict:
    """Return the AI-accountant (Claude) provider settings only — separate
    from the OpenAI-compatible default provider config. The default
    ``base_url`` (``https://api.anthropic.com``) is returned when no
    override is set, so the UI always has a non-empty value to show."""
    cfg = resolve_anthropic_config()
    return {
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "has_api_key": bool(cfg.get("api_key")),
        "default_base_url": "https://api.anthropic.com",
        "default_model": "claude-opus-4-6",
    }


@router.patch("/anthropic-config")
def patch_anthropic_config(payload: AnthropicConfigPatch, _=Depends(require_admin)) -> dict:
    """Update the Anthropic provider settings used by the AI accountant.
    Any field left blank is left unchanged. Use ``api_key = "-"`` to clear
    the stored key. Setting ``base_url`` to an empty string falls back to
    the Anthropic default on the next request."""
    update_anthropic_config(
        base_url=payload.base_url,
        model=payload.model,
        api_key=payload.api_key,
    )
    cfg = resolve_anthropic_config()
    return {
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "has_api_key": bool(cfg.get("api_key")),
        "default_base_url": "https://api.anthropic.com",
        "default_model": "claude-opus-4-6",
    }


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
        # Bank statements aren't wiped by the business-table reset, so they
        # accumulate across resets (and across locale switches — stale IRR
        # statements survive a UK reload). Clear them first so the list
        # reflects only the freshly seeded demo, and so a re-seeded demo
        # statement isn't buried under leftovers.
        db.execute(delete(BankStatementRow))
        db.execute(delete(BankStatement))
        # Payroll children — pay-run lines + runs + pay profiles. PayRun FKs
        # transactions.id (SET NULL) but nothing cascades it, so wipe explicitly.
        db.execute(delete(PayRunLine))
        db.execute(delete(PayRun))
        db.execute(delete(EmployeePayProfile))
        # Purchase orders + receipts — FK entities/invoices/inventory with no
        # ondelete, so clear them before those parents.
        db.execute(delete(GoodsReceiptLine))
        db.execute(delete(GoodsReceipt))
        db.execute(delete(PurchaseOrderLine))
        db.execute(delete(PurchaseOrder))
        # Mileage claims FK entities + transactions (SET NULL); wipe before them.
        db.execute(delete(MileageClaim))
        # Shareholder equity: events FK transactions/entities (SET NULL) so they
        # would SURVIVE the wipe with nulled refs and keep feeding ghost rows
        # into the changes-in-equity statement; shareholdings FK entities.
        # Wipe both explicitly, and reset the company's registered capital.
        db.execute(delete(EquityEvent))
        db.execute(delete(Shareholding))
        # Inbound time entries parked for review are business data too.
        db.execute(delete(PendingTimeEntry))
        # Time billing: entries → overrides → projects, all FK entities/invoices.
        db.execute(delete(TimeEntry))
        db.execute(delete(BillingRateOverride))
        db.execute(delete(Project))
        db.execute(delete(TransactionEntity))
        db.execute(delete(TransactionAttachment))
        db.execute(delete(TransactionLine))
        db.execute(delete(TransactionFeeApplication))
        db.execute(delete(TransactionFee))
        db.execute(delete(PaymentMethod))
        db.execute(delete(InventoryMovement))
        # AR/AP + period-close children that FK transactions.id (and invoices.id)
        # with no ondelete — must go before Invoice/Transaction or the delete
        # 500s with a ForeignKeyViolation once any of these rows exist.
        db.execute(delete(CreditNote))
        db.execute(delete(Payment))
        db.execute(delete(Adjustment))
        db.execute(delete(InvoiceItem))
        db.execute(delete(InventoryItem))
        db.execute(delete(Invoice))  # can reference Transaction via transaction_id
        db.execute(delete(Transaction))
        db.execute(delete(RecurringRule))
        db.execute(delete(BudgetLimit))
        db.execute(delete(TrialBalanceLine))
        db.execute(delete(TrialBalance))
        db.execute(delete(TaxRate))
        db.execute(delete(Entity))
        db.execute(delete(Account))
        # Registered capital is derived from (now-wiped) contributions and
        # capital increases — reset it so the cap table starts clean.
        from app.services.fx_service import _current_company_row
        company = _current_company_row(db)
        if company is not None:
            company.registered_capital = 0
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Reset failed: {str(e)}") from e

    n = seed_chart_if_empty(db, locale=locale_norm)
    seed_payment_methods_if_empty(db)
    seed_admin_user_if_missing(db)
    from app.services.tax_rate_service import seed_tax_rates
    seed_tax_rates(db)

    # Align the reporting-locale AppSetting with the chart we just seeded.
    set_reporting_locale(db, "uk" if locale_norm == "uk" else "ir")
    # Also align the reporting CURRENCY — otherwise the user loads the UK
    # demo (which posts GBP transactions) but every report keeps filtering
    # by the previous reporting currency (typically IRR) and shows £0
    # everywhere. The reporting_currency AppSetting isn't cleared by the
    # business-table wipe above, so we must overwrite it explicitly.
    from app.services.fx_service import set_reporting_currency
    set_reporting_currency(db, "GBP" if locale_norm == "uk" else "IRR")
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


# --- Owner user management (company-scoped, gated to USERS_MANAGE = owner) -----
def _serialize_user(u: User, entity_name: str | None = None) -> dict:
    return {
        "id": str(u.id),
        "username": u.username,
        "role": u.role or Role.OWNER,
        "preferred_language": u.preferred_language or "en",
        "is_admin": bool(u.is_admin),
        "is_active": bool(u.is_active),
        "entity_id": str(u.entity_id) if u.entity_id else None,
        "entity_name": entity_name,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _entity_names(db: Session, user_rows: list[User]) -> dict:
    ids = {u.entity_id for u in user_rows if u.entity_id}
    if not ids:
        return {}
    rows = db.query(Entity.id, Entity.name).filter(Entity.id.in_(ids)).all()
    return {eid: name for eid, name in rows}


def _caller_company_uuid(caller: SessionUser) -> UUID | None:
    return UUID(str(caller.company_id)) if caller.company_id else None


def _company_users_query(db: Session, caller: SessionUser):
    """Users in the caller's company. Users aren't tenant-scoped (no auto
    filter), so we filter by company_id explicitly."""
    q = db.query(User)
    cid = _caller_company_uuid(caller)
    if cid is not None:
        q = q.filter(User.company_id == cid)
    return q


def _get_company_user(db: Session, caller: SessionUser, user_id: UUID) -> User:
    user = db.get(User, user_id)
    # Cross-company (or unknown) targets look like they don't exist.
    if not user or (caller.company_id and str(user.company_id) != str(caller.company_id)):
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _validate_role(role: str) -> str:
    role = (role or "").strip().lower()
    if role not in ALL_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role: {role!r}")
    return role


def _validate_entity_link(db: Session, caller: SessionUser, entity_id: UUID) -> None:
    """The linked entity must be an employee in the caller's company. Entity is
    tenant-scoped so this query is already company-filtered."""
    ent = db.get(Entity, entity_id)
    if not ent:
        raise HTTPException(status_code=400, detail="Entity not found in this company")
    if (ent.type or "").lower() != "employee":
        raise HTTPException(status_code=400, detail="Can only link to an employee entity")


def _count_active_owners(db: Session, caller: SessionUser, exclude_id: UUID | None = None) -> int:
    q = _company_users_query(db, caller).filter(
        User.role == Role.OWNER, User.is_active.is_(True)
    )
    if exclude_id is not None:
        q = q.filter(User.id != exclude_id)
    return q.count()


@router.get("/users")
def list_users(
    db: Session = Depends(get_db), caller: SessionUser = Depends(get_current_user)
) -> list[dict]:
    users = _company_users_query(db, caller).order_by(User.username.asc()).all()
    names = _entity_names(db, users)
    return [_serialize_user(u, names.get(u.entity_id)) for u in users]


@router.post("/users", status_code=201)
def create_user(
    payload: UserCreatePayload,
    db: Session = Depends(get_db),
    caller: SessionUser = Depends(get_current_user),
) -> dict:
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    if db.query(User).filter(User.username == username).first():  # globally unique
        raise HTTPException(status_code=400, detail="Username already exists")
    lang = (payload.preferred_language or "en").strip().lower()
    if lang not in {"en", "fa", "es", "ar"}:
        raise HTTPException(status_code=400, detail="Unsupported language")
    role = _validate_role(payload.role)
    try:
        validate_password_strength(payload.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if payload.entity_id is not None:
        _validate_entity_link(db, caller, payload.entity_id)
    password_hash, password_salt = hash_password(payload.password)
    user = User(
        username=username,
        password_hash=password_hash,
        password_salt=password_salt,
        preferred_language=lang,
        role=role,
        is_admin=(role == Role.OWNER),  # legacy flag tracks the owner role
        is_superadmin=False,            # never provisioned here (platform-level)
        is_active=bool(payload.is_active),
        company_id=_caller_company_uuid(caller),
        entity_id=payload.entity_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    name = db.get(Entity, user.entity_id).name if user.entity_id else None
    return _serialize_user(user, name)


@router.patch("/users/{user_id}")
def update_user(
    user_id: UUID,
    payload: UserUpdatePayload,
    db: Session = Depends(get_db),
    caller: SessionUser = Depends(get_current_user),
) -> dict:
    user = _get_company_user(db, caller, user_id)
    is_self = str(user.id) == str(caller.user_id)
    bump = False

    if payload.password is not None:
        try:
            validate_password_strength(payload.password)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        user.password_hash, user.password_salt = hash_password(payload.password)
        bump = True

    if payload.role is not None:
        new_role = _validate_role(payload.role)
        if new_role != user.role:
            if is_self:
                raise HTTPException(status_code=400, detail="You cannot change your own role")
            # Don't strip the company's last owner.
            if user.role == Role.OWNER and _count_active_owners(db, caller, exclude_id=user.id) == 0:
                raise HTTPException(status_code=400, detail="The company must keep at least one owner")
            user.role = new_role
            user.is_admin = (new_role == Role.OWNER)
            bump = True

    if payload.preferred_language is not None:
        lang = payload.preferred_language.strip().lower()
        if lang not in {"en", "fa", "es", "ar"}:
            raise HTTPException(status_code=400, detail="Unsupported language")
        user.preferred_language = lang

    if payload.is_active is not None and bool(payload.is_active) != user.is_active:
        if is_self and not payload.is_active:
            raise HTTPException(status_code=400, detail="You cannot deactivate yourself")
        if not payload.is_active and user.role == Role.OWNER \
                and _count_active_owners(db, caller, exclude_id=user.id) == 0:
            raise HTTPException(status_code=400, detail="The company must keep at least one active owner")
        user.is_active = bool(payload.is_active)
        bump = True

    if payload.unlink_entity:
        user.entity_id = None
    elif payload.entity_id is not None:
        _validate_entity_link(db, caller, payload.entity_id)
        user.entity_id = payload.entity_id

    # Invalidate live sessions on any security-relevant change.
    if bump:
        user.token_version = int(user.token_version or 0) + 1

    db.commit()
    db.refresh(user)
    name = db.get(Entity, user.entity_id).name if user.entity_id else None
    return _serialize_user(user, name)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    caller: SessionUser = Depends(get_current_user),
) -> None:
    user = _get_company_user(db, caller, user_id)
    if str(user.id) == str(caller.user_id):
        raise HTTPException(status_code=400, detail="You cannot delete yourself")
    if user.username.lower() == "admin":
        raise HTTPException(status_code=400, detail="Default admin user cannot be deleted")
    if user.role == Role.OWNER and _count_active_owners(db, caller, exclude_id=user.id) == 0:
        raise HTTPException(status_code=400, detail="The company must keep at least one owner")
    db.delete(user)
    db.commit()


# --- Company API keys (Owner-only; the /api/v1 integration credential) --------
class ApiKeyCreatePayload(BaseModel):
    label: str = "integration"


@router.get("/api-keys")
def list_api_keys(
    db: Session = Depends(get_db), caller: SessionUser = Depends(get_current_user)
) -> list[dict]:
    from app.models.api_key import ApiKey
    cid = _caller_company_uuid(caller)
    if cid is None:
        return []
    rows = db.query(ApiKey).filter(ApiKey.company_id == cid).order_by(ApiKey.created_at.desc()).all()
    return [{
        "id": str(k.id), "label": k.label, "prefix": k.prefix,
        "revoked": bool(k.revoked),
        "created_at": k.created_at.isoformat() if k.created_at else None,
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
    } for k in rows]


@router.post("/api-keys", status_code=201)
def create_api_key(
    payload: ApiKeyCreatePayload,
    db: Session = Depends(get_db),
    caller: SessionUser = Depends(get_current_user),
) -> dict:
    """Generate a company API key. The raw key is returned ONCE — store it now;
    only its hash is kept server-side."""
    from app.core.api_key_auth import generate_api_key
    from app.models.api_key import ApiKey
    cid = _caller_company_uuid(caller)
    if cid is None:
        raise HTTPException(status_code=400, detail="No company in context.")
    raw, digest, prefix = generate_api_key()
    key = ApiKey(company_id=cid, label=(payload.label or "integration").strip()[:128],
                 key_hash=digest, prefix=prefix)
    db.add(key)
    db.commit()
    db.refresh(key)
    return {
        "id": str(key.id), "label": key.label, "prefix": key.prefix,
        "api_key": raw,  # shown exactly once
        "note": "Store this key now — it cannot be retrieved again.",
    }


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    key_id: UUID,
    db: Session = Depends(get_db),
    caller: SessionUser = Depends(get_current_user),
) -> None:
    from app.models.api_key import ApiKey
    key = db.get(ApiKey, key_id)
    cid = _caller_company_uuid(caller)
    if not key or (cid and key.company_id != cid):
        raise HTTPException(status_code=404, detail="API key not found")
    key.revoked = True
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

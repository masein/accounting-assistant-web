"""Shared, confirm-gated entity creation for AI proposals.

Used by the execute path when the user confirms a ``propose_create_entity`` or a
``propose_create_transaction`` that carries ``new_entities``. Creating an entity
is a master-data write, so it only ever runs from the server-side execute path
(never from the tool itself), and it's audited by the caller.

Type normalization (stated in the PR): contractor / freelancer / subcontractor
/ vendor → ``supplier``; customer → ``client``. Unknown → ``supplier``.

Banks are special: a bank entity is only usable as a payment source/destination
if its ``Entity.code`` points to a real GL cash account (payment posting does
``Account.code == bank_entity.code``). So creating a bank also links or creates
a GL bank account in the locale's cash range (UK ``12xx``, Iran ``111x``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.entity import Entity

_VALID_TYPES = ("client", "supplier", "employee", "bank", "shareholder")
_TYPE_ALIASES = {
    "contractor": "supplier",
    "freelancer": "supplier",
    "subcontractor": "supplier",
    "vendor": "supplier",
    "payee": "supplier",
    "customer": "client",
    "staff": "employee",
    # Equity holders (سهامدار) — NOT payroll staff. Profit distribution and
    # capital movements follow completely different accounting logic.
    "partner": "shareholder",
    "co-founder": "shareholder",
    "cofounder": "shareholder",
    "founder": "shareholder",
    "investor": "shareholder",
    "owner": "shareholder",
}


class EntityCreateError(ValueError):
    """Invalid entity-creation input (bad name/type)."""


@dataclass
class CreatedEntity:
    entity: Entity
    created: bool                 # False when an existing entity was reused
    account_code: str | None = None  # the bank GL account (for banks)
    account_created: bool = False


def normalize_entity_type(raw: str | None) -> str:
    t = (raw or "").strip().lower()
    t = _TYPE_ALIASES.get(t, t)
    return t if t in _VALID_TYPES else "supplier"


# A freelancer/contractor/consultant you PAY for services is a supplier (AP),
# never an employee. Employees are payroll staff paid wages/salary. These cues
# make the type deterministic regardless of which the (non-deterministic) model
# picked.
_SUPPLIER_CUES = (
    "freelancer", "freelance", "contractor", "subcontractor", "sub-contractor",
    "consultant", "consultancy", "self-employed", "self employed", "sole trader",
    "on a contract", "on contract", "contract basis", "for a project",
    "their invoice", "invoiced us", "professional fees",
)
_EMPLOYEE_CUES = (
    "employee", "on payroll", "onto payroll", "on the payroll", "payroll",
    "salary", "salaried", "wages", "paye", "national insurance", "new hire",
    "hired onto", "member of staff", "staff member",
)
# Shareholder/equity language wins over everything — a سهامدار is never an
# employee, even if they also work in the business.
_SHAREHOLDER_CUES = (
    "shareholder", "share holder", "stakeholder", "equity holder", "sharehold",
    "co-founder", "cofounder", "founder", "partner in the company",
    "business partner", "owns shares", "owns a share", "capital contribution",
    "dividend", "سهامدار", "سهام", "شریک", "سود سهام", "آورده",
)


def classify_entity_type(proposed_type: str | None, *, text: str = "",
                         staff_cost: bool | None = None) -> str:
    """Deterministically decide an entity's type, overriding the model's pick.

    * Any freelancer/contractor/consultant/etc. language → ``supplier``.
    * ``employee`` only survives on genuine employment language; an ``employee``
      pick on a non-staff-cost posting (``staff_cost is False`` — e.g. the entry
      debits Professional fees, not wages) is a misclassification → ``supplier``.
    """
    t = normalize_entity_type(proposed_type)
    low = (text or "").lower()
    if any(cue in low for cue in _SHAREHOLDER_CUES):
        return "shareholder"
    if any(cue in low for cue in _SUPPLIER_CUES):
        return "supplier"
    if t == "employee":
        if any(cue in low for cue in _EMPLOYEE_CUES):
            return "employee"
        if staff_cost is False:
            return "supplier"
    return t


def is_staff_cost_code(db, code: str | None) -> bool:
    """True when ``code`` is a staff-cost / wages account — what a genuine
    employee payment debits (UK 70xx/71xx; the locale wages_expense elsewhere)."""
    from app.services.locale_service import get_reporting_locale
    c = (code or "").strip()
    if not c:
        return False
    if (get_reporting_locale(db) or "").strip().lower() == "uk":
        return c.startswith(("70", "71"))
    try:
        from app.services.account_resolver import resolve_account_code
        return c == resolve_account_code(db, "wages_expense")
    except Exception:
        return False


def any_staff_cost(db, codes) -> bool:
    return any(is_staff_cost_code(db, c) for c in (codes or []))


def _validate_name(name: str) -> str:
    name = re.sub(r"\s+", " ", (name or "").strip())
    low = name.lower()
    if (
        len(name) < 2
        or len(name) > 80
        or len(name.split()) > 6
        or re.search(r"\b(via|about|payment|transaction)\b", low)
        or low in {"us", "our", "me", "we", "you", "your", "none", "nobody"}
    ):
        raise EntityCreateError(f"Invalid entity name: {name!r}")
    return name


def _next_bank_account_code(db: Session, locale: str) -> str:
    """Next free GL code in the locale's cash/bank range (UK 12xx, Iran 111x)."""
    if (locale or "").lower() == "uk":
        prefix, start, end = "12", 1201, 1299
    else:
        prefix, start, end = "111", 1111, 1119
    used = {
        c for (c,) in db.execute(
            select(Account.code).where(Account.code.like(f"{prefix}%"))
        ).all()
    }
    for n in range(start, end + 1):
        if str(n) not in used:
            return str(n)
    raise EntityCreateError(f"No free bank account code left in the {prefix}xx range.")


# Contact + bank fields the AI may gather when creating a party.
DETAIL_FIELDS = ("phone", "email", "address", "tax_id", "economic_code",
                 "contact_person", "bank_name", "account_holder",
                 "account_number", "iban", "sort_code")


def _apply_details(entity: Entity, details: dict | None, *, only_blank: bool) -> None:
    for f in DETAIL_FIELDS:
        v = (details or {}).get(f)
        if v is None or not str(v).strip():
            continue
        if only_blank and getattr(entity, f, None):
            continue  # reuse path: enrich blanks, never overwrite existing data
        setattr(entity, f, str(v).strip())


def create_entity(
    db: Session,
    *,
    name: str,
    type_: str,
    existing_account_code: str | None = None,
    locale: str | None = None,
    details: dict | None = None,
) -> CreatedEntity:
    """Get-or-create an entity (idempotent by type + name). For banks, link or
    create a GL cash account and set ``Entity.code`` to it. ``details`` carries
    AI-gathered contact + bank fields. Flushes; the caller commits + audits."""
    etype = normalize_entity_type(type_)
    clean = _validate_name(name)

    existing = db.execute(
        select(Entity).where(Entity.type == etype, Entity.name.ilike(clean))
    ).scalars().first()
    if existing is not None:
        _apply_details(existing, details, only_blank=True)
        # Reuse — but still ensure a bank has a usable GL code.
        if etype == "bank" and not existing.code:
            existing.code = _attach_bank_account(db, clean, existing_account_code, locale)
        db.flush()
        return CreatedEntity(entity=existing, created=False, account_code=existing.code)

    entity = Entity(type=etype, name=clean)
    _apply_details(entity, details, only_blank=False)
    account_code = None
    account_created = False
    if etype == "bank":
        account_code, account_created = _resolve_bank_account(db, clean, existing_account_code, locale)
        entity.code = account_code
    db.add(entity)
    db.flush()
    return CreatedEntity(entity=entity, created=True, account_code=account_code,
                         account_created=account_created)


def _resolve_bank_account(
    db: Session, name: str, existing_account_code: str | None, locale: str | None,
) -> tuple[str, bool]:
    """Return (account_code, created?). Uses an existing cash account if the
    user referenced one, else creates a new GL bank account."""
    from app.services.locale_service import get_reporting_locale

    loc = (locale or get_reporting_locale(db) or "default").strip().lower()
    if existing_account_code:
        acc = db.execute(
            select(Account).where(Account.code == existing_account_code.strip())
        ).scalar_one_or_none()
        if acc:
            return acc.code, False
    from app.services.account_resolver import _ensure_account
    code = _next_bank_account_code(db, loc)
    _ensure_account(db, code, f"{name} — bank account", loc)
    return code, True


def _attach_bank_account(
    db: Session, name: str, existing_account_code: str | None, locale: str | None,
) -> str:
    code, _created = _resolve_bank_account(db, name, existing_account_code, locale)
    return code

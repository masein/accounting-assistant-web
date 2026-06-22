"""Provisioning service: create a company, seed its own isolated chart of
accounts + defaults, and create its single login. Super-admin only."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import hash_password
from app.db.seed import seed_chart_if_empty
from app.db.tenant import tenant_bypass, use_company
from app.models.company import Company
from app.models.user import User

SUPPORTED_LOCALES = {"uk", "ir", "default"}


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "company"


def _unique_slug(db: Session, base: str) -> str:
    slug = base
    i = 2
    while db.execute(select(Company).where(Company.slug == slug)).scalars().first():
        slug = f"{base}-{i}"
        i += 1
    return slug


def seed_company_books(db: Session, company: Company) -> int:
    """Seed a company's own chart of accounts + tax rates + defaults. Runs in
    the company's tenant context so every seeded row is stamped to it."""
    locale = company.locale if company.locale in SUPPORTED_LOCALES else "default"
    seed_locale = "uk" if locale == "uk" else ("ir" if locale == "ir" else "ir")
    with use_company(company.id):
        n = seed_chart_if_empty(db, locale=seed_locale)
        try:
            from app.services.tax_rate_service import seed_tax_rates
            seed_tax_rates(db)
        except Exception:
            pass
        db.flush()
    return n


def provision_company(
    db: Session,
    *,
    name: str,
    locale: str,
    base_currency: str,
    username: str,
    password: str,
) -> tuple[Company, User]:
    """Create a company + its single login + its seeded books. Raises
    ValueError on a duplicate username (globally unique) or bad input."""
    name = (name or "").strip()
    username = (username or "").strip()
    locale = (locale or "default").strip().lower()
    base_currency = (base_currency or "").strip().upper() or "IRR"
    if not name:
        raise ValueError("Company name is required")
    if not username:
        raise ValueError("Login username is required")
    if locale not in SUPPORTED_LOCALES:
        raise ValueError(f"Unsupported locale '{locale}'")

    with tenant_bypass():
        if db.execute(select(User).where(User.username == username)).scalars().first():
            raise ValueError("Username already exists")
        company = Company(
            name=name,
            slug=_unique_slug(db, slugify(name)),
            locale=locale,
            base_currency=base_currency,
            status="active",
        )
        db.add(company)
        db.flush()

        pw_hash, pw_salt = hash_password(password)
        user = User(
            username=username,
            password_hash=pw_hash,
            password_salt=pw_salt,
            is_admin=True,          # company-level admin of their own books
            is_superadmin=False,
            company_id=company.id,
            is_active=True,
        )
        db.add(user)
        db.flush()

    seed_company_books(db, company)
    db.commit()
    return company, user

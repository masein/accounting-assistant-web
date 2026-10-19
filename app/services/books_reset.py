"""A company's books cleared, to import them again (platform admin, scenarios AI1–AI6).

Two schools' books had been imported before the journal importer kept their
own codes and made one heading per ledger, and a misread chat import had left
one-sided vouchers in one of them. The owner chose to clear both and import
the files again (2026-10-19). Nothing like it existed: a company could only be
suspended, and a login can't move to another company.

``summary`` lists what the company holds and what would go — nothing changes.
``reset`` empties every per-company table of the books (entries, accounts,
parties, statements, imports, invoices, payroll, chats, budgets, …) and seeds
the chart again. It keeps what is the company rather than its books: the
company, its logins, its profile, its configuration (calendar, currency, AI,
mail and bots, thresholds), its API keys and devices, its tax and exchange
rates, its AI spending and its audit trail (append-only, and the record of
this reset). The settings that name the old accounts go with them: the
journal import's account map, the posting roles and the closed period (which
would hold back the very years imported again).
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

from sqlalchemy import delete, func, select, true
from sqlalchemy.orm import Session

# What a company is, not its books: never emptied.
KEEP_TABLES = frozenset({
    "users", "audit_logs", "company_profiles", "digest_settings", "api_keys", "push_subscriptions",
    "messenger_links", "household_invites", "ai_usage_events", "tax_rates", "exchange_rates", "app_settings",
})
# The settings that name the old books' accounts or lock their years.
BOOK_SETTINGS = ("journal_import_account_map", "posting_roles", "closed_period")


def _tables():
    import importlib
    import pkgutil

    import app.models as models
    for mod in pkgutil.iter_modules(models.__path__):
        importlib.import_module(f"app.models.{mod.name}")
    from app.db.base import Base
    return [t for t in reversed(Base.metadata.sorted_tables)
            if "company_id" in t.c and t.name not in KEEP_TABLES]


def _of(table, company_id):
    """A company's rows; no company (a single-company install's tooling): all of them."""
    return (table.c.company_id == company_id) if company_id is not None else true()


def summary(db: Session, company_id: uuid.UUID | None) -> dict[str, Any]:
    """What would go: each table the company has rows in, with how many."""
    from app.db.tenant import tenant_bypass
    from app.models.app_setting import AppSetting
    counts: dict[str, int] = {}
    with tenant_bypass():
        for table in _tables():
            n = db.execute(select(func.count()).select_from(table).where(_of(table, company_id))).scalar()
            if n:
                counts[table.name] = int(n)
        settings = list(db.execute(select(AppSetting.key).where(_of(AppSetting.__table__, company_id),
                                                                AppSetting.key.in_(BOOK_SETTINGS))).scalars())
    return {"tables": counts, "settings": sorted(settings), "rows": sum(counts.values())}


def wipe(db: Session, company_id: uuid.UUID | None) -> tuple[dict[str, Any], list[str]]:
    """Delete the company's books (every table but ``KEEP_TABLES``, and its
    ``BOOK_SETTINGS``); flushed, not committed. Returns (what went, the
    attachment files to remove once committed). ``None``: a single-company
    install's tooling, every row (the caller refuses a platform admin that)."""
    from app.db.tenant import tenant_bypass
    from app.models.app_setting import AppSetting
    from app.models.company import Company
    from app.models.transaction import TransactionAttachment
    before = summary(db, company_id)
    with tenant_bypass():
        files = [p for (p,) in db.execute(select(TransactionAttachment.file_path)
                                          .where(_of(TransactionAttachment.__table__, company_id))).all() if p]
        for table in _tables():
            db.execute(delete(table).where(_of(table, company_id)))
        db.execute(delete(AppSetting).where(_of(AppSetting.__table__, company_id), AppSetting.key.in_(BOOK_SETTINGS)))
        # registered capital is the sum of the (now gone) contributions
        company = db.get(Company, company_id) if company_id is not None else None
        if company is not None:
            company.registered_capital = 0
        db.flush()
    db.info.pop(("posting_roles", str(company_id)), None)              # the memo of the old roles
    return before, files


def remove_files(files: list[str]) -> int:
    removed = 0
    for path in files:
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def reset(db: Session, company_id: uuid.UUID, *, actor_user_id: str | None, actor_username: str | None) -> dict[str, Any]:
    """Empty the company's books and seed its chart again, as a new company's.
    One transaction; the attachment files go from disk after it commits."""
    from app.db.tenant import tenant_bypass
    from app.models.company import Company
    from app.services.audit_service import log_audit_event
    from app.services.company_service import seed_company_books

    with tenant_bypass():
        company = db.get(Company, company_id)
    if company is None:
        raise ValueError("no such company")
    before, files = wipe(db, company_id)
    seeded = seed_company_books(db, company)                          # its chart, its rates
    log_audit_event(db, action="reset_books", entity_type="company", entity_id=str(company_id),
                    user_id=actor_user_id, username=actor_username,
                    detail=json.dumps({"removed": before["tables"], "settings": before["settings"],
                                       "accounts_seeded": seeded}, ensure_ascii=False))
    db.commit()
    removed_files = remove_files(files)
    try:
        from app.api.reports import invalidate_dashboard_cache
        invalidate_dashboard_cache()
    except Exception:  # noqa: BLE001 — a cache, best effort
        pass
    return {"removed": before["tables"], "rows": before["rows"], "settings": before["settings"],
            "accounts_seeded": seeded, "files_removed": removed_files}

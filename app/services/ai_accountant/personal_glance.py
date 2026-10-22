"""A person's books at a glance, for the assistant's instructions (scenarios AY3–AY5, 2026-10-22).

The eval set with a person's books found the model failing to find what was there: "my green wallet"
was not «کیف پول سبز», power adapters went under dining out, «چه دسته‌بندی‌هایی داری؟» took nine searches
and missed «سفر», and Amin, who isn't in the books, stopped the cards for a question. A person's chart is
small, so the instructions now list it: each place with its currency, the categories, the people.
It is the same for every message until the books change, so a provider's prompt cache serves it.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.entity import Entity
from app.models.transaction import TransactionLine

MAX_CATEGORIES = 60
MAX_PEOPLE = 40


def categories(db: Session) -> dict[str, list[Account]]:
    """The categories money is spent and earned in: the chart's active accounts with nothing under them,
    by kind (``EXPENSE`` / ``REVENUE``), in code order."""
    from app.services.reporting.common import EXPENSE, REVENUE, classify_account_code
    accounts = db.execute(select(Account).order_by(Account.code)).scalars().all()
    parents = {a.parent_id for a in accounts if a.parent_id}
    out: dict[str, list[Account]] = {EXPENSE: [], REVENUE: []}
    for a in accounts:
        if a.id in parents or not getattr(a, "is_active", True):
            continue
        kind = classify_account_code(a.code)
        if kind in out:
            out[kind].append(a)
    return out


def places(db: Session) -> list[tuple[Account, str, str]]:
    """Where the person's money is: (account, "cash" or "bank", currency). A heading with places under it
    is left out unless entries were made on it."""
    from app.services.ai_accountant.cash_tools import cash_account_codes
    from app.services.fx_base import base_currency
    from app.services.locale_service import get_reporting_locale
    from app.services.personal_goals import account_currencies

    codes = set(cash_account_codes(db, locale=get_reporting_locale(db), mode="personal"))
    accounts = [a for a in db.execute(select(Account).where(Account.code.in_(codes)).order_by(Account.code))
                .scalars().all() if getattr(a, "is_active", True)]
    parents = {a.parent_id for a in accounts if a.parent_id}
    used = set(db.execute(select(TransactionLine.account_id).where(
        TransactionLine.account_id.in_([a.id for a in accounts])).distinct()).scalars().all())
    kept = [a for a in accounts if a.id not in parents or a.id in used]
    ccy = account_currencies(db, {a.code for a in kept}, base_currency(db))
    return [(a, "cash" if a.code.startswith("112") else "bank", ccy[a.code]) for a in kept]


def people(db: Session) -> list[Entity]:
    """The people (and shops, landlords…) in the books; banks are places, not people."""
    return list(db.execute(select(Entity).where(Entity.type != "bank").order_by(Entity.name)
                           .limit(MAX_PEOPLE)).scalars().all())


def _names(accounts: list[Account]) -> str:
    shown = "; ".join(f"{a.code} «{a.name}»" for a in accounts[:MAX_CATEGORIES])
    return shown + ("; …" if len(accounts) > MAX_CATEGORIES else "") if shown else "none"


def glance_block(db: Session) -> str:
    """The lines for the instructions; empty if the books can't be read."""
    from app.services.reporting.common import EXPENSE, REVENUE
    cats = categories(db)
    where = "; ".join(f"{a.code} «{a.name}» ({kind}, {ccy})" for a, kind, ccy in places(db)) or "none yet"
    who = "; ".join(f"«{e.name}» (entity_id {e.id})" for e in people(db)) or "none yet"
    return (
        "\n\n# YOUR BOOKS AT A GLANCE — this person's own places, categories and people, read from the books\n"
        f"Places the money is in: {where}.\n"
        f"Spending categories: {_names(cats[EXPENSE])}.\n"
        f"Income categories: {_names(cats[REVENUE])}.\n"
        f"People in the books: {who}.\n"
        "Take a place, a category or a person from these lists without searching for it, and name it as it is "
        "written here. The user names them loosely and in any language: \"my green wallet\" is «کیف پول سبز», "
        "\"the card\" is their bank account. Someone the user names who is not among these people is new: put "
        "them in ``new_entities`` on each of their cards (the first one confirmed adds them, the others link to "
        "them) — never stop to ask whether to add them. Use ``search_accounts`` for anything not listed.")

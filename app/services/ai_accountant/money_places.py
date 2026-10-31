"""A person's money where they keep it, in the currency it's in (scenarios AB1–AB6).

From the owner's phone: «۷۷.۵ ریال عمان توی کیف پول سبز داریم» in books that
count in Iranian rials was refused, "change your books' currency first". A
person keeps several currencies in several places (rials in a bank at home,
Omani rials in a wallet, dollars in a vault) and wants each kept as it is, not
exchanged. So:

* ``propose_money_place``: money already in a place, a wallet, a vault, a bank
  account, a card. The place is an account of its own, made under cash on hand
  (1120) or the bank account (1110) the first time; its starting balance is an
  entry in the money's own currency against Opening net worth (3110). No rate
  is needed: an entry keeps its own currency, and reads in it.
* ``propose_books_currency``: count the books in another currency instead,
  while they have no entries yet (app/services/books_currency.py).
"""
from __future__ import annotations

import re
from datetime import date as _date
from types import SimpleNamespace
from typing import Any, Literal

from app.services.ai_accountant.base import Replaceable
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.money import Money, grouped, plain

from .base import BaseTool, ToolContext, ToolError
from .time_tools import _register

CASH, BANK, OPENING = "1120", "1110", "3110"


def _fold(s: str) -> str:
    from app.utils.text import fold_fa
    return " ".join(fold_fa((s or "").lower()).split())


def find_place(db, name: str, parent_code: str):
    """The place already in the chart: an account under ``parent_code`` with this name."""
    from app.models.account import Account
    parent = db.execute(select(Account).where(Account.code == parent_code)).scalars().first()
    if parent is None:
        return None, None
    want = _fold(name)
    for acc in db.execute(select(Account).where(Account.parent_id == parent.id)).scalars():
        if _fold(acc.name) == want:
            return parent, acc
    return parent, None


class ProposeMoneyPlaceInput(Replaceable):
    place: str = Field(..., min_length=1, max_length=120,
                       description="What the person calls the place, as they said it: «کیف پول سبز», «گاوصندوق», «حساب ملت», 'blue wallet'.")
    kind: Literal["cash", "bank"] = Field(..., description="cash: a wallet, purse, pocket, vault, safe or box of notes. bank: a bank account or a card.")
    currency: str = Field(..., min_length=3, max_length=3, description="The money's own currency (OMR for Omani rials and baisa, USD for dollars…), not the books'.")
    amount: Money = Field(..., ge=0, description="What is there now, in that currency with its own places: 77.5 Omani rials is 77.500.")
    date: _date | None = Field(None, description="As of when; defaults to today.")


class ProposeMoneyPlace(BaseTool):
    name = "propose_money_place"
    category = "proposal"
    description = (
        "Money the person already has in a place — a wallet, a vault, a bank account, a card — in the currency it "
        "is in, kept as it is (never converted). Makes the place an account of its own the first time (under cash "
        "on hand or the bank account) and records what is there as its starting balance. Use for «۷۷.۵ ریال عمان "
        "توی کیف پول سبز داریم», 'I have 500 dollars in the safe', «۳ میلیون تو حساب ملت دارم». Never for money a "
        "person gave, lent or paid («امیر به من داد»): that is a propose_create_transaction card with them. Returns a confirm card."
    )
    InputSchema = ProposeMoneyPlaceInput

    async def run(self, ctx: ToolContext, args: ProposeMoneyPlaceInput) -> dict[str, Any]:
        from app.core.currencies import name as currency_name
        from app.core.money import check_places
        from app.services.chart_service import suggest_code
        ccy = args.currency.strip().upper()
        try:
            check_places(args.amount, ccy)
        except ValueError as e:
            raise ToolError(str(e), code="invalid_amount") from e
        # «امیر به من ۶۰۰ ریال عمان دیگه داد، گذاشتم توی کیف پول سبز» came here as the wallet's starting balance
        # (eval, 2026-10-25, scenario BC1): money a person gave or lent is between the user and that person
        giver = _person_named(ctx)
        if giver:
            raise ToolError(
                f"This money came from {giver}, so it isn't a starting balance: propose_create_transaction, Dr the "
                f"place / Cr 2130 «بدهی به دیگران» with {giver} on it when {giver} lent or gave it to be paid back "
                f"(the user then owes {giver} more), or Cr an income category for a gift or pay. Money the user gave "
                f"{giver} is Dr 1150 «طلب از دیگران» / Cr the place. Not a question for the user.",
                code="money_from_person")
        parent_code = CASH if args.kind == "cash" else BANK
        parent, existing = find_place(ctx.db, args.place, parent_code)
        if parent is None:
            raise ToolError(f"These books have no account {parent_code} to keep the place under.", code="no_parent")
        if existing is not None and _has_entries(ctx.db, existing.id):
            # a second starting balance would count the money twice: say what it holds, ask where the extra came from
            # «دو تا مبدل برق خریدم ۲ ریال عمان از کیف پول سبز» came here as money in the wallet, and the
            # user was asked where the extra 2 rials came from (eval, 2026-10-22, scenario AZ3)
            raise ToolError(
                f"{existing.name} ({existing.code}) is already in the books with its starting balance. Money "
                f"spent or paid FROM it («خریدم», «دادم», «پرداخت», 'paid', 'bought') is not this: propose it with "
                f"propose_create_transaction, Dr the category / Cr {existing.code}. Only money that came INTO it "
                "needs a source: then ask where it came from (income, a transfer from another place…).",
                code="already_started")
        code = existing.code if existing is not None else suggest_code(ctx.db, parent)
        on = args.date or _date.today()
        description = f"{args.place.strip()} — موجودی اولیه" if _persian(args.place) else f"{args.place.strip()} — starting balance"
        payload = {"place": args.place.strip(), "kind": args.kind, "parent_code": parent_code, "account_code": code,
                   "new_account": existing is None, "currency": ccy, "amount": plain(args.amount), "date": on.isoformat(),
                   "description": description,
                   "lines": [{"account_code": code, "debit": plain(args.amount), "credit": 0},
                             {"account_code": OPENING, "debit": 0, "credit": plain(args.amount)}]}
        token = _register(ctx, self.name, payload)
        summary = (f"{args.place.strip()}: {grouped(args.amount)} {ccy} ({currency_name(ccy)}), its starting balance, "
                   f"kept in {ccy}" + (f"; a new place {code} under {parent.name}." if existing is None else "."))
        return {"confirmation_token": str(token), "status": "pending", "summary": summary, "preview": payload}


# money passing between the user and someone: «داد», «قرض», "lent me"
_GAVE = re.compile(r"داد|قرض|پس\s*گرفت|\b(lent|lend|gave|give|sent|paid|loan|borrow\w*)\b", re.IGNORECASE)


def _person_named(ctx) -> str | None:
    """A person of the books named in a message about money passing between them and the user («امیر به من …
    داد», "Amir lent me…"), or None."""
    from app.models.entity import Entity
    said = f" {_fold(ctx.user_message or '')} "
    if not _GAVE.search(said):
        return None
    for ent in ctx.db.execute(select(Entity).where(Entity.type != "bank")).scalars():
        name = _fold(ent.name)
        if name and f" {name} " in said.replace("،", " ").replace(",", " "):
            return ent.name
    return None


class ProposeRenamePlaceInput(BaseModel):
    place: str = Field(..., min_length=1, max_length=120,
                       description="The place as it is called now, or as the person calls it, or its code: «کیف پول سبز», 112001.")
    new_name: str = Field(..., min_length=1, max_length=120, description="What it should be called: «کیف پول قشنگم».")


def _places(db) -> list:
    """Every place money is kept: the accounts under cash on hand and the bank account."""
    from app.models.account import Account
    parents = db.execute(select(Account.id).where(Account.code.in_((CASH, BANK)))).scalars().all()
    if not parents:
        return []
    return db.execute(select(Account).where(Account.parent_id.in_(parents)).order_by(Account.code)).scalars().all()


def resolve_place(db, said: str):
    """The place ``said`` names: by its code, its name, or the words the person
    was remembered calling it by (an earlier «call it کیف پول قشنگم», kept only
    as a wording before places could be renamed — scenario AK6)."""
    places = _places(db)
    want = _fold(said)
    for acc in places:
        if want in (_fold(acc.code), _fold(acc.name)):
            return acc
    from app.services.learned_preferences import lookup
    learned = lookup(db, said, want="account")
    if learned is not None and learned.score >= 0.99:
        return next((a for a in places if a.code == learned.preference.account_code), None)
    return None


class ProposeRenamePlace(BaseTool):
    name = "propose_rename_place"
    category = "proposal"
    description = (
        "Call a place money is kept — a wallet, a vault, a bank account, a card — by another name: «اسم کیف پول سبز رو "
        "بکن کیف پول قشنگم», 'call my blue wallet the travel wallet'. Renames the account itself, keeping its entries "
        "and balance; every card and report then shows the new name. Returns a confirm card. Use this, never "
        "propose_remember_preference, to rename a place."
    )
    InputSchema = ProposeRenamePlaceInput

    async def run(self, ctx: ToolContext, args: ProposeRenamePlaceInput) -> dict[str, Any]:
        acc = resolve_place(ctx.db, args.place)
        if acc is None:
            known = "; ".join(f"{a.code} {a.name}" for a in _places(ctx.db)) or "none yet"
            raise ToolError(f"No place called {args.place!r}. The places are: {known}. Ask which one.", code="no_place")
        new = " ".join(args.new_name.split())
        if _fold(new) == _fold(acc.name):
            raise ToolError(f"{acc.code} is already called {acc.name}.", code="same_name")
        if any(_fold(a.name) == _fold(new) for a in _places(ctx.db) if a.id != acc.id):
            raise ToolError(f"Another place is already called {new}: ask whether it's that one.", code="name_taken")
        arrow = " ← " if _persian(acc.name + new) else " → "
        payload = {"account_code": acc.code, "old_name": acc.name, "new_name": new,
                   "description": f"{acc.name}{arrow}{new}"}
        token = _register(ctx, self.name, payload)
        return {"confirmation_token": str(token), "status": "pending", "preview": payload,
                "summary": f"Rename {acc.code} {acc.name} → {new}; its entries and balance stay as they are."}


class MoneyBack(BaseModel):
    amount: Money = Field(..., gt=0, description="What came back, in the place's currency: 9.75.")
    place: str = Field(..., min_length=1, max_length=120, description="Where it was put: «کیف پول سبز».")


class ProposeEmptyPlaceInput(BaseModel):
    place: str = Field(..., min_length=1, max_length=120,
                       description="The place everything left: «پاکت توی کشو», its name or code.")
    back: list[MoneyBack] = Field(default_factory=list, max_length=5,
                                  description="Money that came back and where it went; none when nothing did.")
    rest_account: str | None = Field(None, max_length=64,
                                     description="The category the rest was spent on (its code from YOUR BOOKS AT A "
                                                 "GLANCE, 6110 for groceries). Not needed when the money came back whole.")
    currency: str | None = Field(None, min_length=3, max_length=3,
                                 description="Only for a place that holds several currencies: which one left.")
    description: str | None = Field(None, max_length=255, description="What it was, in the user's words.")
    date: _date | None = Field(None, description="When; defaults to today.")


class ProposeEmptyPlace(BaseTool):
    name = "propose_empty_place"
    category = "proposal"
    description = (
        "Everything in a place left it: «هرچی توی پاکت توی کشو بود دادم به همسرم، … ۱۵.۳ برگردوند که گذاشتم توی کیف "
        "پول سبز». Reads the place's balance itself and makes ONE card: what came back to where it went, the rest "
        "under rest_account, the place at zero. Use it instead of working out the rest yourself. Returns a "
        "confirm card."
    )
    InputSchema = ProposeEmptyPlaceInput

    async def run(self, ctx: ToolContext, args: ProposeEmptyPlaceInput) -> dict[str, Any]:
        from app.models.account import Account
        from app.services.ai_accountant.fast_paths import _in_currency
        src = resolve_place(ctx.db, args.place)
        if src is None:
            known = "; ".join(f"{a.code} {a.name}" for a in _places(ctx.db)) or "none yet"
            raise ToolError(f"No place called {args.place!r}. The places are: {known}. Ask which one.", code="no_place")
        held = _held(ctx.db, src.code)
        ccy = (args.currency or "").strip().upper() or (next(iter(held)) if len(held) == 1 else None)
        if ccy is None:
            raise ToolError(f"{src.name} holds {', '.join(sorted(held)) or 'nothing'}: say which currency left it.",
                            code="which_currency")
        balance = held.get(ccy, 0)
        if balance <= 0:
            raise ToolError(f"{src.name} holds no {ccy} to give: nothing to empty.", code="nothing_in_it")
        back, given = [], 0
        for b in args.back:
            to = resolve_place(ctx.db, b.place)
            if to is None or to.id == src.id:
                known = "; ".join(f"{a.code} {a.name}" for a in _places(ctx.db) if a.id != src.id)
                raise ToolError(f"No other place called {b.place!r}. The places are: {known}.", code="no_place")
            back.append((to, plain(b.amount)))
            given += plain(b.amount)
        rest = plain(balance - given)
        if rest < 0:
            raise ToolError(f"{src.name} held {grouped(balance)} {ccy}: {grouped(given)} can't have come back. Ask "
                            "what came back.", code="more_than_held")
        spent = None
        if rest > 0:
            code = (args.rest_account or "").strip()
            spent = ctx.db.execute(select(Account).where(Account.code == code)).scalars().first() if code else None
            if spent is None and code:
                spent = next((a for a in ctx.db.execute(select(Account)).scalars() if _fold(a.name) == _fold(code)), None)
            if spent is None:
                raise ToolError(f"{grouped(rest)} {ccy} of it was spent: on what? Give rest_account (a category's code), "
                                "or ask the user once.", code="rest_account")
        lines = ([{"account_code": spent.code, "debit": rest, "credit": 0}] if spent is not None else []) + \
                [{"account_code": to.code, "debit": amt, "credit": 0} for to, amt in back] + \
                [{"account_code": src.code, "debit": 0, "credit": plain(balance)}]
        persian = _persian((ctx.user_message or "") + src.name)
        lang = "fa" if persian else "en"
        parts = ([f"{_in_currency(rest, ccy, lang)} {spent.name}"] if spent is not None else []) + \
                [f"{_in_currency(amt, ccy, lang)} {'به' if persian else 'to'} {to.name}" for to, amt in back]
        sep = "، " if persian else ", "
        description = args.description or (f"{src.name} خالی شد: " if persian else f"{src.name} emptied: ") + sep.join(parts)
        payload = {"place": src.name, "account_code": src.code, "currency": ccy, "balance": plain(balance),
                   "rest": rest, "amount": plain(balance), "date": (args.date or _date.today()).isoformat(),
                   "description": description[:255], "lines": lines}
        token = _register(ctx, self.name, payload)
        return {"confirmation_token": str(token), "status": "pending", "preview": payload,
                "summary": f"{src.code} {src.name} held {grouped(balance)} {ccy}; all of it leaves: " + "; ".join(parts) + "."}


def _held(db, code: str) -> dict:
    """What a place holds, currency by currency, from its entries."""
    from sqlalchemy import func

    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    from app.services.fx_base import base_currency
    base = base_currency(db) or "IRR"
    out: dict = {}
    for ccy, d, c in db.execute(
            select(Transaction.currency, func.coalesce(func.sum(TransactionLine.debit), 0),
                   func.coalesce(func.sum(TransactionLine.credit), 0))
            .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
            .join(Account, Account.id == TransactionLine.account_id)
            .where(Account.code == code, Transaction.deleted_at.is_(None))
            .group_by(Transaction.currency)).all():
        key = (ccy or base).strip().upper()
        out[key] = plain(out.get(key, 0) + plain(d or 0) - plain(c or 0))
    return {k: v for k, v in out.items() if v}


EXCHANGE = "3120"
EXCHANGE_NAME = {"fa": "تبدیل ارز", "en": "Currency exchange"}


class ProposeCurrencyExchangeInput(BaseModel):
    from_place: str = Field(..., min_length=1, max_length=120,
                            description="Where the money that was changed came from: its name or code («کیف پول والای», 112005).")
    from_currency: str = Field(..., min_length=3, max_length=3, description="The currency given: USD for «دلار».")
    from_amount: Money = Field(..., gt=0, description="How much was given, in that currency: 1900.")
    to_currency: str = Field(..., min_length=3, max_length=3, description="The currency received: OMR for «ریال عمان».")
    to_amount: Money = Field(..., gt=0, description="How much came back, in that currency with its places: 702.200.")
    to_place: str | None = Field(None, max_length=120,
                                 description="Where the money received went, if not the same place.")
    date: _date | None = Field(None, description="When; defaults to today.")


class ProposeCurrencyExchange(BaseTool):
    name = "propose_currency_exchange"
    category = "proposal"
    description = (
        "Money changed from one currency to another — at an exchange office, a bank, a friend: «۱۹۰۰ دلار رو چنج "
        "کردم شد ۷۰۲.۲ ریال عمان», 'I changed 500 dollars into 192 OMR'. The amount given leaves its place in its "
        "currency and the amount received arrives in its own (the same place unless said), both as they were said; "
        "nothing is converted at any other rate. Spending doesn't change. One card for the exchange, beside the "
        "cards for whatever was then paid with the money. Returns a confirm card."
    )
    InputSchema = ProposeCurrencyExchangeInput

    async def run(self, ctx: ToolContext, args: ProposeCurrencyExchangeInput) -> dict[str, Any]:
        from app.core.money import check_places
        from app.services.ai_accountant.fast_paths import _in_currency
        give, get = args.from_currency.strip().upper(), args.to_currency.strip().upper()
        if give == get:
            raise ToolError("Both sides are in one currency: that is money moved between places, a "
                            "propose_create_transaction card.", code="same_currency")
        for amount, ccy in ((args.from_amount, give), (args.to_amount, get)):
            try:
                check_places(amount, ccy)
            except ValueError as e:
                raise ToolError(str(e), code="invalid_amount") from e
        source = resolve_place(ctx.db, args.from_place)
        target = resolve_place(ctx.db, args.to_place) if args.to_place else source
        if source is None or target is None:
            known = "; ".join(f"{a.code} {a.name}" for a in _places(ctx.db)) or "none yet"
            missing = args.from_place if source is None else args.to_place
            raise ToolError(f"No place called {missing!r}. The places are: {known}. Ask which one.", code="no_place")
        persian = _persian((ctx.user_message or "") + source.name)
        lang = "fa" if persian else "en"
        gave, got = _in_currency(args.from_amount, give, lang), _in_currency(args.to_amount, get, lang)
        where = source.name if target.id == source.id else f"{source.name} → {target.name}"
        description = (f"تبدیل {gave} به {got} — {where}" if persian else f"Changed {gave} into {got} — {where}")
        rate = plain(args.to_amount) / plain(args.from_amount)
        on = args.date or _date.today()
        payload = {"from_code": source.code, "from_name": source.name, "from_currency": give,
                   "from_amount": plain(args.from_amount), "to_code": target.code, "to_name": target.name,
                   "to_currency": get, "to_amount": plain(args.to_amount), "rate": f"{rate:.6g}",
                   "date": on.isoformat(), "description": description,
                   # the card: what was given, the title says what came back
                   "currency": give, "amount": plain(args.from_amount)}
        token = _register(ctx, self.name, payload)
        summary = (f"{source.code} {source.name}: {grouped(args.from_amount)} {give} out, "
                   f"{target.code} {target.name}: {grouped(args.to_amount)} {get} in (1 {give} = {rate:.6g} {get}, "
                   f"as given); through {EXCHANGE} «{EXCHANGE_NAME['fa']}», one entry in each currency.")
        return {"confirmation_token": str(token), "status": "pending", "summary": summary, "preview": payload}


def pending_exchanges_into(db, session_id, code: str, currency: str) -> bool:
    """An exchange card of this conversation, not yet confirmed, that brings ``currency`` into ``code``: a
    payment from it in the same message («…که ازش ۲۰۰ ریال دادم») is fine (scenario AZ2)."""
    from app.models.ai_accountant import AIProposal
    if not session_id:
        return False
    for row in db.execute(select(AIProposal).where(AIProposal.session_id == str(session_id),
                                                   AIProposal.tool_name == "propose_currency_exchange",
                                                   AIProposal.status.in_(("pending", "executed")))).scalars():
        p = row.tool_input or {}
        if p.get("to_code") == code and (p.get("to_currency") or "").upper() == currency:
            return True
    return False


def _exchange_account(db):
    """«تبدیل ارز» beside «دارایی خالص اولیه»: made the first time money is changed in books kept before it."""
    from app.models.account import Account
    acc = db.execute(select(Account).where(Account.code == EXCHANGE)).scalars().first()
    if acc is not None:
        return acc
    from app.models.account import AccountLevel
    opening = db.execute(select(Account).where(Account.code == OPENING)).scalars().first()
    name = EXCHANGE_NAME["fa"] if opening is None or _persian(opening.name) else EXCHANGE_NAME["en"]
    acc = Account(code=EXCHANGE, name=name, level=opening.level if opening else AccountLevel.GENERAL,
                  parent_id=opening.parent_id if opening else None, is_active=True)
    db.add(acc)
    db.flush()
    return acc


def _exchange(db, proposal, p: dict, common: dict) -> tuple[str, str]:
    """Two entries, one in each currency, through «تبدیل ارز»: the money given leaves its place, the money
    received arrives in its own; neither is worth the other in the books (docs/PLAN_CURRENCIES.md)."""
    from app.schemas.transaction import TransactionCreate, TransactionLineCreate
    from app.services.ai_accountant.time_execute import _audit
    from app.services.ledger_posting import create_transaction_from_payload
    _exchange_account(db)
    on, desc = _date.fromisoformat(p["date"]), p["description"]
    ref = f"FX-{str(proposal.confirmation_token)[:8]}"
    give, get = plain(p["from_amount"]), plain(p["to_amount"])
    out_ = create_transaction_from_payload(db, TransactionCreate(
        date=on, description=desc, currency=p["from_currency"], reference=ref,
        lines=[TransactionLineCreate(account_code=EXCHANGE, debit=give, credit=0, line_description=desc),
               TransactionLineCreate(account_code=p["from_code"], debit=0, credit=give, line_description=desc)]))
    in_ = create_transaction_from_payload(db, TransactionCreate(
        date=on, description=desc, currency=p["to_currency"], reference=ref,
        lines=[TransactionLineCreate(account_code=p["to_code"], debit=get, credit=0, line_description=desc),
               TransactionLineCreate(account_code=EXCHANGE, debit=0, credit=get, line_description=desc)]))
    ids = [str(out_.id), str(in_.id)]
    audit_id = _audit(db, proposal, entity_type="currency_exchange", entity_id=ids[0],
                      detail={"transaction_ids": ids, "transaction_id": ids[0], "from": p["from_currency"],
                              "to": p["to_currency"], "rate": p.get("rate")}, **common)
    return ids[0], audit_id


def undo_exchange(db, audit, *, actor_user_id: str, actor_username: str | None, ip_address: str | None,
                  delete: bool, tool_name: str):
    """Both entries of an exchange taken back together (scenario AZ1)."""
    import json

    from app.services.ai_accountant.execute_service import undo_entries
    from app.services.book_text import bt
    ids = (json.loads(audit.detail or "{}") or {}).get("transaction_ids") or []
    result = undo_entries(db, audit, ids, actor_user_id=actor_user_id, actor_username=actor_username,
                          ip_address=ip_address, delete=delete, tool_name=tool_name,
                          describe=lambda txn: bt(db, "reversal_of", ref=txn.reference or ""))
    db.commit()
    return result


class ProposeBooksCurrencyInput(BaseModel):
    currency: str = Field(..., min_length=3, max_length=3, description="The currency to count the books in: OMR, AED, USD…")


class ProposeBooksCurrency(BaseTool):
    name = "propose_books_currency"
    category = "proposal"
    description = (
        "Count the person's books in another currency, while they have no entries yet: the person said their "
        "money is mostly in it («ریال عمان» in books that count in Iranian rials). Nothing is posted. Returns a "
        "confirm card; once the books have entries this is refused and each place keeps its own currency instead."
    )
    InputSchema = ProposeBooksCurrencyInput

    async def run(self, ctx: ToolContext, args: ProposeBooksCurrencyInput) -> dict[str, Any]:
        from app.core.currencies import NAMES, name as currency_name
        from app.db.tenant import get_current_company
        from app.services.books_currency import _company, _entries_in_other_currencies
        ccy = args.currency.strip().upper()
        if ccy not in NAMES:
            raise ToolError(f"Choose one of: {', '.join(NAMES)}.", code="unknown_currency")
        cid = get_current_company()
        c = _company(ctx.db, cid)
        if c is None or (c.kind or "") != "personal":
            raise ToolError("Only a person's own books choose their currency.", code="not_personal")
        if _entries_in_other_currencies(ctx.db, c.id):
            raise ToolError("These books already have entries, so their currency stays: keep the money in its own "
                            "currency with propose_money_place.", code="books_have_entries")
        old = (c.base_currency or "IRR").upper()
        payload = {"currency": ccy, "from": old, "description": f"{currency_name(old)} → {currency_name(ccy)}"}
        token = _register(ctx, self.name, payload)
        return {"confirmation_token": str(token), "status": "pending",
                "summary": f"Count the books in {currency_name(ccy)} ({ccy}) instead of {currency_name(old)} ({old}).",
                "preview": payload}


class AskWithChoicesInput(BaseModel):
    options: list[str] = Field(..., min_length=2, max_length=4,
                               description="The answers to tap, short, in the user's language: «دفاتر به ریال عمان», «ریال ایران بماند».")


class AskWithChoices(BaseTool):
    name = "ask_with_choices"
    category = "read"
    description = (
        "When a short question has two to four likely answers, offer them to tap under your words (the phone and "
        "the web show them as buttons; tapping sends the answer as if typed). Ask the question in your reply too. "
        "Nothing is changed."
    )
    InputSchema = AskWithChoicesInput

    async def run(self, ctx: ToolContext, args: AskWithChoicesInput) -> dict[str, Any]:
        return {"options": [o.strip()[:60] for o in args.options if o.strip()][:4]}


def _persian(s: str) -> bool:
    import re
    return bool(re.search(r"[؀-ۿ]", s or ""))


def _has_entries(db, account_id) -> bool:
    from app.models.transaction import Transaction, TransactionLine
    return db.execute(select(TransactionLine.id).join(Transaction, Transaction.id == TransactionLine.transaction_id)
                      .where(TransactionLine.account_id == account_id, Transaction.deleted_at.is_(None))
                      .limit(1)).first() is not None


def execute_money_proposal(db, proposal, *, actor_user_id: str, actor_username: str | None,
                           ip_address: str | None) -> tuple[str | None, str]:
    from app.schemas.transaction import TransactionCreate, TransactionLineCreate
    from app.services.ai_accountant.time_execute import _audit
    from app.services.chart_service import create_account
    from app.services.ledger_posting import create_transaction_from_payload
    p = dict(proposal.tool_input or {})
    common = dict(actor_user_id=actor_user_id, actor_username=actor_username, ip_address=ip_address)
    if proposal.tool_name == "propose_books_currency":
        from app.db.tenant import get_current_company
        from app.services.books_currency import change
        out = change(db, get_current_company(), p["currency"],
                     user=SimpleNamespace(user_id=actor_user_id, username=actor_username))
        audit_id = _audit(db, proposal, entity_type="company", entity_id=str(get_current_company()),
                          detail={"books_currency": out["currency"], "from": p.get("from")}, **common)
        return None, audit_id
    if proposal.tool_name == "propose_rename_place":
        return None, _rename_place(db, proposal, p, common)
    if proposal.tool_name == "propose_currency_exchange":
        return _exchange(db, proposal, p, common)
    if proposal.tool_name == "propose_empty_place":
        txn = create_transaction_from_payload(db, TransactionCreate(
            date=_date.fromisoformat(p["date"]), description=p["description"], currency=p["currency"],
            lines=[TransactionLineCreate(account_code=ln["account_code"], debit=plain(ln["debit"]),
                                         credit=plain(ln["credit"]), line_description=p["description"])
                   for ln in p["lines"]]))
        # a transaction's own audit row: the card's undo takes it back like any entry
        return str(txn.id), _audit(db, proposal, entity_type="transaction", entity_id=str(txn.id),
                                   detail={"emptied": p["account_code"], "currency": p["currency"],
                                           "balance": p["balance"]}, **common)
    # propose_money_place: the place (made now if it's new), then its starting balance in its own currency
    parent, acc = find_place(db, p["place"], p["parent_code"])
    if acc is None:
        acc = create_account(db, name=p["place"], parent_code=p["parent_code"])
    txn = None
    amount = plain(p["amount"])
    if amount:
        txn = create_transaction_from_payload(db, TransactionCreate(
            date=_date.fromisoformat(p["date"]), description=p["description"], currency=p["currency"],
            lines=[TransactionLineCreate(account_code=acc.code, debit=amount, credit=0, line_description=p["description"]),
                   TransactionLineCreate(account_code=OPENING, debit=0, credit=amount, line_description=p["description"])]))
    audit_id = _audit(db, proposal, entity_type="account", entity_id=str(acc.id),
                      detail={"place": acc.name, "code": acc.code, "currency": p["currency"], "amount": amount,
                              "transaction_id": str(txn.id) if txn else None}, **common)
    return (str(txn.id) if txn else None), audit_id


def _rename_place(db, proposal, p: dict, common: dict) -> str:
    """The account itself takes the new name; a bank party kept on it, called
    what the account was, is renamed with it (scenarios AK5, AK6)."""
    import json

    from app.models.account import Account
    from app.models.audit_log import AuditLog
    from app.models.entity import Entity
    from app.services.chart_service import rename
    acc = db.execute(select(Account).where(Account.code == p["account_code"])).scalars().first()
    if acc is None:
        raise ValueError(f"{p['account_code']} is no longer in the books.")
    old = acc.name
    rename(db, acc, name=p["new_name"])
    for party in db.execute(select(Entity).where(Entity.type == "bank", Entity.code == acc.code)).scalars():
        if _fold(party.name) == _fold(old):
            party.name = acc.name
    audit = AuditLog(action="update", entity_type="account", entity_id=str(acc.id),
                     user_id=common["actor_user_id"], username=common["actor_username"], ip_address=common["ip_address"],
                     actor_source="ai-assistant", session_id=proposal.session_id, tool_name=proposal.tool_name,
                     confirmation_token=proposal.confirmation_token, user_message=proposal.user_message,
                     detail=json.dumps({"code": acc.code, "old_name": old, "new_name": acc.name}, ensure_ascii=False))
    db.add(audit)
    db.flush()
    return str(audit.id)


def register_money_tools(registry) -> None:
    registry.register(ProposeMoneyPlace())
    registry.register(ProposeRenamePlace())
    registry.register(ProposeCurrencyExchange())
    registry.register(ProposeEmptyPlace())
    registry.register(ProposeBooksCurrency())
    registry.register(AskWithChoices())

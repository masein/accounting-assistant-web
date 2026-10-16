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
        "توی کیف پول سبز داریم», 'I have 500 dollars in the safe', «۳ میلیون تو حساب ملت دارم». Returns a confirm card."
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
        parent_code = CASH if args.kind == "cash" else BANK
        parent, existing = find_place(ctx.db, args.place, parent_code)
        if parent is None:
            raise ToolError(f"These books have no account {parent_code} to keep the place under.", code="no_parent")
        if existing is not None and _has_entries(ctx.db, existing.id):
            # a second starting balance would count the money twice: say what it holds, ask where the extra came from
            raise ToolError(
                f"{existing.name} ({existing.code}) is already in the books with its starting balance. Ask where "
                "the extra money came from (income, a transfer from another place…) and propose that instead.",
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


def register_money_tools(registry) -> None:
    registry.register(ProposeMoneyPlace())
    registry.register(ProposeBooksCurrency())
    registry.register(AskWithChoices())

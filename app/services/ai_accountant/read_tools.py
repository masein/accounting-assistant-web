"""Read-only tools exposed to Claude.

These never write to the ledger — they answer questions and surface
the data Claude needs to draft a proposal. Each tool validates its
input via Pydantic before touching the DB and returns a small,
structured dict that fits comfortably in Claude's context.

Tools in this file:

* ``find_entity`` — fuzzy + exact lookup with a ``confidence`` score
  per match. Returns up to 10 candidates so Claude can disambiguate.
* ``list_entities`` — paginated browse by type / filter.
* ``query_ledger`` — sums and lists over a date range / account /
  entity.
* ``get_account_balance`` — single account balance as of a date.
* ``get_company_defaults`` — currency, fiscal year, reporting locale.
"""
from __future__ import annotations

import difflib
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.app_setting import AppSetting
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionLine
from app.services.locale_service import get_display_calendar, get_reporting_locale
from app.services.reporting.common import (
    ASSET,
    EQUITY,
    EXPENSE,
    LIABILITY,
    REVENUE,
    balance_from_turnovers,
    classify_account_code,
)

from .base import BaseTool, ToolContext, ToolError
from app.core.money import plain


# Contact + bank fields shown to the model so questions like "what is this
# bank's IBAN / شبا?" answer from the record instead of "not recorded". Only
# non-empty fields are included to keep tool output small.
_ENTITY_DETAIL_FIELDS = (
    "phone", "email", "address", "tax_id", "economic_code", "national_id",
    "contact_person", "bank_name", "account_holder", "account_number",
    "iban", "sort_code",
)


def _entity_details(e: Entity) -> dict[str, Any]:
    # Bank and identity numbers follow the same rule as every other read.
    from app.core.redaction import IDENTITY_FIELDS, may_see_identity
    hidden = () if may_see_identity() else IDENTITY_FIELDS
    return {
        f: str(v).strip()
        for f in _ENTITY_DETAIL_FIELDS
        if f not in hidden and (v := getattr(e, f, None)) and str(v).strip()
    }


# ---------------------------------------------------------------------------
# find_entity
# ---------------------------------------------------------------------------


class FindEntityInput(BaseModel):
    query: str = Field(
        ...,
        description="The name (or partial name) to search for. Case-insensitive.",
        min_length=1,
    )
    type: Literal["client", "bank", "employee", "supplier"] | None = Field(
        None,
        description=(
            "Optional role filter. Omit to search across all entity types "
            "(useful when the user says 'Kim' without specifying whether "
            "Kim is the client or the employee)."
        ),
    )
    limit: int = Field(10, ge=1, le=25, description="Max candidates to return.")
    description: str | None = Field(
        None,
        description=(
            "The transaction's own wording (e.g. 'POS SNAPP TEHRAN', 'paid Kim for design'). "
            "Pass it: the party the user chose before for wording like this comes first, marked learned."
        ),
        max_length=512,
    )


# Words that name a kind of party, not the party: «بانک», "Ltd", «آقای».
_GENERIC_PARTY_WORDS = {"بانک", "bank", "شرکت", "company", "co", "co.", "ltd", "ltd.", "limited", "inc", "plc",
                        "موسسه", "مؤسسه", "آقای", "آقا", "خانم", "جناب", "سرکار", "mr", "mrs", "ms", "dr"}


def _party_words(text: str) -> set[str]:
    words = {w.strip(".,،()-«»\"'") for w in text.replace("\u200c", " ").split()}
    return {w for w in words if w and w not in _GENERIC_PARTY_WORDS}


class FindEntity(BaseTool):
    name = "find_entity"
    category = "read"
    description = (
        "Find clients, employees, suppliers or banks by name. Returns a ranked list "
        "of candidates with a confidence score in [0, 1]. Call this ONCE per name, "
        "then converge: if the top result has confidence ≥ 0.80 (or is the only "
        "match) use it; if several are plausible, list 2–3 and ask the user to pick; "
        "if the best is < 0.50, propose with NO entity link (entity links are "
        "optional). Do NOT call this tool again for the same name — re-searching "
        "wastes the turn budget and dead-ends the request."
    )
    InputSchema = FindEntityInput

    async def run(self, ctx: ToolContext, args: FindEntityInput) -> dict[str, Any]:
        q = args.query.strip()
        if not q:
            raise ToolError("query must be non-empty")

        # Pull all candidates the user could plausibly mean, then score
        # client-side. SQLite (used in tests) doesn't have trigram support;
        # difflib is portable and fast for small entity tables. The type is a
        # preference, not a filter: «ابر اروان» asked for as a supplier is the
        # client of that name, not "no such party" (AI QA, 2026-10-12).
        all_rows = ctx.db.execute(select(Entity)).scalars().all()
        scored: list[tuple[float, Entity]] = []
        from app.utils.text import fold_fa
        q_lower = fold_fa(q.lower())
        q_words = _party_words(q_lower)
        for row in all_rows:
            name = fold_fa(row.name.lower())
            ratio = difflib.SequenceMatcher(None, q_lower, name).ratio()
            # Boost prefix matches — typing "Kim" should rank "Kim Nguyen"
            # above "Joachim".
            if name.startswith(q_lower):
                ratio = min(1.0, ratio + 0.15)
            if q_lower in name:
                ratio = min(1.0, ratio + 0.05)
            # Every distinctive word of the query in the name, in any order:
            # «بانک آینده» is «آینده جردن» (it ranked «بانک دی فرمانیه» first on
            # the shared «بانک»), «علیرضا حیدری» is «حیدری علیرضا» (AI QA, 2026-10-12).
            n_words = _party_words(name)
            if q_words and n_words and q_words <= n_words:
                ratio = max(ratio, 0.9 + 0.1 * len(q_words) / len(n_words))
            scored.append((ratio, row))

        if args.type:
            # the asked-for type narrows; another type only when none of it matches and the name clearly does
            typed_best = max((sc for sc, e in scored if e.type == args.type), default=0.0)
            scored = [(sc if e.type == args.type else (sc - 0.1 if typed_best < 0.6 and sc >= 0.9 else 0.0), e)
                      for sc, e in scored]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        # The party the user chose before for this wording (roadmap §5.4).
        from app.services.learned_preferences import lookup, mark_used
        learned = lookup(ctx.db, args.description or q, want="entity")
        learned_entity = None
        if learned is not None:
            learned_entity = next((e for (_s, e) in scored if e.id == learned.preference.entity_id), None)
            if learned_entity is not None:
                scored = [(0.95, learned_entity)] + [x for x in scored if x[1].id != learned_entity.id]
                mark_used(ctx.db, learned.preference)
        top = scored[: args.limit]

        out = {
            "query": q,
            "matches": [
                {
                    "entity_id": str(e.id),
                    "name": e.name,
                    "type": e.type,
                    "code": e.code,
                    "confidence": round(score, 3),
                    **({"learned": True} if learned_entity is not None and e.id == learned_entity.id else {}),
                    **_entity_details(e),
                }
                for (score, e) in top
                if score >= 0.30  # filter pure-noise matches
            ],
        }
        if learned_entity is not None:
            out["learned_note"] = (f"The user chose {learned_entity.name} before for "
                                   f"\"{learned.preference.label}\".")
        return out


# ---------------------------------------------------------------------------
# list_entities
# ---------------------------------------------------------------------------


class ListEntitiesInput(BaseModel):
    type: Literal["client", "bank", "employee", "supplier"] | None = None
    limit: int = Field(50, ge=1, le=200)
    name_contains: str | None = Field(
        None,
        description="Optional case-insensitive substring filter.",
    )


class ListEntities(BaseTool):
    name = "list_entities"
    category = "read"
    description = (
        "List entities (clients, banks, employees, suppliers) for browsing. Use this "
        "when the user wants an overview (\"show me all our clients\", \"who do we "
        "owe money to\"). For looking up a specific entity by name, prefer "
        "``find_entity``."
    )
    InputSchema = ListEntitiesInput

    async def run(self, ctx: ToolContext, args: ListEntitiesInput) -> dict[str, Any]:
        stmt = select(Entity).order_by(Entity.name).limit(args.limit)
        if args.type:
            stmt = stmt.where(Entity.type == args.type)
        if args.name_contains:
            needle = f"%{args.name_contains.lower()}%"
            stmt = stmt.where(func.lower(Entity.name).like(needle))
        rows = ctx.db.execute(stmt).scalars().all()
        return {
            "count": len(rows),
            "entities": [
                {
                    "entity_id": str(e.id),
                    "name": e.name,
                    "type": e.type,
                    "code": e.code,
                    **_entity_details(e),
                }
                for e in rows
            ],
        }


# ---------------------------------------------------------------------------
# query_ledger
# ---------------------------------------------------------------------------


class QueryLedgerInput(BaseModel):
    from_date: date | None = Field(
        None, description="Inclusive lower bound. Defaults to the start of the current year."
    )
    to_date: date | None = Field(
        None, description="Inclusive upper bound. Defaults to today."
    )
    account_code: str | None = Field(
        None,
        description=(
            "Account code (or code prefix — e.g. '11' for all current assets, '61' "
            "for all SG&A). Omit to query every account."
        ),
    )
    entity_id: str | None = Field(
        None, description="Restrict to transactions linked to this entity."
    )
    description_contains: str | None = Field(
        None,
        description="Filter by case-insensitive substring of the transaction description.",
    )
    currency: str | None = Field(
        None,
        description=(
            "Omit for every currency together at its base-currency value (the "
            "company's reporting currency). Give a code (USD, EUR…) for entries in "
            "that currency only, in their own amounts."
        ),
    )
    group_by: Literal["none", "month", "account"] = Field(
        "none",
        description=(
            "Aggregation. 'none' returns up to 50 individual transactions; 'month' "
            "returns sums per calendar month; 'account' returns sums per account."
        ),
    )
    limit: int = Field(50, ge=1, le=200)


class QueryLedger(BaseTool):
    name = "query_ledger"
    category = "read"
    description = (
        "Query the general ledger. Answers \"how much did we spend on X?\", \"what's "
        "outstanding from Client Y?\", \"show me last 10 transactions on account 6110\". "
        "Returns sums + a small sample of underlying rows. Pure read — never modifies "
        "the books."
    )
    InputSchema = QueryLedgerInput

    async def run(self, ctx: ToolContext, args: QueryLedgerInput) -> dict[str, Any]:
        today = date.today()
        from_d = args.from_date or _year_bounds(ctx.db, of=today)[0]
        to_d = args.to_date or today

        # Build the line-level query joined to its transaction.
        stmt = (
            select(Transaction, TransactionLine, Account)
            .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
            .join(Account, Account.id == TransactionLine.account_id)
            .where(
                Transaction.deleted_at.is_(None),
                Transaction.date >= from_d,
                Transaction.date <= to_d,
            )
            .order_by(Transaction.date.desc(), Transaction.id)
        )

        if args.account_code:
            stmt = stmt.where(Account.code.like(f"{args.account_code}%"))
        ccy = (args.currency or "").strip().upper() or None
        if ccy:
            stmt = stmt.where(Transaction.currency == ccy)

        if args.entity_id:
            stmt = stmt.where(
                Transaction.id.in_(
                    select(TransactionEntity.transaction_id).where(
                        TransactionEntity.entity_id == args.entity_id
                    )
                )
            )

        if args.description_contains:
            stmt = stmt.where(
                func.lower(Transaction.description).like(
                    f"%{args.description_contains.lower()}%"
                )
            )

        rows = ctx.db.execute(stmt).all()

        from app.services.fx_base import base_currency, unconverted_note
        from app.services.reporting.repository import line_dr_cr
        # one currency's own amounts, else every currency at base value
        amounts = [line_dr_cr(line, ccy) for (_t, line, _a) in rows]
        total_debit = sum(d for d, _ in amounts)
        total_credit = sum(c for _, c in amounts)
        # Net by account-nature: for a single asset/expense query this is
        # the natural "amount spent / paid"; for revenue / liability it's
        # the natural "amount received / owed".
        signed_net = 0
        if args.account_code:
            sample = classify_account_code(args.account_code)
            if sample in (ASSET, EXPENSE):
                signed_net = total_debit - total_credit
            elif sample in (LIABILITY, EQUITY, REVENUE):
                signed_net = total_credit - total_debit

        result: dict[str, Any] = {
            "period": {"from": from_d.isoformat(), "to": to_d.isoformat()},
            "currency": ccy or base_currency(ctx.db),
            "total_debit": total_debit,
            "total_credit": total_credit,
            "signed_net": signed_net,
            "row_count": len(rows),
        }
        if not ccy and (left_out := unconverted_note(ctx.db)):
            result["unconverted"] = left_out

        if args.group_by == "month":
            # the company's months: Jalali for an Iranian company (§3.5)
            from app.services.calendar_periods import company_calendar, month_key, month_label
            cal = company_calendar(ctx.db)
            buckets: dict[str, dict[str, int]] = {}
            for txn, line, _acc in rows:
                key = month_key(txn.date, cal)
                slot = buckets.setdefault(key, {"debit": 0, "credit": 0, "count": 0})
                d, c = line_dr_cr(line, ccy)
                slot["debit"] += d
                slot["credit"] += c
                slot["count"] += 1
            result["by_month"] = [
                {"month": m, "label": month_label(m), **buckets[m]}
                for m in sorted(buckets)
            ]
        elif args.group_by == "account":
            buckets = {}
            for _txn, line, acc in rows:
                key = acc.code
                slot = buckets.setdefault(
                    key,
                    {"name": acc.name, "debit": 0, "credit": 0, "count": 0},
                )
                d, c = line_dr_cr(line, ccy)
                slot["debit"] += d
                slot["credit"] += c
                slot["count"] += 1
            result["by_account"] = [
                {"account_code": code, **buckets[code]}
                for code in sorted(buckets)
            ]
        else:
            result["sample"] = [
                {
                    "transaction_id": str(txn.id),
                    "date": txn.date.isoformat(),
                    "account_code": acc.code,
                    "account_name": acc.name,
                    "debit": plain(line.debit or 0),
                    "credit": plain(line.credit or 0),
                    "currency": txn.currency,
                    # the same in the base currency (None: no rate yet)
                    "base_debit": line.base_debit,
                    "base_credit": line.base_credit,
                    "description": txn.description,
                }
                for (txn, line, acc) in rows[: args.limit]
            ]
        return result


# ---------------------------------------------------------------------------
# get_account_balance
# ---------------------------------------------------------------------------


class GetAccountBalanceInput(BaseModel):
    account_code: str = Field(..., description="Exact account code (e.g. '1110').")
    as_of: date | None = Field(
        None, description="Snapshot date. Defaults to today."
    )
    currency: str | None = Field(
        None,
        description=(
            "Omit for every currency together at its base-currency value (the "
            "company's reporting currency). Give a code (USD, EUR…) for entries in "
            "that currency only, in their own amounts."
        ),
    )


class GetAccountBalance(BaseTool):
    name = "get_account_balance"
    category = "read"
    description = (
        "Return the balance of one account as of a date — useful for cash-on-hand "
        "queries, checking AR/AP, or confirming a counterparty balance before "
        "proposing a transaction."
    )
    InputSchema = GetAccountBalanceInput

    async def run(self, ctx: ToolContext, args: GetAccountBalanceInput) -> dict[str, Any]:
        code = args.account_code.strip()
        as_of = args.as_of or date.today()

        acc = ctx.db.execute(select(Account).where(Account.code == code)).scalar_one_or_none()
        if acc is None:
            raise ToolError(f"Account {code!r} not found", code="account_not_found")

        # Sum the lines for this account up to as_of — one currency's own
        # amounts, else every currency at base value (roadmap §4.6).
        from app.services.fx_base import base_currency, unconverted_note
        from app.services.reporting.repository import amount_columns
        ccy = (args.currency or "").strip().upper() or None
        dr, cr = amount_columns(ccy)
        stmt = (
            select(
                func.coalesce(func.sum(dr), 0),
                func.coalesce(func.sum(cr), 0),
            )
            .select_from(TransactionLine)
            .join(Transaction, Transaction.id == TransactionLine.transaction_id)
            .where(
                TransactionLine.account_id == acc.id,
                Transaction.deleted_at.is_(None),
                Transaction.date <= as_of,
            )
        )
        if ccy:
            stmt = stmt.where(Transaction.currency == ccy)
        debit_sum, credit_sum = ctx.db.execute(stmt).one()
        acc_type = classify_account_code(code)
        balance = balance_from_turnovers(acc_type, plain(debit_sum or 0), plain(credit_sum or 0))
        return {
            "account_code": code,
            "account_name": acc.name,
            "account_type": acc_type,
            "as_of": as_of.isoformat(),
            "currency": ccy or base_currency(ctx.db),
            "debit_total": plain(debit_sum or 0),
            "credit_total": plain(credit_sum or 0),
            "balance": plain(balance),
            **({"unconverted": note} if not ccy and (note := unconverted_note(ctx.db)) else {}),
        }


# ---------------------------------------------------------------------------
# search_accounts
# ---------------------------------------------------------------------------


# Locale-aware category → (synonyms, candidate account codes). The synonyms
# let a plain-English request ("office supplies", "cash") match a chart whose
# accounts are worded differently or use opaque nominal codes; the candidate
# codes pin the resolution to the right account when it exists. Only codes
# present in the seeded chart are ever returned (verified at query time).
_ACCOUNT_ALIASES: dict[str, dict[str, tuple[list[str], list[str]]]] = {
    "uk": {
        "cash / bank": (["cash", "petty cash", "bank", "current account", "in hand"], ["1200", "1220", "1210"]),
        "office supplies": (["office supplies", "stationery", "printing", "office expenses", "telephone", "phone"], ["7600"]),
        "rent": (["rent", "rates"], ["7200"]),
        "utilities": (["utilities", "light", "heat", "power", "electricity", "gas", "water"], ["7300"]),
        "motor / travel": (["motor", "vehicle", "fuel", "travel", "entertainment", "mileage"], ["7400", "7500"]),
        "wages / salary": (["wages", "salary", "salaries", "payroll", "staff"], ["7100", "7000"]),
        "sales / revenue": (["sales", "revenue", "turnover", "income"], ["4000", "4200"]),
        "purchases / cogs": (["purchases", "cogs", "cost of sales", "cost of goods", "stock", "materials"], ["5000"]),
        "trade debtors": (["debtors", "receivable", "accounts receivable", "ar", "owed to us"], ["1100"]),
        "trade creditors": (["creditors", "payable", "accounts payable", "ap", "we owe", "suppliers"], ["2100"]),
        "vat": (["vat", "sales tax", "tax payable"], ["2200", "1400"]),
        "bank charges / fees": (["bank charges", "bank fees", "commission", "fee"], ["8000"]),
        "professional fees": (["professional fees", "accountant", "legal", "consultant", "audit"], ["7800"]),
        "repairs": (["repairs", "maintenance"], ["7700"]),
    },
    "ir": {
        "cash / bank": (["cash", "bank", "petty cash", "موجودی", "نقد", "بانک", "صندوق"], ["1110"]),
        "operating expense": (["office supplies", "stationery", "rent", "utilities", "expense", "هزینه", "اجاره", "ملزومات", "قبض"], ["6112"]),
        "wages / salary": (["wages", "salary", "payroll", "حقوق", "دستمزد"], ["6110"]),
        "sales / revenue": (["sales", "revenue", "income", "فروش", "درآمد"], ["4110"]),
        "receivable": (["receivable", "debtors", "ar", "دریافتنی"], ["1112"]),
        "payable": (["payable", "creditors", "ap", "پرداختنی"], ["2110"]),
        "financial expense": (["interest", "finance", "bank charge", "مالی", "بهره", "کارمزد"], ["6210"]),
        "capital / equity": (["capital", "equity", "owner", "سرمایه"], ["3110"]),
    },
    # Personal-finance chart (app/db/seed.py PERSONAL_SEED_ACCOUNTS): 1110 is
    # the BANK/card account, 1120 the cash on hand — the reverse of what the
    # Iranian SME aliases would suggest, which is why personal mode gets its
    # own table (QA 2026-09-24 A.8-2: "نقدی" was posted to the bank account).
    "personal": {
        "cash on hand": (["cash", "cash on hand", "نقد", "نقدی", "پول نقد", "اسکناس"], ["1120"]),
        "bank / card": (["bank", "card", "transfer", "بانک", "کارت", "کارت به کارت", "انتقال", "حساب بانکی", "پوز"], ["1110"]),
        "gold / fx savings": (["gold", "coin", "طلا", "سکه"], ["1130"]),
        "fx savings": (["dollar", "euro", "fx", "دلار", "یورو", "ارز"], ["1140"]),
        "food": (["food", "grocery", "groceries", "supermarket", "خوراک", "سوپرمارکت", "میوه", "نان", "غذا"], ["6110"]),
        "housing": (["rent", "housing", "اجاره", "مسکن", "شارژ"], ["6120"]),
        "transport": (["transport", "taxi", "fuel", "petrol", "metro", "snapp", "حمل", "تاکسی", "بنزین", "مترو", "اسنپ"], ["6130"]),
        "bills": (["bill", "bills", "utilities", "electricity", "water", "gas", "internet", "قبض", "قبوض", "برق", "آب", "گاز", "اینترنت"], ["6140"]),
        "health": (["health", "doctor", "pharmacy", "medicine", "سلامت", "درمان", "دکتر", "دارو"], ["6150"]),
        "education": (["education", "school", "course", "book", "آموزش", "کلاس", "شهریه", "کتاب"], ["6160"]),
        "clothing": (["clothes", "clothing", "پوشاک", "لباس", "کفش"], ["6170"]),
        "leisure / dining": (["restaurant", "cafe", "lunch", "dinner", "leisure", "cinema", "رستوران", "کافه", "ناهار", "شام", "تفریح", "سینما"], ["6180"]),
        "travel": (["travel", "hotel", "airbnb", "hostel", "flight", "airfare", "vacation", "holiday",
                    "سفر", "مسافرت", "هتل", "اقامت", "پرواز", "بلیط هواپیما"], ["6185"]),
        "subscriptions": (["subscription", "اشتراک"], ["6190"]),
        "family / gifts": (["gift", "family", "هدیه", "کادو", "خانواده"], ["6195"]),
        "bank fees": (["bank fee", "fee", "کارمزد"], ["6210"]),
        "misc": (["misc", "miscellaneous", "متفرقه"], ["6220"]),
        "salary income": (["salary", "wages", "حقوق", "دستمزد"], ["4110"]),
        "freelance income": (["freelance", "project income", "درآمد آزاد", "پروژه"], ["4120"]),
        "loan / installment": (["loan", "installment", "وام", "قسط", "اقساط"], ["2110", "2120"]),
        # money between the person and someone else (scenarios AG1–AG6); matched as substrings, so no
        # bare «lent» (excellent), «trip» (Stripe) or «visa» (a Visa card)
        "money I owe": (["i owe", "owe him", "owe her", "paid for me", "بدهی به", "بدهکارم", "قرض گرفتم"], ["2130"]),
        "money owed to me": (["owed to me", "owes me", "lent to", "i lent", "طلب از", "طلبکارم", "قرض دادم"], ["1150"]),
        # money the person already had when they started (scenario Z4): a chart opened in English
        # names it "Opening net worth", and «افتتاحیه» found nothing. Not «سرمایه»: in a person's
        # books that is investment (سرمایه‌گذاری, 4130) as often as capital
        "opening net worth": (["opening", "starting balance", "net worth", "افتتاحیه", "اولیه", "موجودی اولیه",
                               "خالص دارایی"], ["3110"]),
    },
    "default": {
        "cash / bank": (["cash", "bank", "petty cash"], ["1110", "1200"]),
        "expense": (["expense", "supplies", "rent", "utilities"], ["6112", "7600"]),
        "wages / salary": (["wages", "salary", "payroll"], ["6110", "7100"]),
        "sales / revenue": (["sales", "revenue", "income"], ["4110", "4000"]),
    },
}


def _normal_balance(acc_type: str) -> str:
    return "debit" if acc_type in (ASSET, EXPENSE) else "credit"


# Words for the same thing in a chart's account names: «لوازم التحریر» is the
# stationery account «هزینه ملزومات و نوشت افزار» in a Sepidar chart, and no
# fuzzy ratio sees it (AI QA on migrated books, 2026-10-12). A query naming one
# member matches an account named with another member of the same group.
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("لوازم التحریر", "لوازم تحریر", "نوشت افزار", "نوشت‌افزار", "نوشتافزار", "ملزومات", "لوازم اداری", "stationery",
     "office supplies"),
    ("اجاره", "کرایه", "rent"),
    ("حقوق", "دستمزد", "salary", "wages", "payroll"),
    ("بیمه", "insurance"),
    ("اینترنت", "سرور", "هاست", "زیرساخت", "internet", "hosting", "server"),
    ("پذیرایی", "آبدارخانه", "نظافت", "cleaning", "refreshments"),
    ("ایاب و ذهاب", "حمل و نقل", "آژانس", "اسنپ", "تاکسی", "کرایه ماشین", "transport", "taxi"),
    ("تعمیر", "نگهداری", "repairs", "maintenance"),
    ("کارمزد", "bank charges", "bank fee"),
    ("شهریه", "tuition"),
    ("وام", "تسهیلات", "loan"),
    ("بانک", "bank"),
    ("درآمد", "فروش", "revenue", "income", "sales"),
    ("تلفن", "موبایل", "phone", "telephone"),
    ("آب و برق", "برق", "گاز", "قبض", "utilities", "electricity"),
)

# The posting role an alias category stands for: a migrated chart answers with
# its own account (its "expense" is 611299, not the seed's 6112).
_ALIAS_ROLES = {"cash / bank": "bank", "operating expense": "expense", "wages / salary": "wages_expense",
                "sales / revenue": "revenue", "receivable": "ar", "payable": "ap", "financial expense": "bank_fee",
                "capital / equity": "share_capital"}

# Fewer postings than this and the books have no history to prefer: a new
# company's first entries go to the chart's defaults as before.
_ESTABLISHED_LINES = 30


def _synonym_match(q: str, name: str) -> bool:
    return any(any(w in q for w in group) and any(w in name for w in group) for group in _SYNONYM_GROUPS)


class SearchAccountsInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "Plain-language category or account name to resolve to a code, e.g. "
            "'office supplies', 'cash', 'rent', 'sales', 'bank charges'. "
            "Matches the chart by name AND code, with a synonym map so common "
            "categories resolve even when the chart wording differs."
        ),
        min_length=1,
    )
    account_type: Literal["ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE"] | None = Field(
        None, description="Optional filter by statement nature."
    )
    description: str | None = Field(
        None,
        description=(
            "The transaction's own wording (e.g. 'POS SNAPP TEHRAN', 'paid Kim for design'). "
            "Pass it: what the user chose before for wording like this comes first, marked learned."
        ),
        max_length=512,
    )
    limit: int = Field(8, ge=1, le=25, description="Max accounts to return.")


class SearchAccounts(BaseTool):
    name = "search_accounts"
    category = "read"
    description = (
        "Resolve a plain-language category (e.g. 'office supplies', 'cash', "
        "'rent', 'sales') to actual chart-of-accounts codes. Returns the best "
        "matching accounts as {code, name, type, normal_balance}. ALWAYS use "
        "this to find the account_code for each leg before "
        "propose_create_transaction — do NOT guess code prefixes. One search "
        "per category is enough."
    )
    InputSchema = SearchAccountsInput

    async def run(self, ctx: ToolContext, args: SearchAccountsInput) -> dict[str, Any]:
        q = args.query.strip().lower()
        if not q:
            raise ToolError("query must be non-empty")

        locale = get_reporting_locale(ctx.db)
        alias_key = "personal" if getattr(ctx, "mode", "default") == "personal" else locale
        aliases = _ACCOUNT_ALIASES.get(alias_key, _ACCOUNT_ALIASES["default"])

        # An alias whose synonym appears IN the query (e.g. query "office
        # supplies" contains synonym "office supplies") contributes its
        # preferred codes — the category→code mapping. We only use aliases for
        # this boost, NOT to expand the fuzzy terms: dumping every synonym into
        # the name match cross-contaminates (e.g. "bank charges" would pull in
        # "petty cash"). Match is one-directional (synonym ⊂ query) so "sales"
        # doesn't fire the "cost of sales" synonym.
        from app.utils.text import fold_fa
        q = fold_fa(q)
        preferred_codes: set[str] = set()
        for label, (synonyms, codes) in aliases.items():
            if any(s in q for s in synonyms):
                preferred_codes.update(codes)
                role = _ALIAS_ROLES.get(label) if alias_key != "personal" else None
                if role:
                    from app.services.account_resolver import find_account_code
                    if (own := find_account_code(ctx.db, role)):
                        preferred_codes.add(own)

        from app.models.account import AccountLevel

        # Deactivated accounts take no postings (roadmap §4.5): never propose one.
        rows = ctx.db.execute(select(Account).where(Account.is_active.is_(True))).scalars().all()
        # What the books actually use: a migrated company keeps the seed chart's
        # accounts beside its own, and «اجاره» went to the seed's never-used
        # 6112 over the company's 611228 «هزینه اجاره» (AI QA, 2026-10-12).
        used = dict(ctx.db.execute(
            select(TransactionLine.account_id, func.count())
            .join(Transaction, Transaction.id == TransactionLine.transaction_id)
            .where(Transaction.deleted_at.is_(None))
            .group_by(TransactionLine.account_id)).all())
        established = sum(used.values()) >= _ESTABLISHED_LINES
        scored: list[tuple[float, Account, str]] = []
        for acc in rows:
            # Only postable (leaf) accounts — group/header accounts can't take
            # a journal line, so they'd be useless to propose against.
            if getattr(acc, "level", None) == AccountLevel.GROUP:
                continue
            name_l = fold_fa((acc.name or "").lower())
            code_l = (acc.code or "").lower()
            acc_type = classify_account_code(acc.code or "")
            if args.account_type and acc_type != args.account_type:
                continue
            # Direct fuzzy/substring score of the raw query against the name.
            score = difflib.SequenceMatcher(None, q, name_l).ratio()
            if q in name_l:
                score = max(score, 0.85)
            if q == code_l or q in code_l:
                score = max(score, 0.9)
            if _synonym_match(q, name_l):
                score = max(score, 0.88)
            by_name = q in name_l or q in code_l or _synonym_match(q, name_l)
            in_use = bool(used.get(acc.id))
            # Strong boost when the chart actually has an alias's preferred code —
            # one the books use, or any in books with no history yet; an unused
            # seed account in established books stays a fallback.
            if (acc.code or "") in preferred_codes:
                score = max(score, 0.97 if (in_use or not established) else 0.55)
            if in_use and score >= 0.45:
                score = min(0.99, score + 0.05)
            elif established:
                score = min(score, 0.84)            # an exact name the books never use («فروش» 4110) stays below one they do
            if score >= 0.45:
                scored.append((score, acc, acc_type, by_name))

        # An account named for what was asked beats one only an alias points to:
        # «اجاره» is the rent account, not the company's general expense.
        if any(by_name and sc >= 0.85 for sc, _a, _t, by_name in scored):
            scored = [(sc if by_name else min(sc, 0.84), a, t, by_name) for sc, a, t, by_name in scored]
        scored = [(sc, a, t) for sc, a, t, _b in scored]
        scored.sort(key=lambda t: t[0], reverse=True)
        # The user's own earlier choice for this wording goes first (roadmap §5.4).
        from app.services.learned_preferences import lookup, mark_used
        learned = lookup(ctx.db, args.description or args.query, want="account")
        learned_acc = None
        if learned is not None:
            learned_acc = next((a for (_s, a, _t) in scored if a.code == learned.preference.account_code), None)
            if learned_acc is not None:
                scored = [(1.0, learned_acc, classify_account_code(learned_acc.code))] + [
                    x for x in scored if x[1].code != learned_acc.code]
                mark_used(ctx.db, learned.preference)
        top = scored[: args.limit]
        out = {
            "query": args.query,
            "reporting_locale": locale,
            "matches": [
                {
                    "code": acc.code,
                    "name": acc.name,
                    "type": acc_type,
                    "normal_balance": _normal_balance(acc_type),
                    "confidence": round(score, 3),
                    **({"learned": True} if learned_acc is not None and acc.code == learned_acc.code else {}),
                }
                for (score, acc, acc_type) in top
            ],
        }
        if learned_acc is not None:
            out["learned_note"] = (f"The user chose {learned_acc.code} ({learned_acc.name}) before for "
                                   f"\"{learned.preference.label}\" — use it unless they say otherwise now.")
        if not any(sc >= 0.85 for sc, _a, _t in top):
            # «حساب تولیدی آرد روشن تهران چقدر مونده؟» names a party, not an account: the
            # model read a cash account's 0 as its balance (AI QA on migrated books, 2026-10-12)
            q_words = _party_words(q)
            party = next((e for e in ctx.db.execute(select(Entity)).scalars()
                          if q_words and q_words <= _party_words(fold_fa((e.name or "").lower()))), None)
            if party is not None:
                # …and the account its money moves through: a client's receipt credits receivables,
                # not «وام کارکنان» (AI QA on migrated books, 2026-10-12)
                from app.services.account_resolver import find_account_code
                role = {"client": "ar", "customer": "ar", "supplier": "ap", "employee": "net_pay_payable",
                        "shareholder": "shareholder_current"}.get(party.type)
                code = find_account_code(ctx.db, role) if role else None
                via = (f" Money received from or paid to it goes through {code} "
                       f"({'receivables' if role == 'ar' else 'its ledger account'}), linked to the party." if code else "")
                out["party_note"] = (f"No account is named {args.query!r}, but {party.name!r} is a {party.type}: its "
                                     f"balance is get_party_balances(party=...), its entries query_ledger(entity_id='{party.id}')."
                                     + via)
        return out


# ---------------------------------------------------------------------------
# get_financial_statement
# ---------------------------------------------------------------------------


_INCEPTION = date(1900, 1, 1)


class GetFinancialStatementInput(BaseModel):
    statement: Literal["balance_sheet", "income_statement", "trial_balance", "cash_flow"] = Field(
        ..., description="Which statement to build."
    )
    from_date: date | None = Field(
        None, description="Period start (income_statement / cash_flow). Defaults to the start of the current year."
    )
    to_date: date | None = Field(None, description="As-of / period end. Defaults to today.")
    currency: str | None = Field(None, description="Filter by currency; defaults to the reporting currency.")
    year: str | None = Field(
        None, pattern=r"^\d{4}$",
        description="A whole year instead of dates: '1405' (Jalali, «امسال» in a Jalali company) or '2026'.")
    month: str | None = Field(
        None, pattern=r"^\d{4}-\d{2}$",
        description="A whole month instead of dates, in the company's calendar: '1405-05' is Mordad 1405.")


def _year_bounds(db: Session, year: str | None = None, *, of: date | None = None) -> tuple[date, date]:
    """A year's first and last day in the company's calendar: «امسال» in a
    Jalali company starts on 1 Farvardin, not 1 January (AI QA, 2026-10-12)."""
    from app.services.calendar_periods import company_calendar
    from app.utils.jalali import gregorian_to_jalali, jalali_to_gregorian
    if year is not None:
        y = int(year)
        jalali = y < 1700
    else:
        jalali = company_calendar(db) == "jalali"
        y = gregorian_to_jalali(of or date.today())[0] if jalali else (of or date.today()).year
    if jalali:
        return jalali_to_gregorian(y, 1, 1), jalali_to_gregorian(y + 1, 1, 1) - timedelta(days=1)
    return date(y, 1, 1), date(y, 12, 31)


class GetFinancialStatement(BaseTool):
    name = "get_financial_statement"
    category = "read"
    description = (
        "Build a complete financial statement deterministically from the "
        "ledger: balance_sheet (Assets = Liabilities + Equity), "
        "income_statement (P&L), trial_balance (total debits == total "
        "credits), or cash_flow. ALWAYS use this for 'balance sheet', 'P&L', "
        "'trial balance' or 'cash flow' questions — never hand-sum individual "
        "account balances. Relay the returned totals; the figures already "
        "balance."
    )
    InputSchema = GetFinancialStatementInput

    async def run(self, ctx: ToolContext, args: GetFinancialStatementInput) -> dict[str, Any]:
        from app.services.reporting.repository import trial_balance_rows

        to_date = args.to_date or date.today()
        currency = args.currency  # None → all currencies (repository handles)
        if args.year or args.month:
            if args.year:
                first, last = _year_bounds(ctx.db, args.year)
            else:
                from app.services.calendar_periods import key_bounds
                first, last = key_bounds(args.month)
            args = args.model_copy(update={"from_date": first, "to_date": min(last, date.today())})
            to_date = args.to_date

        def _balances(from_date: date) -> list[tuple[str, str, str, int]]:
            """(code, name, type, signed_balance) over [from_date, to_date]."""
            out = []
            for code, name, d, c in trial_balance_rows(ctx.db, from_date, to_date, currency=currency):
                t = classify_account_code(code)
                out.append((code, name, t, balance_from_turnovers(t, d, c)))
            return out

        if args.statement == "trial_balance":
            rows = _balances(_INCEPTION)
            lines, tot_dr, tot_cr = [], 0, 0
            for code, name, t, bal in rows:
                if bal == 0:
                    continue
                dr = bal if t in (ASSET, EXPENSE) else 0
                cr = bal if t not in (ASSET, EXPENSE) else 0
                # A negative natural balance flips the column.
                if bal < 0:
                    dr, cr = (0, -bal) if t in (ASSET, EXPENSE) else (-bal, 0)
                tot_dr += dr
                tot_cr += cr
                lines.append({"code": code, "name": name, "debit": dr, "credit": cr})
            return {
                "statement": "trial_balance", "as_of": to_date.isoformat(),
                "lines": lines, "total_debit": tot_dr, "total_credit": tot_cr,
                "balanced": tot_dr == tot_cr,
            }

        if args.statement in ("income_statement", "cash_flow"):
            from_date = args.from_date or _year_bounds(ctx.db, of=to_date)[0]

        if args.statement == "income_statement":
            rows = _balances(from_date)
            revenue = [(c, n, b) for c, n, t, b in rows if t == REVENUE and b]
            expense = [(c, n, b) for c, n, t, b in rows if t == EXPENSE and b]
            rev_total = sum(b for _, _, b in revenue)
            exp_total = sum(b for _, _, b in expense)
            return {
                "statement": "income_statement",
                "period": {"from": from_date.isoformat(), "to": to_date.isoformat()},
                "revenue": [{"code": c, "name": n, "amount": b} for c, n, b in revenue],
                "expenses": [{"code": c, "name": n, "amount": b} for c, n, b in expense],
                "revenue_total": rev_total, "expense_total": exp_total,
                "net_income": rev_total - exp_total,
            }

        if args.statement == "cash_flow":
            from app.services.cash_service import cash_on_hand
            opening = cash_on_hand(ctx.db, locale=get_reporting_locale(ctx.db),
                                   currency=currency, as_of=from_date - timedelta(days=1))
            closing = cash_on_hand(ctx.db, locale=get_reporting_locale(ctx.db),
                                   currency=currency, as_of=to_date)
            return {
                "statement": "cash_flow",
                "period": {"from": from_date.isoformat(), "to": to_date.isoformat()},
                "opening_cash": opening, "closing_cash": closing,
                "net_change_in_cash": closing - opening,
            }

        # balance_sheet — cumulative balances from inception.
        rows = _balances(_INCEPTION)
        assets = [(c, n, b) for c, n, t, b in rows if t == ASSET and b]
        liabilities = [(c, n, b) for c, n, t, b in rows if t == LIABILITY and b]
        equity_posted = [(c, n, b) for c, n, t, b in rows if t == EQUITY and b]
        rev_total = sum(b for _, _, t, b in rows if t == REVENUE)
        exp_total = sum(b for _, _, t, b in rows if t == EXPENSE)
        net_income = rev_total - exp_total  # retained earnings, folded into equity
        assets_total = sum(b for _, _, b in assets)
        liab_total = sum(b for _, _, b in liabilities)
        equity_total = sum(b for _, _, b in equity_posted) + net_income
        return {
            "statement": "balance_sheet", "as_of": to_date.isoformat(),
            "assets": [{"code": c, "name": n, "amount": b} for c, n, b in assets],
            "liabilities": [{"code": c, "name": n, "amount": b} for c, n, b in liabilities],
            "equity": [{"code": c, "name": n, "amount": b} for c, n, b in equity_posted],
            "retained_earnings": net_income,
            "assets_total": assets_total,
            "liabilities_total": liab_total,
            "equity_total": equity_total,
            "balanced": assets_total == liab_total + equity_total,
        }


# ---------------------------------------------------------------------------
# get_company_defaults
# ---------------------------------------------------------------------------


class GetCompanyDefaultsInput(BaseModel):
    pass


class GetCompanyDefaults(BaseTool):
    name = "get_company_defaults"
    category = "read"
    description = (
        "Return the company defaults: reporting locale (ir / uk / default), display "
        "calendar (gregorian / jalali), default currency, today's date. Call this "
        "once at the start of a conversation to anchor every subsequent proposal."
    )
    InputSchema = GetCompanyDefaultsInput

    async def run(self, ctx: ToolContext, args: GetCompanyDefaultsInput) -> dict[str, Any]:
        locale = get_reporting_locale(ctx.db)
        calendar = get_display_calendar(ctx.db)
        # Default currency by locale: IRR for Iran, GBP for UK, IRR otherwise
        # (matches the existing transaction model default).
        currency = {"ir": "IRR", "uk": "GBP"}.get(locale, "IRR")

        # Pull any explicit default-currency override from app_settings.
        row = ctx.db.execute(
            select(AppSetting).where(AppSetting.key == "default_currency")
        ).scalar_one_or_none()
        if row and (row.value or "").strip():
            currency = row.value.strip()

        return {
            "reporting_locale": locale,
            "display_calendar": calendar,
            "default_currency": currency,
            "today": date.today().isoformat(),
        }


# ---------------------------------------------------------------------------
# get_tax_summary
# ---------------------------------------------------------------------------


class GetTaxSummaryInput(BaseModel):
    from_date: date | None = Field(None, description="Period start (defaults to the current quarter start).")
    to_date: date | None = Field(None, description="Period end (defaults to today).")
    currency: str | None = Field(None, description="Filter by currency; defaults to all.")


class GetTaxSummary(BaseTool):
    name = "get_tax_summary"
    category = "read"
    description = (
        "Estimate VAT / sales tax for a period: output tax (on sales), input "
        "tax (on purchases) and net tax = output − input, computed from the "
        "tax rates recorded on invoices. Use this for 'how much tax/VAT do I "
        "owe' questions. The result includes 'assumptions' (the rates used) and "
        "a 'caveat'. ALWAYS relay the caveat to the user verbatim — even if they "
        "say 'just give me the number' — and state the assumptions. Do NOT "
        "invent tax law, rates for other jurisdictions, or filing deadlines; if "
        "asked, say the current rules must be verified with a tax professional."
    )
    InputSchema = GetTaxSummaryInput

    async def run(self, ctx: ToolContext, args: GetTaxSummaryInput) -> dict[str, Any]:
        from app.services.tax_service import compute_tax_summary

        today = date.today()
        to_date = args.to_date or today
        from_date = args.from_date
        if from_date is None:
            q_start_month = ((today.month - 1) // 3) * 3 + 1
            from_date = date(today.year, q_start_month, 1)
        return compute_tax_summary(ctx.db, from_date, to_date, currency=args.currency)


# ---------------------------------------------------------------------------
# Registry helper
# ---------------------------------------------------------------------------


def register_read_tools(registry) -> None:
    from app.services.ai_accountant.cash_tools import register_cash_tools
    from app.services.ai_accountant.spending_tools import register_spending_tools

    registry.register(FindEntity())
    registry.register(ListEntities())
    registry.register(QueryLedger())
    registry.register(GetAccountBalance())
    registry.register(SearchAccounts())
    registry.register(GetFinancialStatement())
    registry.register(GetTaxSummary())
    registry.register(GetCompanyDefaults())
    register_spending_tools(registry)
    register_cash_tools(registry)

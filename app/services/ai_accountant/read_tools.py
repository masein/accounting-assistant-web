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

        stmt = select(Entity)
        if args.type:
            stmt = stmt.where(Entity.type == args.type)

        # Pull all candidates the user could plausibly mean, then score
        # client-side. SQLite (used in tests) doesn't have trigram support;
        # difflib is portable and fast for small entity tables.
        all_rows = ctx.db.execute(stmt).scalars().all()
        scored: list[tuple[float, Entity]] = []
        q_lower = q.lower()
        for row in all_rows:
            ratio = difflib.SequenceMatcher(None, q_lower, row.name.lower()).ratio()
            # Boost prefix matches — typing "Kim" should rank "Kim Nguyen"
            # above "Joachim".
            if row.name.lower().startswith(q_lower):
                ratio = min(1.0, ratio + 0.15)
            if q_lower in row.name.lower():
                ratio = min(1.0, ratio + 0.05)
            scored.append((ratio, row))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        top = scored[: args.limit]

        return {
            "query": q,
            "matches": [
                {
                    "entity_id": str(e.id),
                    "name": e.name,
                    "type": e.type,
                    "code": e.code,
                    "confidence": round(score, 3),
                }
                for (score, e) in top
                if score >= 0.30  # filter pure-noise matches
            ],
        }


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
        from_d = args.from_date or date(today.year, 1, 1)
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

        total_debit = sum(int(line.debit or 0) for (_t, line, _a) in rows)
        total_credit = sum(int(line.credit or 0) for (_t, line, _a) in rows)
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
            "total_debit": total_debit,
            "total_credit": total_credit,
            "signed_net": signed_net,
            "row_count": len(rows),
        }

        if args.group_by == "month":
            buckets: dict[str, dict[str, int]] = {}
            for txn, line, _acc in rows:
                key = txn.date.strftime("%Y-%m")
                slot = buckets.setdefault(key, {"debit": 0, "credit": 0, "count": 0})
                slot["debit"] += int(line.debit or 0)
                slot["credit"] += int(line.credit or 0)
                slot["count"] += 1
            result["by_month"] = [
                {"month": m, **buckets[m]}
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
                slot["debit"] += int(line.debit or 0)
                slot["credit"] += int(line.credit or 0)
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
                    "debit": int(line.debit or 0),
                    "credit": int(line.credit or 0),
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

        # Sum the lines for this account up to as_of.
        stmt = (
            select(
                func.coalesce(func.sum(TransactionLine.debit), 0),
                func.coalesce(func.sum(TransactionLine.credit), 0),
            )
            .select_from(TransactionLine)
            .join(Transaction, Transaction.id == TransactionLine.transaction_id)
            .where(
                TransactionLine.account_id == acc.id,
                Transaction.deleted_at.is_(None),
                Transaction.date <= as_of,
            )
        )
        debit_sum, credit_sum = ctx.db.execute(stmt).one()
        acc_type = classify_account_code(code)
        balance = balance_from_turnovers(acc_type, int(debit_sum or 0), int(credit_sum or 0))
        return {
            "account_code": code,
            "account_name": acc.name,
            "account_type": acc_type,
            "as_of": as_of.isoformat(),
            "debit_total": int(debit_sum or 0),
            "credit_total": int(credit_sum or 0),
            "balance": int(balance),
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
# Registry helper
# ---------------------------------------------------------------------------


def register_read_tools(registry) -> None:
    registry.register(FindEntity())
    registry.register(ListEntities())
    registry.register(QueryLedger())
    registry.register(GetAccountBalance())
    registry.register(GetCompanyDefaults())

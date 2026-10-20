"""Personal-mode read tools (roadmap 2026-09 §4.12): the monthly report card
and the savings goals, as the "My finances" page shows them; and the money
between the user and other people, person by person (scenarios AG3–AG6)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .base import BaseTool, ToolContext, ToolError


class GetReportCardInput(BaseModel):
    month: str | None = Field(None, pattern=r"^\d{4}-\d{2}$",
                              description="YYYY-MM in the user's calendar (1405-06 = Shahrivar); default last month.")


class GetReportCard(BaseTool):
    name = "get_report_card"
    category = "read"
    description = (
        "The user's monthly report card: income, spending, what they saved and their savings rate, against last month "
        "and the three-month average; the biggest spending categories and what rose; budgets kept; how net worth "
        "changed; savings goals; three checks (saved 10 %+, spent no more than usual, inside the budgets). Use for "
        "'how did I do last month?', 'کارنامه این ماهم چطوره؟', 'did I save anything in Shahrivar?'. Pure read."
    )
    InputSchema = GetReportCardInput

    async def run(self, ctx: ToolContext, args: GetReportCardInput) -> dict[str, Any]:
        from app.services.report_card import report_card
        try:
            card = report_card(ctx.db, args.month)
        except (ValueError, KeyError) as e:
            raise ToolError(f"{args.month!r} isn't a month (YYYY-MM).", code="bad_month") from e
        return card


class GetSavingsGoalsInput(BaseModel):
    include_archived: bool = Field(False, description="Include goals the user put away.")


class GetSavingsGoals(BaseTool):
    name = "get_savings_goals"
    category = "read"
    description = (
        "The user's savings goals: target, saved so far (the account's value today), what's left, what each month "
        "still needs to reach it by its date, their recent pace and whether they're on track. Use for 'am I on track "
        "for the car?', 'how far am I from my emergency fund?'. Goals are added on the My finances page. Pure read."
    )
    InputSchema = GetSavingsGoalsInput

    async def run(self, ctx: ToolContext, args: GetSavingsGoalsInput) -> dict[str, Any]:
        from app.services.personal_goals import list_goals
        goals = list_goals(ctx.db, include_archived=args.include_archived)
        return {"goals": goals, "count": len(goals),
                "note": None if goals else "No savings goals yet — they're added on the My finances page."}


class GetNetWorthInput(BaseModel):
    pass


class GetNetWorth(BaseTool):
    name = "get_net_worth"
    category = "read"
    description = (
        "The user's net worth today: what they own minus what they owe, with gold and foreign currency restated at "
        "today's rates (and what that revaluation gained), each holding with its quantity, and the month-end trend "
        "over the last twelve months. Use for 'how much am I worth?', 'دارایی خالص من چقدره؟', 'how much is my gold "
        "worth now?'. Holdings and rates are kept on the My finances page. Pure read."
    )
    InputSchema = GetNetWorthInput

    async def run(self, ctx: ToolContext, args: GetNetWorthInput) -> dict[str, Any]:
        from datetime import date as _date, timedelta
        from app.services.net_worth_service import compute_net_worth
        nw = compute_net_worth(ctx.db)

        def month_end(key: str) -> str:
            y, m = int(key[:4]), int(key[5:7])
            first_next = _date(y + (m == 12), m % 12 + 1, 1)
            return min(first_next - timedelta(days=1), nw.as_of).isoformat()

        def line(item) -> dict[str, Any]:
            return {"account_code": item.account_code, "account_name": item.account_name, "book_value": item.book_value,
                    "market_value": item.market_value, "unrealized_gain": item.unrealized_gain, "revalued": item.revalued,
                    "unit": item.unit, "quantity": item.quantity, "rate": item.rate}
        return {"as_of": nw.as_of.isoformat(), "currency": nw.currency, "net_worth": nw.net_worth,
                "total_assets": nw.total_assets, "total_liabilities": nw.total_liabilities,
                "unrealized_gain": nw.unrealized_gain, "assets": [line(x) for x in nw.assets],
                "liabilities": [line(x) for x in nw.liabilities],
                "trend": [{"period": p, "date": month_end(p), "value": v} for p, v in nw.trend],
                "missing_rates": sorted(set(nw.missing_rates))}


# A person's chart (app/db/seed.py PERSONAL_SEED_ACCOUNTS): «بدهی به دیگران»
# (Money I owe) and «طلب از دیگران» (Money owed to me), and any account the
# user opened under them.
MONEY_I_OWE, MONEY_OWED_TO_ME = "2130", "1150"


def owed_between_people(code: str | None) -> bool:
    """Is ``code`` money owed between the user and another person?"""
    code = (code or "").strip()
    return any(code == c or code.startswith(c) for c in (MONEY_I_OWE, MONEY_OWED_TO_ME))


class GetPeopleBalancesInput(BaseModel):
    person: str | None = Field(None, description="One person's name, or find_entity's id; leave out for everyone.")


class GetPeopleBalances(BaseTool):
    name = "get_people_balances"
    category = "read"
    description = (
        "Money between the user and other people, person by person, each currency on its own: what the user owes "
        "each person («بدهی به دیگران», Money I owe) and what each person owes the user («طلب از دیگران», Money "
        "owed to me), from the entries that name that person. Use for 'how much do I owe Amir?', «چقدر به امیر "
        "بدهکارم؟», 'who owes me?', «به کی بدهکارم؟». Amounts on those accounts that name no one are given apart "
        "(``not_tied_to_anyone``). Never answer one person's debt from the whole account. Pure read."
    )
    InputSchema = GetPeopleBalancesInput

    async def run(self, ctx: ToolContext, args: GetPeopleBalancesInput) -> dict[str, Any]:
        return people_balances(ctx.db, args.person)


def people_balances(db, person: str | None = None, *, with_last: bool = False) -> dict[str, Any]:
    """Money between the user and each person, each currency on its own (get_people_balances);
    ``with_last`` adds each person's last entry's date (the weekly review's «آخرین بار», AL10)."""
    import uuid
    from datetime import date as _date

    from sqlalchemy import select

    from app.core.money import plain
    from app.models.account import Account
    from app.models.entity import Entity, TransactionEntity
    from app.models.transaction import Transaction, TransactionLine
    from app.services.ai_accountant.entity_create import normalize_match_name
    from app.utils.text import fold_fa

    rows = db.execute(
        select(Transaction.id, Transaction.currency, Transaction.date, Account.code, TransactionLine.debit,
               TransactionLine.credit)
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(Transaction.deleted_at.is_(None))
    ).all()
    rows = [r for r in rows if owed_between_people(r.code)]
    whose: dict[Any, Entity] = {}
    if rows:
        links = db.execute(
            select(TransactionEntity.transaction_id, Entity)
            .join(Entity, Entity.id == TransactionEntity.entity_id)
            .where(TransactionEntity.transaction_id.in_({r.id for r in rows}), Entity.type != "bank")
            .order_by(Entity.name)).all()
        for txn_id, ent in links:
            whose.setdefault(txn_id, ent)          # one person per entry: the first it names
    people: dict[str, dict[str, Any]] = {}
    loose = {"you_owe": {}, "owes_you": {}}

    def add(book: dict, cur: str, amount) -> None:
        book[cur] = book.get(cur, 0) + amount

    for r in rows:
        cur = (r.currency or "").upper()
        side, amount = (("you_owe", r.credit - r.debit) if r.code.startswith(MONEY_I_OWE)
                        else ("owes_you", r.debit - r.credit))
        ent = whose.get(r.id)
        if ent is None:
            add(loose[side], cur, amount)
            continue
        # one person by name: «Amir» made as someone owed and again as someone owing is still Amir
        who = people.setdefault(normalize_match_name(ent.name), {"person": ent.name, "entity_id": str(ent.id),
                                                                 "you_owe": {}, "owes_you": {}, "last": r.date})
        add(who[side], cur, amount)
        who["last"] = max(who["last"], r.date)

    def settled(book: dict) -> dict:
        return {cur: plain(v) for cur, v in sorted(book.items()) if v}

    out_people = [{**{k: v for k, v in p.items() if k != "last"}, "you_owe": settled(p["you_owe"]),
                   "owes_you": settled(p["owes_you"]), **({"last_entry": p["last"].isoformat()} if with_last else {})}
                  for p in sorted(people.values(), key=lambda p: fold_fa(p["person"]).lower())]
    asked = (person or "").strip()
    if asked:
        try:
            ent = db.get(Entity, uuid.UUID(asked))
            asked = ent.name if ent is not None else asked
        except ValueError:
            pass
        needle = fold_fa(asked).lower()
        out_people = [p for p in out_people if needle in fold_fa(p["person"]).lower()]
    else:
        out_people = [p for p in out_people if p["you_owe"] or p["owes_you"]]
    out: dict[str, Any] = {"as_of": _date.today().isoformat(), "people": out_people,
                           "not_tied_to_anyone": {k: settled(v) for k, v in loose.items()}}
    if asked and not out_people:
        out["note"] = f"No entry names {person!r}: nothing owed either way."
    elif asked and all(not p["you_owe"] and not p["owes_you"] for p in out_people):
        out["note"] = "All settled: nothing owed either way."
    return out


def register_personal_tools(registry) -> None:
    registry.register(GetReportCard())
    registry.register(GetSavingsGoals())
    registry.register(GetNetWorth())
    registry.register(GetPeopleBalances())

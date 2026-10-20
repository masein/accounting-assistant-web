"""Can I afford it? and a savings goal from the chat (scenarios AM1–AM10).

``check_affordability`` reads the books (app/services/affordability.py) and the
reply's card draws its answer; ``propose_savings_goal`` makes the goal the
answer suggests, with a savings place of its own unless one is named — the
weekly review then reminds about it (AL6, AL7).
"""
from __future__ import annotations

from datetime import date as _date
from typing import Any

from pydantic import BaseModel, Field

from app.core.money import Money, grouped, plain

from .base import BaseTool, ToolContext, ToolError
from .time_tools import _register


class CheckAffordabilityInput(BaseModel):
    price: Money = Field(..., gt=0, description="What it costs, in its own currency with its places: 1,200.50 is 1200.50. "
                                                "Tomans are converted to rials first (×10).")
    currency: str | None = Field(None, min_length=3, max_length=3, description="Its currency when not the books' own: USD, OMR…")
    item: str | None = Field(None, max_length=80, description="What it is, in the user's words: «لپ‌تاپ», 'a used car'.")


class CheckAffordability(BaseTool):
    name = "check_affordability"
    category = "read"
    description = (
        "Can the user afford something? «می‌خوام یه لپ‌تاپ ۸۰ میلیونی بخرم، می‌تونم؟», 'can I afford a $1,200 phone?', "
        "'should I buy it in installments?'. Reads the books: money in the cash and bank places, the last three months' "
        "average income and spending, payments due in 30 days, what the savings goals need. Returns a verdict — now, "
        "wait (a few months), installments (how many, how much each), save (how much a month, how long), not_now, "
        "too_new — with its figures; the reply's card shows them. Never investment advice."
    )
    InputSchema = CheckAffordabilityInput

    async def run(self, ctx: ToolContext, args: CheckAffordabilityInput) -> dict[str, Any]:
        from app.services.affordability import AffordError, check
        try:
            return check(ctx.db, price=args.price, currency=args.currency, item=args.item)
        except AffordError as e:
            raise ToolError(str(e), code=e.code) from e


class ProposeSavingsGoalInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=80, description="What it is for: «ماشین», 'laptop'.")
    target_amount: Money = Field(..., gt=0, description="How much, in the books' currency.")
    target_date: _date | None = Field(None, description="By when (optional).")
    place: str | None = Field(None, max_length=120, description="Where the savings are kept, if the user names a place "
                                                                "(«حساب پس‌انداز», 1140); else a place of its own is made.")


class ProposeSavingsGoal(BaseTool):
    name = "propose_savings_goal"
    category = "proposal"
    description = (
        "Make a savings goal: «یک هدف پس‌انداز ۳۰۰ میلیونی برای ماشین تا دی ۱۴۰۶ بساز», 'save 2,000 for a holiday by "
        "June'. Its money is kept in a place of its own («پس‌انداز ماشین», made under the bank accounts) unless the user "
        "names one. The weekly review then says how it is going and reminds to put money aside. Returns a confirm card."
    )
    InputSchema = ProposeSavingsGoalInput

    async def run(self, ctx: ToolContext, args: ProposeSavingsGoalInput) -> dict[str, Any]:
        from app.services.ai_accountant.money_places import BANK, _persian, find_place, resolve_place
        from app.services.fx_base import base_currency
        name = " ".join(args.name.split())
        if args.place:
            acc = resolve_place(ctx.db, args.place)
            if acc is None:
                raise ToolError(f"No place called {args.place!r}: ask which one, or leave it for a place of its own.",
                                code="no_place")
            code, place, new = acc.code, acc.name, False
        else:
            place = f"پس‌انداز {name}" if _persian(name) else f"{name} savings"
            _parent, existing = find_place(ctx.db, place, BANK)
            code, new = (existing.code if existing is not None else None), existing is None
        ccy = base_currency(ctx.db)
        payload = {"name": name, "target_amount": plain(args.target_amount), "currency": ccy,
                   "target_date": args.target_date.isoformat() if args.target_date else None,
                   "place": place, "account_code": code, "new_place": new,
                   "description": (f"هدف پس‌انداز: {name}" if _persian(name) else f"Savings goal: {name}")}
        token = _register(ctx, self.name, payload)
        by = f" by {args.target_date.isoformat()}" if args.target_date else ""
        return {"confirmation_token": str(token), "status": "pending", "preview": payload,
                "summary": (f"Savings goal «{name}»: {grouped(args.target_amount)} {ccy}{by}, kept in {place}"
                            + (" (a new place)." if new else "."))}


def execute_savings_goal(db, proposal, *, actor_user_id: str, actor_username: str | None,
                         ip_address: str | None) -> tuple[None, str]:
    from app.models.savings_goal import SavingsGoal
    from app.services.ai_accountant.money_places import BANK, find_place
    from app.services.ai_accountant.time_execute import _audit
    from app.services.chart_service import create_account
    from app.services.personal_goals import check
    p = dict(proposal.tool_input or {})
    code = p.get("account_code")
    if not code:
        _parent, acc = find_place(db, p["place"], BANK)
        acc = acc or create_account(db, name=p["place"], parent_code=BANK)
        code = acc.code
    check(db, name=p["name"], account_code=code, target_amount=p["target_amount"])
    goal = SavingsGoal(name=p["name"], account_code=code, target_amount=p["target_amount"],
                       target_date=_date.fromisoformat(p["target_date"]) if p.get("target_date") else None)
    db.add(goal)
    db.flush()
    audit_id = _audit(db, proposal, entity_type="savings_goal", entity_id=str(goal.id),
                      detail={"name": goal.name, "account_code": code, "target_amount": p["target_amount"],
                              "target_date": p.get("target_date")},
                      actor_user_id=actor_user_id, actor_username=actor_username, ip_address=ip_address)
    return None, audit_id


def register_afford_tools(registry, *, personal: bool) -> None:
    registry.register(CheckAffordability())
    if personal:
        registry.register(ProposeSavingsGoal())

"""AI-accountant proposal tools for shareholder equity — contributions (آورده),
capital increases (افزایش سرمایه), dividend declarations (سود سهام), and
shareholder current accounts (حساب جاری). Each registers a confirm-gated
AIProposal; nothing posts to the GL until the user clicks Confirm and
``execute_equity_proposal`` runs.
"""
from __future__ import annotations

import uuid
from datetime import date as _date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.ai_accountant import AIProposal
from app.models.entity import Entity

from .base import BaseTool, ToolContext, ToolError

PROPOSAL_TTL = timedelta(minutes=10)
_MAX_AMOUNT = 10**15  # digit-garbage guard (minor units)


def _resolve_date(raw: str | None) -> str:
    if not raw:
        return _date.today().isoformat()
    try:
        return _date.fromisoformat(str(raw).strip()).isoformat()
    except (ValueError, TypeError) as e:
        raise ToolError(f"Invalid date {raw!r} — use YYYY-MM-DD.", code="invalid_date") from e


def _check_amount(amount: int, what: str = "Amount") -> int:
    amount = int(amount or 0)
    if amount <= 0:
        raise ToolError(f"{what} must be a positive number of minor units.", code="invalid_amount")
    if amount > _MAX_AMOUNT:
        raise ToolError(f"{what} {amount} is implausibly large — check the figure.", code="amount_too_large")
    return amount


def _resolve_shareholder(db, name: str) -> Entity:
    matches = db.execute(
        select(Entity).where(Entity.type == "shareholder", Entity.name.ilike((name or "").strip()))
    ).scalars().all()
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ToolError(
            f"Multiple shareholders named {name!r} — use find_entity and disambiguate.",
            code="ambiguous_shareholder",
        )
    raise ToolError(
        f"No shareholder named {name!r}. Register them first with propose_create_entity "
        f"(type='shareholder'), then retry — shareholders are equity holders, never employees.",
        code="shareholder_not_found",
    )


def _persist(ctx: ToolContext, tool_name: str, payload: dict) -> str:
    token = uuid.uuid4()
    proposal = AIProposal(
        confirmation_token=token, user_id=ctx.user_id,
        session_id=ctx.chat_session_id, tool_name=tool_name,
        tool_input=payload, user_message=ctx.user_message, status="pending",
    )
    ctx.db.add(proposal)
    ctx.db.commit()
    ctx.db.refresh(proposal)
    return str(token)


def _envelope(token: str, tool_name: str, summary: str, payload: dict) -> dict[str, Any]:
    return {
        "confirmation_token": token,
        "status": "pending",
        "expires_at": (datetime.now(timezone.utc) + PROPOSAL_TTL).isoformat(),
        "summary": summary,
        "tool_name": tool_name,
        "preview": payload,
        "next_steps": (
            "Show this as an action card. The postings are made only when the user "
            "clicks Confirm. Briefly say what you proposed and end your turn."
        ),
    }


# --------------------------------------------------------------------------- #
# 1. Contribution (آورده)
# --------------------------------------------------------------------------- #
class ProposeContributionInput(BaseModel):
    shareholder_name: str = Field(..., min_length=2, max_length=120,
                                  description="The shareholder who is contributing (must already be a shareholder).")
    amount: int = Field(..., gt=0, description="Amount contributed, in minor units (e.g. 500000000 for 500m).")
    date: str | None = Field(None, description="Entry date YYYY-MM-DD; defaults to today.")
    to_capital: bool = Field(True, description="True → credit share capital (raises registered capital); "
                                               "False → credit the shareholder's current account (not yet capitalised).")


class ProposeContribution(BaseTool):
    name = "propose_shareholder_contribution"
    category = "proposal"
    description = (
        "Register a pending shareholder CONTRIBUTION / آورده (a shareholder injects "
        "cash into the company) for the user to confirm. Posts DR bank / CR share "
        "capital (or CR shareholder current account if to_capital=False). Use for "
        "'Cyrus contributed 500m as capital'. The shareholder must already exist "
        "(propose_create_entity type='shareholder' first if not)."
    )
    InputSchema = ProposeContributionInput

    async def run(self, ctx: ToolContext, args: ProposeContributionInput) -> dict[str, Any]:
        amount = _check_amount(args.amount, "Contribution")
        ent = _resolve_shareholder(ctx.db, args.shareholder_name)
        txn_date = _resolve_date(args.date)
        payload = {"entity_id": str(ent.id), "entity_name": ent.name, "amount": amount,
                   "date": txn_date, "to_capital": bool(args.to_capital)}
        dest = "share capital" if args.to_capital else "current account"
        summary = f"Proposed contribution: {ent.name} → {amount:,} to {dest} (DR bank / CR {dest})"
        token = _persist(ctx, self.name, payload)
        return _envelope(token, self.name, summary, payload)


# --------------------------------------------------------------------------- #
# 2. Capital increase (افزایش سرمایه)
# --------------------------------------------------------------------------- #
class ProposeCapitalIncreaseInput(BaseModel):
    amount: int = Field(..., gt=0, description="Amount to raise registered capital by, in minor units.")
    source: Literal["retained_earnings", "cash", "revaluation_surplus"] = Field(
        "retained_earnings", description="Where the increase is funded from.")
    date: str | None = Field(None, description="Entry date YYYY-MM-DD; defaults to today.")


class ProposeCapitalIncrease(BaseTool):
    name = "propose_capital_increase"
    category = "proposal"
    description = (
        "Register a pending CAPITAL INCREASE / افزایش سرمایه for the user to confirm. "
        "Raises registered share capital, funded from retained earnings (DR retained "
        "earnings / CR share capital), new cash, or revaluation surplus. Use for "
        "'increase registered capital by 1bn from retained earnings'."
    )
    InputSchema = ProposeCapitalIncreaseInput

    async def run(self, ctx: ToolContext, args: ProposeCapitalIncreaseInput) -> dict[str, Any]:
        amount = _check_amount(args.amount, "Capital increase")
        txn_date = _resolve_date(args.date)
        payload = {"amount": amount, "source": args.source, "date": txn_date}
        src = {"retained_earnings": "retained earnings", "cash": "new cash",
               "revaluation_surplus": "revaluation surplus"}[args.source]
        summary = f"Proposed capital increase: +{amount:,} share capital from {src}"
        token = _persist(ctx, self.name, payload)
        return _envelope(token, self.name, summary, payload)


# --------------------------------------------------------------------------- #
# 3. Dividend declaration (سود سهام) — allocated by cap table
# --------------------------------------------------------------------------- #
class ProposeDeclareDividendInput(BaseModel):
    total_amount: int = Field(..., gt=0, description="Total dividend to declare, in minor units.")
    date: str | None = Field(None, description="Declaration date YYYY-MM-DD; defaults to today.")


class ProposeDeclareDividend(BaseTool):
    name = "propose_declare_dividend"
    category = "proposal"
    description = (
        "Register a pending DIVIDEND DECLARATION / سود سهام for the user to confirm, "
        "allocated across shareholders by their cap-table ownership. Posts, per "
        "shareholder, DR retained earnings / CR dividends payable. Use for "
        "'distribute a 100m dividend to shareholders'. Requires a populated cap table."
    )
    InputSchema = ProposeDeclareDividendInput

    async def run(self, ctx: ToolContext, args: ProposeDeclareDividendInput) -> dict[str, Any]:
        from app.services import equity_service as eq

        amount = _check_amount(args.total_amount, "Dividend")
        txn_date = _resolve_date(args.date)
        try:
            allocs = eq.allocate_by_cap_table(ctx.db, amount)
        except eq.EquityError as e:
            raise ToolError(str(e), code="cap_table_empty") from e
        alloc_preview = []
        for eid, amt in allocs:
            ent = ctx.db.get(Entity, eid)
            alloc_preview.append({"entity_id": str(eid), "entity_name": (ent.name if ent else None), "amount": amt})
        payload = {"total_amount": amount, "date": txn_date, "allocations": alloc_preview}
        split = ", ".join(f"{a['entity_name']} {a['amount']:,}" for a in alloc_preview)
        summary = f"Proposed dividend: {amount:,} from retained earnings, split — {split}"
        token = _persist(ctx, self.name, payload)
        return _envelope(token, self.name, summary, payload)


# --------------------------------------------------------------------------- #
# 4. Shareholder current account (حساب جاری)
# --------------------------------------------------------------------------- #
class ProposeCurrentAccountInput(BaseModel):
    shareholder_name: str = Field(..., min_length=2, max_length=120,
                                  description="The shareholder whose current account moves.")
    amount: int = Field(..., gt=0, description="Amount, in minor units.")
    direction: Literal["in", "out"] = Field(
        ..., description="'in' = shareholder lends to / is owed by the company; "
                         "'out' = shareholder withdraws from the company.")
    date: str | None = Field(None, description="Entry date YYYY-MM-DD; defaults to today.")


class ProposeCurrentAccount(BaseTool):
    name = "propose_shareholder_current_account"
    category = "proposal"
    description = (
        "Register a pending SHAREHOLDER CURRENT ACCOUNT / حساب جاری movement for the "
        "user to confirm. 'out' (withdrawal): DR current account / CR bank; 'in' "
        "(loan to company): DR bank / CR current account. Use for 'شریک Sara "
        "withdrew 50m' (direction='out')."
    )
    InputSchema = ProposeCurrentAccountInput

    async def run(self, ctx: ToolContext, args: ProposeCurrentAccountInput) -> dict[str, Any]:
        amount = _check_amount(args.amount, "Current-account amount")
        ent = _resolve_shareholder(ctx.db, args.shareholder_name)
        txn_date = _resolve_date(args.date)
        payload = {"entity_id": str(ent.id), "entity_name": ent.name, "amount": amount,
                   "date": txn_date, "direction": args.direction}
        verb = "withdrew" if args.direction == "out" else "lent to the company"
        summary = f"Proposed current-account movement: {ent.name} {verb} {amount:,}"
        token = _persist(ctx, self.name, payload)
        return _envelope(token, self.name, summary, payload)


EQUITY_TOOL_NAMES = (
    "propose_shareholder_contribution",
    "propose_capital_increase",
    "propose_declare_dividend",
    "propose_shareholder_current_account",
)


def register_equity_tools(registry) -> None:
    registry.register(ProposeContribution())
    registry.register(ProposeCapitalIncrease())
    registry.register(ProposeDeclareDividend())
    registry.register(ProposeCurrentAccount())

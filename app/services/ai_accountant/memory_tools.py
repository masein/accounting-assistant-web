"""propose_remember_preference (roadmap 2026-09 §5.4): "always put Snapp
under travel" becomes a learned preference — through a confirm card, like
every change the assistant makes. Once saved, the statement categoriser,
``search_accounts`` and ``find_entity`` use it, and it is listed on the chat
page where it can be removed."""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.account import Account, AccountLevel
from app.models.entity import Entity

from .base import BaseTool, ToolContext, ToolError
from .time_tools import _register


class ProposeRememberPreferenceInput(BaseModel):
    description: str = Field(..., min_length=2, max_length=256,
                             description="The wording to recognise, e.g. 'Snapp', 'TESCO STORES', 'اجاره دفتر'.")
    account_code: str | None = Field(None, description="The account to use for it (from search_accounts).")
    entity_id: str | None = Field(None, description="The party to link it to (from find_entity).")


class ProposeRememberPreference(BaseTool):
    name = "propose_remember_preference"
    category = "proposal"
    description = (
        "Remember the user's choice of account and/or party for a recurring wording, so statement rows and "
        "future entries with that wording get it automatically. Use when the user corrects a category or party "
        "and wants it kept ('always', 'from now on', 'همیشه'). Needs account_code and/or entity_id. Returns a "
        "confirm card."
    )
    InputSchema = ProposeRememberPreferenceInput

    async def run(self, ctx: ToolContext, args: ProposeRememberPreferenceInput) -> dict[str, Any]:
        from app.services.statement_categorizer import normalize_narration
        if not normalize_narration(args.description):
            raise ToolError("That wording is too generic to recognise (numbers or common words only).",
                            code="too_generic")
        if not args.account_code and not args.entity_id:
            raise ToolError("Give an account_code and/or an entity_id to remember.", code="nothing_to_remember")
        payload: dict[str, Any] = {"description": args.description.strip()}
        parts = []
        if args.account_code:
            acc = ctx.db.execute(select(Account).where(Account.code == args.account_code.strip())).scalars().first()
            if acc is None or acc.level == AccountLevel.GROUP or acc.is_active is False:
                raise ToolError(f"Account {args.account_code!r} is not a postable account.", code="bad_account")
            payload.update(account_code=acc.code, account_name=acc.name)
            parts.append(f"account {acc.code} {acc.name}")
        if args.entity_id:
            try:
                ent = ctx.db.get(Entity, uuid.UUID(args.entity_id))
            except ValueError:
                ent = None
            if ent is None:
                raise ToolError(f"Entity {args.entity_id!r} not found.", code="bad_entity")
            payload.update(entity_id=str(ent.id), entity_name=ent.name)
            parts.append(f"{ent.type} {ent.name}")
        token = _register(ctx, self.name, payload)
        return {"confirmation_token": str(token), "status": "pending", "preview": payload,
                "summary": f"From now on, \"{payload['description']}\" → " + " and ".join(parts)}


def register_memory_tools(reg) -> None:
    reg.register(ProposeRememberPreference())

"""Proposal tools exposed to Claude.

Unlike read tools, these *register* a pending write — they do **not**
mutate the ledger. The flow per the design brief:

1. Claude calls ``propose_create_transaction`` with a fully-validated
   payload.
2. The tool persists the payload to ``ai_proposals`` and returns a
   ``confirmation_token`` (UUID) + a human-readable summary.
3. The orchestrator surfaces the summary to the user via the chat UI.
   The frontend renders the proposal as an inline action card.
4. The user clicks Confirm. The frontend calls
   ``POST /ai-accountant/execute`` with the token.
5. That endpoint (not in this file) commits the write in a single DB
   transaction, writes the audit log, and returns the receipt.
6. Calling ``execute`` twice with the same token is a no-op (the
   proposal row's ``status == 'executed'`` is the idempotency latch).

Tools in this file only do step 2 — register the proposal.

Currently implemented:

* ``propose_create_transaction`` — the v1 scope from the brief
  (Section 8 "Implementation order"). Future proposal tools
  (entity creation, invoice, mark-paid, soft-delete) follow the same
  pattern and can be added incrementally.
"""
from __future__ import annotations

import uuid
from datetime import date as _date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.ai_accountant import AIProposal
from app.models.entity import Entity

from .base import BaseTool, ToolContext, ToolError
from .date_resolver import resolve_entry_date


PROPOSAL_TTL = timedelta(minutes=10)

# Above this a proposed amount is digit garbage, never a real entry (the
# Asiatech OCR bug proposed 8.45e17). Shared definition with ocr_extract.
from app.services.ocr_extract import MAX_SANE_AMOUNT

# How far the proposed total may diverge from the closest source amount
# before we refuse to silently propose. 10× catches the "300 → 30,000"
# mis-scale and OCR digit-concatenation while tolerating aggregation.
_AMOUNT_DIVERGENCE_FACTOR = 10


def _guard_amount(ctx: ToolContext, proposed_total: int) -> None:
    """Block impossible or wildly-mismatched amounts before a proposal is
    registered. Raises ToolError (which the model surfaces to the user) so a
    bad figure is corrected/confirmed rather than one-click committed."""
    if proposed_total > MAX_SANE_AMOUNT:
        raise ToolError(
            f"Proposed amount {proposed_total:,} is impossibly large and was "
            f"almost certainly mis-read. Re-read the document's labelled total "
            f"or ask the user for the correct amount before proposing.",
            code="amount_out_of_range",
        )
    sources = [int(a) for a in (getattr(ctx, "source_amounts", None) or []) if a]
    if not sources or proposed_total <= 0:
        return
    # Pass if ANY source amount is within the divergence factor of the
    # proposal (handles aggregation across several source numbers).
    for src in sources:
        if src <= 0:
            continue
        hi, lo = max(proposed_total, src), min(proposed_total, src)
        if hi <= lo * _AMOUNT_DIVERGENCE_FACTOR:
            return
    closest = min(sources, key=lambda s: abs(s - proposed_total))
    raise ToolError(
        f"Proposed total {proposed_total:,} does not match the amount found in "
        f"the source (~{closest:,}). Don't propose this figure — confirm the "
        f"correct amount with the user, or use the document's labelled total.",
        code="amount_mismatch",
    )


# ---------------------------------------------------------------------------
# propose_create_transaction
# ---------------------------------------------------------------------------


class ProposedLine(BaseModel):
    account_code: str = Field(
        ...,
        description="Exact account code from the chart of accounts (e.g. '1110', '6110').",
        min_length=1,
    )
    debit: int = Field(
        0, ge=0,
        description=(
            "Debit amount in WHOLE units of the transaction's currency (whole "
            "pounds, whole euros, whole dollars, whole rials) — exactly the "
            "number the user/document states. Do NOT convert to pence/cents/"
            "minor units; do NOT multiply by 100. £300 is 300, not 30000."
        ),
    )
    credit: int = Field(
        0, ge=0,
        description=(
            "Credit amount in WHOLE units of the transaction's currency — "
            "same scale as debit. Never multiply by 100 / convert to minor units."
        ),
    )
    line_description: str | None = Field(None, max_length=512)

    @model_validator(mode="after")
    def _exactly_one_of_debit_or_credit(self) -> "ProposedLine":
        if (self.debit > 0) == (self.credit > 0):
            raise ValueError(
                "Each line must have exactly one of debit > 0 or credit > 0 (never both, never neither)."
            )
        return self


class ProposedEntityLink(BaseModel):
    entity_id: str = Field(..., description="UUID of an existing entity (use find_entity to resolve names first).")
    role: Literal["client", "supplier", "payee", "bank", "employee"] = Field(
        ..., description="The entity's role on this transaction."
    )


class ProposeCreateTransactionInput(BaseModel):
    date: _date = Field(..., description="Transaction date (ISO YYYY-MM-DD). Defaults to today if unstated.")
    description: str = Field(
        ...,
        description="Human-readable summary, e.g. 'Q2 sales — cash receipt' or 'paid Kim Nguyen — salary'.",
        min_length=1, max_length=1024,
    )
    reference: str | None = Field(
        None,
        description="Optional reference number (invoice #, receipt #, voucher #).",
        max_length=128,
    )
    currency: str = Field(
        "IRR",
        description=(
            "ISO currency code (IRR, GBP, USD, EUR…). Use the currency the "
            "user or document stated; otherwise default to the company "
            "reporting currency from get_company_defaults. NEVER relabel a "
            "stated currency (e.g. don't turn GBP into IRR)."
        ),
        min_length=3, max_length=8,
    )
    lines: list[ProposedLine] = Field(
        ...,
        description=(
            "The journal lines. Must be balanced (total debit == total credit) and have "
            "at least two entries (one debit, one credit)."
        ),
        min_length=2,
    )
    entity_links: list[ProposedEntityLink] = Field(
        default_factory=list,
        description="Optional links to entities (client, employee, supplier, bank, payee).",
    )
    attachment_ids: list[str] = Field(
        default_factory=list,
        description=(
            "IDs of document files (invoice/receipt) to attach to this transaction. "
            "When the user attached a document this turn, include its attachment_id "
            "here so the file links to the entry on confirm."
        ),
    )

    @model_validator(mode="after")
    def _balanced(self) -> "ProposeCreateTransactionInput":
        total_dr = sum(ln.debit for ln in self.lines)
        total_cr = sum(ln.credit for ln in self.lines)
        if total_dr != total_cr:
            raise ValueError(
                f"Unbalanced: total debit = {total_dr}, total credit = {total_cr}. "
                f"Adjust the line amounts so they balance before proposing."
            )
        if total_dr == 0:
            raise ValueError("Zero-amount transaction — debit and credit totals are both 0.")
        return self


class ProposeCreateTransaction(BaseTool):
    name = "propose_create_transaction"
    category = "proposal"
    description = (
        "Register a pending journal entry. The transaction is NOT written to the ledger "
        "yet — the user must explicitly confirm via the action card in the chat UI. "
        "This tool returns a confirmation_token and a human-readable summary; the "
        "actual write happens when the user clicks Confirm. "
        "\n\n"
        "Always: (1) resolve account_codes from the chart of accounts (call query_ledger "
        "with a code prefix if you're unsure which account fits). "
        "(2) resolve entity names via find_entity before referencing them in "
        "entity_links. (3) ensure the lines balance (total debit == total credit). "
        "(4) ask the user to confirm ambiguous fields (date, currency, account) before "
        "proposing — never guess on writes."
    )
    InputSchema = ProposeCreateTransactionInput

    async def run(self, ctx: ToolContext, args: ProposeCreateTransactionInput) -> dict[str, Any]:
        # Amount sanity guard (financial safety): a wrong amount a user might
        # one-click confirm is the worst failure. Reject impossible magnitudes
        # outright, and cross-check the proposed total against the amounts in
        # the source (OCR'd document total / numbers in the user's message).
        _guard_amount(ctx, sum(ln.debit for ln in args.lines))

        # Anchor the entry date to reality. The model is unreliable at "today"
        # (it dated a "…today" expense 2023-10-18 and mid-session copied an
        # earlier invoice's date), so resolve relative/missing dates from the
        # server's clock. OCR/document turns keep the document's own date.
        args.date = resolve_entry_date(
            ctx.user_message, args.date,
            has_attachment=bool(getattr(ctx, "attachment_ids", None)),
        )

        # Resolve and validate every account code referenced in the lines.
        codes = [ln.account_code for ln in args.lines]
        existing = ctx.db.execute(
            select(Account).where(Account.code.in_(codes))
        ).scalars().all()
        found: dict[str, Account] = {a.code: a for a in existing}
        missing = [c for c in codes if c not in found]
        if missing:
            raise ToolError(
                f"Account code(s) not found in chart of accounts: {missing}. "
                f"Call query_ledger or get_account_balance to discover the right code.",
                code="account_not_found",
            )

        # Resolve entity links.
        if args.entity_links:
            entity_ids = [link.entity_id for link in args.entity_links]
            try:
                entity_uuids = [uuid.UUID(eid) for eid in entity_ids]
            except (TypeError, ValueError) as e:
                raise ToolError(
                    f"entity_id must be a UUID returned by find_entity: {e}",
                    code="invalid_entity_id",
                ) from e
            ents = ctx.db.execute(
                select(Entity).where(Entity.id.in_(entity_uuids))
            ).scalars().all()
            found_eids = {str(e.id) for e in ents}
            missing_eids = [eid for eid in entity_ids if eid not in found_eids]
            if missing_eids:
                raise ToolError(
                    f"Entity ID(s) not found: {missing_eids}. Call find_entity to "
                    f"discover the right ID.",
                    code="entity_not_found",
                )

        # Persist the proposal. tool_input is the validated dict; we
        # serialise dates as ISO strings so JSON storage round-trips
        # losslessly.
        token = uuid.uuid4()
        payload = args.model_dump(mode="json")  # dates → ISO strings

        # Always link any attachments uploaded with this chat turn, merging
        # them with whatever the model put in attachment_ids. This makes the
        # file follow the entry even if a weaker model forgets to echo the
        # UUID into the proposal (chat-attachment feature).
        merged_attachments = list(
            dict.fromkeys([*payload.get("attachment_ids", []), *(ctx.attachment_ids or [])])
        )
        payload["attachment_ids"] = merged_attachments

        proposal = AIProposal(
            confirmation_token=token,
            user_id=ctx.user_id,
            session_id=ctx.chat_session_id,
            tool_name=self.name,
            tool_input=payload,
            user_message=ctx.user_message,
            status="pending",
        )
        ctx.db.add(proposal)
        ctx.db.commit()
        ctx.db.refresh(proposal)

        # Build the human-readable summary Claude will surface to the user.
        total = sum(ln.debit for ln in args.lines)
        summary_lines = []
        for ln in args.lines:
            acc = found[ln.account_code]
            if ln.debit:
                summary_lines.append(f"  DR {ln.account_code} {acc.name}: {ln.debit:,}")
            else:
                summary_lines.append(f"  CR {ln.account_code} {acc.name}: {ln.credit:,}")
        summary = (
            f"Proposed journal entry on {args.date.isoformat()} ({args.currency}):\n"
            f"  {args.description}\n"
            + "\n".join(summary_lines)
            + f"\n  Total: {total:,} {args.currency}"
        )

        expires_at = (datetime.now(timezone.utc) + PROPOSAL_TTL).isoformat()
        return {
            "confirmation_token": str(token),
            "status": "pending",
            "expires_at": expires_at,
            "summary": summary,
            "tool_name": self.name,
            "preview": payload,
            "next_steps": (
                "Show this proposal to the user as an action card. They must click "
                "Confirm in the UI to commit it; clicking Cancel discards it. The "
                "card is rendered automatically by the chat UI — do not re-paste the "
                "summary verbatim, just briefly explain what you proposed."
            ),
        }


# ---------------------------------------------------------------------------
# Registry helper
# ---------------------------------------------------------------------------


def register_proposal_tools(registry) -> None:
    registry.register(ProposeCreateTransaction())

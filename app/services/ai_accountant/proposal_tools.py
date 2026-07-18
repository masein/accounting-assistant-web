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

import logging
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
from .entity_create import (
    EntityCreateError,
    _next_bank_account_code,
    _validate_name,
    any_staff_cost,
    classify_entity_type,
    normalize_entity_type,
)


logger = logging.getLogger(__name__)

PROPOSAL_TTL = timedelta(minutes=10)

# Above this a proposed amount is digit garbage, never a real entry (the
# Asiatech OCR bug proposed 8.45e17). Shared definition with ocr_extract.
from app.services.ocr_extract import MAX_SANE_AMOUNT

# How far the proposed total may diverge from the closest source amount
# before we refuse to silently propose. 10× catches the "300 → 30,000"
# mis-scale and OCR digit-concatenation while tolerating aggregation.
_AMOUNT_DIVERGENCE_FACTOR = 10

# Phrases that opt a future-dated entry in as an intentional schedule (en +
# the fa/es/ar equivalents). Substring match on the user's message.
_SCHEDULED_TERMS = (
    "schedul", "future", "upcoming", "in advance", "post-dat", "postdat",
    "زمان‌بندی", "زمانبندی", "برنامه‌ریزی", "آینده", "programad", "agendad",
    "futur", "مجدول", "مستقبل",
)


def _mentions_scheduled(message: str | None) -> bool:
    t = (message or "").lower()
    return any(term in t for term in _SCHEDULED_TERMS)


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


# Money-out / money-in cues for the direction guard. An outflow that wrongly
# debits the cash/bank account (a receipt) is the bug we correct.
_OUTFLOW_TERMS = ("paid", "pay ", "paying", "spent", "spend", "withdrew", "withdrawn",
                  "i pay", "we pay", "settle", "settled")
_INFLOW_TERMS = ("received", "receive", "refund", "deposit", "deposited", "paid us",
                 "paid me", "paid into", "into the bank", "into our", "from a client",
                 "from client", "from a customer", "from customer", "reimbursed us",
                 "credited to")


def _cash_predicate_for(ctx: ToolContext):
    from app.services.cash_service import cash_account_predicate
    from app.services.locale_service import get_reporting_locale
    base = cash_account_predicate(get_reporting_locale(ctx.db))
    bank_code = None
    try:
        from app.services.account_resolver import resolve_account_code
        bank_code = resolve_account_code(ctx.db, "bank")
    except Exception:
        pass
    return lambda c: bool(base(c or "")) or (bank_code is not None and c == bank_code)


def _guard_direction(ctx: ToolContext, args) -> None:
    """Stop a reversed cash entry reaching Confirm. When the user clearly
    describes money LEAVING ("I paid …", "paid from the bank") but the proposed
    entry DEBITS the cash/bank account (the direction of a receipt), the whole
    entry was reversed — flip every line so the bank is credited (cash out).
    A supplier/contractor payee must not invert this. Inflows are left alone."""
    msg = (ctx.user_message or "").lower()
    if not msg:
        return
    outflow = any(term in msg for term in _OUTFLOW_TERMS)
    inflow = any(term in msg for term in _INFLOW_TERMS)
    if not outflow or inflow:
        return  # not an unambiguous payment

    is_cash = _cash_predicate_for(ctx)
    cash_lines = [ln for ln in args.lines if is_cash(ln.account_code)]
    if not cash_lines:
        return
    # A genuine payment credits cash. If every cash leg is DEBITED (and none
    # credited) the entry is reversed — flip all lines (keeps it balanced).
    if all(ln.debit > 0 for ln in cash_lines) and not any(ln.credit > 0 for ln in cash_lines):
        for ln in args.lines:
            ln.debit, ln.credit = ln.credit, ln.debit
        logger.info(
            "ai-accountant: auto-corrected reversed payment direction "
            "(message indicated outflow but cash was debited)."
        )


# A customer-receipt cue: money came in from a client/on an invoice.
_CUSTOMER_RECEIPT_TERMS = (
    "from client", "from a client", "from customer", "from a customer",
    "client paid", "customer paid", "paid us", "paid me", "for invoice",
    "on invoice", "invoice payment", "received from",
)


def _guard_receipt_account(ctx: ToolContext, args) -> None:
    """A customer RECEIPT must clear trade debtors (AR), not be credited to
    'sales returns' (a contra-revenue account for refunds). When the message is
    a customer receipt and a line CREDITS the sales-returns account, re-point
    that credit to trade debtors. A genuine refund DEBITS sales returns, so it
    is never touched. Skipped where the locale has no distinct returns account."""
    msg = (ctx.user_message or "").lower()
    if not msg:
        return
    inflow = any(term in msg for term in _INFLOW_TERMS)
    customer = any(term in msg for term in _CUSTOMER_RECEIPT_TERMS)
    if not inflow or not customer:
        return
    try:
        from app.services.account_resolver import resolve_account_code
        returns_code = resolve_account_code(ctx.db, "sales_returns")
        revenue_code = resolve_account_code(ctx.db, "revenue")
        ar_code = resolve_account_code(ctx.db, "ar")
    except Exception:
        return
    # If 'sales returns' isn't a distinct contra account (e.g. Iran maps it to
    # revenue), crediting it on a receipt is acceptable — nothing to steer.
    if returns_code == revenue_code:
        return
    steered = False
    for ln in args.lines:
        if ln.credit > 0 and ln.account_code == returns_code:
            ln.account_code = ar_code   # clear the receivable instead
            steered = True
    if steered:
        logger.info(
            "ai-accountant: steered a customer receipt off sales-returns (%s) "
            "to trade debtors (%s).", returns_code, ar_code
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


class ProposedNewEntity(BaseModel):
    """A party to CREATE on Confirm and link to the transaction. Use only when
    find_entity found no match and the user clearly named a real party."""
    name: str = Field(..., min_length=2, max_length=80,
                      description="The party's name, e.g. 'Dan Campbell'.")
    type: Literal["client", "supplier", "employee", "bank"] = Field(
        ...,
        description=(
            "client | supplier | employee | bank. Map a contractor / freelancer "
            "/ subcontractor / vendor to 'supplier', and a customer to 'client'."
        ),
    )
    role: Literal["client", "supplier", "payee", "bank", "employee"] | None = Field(
        None, description="Role on this transaction; defaults from type when omitted."
    )
    existing_account_code: str | None = Field(
        None,
        description="For type='bank' ONLY: link to this existing cash account code "
                    "instead of creating a new GL bank account.",
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
        description="Optional links to EXISTING entities (resolved via find_entity).",
    )
    new_entities: list[ProposedNewEntity] = Field(
        default_factory=list,
        description=(
            "New parties to CREATE on Confirm and link to this entry. Use when "
            "find_entity returned no usable match and the user is clearly naming "
            "a real party (e.g. 'Dan is a contractor', 'new supplier Acme'). The "
            "entity is created only when the user confirms — never silently."
        ),
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
        scheduled = _mentions_scheduled(ctx.user_message)
        args.date = resolve_entry_date(
            ctx.user_message, args.date,
            has_attachment=bool(getattr(ctx, "attachment_ids", None)),
            scheduled=scheduled,
        )

        # Future-date guard, enforced server-side against the REAL today (the
        # model judged "future" against its training-era clock and wrongly
        # refused valid past dates). A past/today date always records; reject
        # only a date >1 day ahead that the user didn't say is scheduled.
        if args.date > _date.today() + timedelta(days=1) and not scheduled:
            raise ToolError(
                f"{args.date.isoformat()} is in the future. I can only record past or "
                f"present dates unless you say it's scheduled (e.g. 'schedule it for …').",
                code="future_date",
            )

        # Block proposing into a closed (locked) period.
        from app.services.period_service import get_closed_period
        locked_through = get_closed_period(ctx.db)
        if locked_through is not None and args.date <= locked_through:
            raise ToolError(
                f"The books are closed through {locked_through.isoformat()}, so I can't "
                f"record an entry dated {args.date.isoformat()}. Ask an admin to reopen "
                f"the period, or use a later date.",
                code="period_locked",
            )

        # Direction / account guards run BEFORE validation so any corrected
        # account code is validated and included in the summary lookup.
        #  - a payment ("I paid …") must CREDIT cash, never debit it; flip a
        #    wholesale-reversed entry (the supplier-payment bug).
        #  - a customer receipt must clear trade debtors, not sales returns.
        _guard_direction(ctx, args)
        _guard_receipt_account(ctx, args)

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

        # Validate and preview any new entities to be created on Confirm. The
        # type is decided DETERMINISTICALLY (a freelancer/contractor is a
        # supplier, not an employee), overriding the model's non-deterministic
        # pick, using the message + description + whether the entry debits a
        # staff-cost account.
        debit_codes = [ln.account_code for ln in args.lines if ln.debit > 0]
        staff_cost = any_staff_cost(ctx.db, debit_codes) if debit_codes else None
        classify_text = f"{ctx.user_message or ''} {args.description or ''}"
        new_entity_previews: list[dict[str, Any]] = []
        for ne in args.new_entities:
            try:
                clean = _validate_name(ne.name)
            except EntityCreateError as e:
                raise ToolError(str(e), code="invalid_entity_name") from e
            etype = classify_entity_type(ne.type, text=classify_text, staff_cost=staff_cost)
            preview = {
                "name": clean, "type": etype,
                "role": (ne.role or _default_role_for_type(etype)),
            }
            if etype == "bank":
                # Preview the GL cash account the bank will use (existing or the
                # next free code) — created for real only on Confirm.
                if ne.existing_account_code and ctx.db.execute(
                    select(Account.id).where(Account.code == ne.existing_account_code.strip())
                ).first():
                    preview["account_code"] = ne.existing_account_code.strip()
                    preview["account_existing"] = True
                else:
                    from app.services.locale_service import get_reporting_locale
                    try:
                        preview["account_code"] = _next_bank_account_code(
                            ctx.db, (get_reporting_locale(ctx.db) or "default"))
                        preview["account_existing"] = False
                    except EntityCreateError:
                        preview["account_code"] = None
            new_entity_previews.append(preview)

        # Persist the proposal. tool_input is the validated dict; we
        # serialise dates as ISO strings so JSON storage round-trips
        # losslessly.
        token = uuid.uuid4()
        payload = args.model_dump(mode="json")  # dates → ISO strings
        # Persist the deterministically-classified types (and the resolved role)
        # so Confirm creates each party with the corrected type, not the model's.
        for i, prev in enumerate(new_entity_previews):
            if i < len(payload.get("new_entities", [])):
                payload["new_entities"][i]["type"] = prev["type"]
                payload["new_entities"][i]["role"] = prev["role"]

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
        for p in new_entity_previews:
            if p["type"] == "bank" and p.get("account_code"):
                verb = "use" if p.get("account_existing") else "new"
                summary += f"\n  Will create bank: {p['name']} → {verb} cash account {p['account_code']}"
            else:
                summary += f"\n  Will create {p['type']}: {p['name']}"

        # Expense-approval routing: when the proposed total exceeds the company
        # approval threshold, flag the card as needing approval. The entry still
        # only commits on an explicit user Confirm — this just surfaces that an
        # over-threshold expense should be routed/reviewed, never auto-posted.
        needs_approval = False
        try:
            from app.services.expense_settings import get_approval_threshold
            threshold = get_approval_threshold(ctx.db)
            if threshold > 0 and total > threshold:
                needs_approval = True
                summary += (
                    f"\n  ⚠ Over the approval threshold ({threshold:,} {args.currency}) — "
                    f"needs approval before it is posted."
                )
        except Exception:  # settings are best-effort; never block a proposal
            pass

        expires_at = (datetime.now(timezone.utc) + PROPOSAL_TTL).isoformat()
        return {
            "confirmation_token": str(token),
            "status": "pending",
            "needs_approval": needs_approval,
            "new_entities": new_entity_previews,
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
# propose_create_entity (standalone master-data create)
# ---------------------------------------------------------------------------


def _default_role_for_type(etype: str) -> str:
    return {"client": "client", "supplier": "supplier", "employee": "employee",
            "bank": "bank", "shareholder": "shareholder"}.get(etype, "supplier")


class ProposeCreateEntityInput(BaseModel):
    name: str = Field(..., min_length=2, max_length=80,
                      description="The party's name, e.g. 'Acme Ltd' or 'Dan Campbell'.")
    type: Literal["client", "supplier", "employee", "bank", "shareholder"] = Field(
        ...,
        description=(
            "client | supplier | employee | bank | shareholder. Map contractor / "
            "freelancer / subcontractor / vendor → 'supplier'; customer → 'client'; "
            "partner / founder / investor / سهامدار → 'shareholder' (NEVER "
            "'employee' — equity holders follow different accounting)."
        ),
    )
    existing_account_code: str | None = Field(
        None,
        description="For type='bank' ONLY: link to this existing cash account code "
                    "instead of creating a new GL bank account.",
    )


class ProposeCreateEntity(BaseTool):
    name = "propose_create_entity"
    category = "proposal"
    description = (
        "Register a pending NEW ENTITY (client / supplier / employee / bank) for "
        "the user to confirm. Creating an entity is a master-data write, so it is "
        "NOT saved until the user clicks Confirm on the action card. Use this when "
        "find_entity found no match and the user is onboarding a real party with no "
        "transaction (e.g. 'add Acme Ltd as a client'). To create a party AND record "
        "a payment in one step, use propose_create_transaction's new_entities field "
        "instead. For type='bank', a GL cash account is created (or linked) so the "
        "bank can immediately be used as a payment source."
    )
    InputSchema = ProposeCreateEntityInput

    async def run(self, ctx: ToolContext, args: ProposeCreateEntityInput) -> dict[str, Any]:
        try:
            clean = _validate_name(args.name)
        except EntityCreateError as e:
            raise ToolError(str(e), code="invalid_entity_name") from e
        # Deterministic type: a freelancer/contractor named in the message is a
        # supplier even if the model said "employee".
        etype = classify_entity_type(args.type, text=(ctx.user_message or ""))

        preview: dict[str, Any] = {"name": clean, "type": etype,
                                   "role": _default_role_for_type(etype)}
        summary = f"Proposed new {etype}: {clean}"
        if etype == "bank":
            from app.services.locale_service import get_reporting_locale
            if args.existing_account_code and ctx.db.execute(
                select(Account.id).where(Account.code == args.existing_account_code.strip())
            ).first():
                preview["account_code"] = args.existing_account_code.strip()
                preview["account_existing"] = True
                summary = f"Proposed new bank: {clean} → use cash account {preview['account_code']}"
            else:
                try:
                    preview["account_code"] = _next_bank_account_code(
                        ctx.db, (get_reporting_locale(ctx.db) or "default"))
                    preview["account_existing"] = False
                    summary = f"Proposed new bank: {clean} → new cash account {preview['account_code']}"
                except EntityCreateError:
                    preview["account_code"] = None

        token = uuid.uuid4()
        payload = {"name": clean, "type": etype,
                   "existing_account_code": args.existing_account_code}
        proposal = AIProposal(
            confirmation_token=token, user_id=ctx.user_id,
            session_id=ctx.chat_session_id, tool_name=self.name,
            tool_input=payload, user_message=ctx.user_message, status="pending",
        )
        ctx.db.add(proposal)
        ctx.db.commit()
        ctx.db.refresh(proposal)

        expires_at = (datetime.now(timezone.utc) + PROPOSAL_TTL).isoformat()
        return {
            "confirmation_token": str(token),
            "status": "pending",
            "new_entities": [preview],
            "expires_at": expires_at,
            "summary": summary,
            "tool_name": self.name,
            "preview": payload,
            "next_steps": (
                "Show this as an action card. The entity is created only when the "
                "user clicks Confirm. Briefly say what you proposed and end your turn."
            ),
        }


# ---------------------------------------------------------------------------
# propose_update_entity (rename / retype an EXISTING party)
# ---------------------------------------------------------------------------


class ProposeUpdateEntityInput(BaseModel):
    entity_id: str | None = Field(
        None, description="The entity's id (from find_entity / list_entities). Preferred.")
    current_name: str | None = Field(
        None, min_length=2, max_length=120,
        description="Exact current name, used to look the entity up when no id is given.")
    new_name: str | None = Field(
        None, min_length=2, max_length=80, description="The new name, when renaming.")
    new_type: Literal["client", "supplier", "employee", "bank", "shareholder"] | None = Field(
        None, description="The corrected type, when re-classifying (e.g. employee → shareholder).")

    @model_validator(mode="after")
    def _check(self):
        if not self.entity_id and not self.current_name:
            raise ValueError("Give entity_id or current_name to identify the entity.")
        if self.new_name is None and self.new_type is None:
            raise ValueError("Nothing to change — give new_name and/or new_type.")
        return self


class ProposeUpdateEntity(BaseTool):
    name = "propose_update_entity"
    category = "proposal"
    description = (
        "Rename or re-classify an EXISTING entity (client / supplier / employee / "
        "bank / shareholder), pending the user's confirmation. USE THIS — never "
        "propose_create_entity — when the user wants to change a party that "
        "already exists (rename it, fix a default name, or correct its type, "
        "e.g. an employee who is actually a shareholder). Look the entity up "
        "first with find_entity, then pass its entity_id here."
    )
    InputSchema = ProposeUpdateEntityInput

    async def run(self, ctx: ToolContext, args: ProposeUpdateEntityInput) -> dict[str, Any]:
        from app.models.entity import Entity

        entity = None
        if args.entity_id:
            try:
                entity = ctx.db.get(Entity, uuid.UUID(str(args.entity_id)))
            except (ValueError, TypeError):
                entity = None
        if entity is None and args.current_name:
            matches = ctx.db.execute(
                select(Entity).where(Entity.name.ilike(args.current_name.strip()))
            ).scalars().all()
            if len(matches) == 1:
                entity = matches[0]
            elif len(matches) > 1:
                raise ToolError(
                    f"Multiple entities are named {args.current_name!r} — use "
                    "find_entity and pass the exact entity_id.",
                    code="ambiguous_entity",
                )
        if entity is None:
            raise ToolError(
                "Entity not found. Use find_entity to locate it, then pass its "
                "entity_id.", code="entity_not_found",
            )

        new_name = None
        if args.new_name is not None:
            try:
                new_name = _validate_name(args.new_name)
            except EntityCreateError as e:
                raise ToolError(str(e), code="invalid_entity_name") from e

        changes = []
        if new_name and new_name != entity.name:
            changes.append(f"rename '{entity.name}' → '{new_name}'")
        if args.new_type and args.new_type != entity.type:
            changes.append(f"type {entity.type} → {args.new_type}")
        if not changes:
            raise ToolError("The entity already matches the requested values — nothing to change.",
                            code="no_change")

        token = uuid.uuid4()
        payload = {"entity_id": str(entity.id), "new_name": new_name,
                   "new_type": args.new_type,
                   "old_name": entity.name, "old_type": entity.type}
        proposal = AIProposal(
            confirmation_token=token, user_id=ctx.user_id,
            session_id=ctx.chat_session_id, tool_name=self.name,
            tool_input=payload, user_message=ctx.user_message, status="pending",
        )
        ctx.db.add(proposal)
        ctx.db.commit()
        ctx.db.refresh(proposal)

        summary = f"Proposed update to {entity.name}: " + "; ".join(changes)
        expires_at = (datetime.now(timezone.utc) + PROPOSAL_TTL).isoformat()
        return {
            "confirmation_token": str(token),
            "status": "pending",
            "expires_at": expires_at,
            "summary": summary,
            "tool_name": self.name,
            "preview": payload,
            "next_steps": (
                "Show this as an action card. The change applies only when the "
                "user clicks Confirm. Briefly say what will change and end your turn."
            ),
        }


# ---------------------------------------------------------------------------
# Registry helper
# ---------------------------------------------------------------------------


def register_proposal_tools(registry) -> None:
    registry.register(ProposeCreateTransaction())
    registry.register(ProposeCreateEntity())
    registry.register(ProposeUpdateEntity())

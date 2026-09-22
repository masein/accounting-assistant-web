"""Read tool: review a bank statement against the books.

Gives the assistant the same contradiction list the Bank Statements page
shows (``POST /brain/bank-statements/{id}/review``) so it can walk the user
through the differences one at a time and propose each fix through the
normal confirm-gated proposal tools. Read-only: it reconciles (row statuses)
but never posts.
"""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.bank_statement import BankStatement
from app.services.ai_accountant.base import BaseTool, ToolContext, ToolError


class ReviewBankStatementInput(BaseModel):
    statement_id: str | None = Field(
        None,
        description=(
            "UUID of the imported statement (given in the 'Bank statement imported' "
            "context or by the user). Omit to review the most recently imported statement."
        ),
    )
    max_findings: int = Field(
        12, ge=1, le=40,
        description="How many findings to return (most important first).",
    )


class ReviewBankStatement(BaseTool):
    name = "review_bank_statement"
    category = "read"
    description = (
        "Check an imported bank statement against the books and list the contradictions, "
        "each with a suggested fix: 'unrecorded' bank rows (no book entry → propose posting "
        "them with propose_create_transaction, passing bank_statement_row_id), "
        "'needs_confirmation' rows (same amount as a book entry, different date/narration → "
        "tell the user to approve the match), 'amount_mismatch' (bank and book disagree on "
        "the amount → explain and ask which is right), 'missing_in_bank' (book entry the bank "
        "never saw → ask whether it really happened; propose_reverse_transaction if not), "
        "'duplicate' (already imported, no action) and 'balance_gap' (closing balance vs book "
        "balance). Returns the bank account code to use as the cash leg. Call this when the "
        "user asks to check, review, reconcile or fix a statement, then handle ONE finding "
        "per turn."
    )
    InputSchema = ReviewBankStatementInput

    async def run(self, ctx: ToolContext, args: ReviewBankStatementInput) -> dict[str, Any]:
        from app.services.statement_review import build_statement_review

        db = ctx.db
        stmt = None
        if args.statement_id:
            try:
                stmt = db.get(BankStatement, uuid.UUID(str(args.statement_id)))
            except (ValueError, TypeError):
                raise ToolError(f"Invalid statement_id: {args.statement_id!r}")
            if stmt is None:
                raise ToolError("No bank statement with that id. Ask the user to upload it first.")
        else:
            stmt = db.execute(
                select(BankStatement).order_by(BankStatement.created_at.desc())
            ).scalars().first()
            if stmt is None:
                raise ToolError(
                    "No bank statement has been imported yet. Ask the user to attach the "
                    "statement file (PDF, image, CSV or Excel) in this chat."
                )

        review = build_statement_review(db, stmt)
        data = review.model_dump(mode="json")
        findings = data.pop("findings")
        total = len(findings)
        data["findings"] = findings[: args.max_findings]
        data["findings_total"] = total
        data["findings_truncated"] = total > args.max_findings
        data["how_to_fix"] = {
            "post_row": (
                "propose_create_transaction with bank_statement_row_id=<row_id>; a bank DEBIT "
                "(direction 'out') is Dr <suggested/expense account> / Cr <bank_account_code>, a "
                "bank CREDIT ('in') is Dr <bank_account_code> / Cr <revenue or receivable>. Use "
                "the statement's currency and the row's date and description."
            ),
            "approve_match": "No posting: tell the user it is the same entry and to approve it on the Bank Statements page.",
            "review_entry": "Explain both sides and ask the user which is right before proposing anything.",
        }
        return data


def register_statement_tools(registry) -> None:
    registry.register(ReviewBankStatement())

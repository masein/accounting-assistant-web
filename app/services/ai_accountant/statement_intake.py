"""Chat smart-intake for bank statements.

A statement PDF / image dropped into the AI chat used to go down the
receipt-OCR path, which looks for ONE vendor / date / total — so the model
saw a wall of rows, said "I can't extract them accurately" and asked the
user to type the transactions by hand (QA finding 1). Now the turn is
recognised deterministically and routed through the real import pipeline:

1. ``looks_like_bank_statement`` on the filename, the user's message and the
   document's embedded text.
2. ``import_statement_bytes`` — same parse / dedup / categorise / persist as
   the Bank Statements page upload.
3. ``build_statement_review`` — check it against the books straight away.
4. A deterministic reply (no LLM call, nothing posted) plus an ``intake``
   card the chat renders with "Fix step by step" and "Open in Bank
   statements" buttons.

Users without books-write permission fall through to the ordinary OCR
context, exactly as before.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.transaction import TransactionAttachment

logger = logging.getLogger(__name__)


@dataclass
class StatementTurn:
    text: str
    intake: dict[str, Any]


def _fmt(n: int | None) -> str:
    return f"{int(n):,}" if n is not None else "—"


def _reply(lang: str, intake: dict[str, Any]) -> str:
    c = intake.get("counts") or {}
    bank = intake.get("bank_name") or ""
    rows = intake.get("total_rows") or 0
    unrec, conf, mism, miss, dup = (
        c.get("unrecorded", 0), c.get("needs_confirmation", 0),
        c.get("amount_mismatch", 0), c.get("missing_in_bank", 0), c.get("duplicates", 0),
    )
    bal = intake.get("balance") or {}
    gap = bal.get("gap")
    if lang == "fa":
        parts = [f"صورتحساب {bank} را خواندم: {rows} ردیف"]
        span = ""
        if intake.get("from_date") and intake.get("to_date"):
            span = f" ({intake['from_date']} تا {intake['to_date']})"
        parts[0] += span + " و با دفاتر مقایسه کردم."
        bits = []
        if c.get("matched"):
            bits.append(f"{c['matched']} ردیف قبلاً در دفاتر ثبت شده")
        if unrec:
            bits.append(f"{unrec} ردیف در دفاتر نیست")
        if conf:
            bits.append(f"{conf} ردیف احتمالاً همان سند ثبت‌شده است (تاریخ یا شرح فرق دارد)")
        if mism:
            bits.append(f"{mism} ردیف با سند دفاتر در مبلغ اختلاف دارد")
        if miss:
            bits.append(f"{miss} سند در دفاتر هست که در صورتحساب نیست")
        if dup:
            bits.append(f"{dup} ردیف قبلاً از صورتحساب دیگری وارد شده")
        if bits:
            parts.append("؛ ".join(bits) + ".")
        if gap:
            parts.append(f"مانده پایانی صورتحساب با دفاتر {_fmt(abs(gap))} اختلاف دارد"
                         + (" که با ثبت ردیف‌های جدید صفر می‌شود." if bal.get("explained") else "."))
        if intake.get("clean"):
            parts.append("همه‌چیز با دفاتر می‌خواند؛ کاری لازم نیست.")
        else:
            parts.append("برای رفع قدم‌به‌قدم روی «رفع قدم‌به‌قدم» بزنید یا بگویید «بعدی»؛ چیزی بدون تأیید شما ثبت نمی‌شود.")
        return " ".join(parts)
    parts = [f"I read your {bank} statement: {rows} rows"]
    if intake.get("from_date") and intake.get("to_date"):
        parts[0] += f" ({intake['from_date']} to {intake['to_date']})"
    parts[0] += " and checked them against the books."
    bits = []
    if c.get("matched"):
        bits.append(f"{c['matched']} already recorded")
    if unrec:
        bits.append(f"{unrec} not in the books")
    if conf:
        bits.append(f"{conf} probably the same entry with a different date or narration")
    if mism:
        bits.append(f"{mism} with a different amount than the books")
    if miss:
        bits.append(f"{miss} book entr{'y' if miss == 1 else 'ies'} the bank never shows")
    if dup:
        bits.append(f"{dup} imported before")
    if bits:
        parts.append("; ".join(bits) + ".")
    if gap:
        parts.append(f"The closing balance differs from the books by {_fmt(abs(gap))}"
                     + (", which posting the new rows would close." if bal.get("explained") else "."))
    if intake.get("clean"):
        parts.append("Everything matches — nothing to do.")
    else:
        parts.append("Click \"Fix step by step\" (or say \"next\") and I'll take the differences one at a time; nothing is posted without your confirmation.")
    return " ".join(parts)


def _duplicate_reply(lang: str, errors: list[str]) -> str:
    note = errors[0] if errors else ""
    if lang == "fa":
        return "این فایل قبلاً وارد شده است. " + note + " می‌توانید همان صورتحساب را از کارت زیر باز کنید یا با دفاتر بررسی کنید."
    return "This file was already imported. " + note + " Open it from the card below, or ask me to check it against the books."


async def maybe_statement_intake(
    db: Session,
    *,
    user_role: str | None,
    attachments: list[TransactionAttachment],
    message: str,
    lang: str,
) -> StatementTurn | None:
    """Return a deterministic turn when one of ``attachments`` is a bank
    statement the user may import; ``None`` to fall through to OCR."""
    from app.core.permissions import Perm, role_can
    from app.services.ocr_extract import _extract_pdf_text
    from app.services.statement_import import (
        guess_bank_name,
        import_statement_bytes,
        looks_like_bank_statement,
    )

    if not role_can(user_role, Perm.BOOKS_WRITE):
        return None

    for att in attachments:
        ctype = (att.content_type or "").lower()
        if ctype not in ("application/pdf", "image/jpeg", "image/png", "image/webp"):
            continue
        path = Path(att.file_path)
        if not path.exists():
            continue
        text = _extract_pdf_text(path) if ctype == "application/pdf" else ""
        if not looks_like_bank_statement(filename=att.file_name or "", message=message or "", text=text):
            continue

        bank_name = guess_bank_name(att.file_name or "", message or "", text[:4000])
        try:
            content = path.read_bytes()
            result = await import_statement_bytes(
                db, content=content, filename=att.file_name or path.name,
                content_type=ctype, bank_name=bank_name,
            )
        except Exception as e:  # noqa: BLE001 — a parse failure is a soft outcome in chat
            detail = getattr(e, "detail", None) or str(e)
            logger.warning("chat statement import failed for %s: %s", att.file_name, detail)
            text_out = (
                f"صورتحساب را شناختم اما نتوانستم ردیف‌ها را بخوانم: {detail} "
                "آن را به‌صورت CSV یا Excel از بانک خروجی بگیرید و دوباره پیوست کنید."
                if lang == "fa" else
                f"I recognised a bank statement but couldn't read its rows: {detail} "
                "Export it from the bank as CSV or Excel and attach that instead."
            )
            return StatementTurn(text=text_out, intake={
                "kind": "bank_statement", "status": "failed", "bank_name": bank_name,
                "file_name": att.file_name, "error": str(detail),
            })

        if result.status == "duplicate" and result.duplicate_of:
            intake = {
                "kind": "bank_statement", "status": "duplicate",
                "statement_id": str(result.duplicate_of), "bank_name": bank_name,
                "file_name": att.file_name, "errors": list(result.errors or []),
            }
            return StatementTurn(text=_duplicate_reply(lang, list(result.errors or [])), intake=intake)
        if result.status == "needs_mapping" or result.id is None:
            text_out = (
                "ستون‌های این صورتحساب را نشناختم. آن را در صفحهٔ «صورتحساب‌های بانکی» بارگذاری کنید تا ستون‌ها را دستی مشخص کنید."
                if lang == "fa" else
                "I couldn't tell the statement's columns apart. Upload it on the Bank statements page to map the columns by hand."
            )
            return StatementTurn(text=text_out, intake={
                "kind": "bank_statement", "status": "needs_mapping", "bank_name": bank_name,
                "file_name": att.file_name,
            })

        from app.models.bank_statement import BankStatement
        from app.services.statement_review import build_statement_review

        stmt = db.get(BankStatement, result.id)
        review = build_statement_review(db, stmt)
        rv = review.model_dump(mode="json")
        intake = {
            "kind": "bank_statement",
            "status": "imported",
            "statement_id": str(result.id),
            "bank_name": bank_name,
            "file_name": att.file_name,
            "total_rows": review.total_rows,
            "from_date": rv.get("from_date"),
            "to_date": rv.get("to_date"),
            "currency": review.currency,
            "counts": rv.get("counts") or {},
            "balance": rv.get("balance"),
            "clean": review.clean,
            "findings_preview": (rv.get("findings") or [])[:3],
        }
        return StatementTurn(text=_reply(lang, intake), intake=intake)
    return None

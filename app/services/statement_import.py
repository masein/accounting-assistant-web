"""Bank statement import + reconcile as a service.

The upload and reconcile endpoints in ``app/api/brain.py`` used to hold this
logic inline, which meant the only way to import a statement was the Bank
Statements page. The AI chat needs the same pipeline for a statement PDF the
user drops into the conversation (QA: the assistant answered "I can't
extract them accurately" and asked the user to type the rows), so the body
lives here and both callers share it:

* ``import_statement_bytes`` — parse (CSV / Excel / vision OCR of an image or
  PDF), file-level duplicate gate, row-level dedup, chart-aware
  categorisation, optional LLM tier, persist. Returns the same response the
  upload endpoint always has.
* ``reconcile_statement_rows`` — match every row against the ledger, count
  the outcome, compute the exact unreconciled difference and the fee /
  interest suggestions. Rows the user already posted, approved or skipped
  keep their status; a re-run never hands them back as new work.
* ``looks_like_bank_statement`` — the cheap, deterministic test that decides
  whether an attached image / PDF is a statement (route to this pipeline)
  or a receipt / invoice (route to the OCR-to-proposal path).
"""
from __future__ import annotations

import hashlib
import json as _json
import logging
import re
import unicodedata
import uuid
from datetime import date
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.account import Account
from app.models.bank_statement import BankStatement, BankStatementRow
from app.schemas.brain import BankStatementUploadResponse, FeeSuggestion, ReconcileResponse

logger = logging.getLogger(__name__)

SUPPORTED_STATEMENT_EXTENSIONS = (".csv", ".tsv", ".xlsx", ".xls", ".jpg", ".jpeg", ".png", ".webp", ".pdf")


# ---------------------------------------------------------------------------
# Detection — is this attachment a bank statement?
# ---------------------------------------------------------------------------

# Words that only ever appear on a statement, in the four UI languages plus
# the Iranian banks a user is likely to name in the message or filename.
_STATEMENT_WORDS = (
    "statement", "transactions", "gardesh", "extracto", "movimientos",
    "صورتحساب", "صورت حساب", "صورت‌حساب", "گردش", "تراکنش", "کشف حساب", "كشف حساب",
    "حركات الحساب",
)
_BANK_WORDS = (
    "bank", "mellat", "melli", "saman", "tejarat", "saderat", "pasargad", "parsian",
    "sepah", "refah", "keshavarzi", "maskan", "ayandeh", "shahr", "sina", "eghtesad",
    "resalat", "blu", "hsbc", "barclays", "lloyds", "natwest", "monzo", "revolut",
    "بانک", "ملت", "ملی", "سامان", "تجارت", "صادرات", "پاسارگاد", "پارسیان", "سپه",
    "رفاه", "کشاورزی", "مسکن", "آینده", "شهر", "سینا", "اقتصاد نوین", "رسالت", "بلو",
    "banco", "بنك", "مصرف",
)
_HEADER_WORDS = (
    "balance", "debit", "credit", "withdrawal", "deposit", "closing balance",
    "مانده", "بدهکار", "بستانکار", "واریز", "برداشت", "گردش",
    "saldo", "cargo", "abono", "الرصيد", "مدين", "دائن",
)
_DATE_RE = re.compile(r"(?:1[34]\d{2}|20\d{2})[/\-.](?:0?[1-9]|1[0-2])[/\-.](?:0?[1-9]|[12]\d|3[01])(?!\d)")
_AMOUNT_RE = re.compile(r"(?<![\d,])\d{1,3}(?:,\d{3}){1,}(?![\d,])")
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _norm(text: str) -> str:
    # NFKC folds Arabic presentation forms (ﺑﺪﻫﮑﺎﺭ) back to base letters so the
    # header words match what pypdf extracts from Iranian bank PDFs.
    return unicodedata.normalize("NFKC", text or "").translate(_DIGITS).lower()


def statement_text_score(text: str) -> tuple[int, int]:
    """(number of dated-row tokens, number of header words) in ``text``."""
    t = _norm(text)
    dates = len(_DATE_RE.findall(t))
    headers = sum(1 for w in _HEADER_WORDS if w in t)
    return dates, headers


def looks_like_bank_statement(*, filename: str = "", message: str = "", text: str = "") -> bool:
    """Cheap, deterministic statement detector.

    * The filename or the user's message names a statement or a bank
      ("mellat-transactions.pdf", "these are the Mellat transactions") → yes.
    * The document's own text has a table of dated rows with money amounts
      and statement header words (balance / بدهکار / مانده …) → yes.
    * Anything else (a receipt with one total, a contract) → no.
    """
    hint = _norm(f"{filename} {message}")
    if any(w in hint for w in _STATEMENT_WORDS):
        return True
    if any(w in hint for w in _BANK_WORDS) and ("add" in hint or "import" in hint or "ثبت" in hint or "اضافه" in hint or "record" in hint):
        return True
    if not text:
        return False
    dates, headers = statement_text_score(text)
    amounts = len(_AMOUNT_RE.findall(_norm(text)))
    return dates >= 5 and amounts >= 5 and headers >= 2


_BANK_NAMES = (
    ("mellat", "Mellat"), ("ملت", "Mellat"), ("melli", "Melli"), ("ملی", "Melli"),
    ("saman", "Saman"), ("سامان", "Saman"), ("tejarat", "Tejarat"), ("تجارت", "Tejarat"),
    ("saderat", "Saderat"), ("صادرات", "Saderat"), ("pasargad", "Pasargad"), ("پاسارگاد", "Pasargad"),
    ("parsian", "Parsian"), ("پارسیان", "Parsian"), ("sepah", "Sepah"), ("سپه", "Sepah"),
    ("refah", "Refah"), ("رفاه", "Refah"), ("keshavarzi", "Keshavarzi"), ("کشاورزی", "Keshavarzi"),
    ("maskan", "Maskan"), ("مسکن", "Maskan"), ("ayandeh", "Ayandeh"), ("آینده", "Ayandeh"),
    ("shahr", "Shahr"), ("شهر", "Shahr"), ("sina", "Sina"), ("سینا", "Sina"),
    ("resalat", "Resalat"), ("رسالت", "Resalat"), ("blu", "Blu"), ("بلو", "Blu"),
    ("hsbc", "HSBC"), ("barclays", "Barclays"), ("lloyds", "Lloyds"), ("natwest", "NatWest"),
    ("monzo", "Monzo"), ("revolut", "Revolut"),
)


def guess_bank_name(*sources: str) -> str:
    """Best-effort bank name from the filename / message / document text."""
    hint = _norm(" ".join(s for s in sources if s))
    for key, name in _BANK_NAMES:
        if key in hint:
            return name
    return "Unknown"


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

async def import_statement_bytes(
    db: Session,
    *,
    content: bytes,
    filename: str,
    content_type: str | None,
    bank_name: str = "Unknown",
    column_map: dict[str, int] | str | None = None,
    confirm_duplicate: bool = False,
) -> BankStatementUploadResponse:
    """Parse and persist a bank statement. Raises HTTPException for the same
    conditions the upload endpoint always did (bad type, unreadable file,
    zero rows); returns ``status="duplicate"`` / ``"needs_mapping"``
    responses instead of raising for those soft outcomes."""
    filename = filename or "unknown"
    ext = Path(filename).suffix.lower()

    from app.services.bank_statement_parser import (
        classify_transaction,
        parse_csv,
        parse_excel,
        parse_ocr_rows,
    )

    if ext not in SUPPORTED_STATEMENT_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    # File-level duplicate detection: hash the raw bytes. If we've imported an
    # identical file before, flag it and ask the user to confirm rather than
    # silently importing the same transactions twice.
    content_hash = hashlib.sha256(content).hexdigest()
    if not confirm_duplicate:
        existing = db.execute(
            select(BankStatement).where(BankStatement.content_hash == content_hash)
            .order_by(BankStatement.created_at.desc())
        ).scalars().first()
        if existing:
            return BankStatementUploadResponse(
                id=None,
                status="duplicate",
                total_rows=0,
                bank_name=bank_name,
                source_type=ext.lstrip("."),
                duplicate=True,
                duplicate_of=existing.id,
                errors=[
                    f"This file was already imported on {existing.created_at:%Y-%m-%d} "
                    f"as '{existing.source_filename}'."
                ],
            )

    parsed_column_map: dict[str, int] | None = None
    if column_map:
        try:
            raw = _json.loads(column_map) if isinstance(column_map, str) else column_map
            parsed_column_map = {str(k): int(v) for k, v in raw.items()}
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=422, detail="Invalid column_map: expected a JSON object of role → column index.")

    # Any failure parsing the document (corrupt file, OCR/model error, an
    # unreadable Persian PDF) must surface as a clean JSON 422 — never an
    # unhandled 500 / plain-text "Internal Server Error" that the frontend
    # then chokes on with "Unexpected token 'I'…".
    try:
        if ext in (".csv", ".tsv"):
            result = parse_csv(content, bank_name=bank_name, column_map=parsed_column_map)
        elif ext in (".xlsx", ".xls"):
            tmp_path = Path("/tmp") / f"bs_{uuid.uuid4().hex}{ext}"
            tmp_path.write_bytes(content)
            try:
                result = parse_excel(str(tmp_path), bank_name=bank_name, column_map=parsed_column_map)
            finally:
                tmp_path.unlink(missing_ok=True)
        else:  # image / PDF → vision row-extraction, fall back to text scraping
            from app.services.bank_statement_parser import parse_vision_rows
            from app.services import ocr_extract

            ctype = content_type or ("application/pdf" if ext == ".pdf" else "image/jpeg")
            tmp_path = Path("/tmp") / f"bs_{uuid.uuid4().hex}{ext}"
            tmp_path.write_bytes(content)
            try:
                result = None
                # Primary: same vision pipeline the invoice path uses, asking
                # for row-structured JSON — reads dense Persian RTL tables the
                # free-text regex parser can't.
                try:
                    vrows = await ocr_extract.extract_statement_rows(str(tmp_path), ctype)
                    if vrows:
                        result = parse_vision_rows(vrows, bank_name=bank_name)
                except Exception:
                    logger.warning("vision statement OCR failed for %s — falling back to text", filename, exc_info=True)
                # Fallback: embedded-text row scraping.
                if result is None or not result.rows:
                    ocr_result = await ocr_extract.extract_from_attachment(str(tmp_path), ctype)
                    raw_text = ocr_result.get("raw_text", "") or ""
                    result = parse_ocr_rows(raw_text, bank_name=bank_name)
                result.source_type = "ocr_pdf" if ext == ".pdf" else "ocr_image"
            finally:
                tmp_path.unlink(missing_ok=True)
    except HTTPException:
        raise
    except Exception:
        logger.exception("bank-statement parse failed for %s", filename)
        raise HTTPException(
            status_code=422,
            detail=(
                "Couldn't read this statement automatically. Try exporting it as "
                "CSV or Excel, or check that an AI model is configured for OCR."
            ),
        )

    # Unknown column layout → return a structured "needs mapping" response so
    # the UI can present a mapping step, rather than rejecting the file.
    if getattr(result, "needs_mapping", False):
        return BankStatementUploadResponse(
            id=None,
            status="needs_mapping",
            total_rows=0,
            bank_name=bank_name,
            source_type=result.source_type,
            needs_mapping=True,
            headers=result.headers,
            required_fields=["date", "amount", "description"],
            errors=result.errors,
        )

    # A document we opened but couldn't extract any rows from is still a
    # soft failure for the user — tell them clearly instead of saving an
    # empty statement that looks like success.
    if not result.rows:
        detail = "No transaction rows could be read from this statement."
        if result.errors:
            detail += " " + "; ".join(result.errors[:3])
        raise HTTPException(status_code=422, detail=detail)

    stmt = BankStatement(
        bank_name=bank_name,
        source_type=result.source_type,
        source_filename=filename,
        content_hash=content_hash,
        currency=result.currency,
        from_date=result.from_date,
        to_date=result.to_date,
        status="parsed",
        total_rows=len(result.rows),
    )
    db.add(stmt)
    db.flush()

    from app.services.statement_categorizer import suggest_for_row
    from app.services.statement_dedup import duplicate_row_indices

    # An overlapping re-import (Aug 1-15, then Aug 1-31) has a different file
    # hash, so the content-hash gate above lets it through. Flag the rows that
    # are individually already on file so they aren't posted twice.
    dupe_idx = duplicate_row_indices(db, result.rows, exclude_statement_id=stmt.id)

    unresolved: list[BankStatementRow] = []
    for i, row in enumerate(result.rows):
        # Chart-aware suggestion first (history, then bilingual keywords
        # resolved against this tenant's own accounts). The legacy keyword
        # table only knows Iranian codes, so it's a last resort and its code is
        # dropped unless that account actually exists here.
        hit = suggest_for_row(db, row.description, is_debit=row.debit > 0)
        if hit is not None:
            cat, code = hit.category, hit.account_code
        else:
            cat, code = classify_transaction(row.description)
            if code and not db.execute(
                select(Account).where(Account.code == code)
            ).scalars().first():
                code = None
        db_row = BankStatementRow(
            statement_id=stmt.id,
            row_index=row.row_index,
            tx_date=row.tx_date,
            description=row.description,
            reference=row.reference,
            debit=row.debit,
            credit=row.credit,
            balance=row.balance,
            counterparty=row.counterparty,
            raw_text=row.raw_text,
            confidence=row.confidence,
            category=cat,
            suggested_account_code=code,
            recon_status="duplicate" if i in dupe_idx else "unmatched",
        )
        db.add(db_row)
        if code is None and i not in dupe_idx:
            unresolved.append(db_row)

    db.flush()

    # Long tail: whatever the deterministic tiers couldn't place goes to the
    # model in ONE batched call. Best-effort by design — the import is already
    # complete and valid at this point, so a model that is slow, down or simply
    # not configured just leaves those rows blank for the user to fill in.
    if unresolved and settings.statement_llm_categorization:
        try:
            from app.services.statement_llm_categorizer import suggest_unknown

            hits = await suggest_unknown(
                db, [(r.id, r.description or "", r.debit > 0) for r in unresolved]
            )
            for r in unresolved:
                hit = hits.get(r.id)
                if hit is not None:
                    r.category, r.suggested_account_code = hit.category, hit.account_code
        except Exception:  # noqa: BLE001 - never fail an import over this
            logger.warning("LLM categorization pass skipped", exc_info=True)

    db.commit()
    return BankStatementUploadResponse(
        id=stmt.id,
        status=stmt.status,
        total_rows=len(result.rows),
        bank_name=bank_name,
        source_type=result.source_type,
        errors=result.errors,
        skipped_rows=getattr(result, "skipped_rows", 0),
        duplicate_rows=len(dupe_idx),
    )


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

# Statuses a re-run must not overwrite: the user has already decided.
_SETTLED_STATUSES = frozenset({"duplicate", "skipped"})


def _row_is_settled(row: BankStatementRow) -> bool:
    return (
        row.recon_status in _SETTLED_STATUSES
        or row.created_transaction_id is not None
        or (row.user_approved and row.recon_status == "matched")
    )


def reconcile_statement_rows(db: Session, stmt: BankStatement) -> ReconcileResponse:
    """Run automatic reconciliation on a parsed bank statement and persist
    each row's status. Idempotent for rows the user already dealt with."""
    rows = db.execute(
        select(BankStatementRow).where(BankStatementRow.statement_id == stmt.id)
        .order_by(BankStatementRow.row_index)
    ).scalars().all()

    from app.services.reconciliation import detect_missing_entries
    from app.services.reconciliation import reconcile_statement as _reconcile

    results = _reconcile(db, rows)

    matched = partial = unmatched = duplicates = auto_matched = 0
    open_pairs = []  # (row, result) still open after this pass
    for row, result in zip(rows, results):
        # A row already recognised at import as previously imported stays a
        # duplicate: reconciling must not hand it back as postable. Likewise a
        # row the user posted / approved / skipped keeps that decision.
        if _row_is_settled(row):
            if row.recon_status == "duplicate":
                duplicates += 1
            elif row.recon_status == "matched":
                matched += 1
            continue
        row.recon_status = result.status
        if result.best_match:
            row.matched_transaction_id = result.best_match.transaction_id
        if result.auto_match:
            auto_matched += 1
        if result.status == "matched":
            matched += 1
        elif result.status == "partial":
            partial += 1
            open_pairs.append((row, result))
        elif result.status == "duplicate":
            duplicates += 1
        else:
            unmatched += 1
            open_pairs.append((row, result))

    # Detect missing entries
    matched_ids = {r.matched_transaction_id for r in rows if r.matched_transaction_id}
    matched_ids |= {r.created_transaction_id for r in rows if r.created_transaction_id}
    missing = (
        detect_missing_entries(
            db, stmt.from_date or rows[0].tx_date, stmt.to_date or rows[-1].tx_date, matched_ids
        )
        if rows else []
    )

    # Exact unreconciled difference: net of every bank row that didn't match a
    # ledger transaction (credit = money in, debit = money out). Reported as-is
    # — we never force this to zero.
    unreconciled = sum((row.credit - row.debit) for row, _ in open_pairs)

    # Fee/interest suggestions: unmatched lines that look like a bank fee or
    # interest credit. Offered as confirm-gated postings (never auto-posted),
    # to the locale-aware bank-charges / interest-income account.
    from app.services.account_resolver import resolve_account_code

    fee_suggestions: list[FeeSuggestion] = []
    fee_code = fee_name = int_code = int_name = None
    for row, _ in open_pairs:
        kind = classify_fee_row(row)
        if not kind:
            continue
        if kind == "bank_fee":
            if fee_code is None:
                fee_code = resolve_account_code(db, "bank_fee")
                fee_name = account_name(db, fee_code)
            code, name, direction = fee_code, fee_name, "debit"
            amount = row.debit if row.debit > 0 else row.credit
        else:  # interest_income
            if int_code is None:
                int_code = resolve_account_code(db, "interest_income")
                int_name = account_name(db, int_code)
            code, name, direction = int_code, int_name, "credit"
            amount = row.credit if row.credit > 0 else row.debit
        if amount <= 0:
            continue
        fee_suggestions.append(FeeSuggestion(
            row_id=row.id, row_index=row.row_index, tx_date=row.tx_date,
            description=row.description, amount=amount, direction=direction,
            kind=kind, account_code=code, account_name=name or code,
        ))

    stmt.status = "reviewing"
    stmt.matched_rows = matched
    stmt.new_rows = unmatched
    db.commit()

    return ReconcileResponse(
        total_rows=len(rows),
        matched=matched,
        partial=partial,
        unmatched=unmatched,
        duplicates=duplicates,
        auto_matched=auto_matched,
        missing_in_bank=len(missing),
        unreconciled_difference=unreconciled,
        currency=stmt.currency,
        fee_suggestions=fee_suggestions,
    )


def account_name(db: Session, code: str) -> str | None:
    acc = db.execute(select(Account).where(Account.code == code)).scalar_one_or_none()
    return acc.name if acc else None


# Keyword heuristics for spotting an unmatched bank line as a fee or interest.
_FEE_KEYWORDS = ("fee", "charge", "commission", "service charge", "overdraft",
                 "کارمزد", "هزینه بانک", "حق‌الزحمه")
_INTEREST_KEYWORDS = ("interest", "credit interest", "سود", "بهره")


def classify_fee_row(row: BankStatementRow) -> str | None:
    """Return 'bank_fee', 'interest_income', or None for a bank statement row,
    using the category assigned at import plus a description keyword fallback."""
    cat = (row.category or "").lower()
    if cat == "bank_fee":
        return "bank_fee"
    if cat == "interest":
        return "interest_income"
    low = (row.description or "").lower()
    if any(k in low for k in _INTEREST_KEYWORDS):
        # A money-in interest line; a debit "interest" is loan interest paid,
        # which is an expense, not interest income — skip it here.
        return "interest_income" if row.credit > 0 else None
    if any(k in low for k in _FEE_KEYWORDS):
        return "bank_fee"
    return None

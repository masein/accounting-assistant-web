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

import asyncio
import functools
import hashlib
import json as _json
import logging
import re
import unicodedata
import uuid
from datetime import date, datetime, timedelta, timezone
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
    ("day bank", "Day"), ("بانک دی", "Day"), ("dey bank", "Day"),          # never «دی» alone: a month
    ("hsbc", "HSBC"), ("barclays", "Barclays"), ("lloyds", "Lloyds"), ("natwest", "NatWest"),
    ("monzo", "Monzo"), ("revolut", "Revolut"),
)


def guess_bank_name(*sources: str, document_text: str = "") -> str:
    """Best-effort bank name.

    The filename and the user's message are trusted; the document body is
    consulted only through its first lines (the statement header). A merchant
    called "Refah store" deep in the rows must not turn a Mellat statement
    into a "Refah" one (QA 2026-09-24)."""
    hint = _norm(" ".join(s for s in sources if s))
    for key, name in _BANK_NAMES:
        if _names(key, hint):
            return name
    # Only the first line — a statement's title, never its rows.
    first = ((document_text or "").strip().splitlines() or [""])[0]
    head = _norm(first[:160])
    for key, name in _BANK_NAMES:
        if _names(key, head):
            return name
    return "Unknown"


def _names(key: str, text: str) -> bool:
    """A bank's name as a word of ``text``: «درآمد شهریه» named Bank Shahr, and a father's
    instructions for a Day Bank statement went to a «Shahr» one (2026-10-20). Latin names
    still match inside a file name («mellat-transactions», "parsian2026")."""
    if key.isascii():
        return key in text
    return re.search(rf"(?<!\w){re.escape(key)}(?!\w)", text) is not None


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

async def _store_rows(db: Session, stmt: BankStatement, result) -> set[int]:
    """A statement's rows into the books' review: amounts to the currency's
    places, rows already on file flagged, each row's account suggested.
    Flushed; the caller commits. Returns the indexes of the duplicate rows."""
    stmt.currency = (result.currency or "").strip().upper() or statement_currency(db, stmt)
    dates = [r.tx_date for r in result.rows if r.tx_date]
    if dates and stmt.from_date is None:
        stmt.from_date, stmt.to_date = min(dates), max(dates)
    # the reader keeps what the file says (7.50); the books keep the currency's
    # places — pence for pounds, whole rials
    from app.core.money import quantize
    for r in result.rows:
        r.debit, r.credit = quantize(r.debit or 0, stmt.currency), quantize(r.credit or 0, stmt.currency)
        if r.balance is not None:
            r.balance = quantize(r.balance, stmt.currency)
    if stmt not in db:
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
        # Chart-aware suggestion (the user's corrections, history, bilingual
        # keywords resolved against this tenant's own accounts). What it can't
        # place goes to the model below, or stays blank for the user. (The old
        # fixed-code keyword table is gone: it matched "fee" in "coffee" and
        # "tax" in "taxi", filed a payment as revenue, and a transfer to the
        # bank account itself.)
        hit = suggest_for_row(db, row.description, is_debit=row.debit > 0)
        cat, code = (hit.category, hit.account_code) if hit is not None else (None, None)
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

    return dupe_idx


async def import_statement_bytes(
    db: Session,
    *,
    content: bytes,
    filename: str,
    content_type: str | None,
    bank_name: str = "Unknown",
    column_map: dict[str, int] | str | None = None,
    confirm_duplicate: bool = False,
    pdf_password: str | None = None,
) -> BankStatementUploadResponse:
    """Parse and persist a bank statement. Raises HTTPException for the same
    conditions the upload endpoint always did (bad type, unreadable file,
    zero rows); returns ``status="duplicate"`` / ``"needs_mapping"``
    responses instead of raising for those soft outcomes."""
    filename = filename or "unknown"
    ext = Path(filename).suffix.lower()

    from app.services.bank_statement_parser import (
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
            select(BankStatement).where(BankStatement.content_hash == content_hash,
                                        BankStatement.status != "failed")      # a read that stopped: read it again (AJ8)
            .order_by(BankStatement.created_at.desc())
        ).scalars().first()
        if existing is not None:
            settle_stale_reading(db, existing)
            if existing.status == "failed":
                existing = None
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

    # A locked PDF is unlocked in memory first (the duplicate check above used
    # the file as it came); without the right password the caller asks for it.
    if ext == ".pdf":
        from app.services.pdf_unlock import PasswordNeeded, unlock
        try:
            content = unlock(content, pdf_password)
        except PasswordNeeded as exc:
            return BankStatementUploadResponse(
                id=None, status="needs_password", total_rows=0, bank_name=bank_name, source_type="pdf",
                needs_password=True, password_wrong=exc.wrong,
                errors=["The PDF password isn't right." if exc.wrong else "This PDF is password-protected."],
            )

    report: dict | None = None           # what a picture's reading found (scenarios AJ2–AJ4)
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
            if ext == ".pdf":
                ctype = "application/pdf"
            tmp_path = Path("/tmp") / f"bs_{uuid.uuid4().hex}{ext}"
            tmp_path.write_bytes(content)
            if ext == ".pdf" and ocr_extract.pdf_page_count(tmp_path) > SYNC_PAGES:
                # every page, read after the upload returns: 22 pages in one call read the
                # first 4 and said nothing of the rest (2026-10-20, scenario AJ1)
                tmp_path.unlink(missing_ok=True)
                return await _start_reading(db, content=content, filename=filename, ctype=ctype,
                                            bank_name=bank_name, content_hash=content_hash)
            try:
                result = None
                # Primary: same vision pipeline the invoice path uses, asking
                # for row-structured JSON — reads dense Persian RTL tables the
                # free-text regex parser can't. Every page, each on its own.
                try:
                    head_account = None
                    if bank_name in ("", "Unknown") and ext == ".pdf" and not _has_text(tmp_path):
                        # pictures only: its first page names the bank (AJ5); a PDF with text, the text does
                        bank_name, head_account = await _bank_from_head(str(tmp_path), ctype)
                    out = await ocr_extract.extract_statement_pages(str(tmp_path), ctype)
                    if out["rows"]:
                        report = _reading_report(out)                  # mends a misread amount first (AJ3)
                        result = parse_vision_rows(out["rows"], bank_name=bank_name)
                        result.account_number = result.account_number or head_account
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
        raise HTTPException(status_code=422, detail=(
            f"No transaction rows could be read from this statement. {'; '.join(result.errors[:3])}" if result.errors
            else "No transaction rows could be read from this statement."))

    stmt = BankStatement(
        bank_name=bank_name,
        account_number=result.account_number,
        source_type=result.source_type,
        source_filename=filename,
        content_hash=content_hash,
        from_date=result.from_date,
        to_date=result.to_date,
        status="parsed",
        total_rows=len(result.rows),
    )
    if report:
        stmt.read_report = _json.dumps(report, ensure_ascii=False)
        stmt.pages_total, stmt.pages_read = report.get("pages"), report.get("pages_read")
    dupe_idx = await _store_rows(db, stmt, result)
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
        pages_total=stmt.pages_total,
        read_report=report,
    )


# ---------------------------------------------------------------------------
# A statement of pictures, read page by page (scenarios AJ1–AJ9)
# ---------------------------------------------------------------------------

# Up to this many pages a picture statement is read while the upload waits; a longer one after it returns.
SYNC_PAGES = 2
# A read that hasn't moved for this long stopped (the server restarted): it is said, and the file can come again.
STALE_READING = timedelta(minutes=20)
_READING: set = set()                   # the reads in flight, kept from the garbage collector


def _reading_report(out: dict) -> dict:
    """What a picture's reading found: its pages, the pages read and not,
    its rows, the amounts its own balances mended, and its running
    balance's check (scenarios AJ2–AJ4). Mends ``out["rows"]`` in place."""
    from app.services.statement_checks import balance_check, mend_amounts
    rows = out.get("rows") or []
    mended = mend_amounts(rows)
    return {"pages": out.get("pages"), "pages_read": out.get("pages_read"),
            "failed_pages": out.get("failed_pages") or [], "beyond": out.get("beyond") or 0,
            "rows": len(rows), "mended": mended, "balance": balance_check(rows)}


def read_report(stmt: BankStatement) -> dict | None:
    try:
        return _json.loads(stmt.read_report) if stmt.read_report else None
    except ValueError:
        return None


def settle_stale_reading(db: Session, stmt: BankStatement) -> None:
    """A read that stopped (the server restarted) is said to have, not left «reading» for ever (AJ8)."""
    if stmt.status != "reading":
        return
    moved = stmt.updated_at or stmt.created_at
    if moved is not None and moved.tzinfo is None:
        moved = moved.replace(tzinfo=timezone.utc)
    if moved is not None and datetime.now(timezone.utc) - moved > STALE_READING:
        stmt.status = "failed"
        stmt.read_report = _json.dumps({**(read_report(stmt) or {}), "stopped": True}, ensure_ascii=False)
        db.commit()


def _has_text(path: Path) -> bool:
    from app.services.ocr_extract import _extract_pdf_text
    try:
        return bool((_extract_pdf_text(path) or "").strip())
    except Exception:  # noqa: BLE001
        return False


async def _bank_from_head(path: str, ctype: str) -> tuple[str, str | None]:
    """The bank and account a statement's first page names (scenario AJ5)."""
    from app.services import ocr_extract
    head = await ocr_extract.read_statement_head(path, ctype)
    bank = guess_bank_name(head.get("bank") or "") if head.get("bank") else "Unknown"
    return bank, head.get("account_number")


async def _start_reading(db: Session, *, content: bytes, filename: str, ctype: str, bank_name: str,
                         content_hash: str) -> BankStatementUploadResponse:
    """A long statement of pictures: on file at once as «reading», read after the upload returns."""
    import tempfile

    from app.db.tenant import get_current_company
    from app.services import ocr_extract
    folder = Path(tempfile.gettempdir()) / "statement_reading"
    folder.mkdir(parents=True, exist_ok=True)
    stmt = BankStatement(bank_name=bank_name or "Unknown", source_type="ocr_pdf", source_filename=filename,
                         content_hash=content_hash, status="reading", total_rows=0, pages_read=0)
    stmt.currency = statement_currency(db, stmt)
    db.add(stmt)
    db.flush()
    path = folder / f"{stmt.id}.pdf"
    path.write_bytes(content)
    stmt.pages_total = ocr_extract.pdf_page_count(path)
    db.commit()
    job = _read_in_background(stmt.id, get_current_company(), str(path), ctype,
                              bind=db.get_bind() if settings.statement_read_inline else None)
    if settings.statement_read_inline:
        await job
        db.expire_all()
    else:
        task = asyncio.get_running_loop().create_task(job)
        _READING.add(task)
        task.add_done_callback(_READING.discard)
    stmt = db.get(BankStatement, stmt.id)
    return BankStatementUploadResponse(
        id=stmt.id, status=stmt.status, total_rows=stmt.total_rows, bank_name=stmt.bank_name,
        source_type="ocr_pdf", pages_total=stmt.pages_total, read_report=read_report(stmt))


async def _read_in_background(statement_id, company_id, path: str, ctype: str, *, bind=None) -> None:
    """Every page of a statement of pictures, read and stored, its progress
    said as it goes; any failure leaves it «failed» with why, never «reading»."""
    from app.db.session import SessionLocal
    from app.db.tenant import use_company
    from app.services import ocr_extract
    from app.services.bank_statement_parser import parse_vision_rows
    import contextlib
    db = Session(bind=bind) if bind is not None else SessionLocal()
    scope = (lambda: use_company(company_id)) if company_id is not None else contextlib.nullcontext
    try:
        with scope():
            stmt = db.get(BankStatement, statement_id)
            if stmt is None:
                return

            def progress(done: int, total: int) -> None:
                stmt.pages_read = done
                db.commit()

            try:
                bank, account = (stmt.bank_name, None)
                if stmt.bank_name in ("", "Unknown"):
                    bank, account = await _bank_from_head(path, ctype)
                out = await ocr_extract.extract_statement_pages(path, ctype, on_page=progress)
                report = _reading_report(out)                          # mends a misread amount first (AJ3)
                result = parse_vision_rows(out["rows"], bank_name=bank)
            except Exception as exc:  # noqa: BLE001 — said on the statement, never left «reading»
                logger.warning("statement %s couldn't be read", statement_id, exc_info=True)
                db.rollback()
                stmt.status = "failed"
                stmt.read_report = _json.dumps({"error": str(exc)[:300]}, ensure_ascii=False)
                db.commit()
                return
            if not result.rows:
                stmt.status = "failed"
                stmt.read_report = _json.dumps({**report, "error": "no rows"}, ensure_ascii=False)
                db.commit()
                return
            stmt.bank_name = bank or stmt.bank_name
            stmt.account_number = stmt.account_number or result.account_number or account
            stmt.source_type = "ocr_pdf"
            stmt.total_rows = len(result.rows)
            await _store_rows(db, stmt, result)
            stmt.status = "parsed"
            stmt.pages_total, stmt.pages_read = report["pages"], report["pages_read"]
            stmt.read_report = _json.dumps(report, ensure_ascii=False)
            db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("statement %s reading failed", statement_id)
        try:
            db.rollback()
            with scope():
                stmt = db.get(BankStatement, statement_id)
                if stmt is not None and stmt.status == "reading":
                    stmt.status = "failed"
                    db.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()
        Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Which GL account is this statement about?
# ---------------------------------------------------------------------------

_MASK = re.compile(r"[*xX•●]+")


def _digits(text: str | None) -> str:
    from app.services.bank_sms import normalize
    return re.sub(r"\D", "", normalize(text or ""))


def account_number_matches(seen: str | None, on_file: str | None) -> bool:
    """A statement's account number against one on file. A masked number
    ("01***789", "6037****1234") matches on its visible head and tail, with at
    least 4 digits showing; a full one matches exactly, or as the end of the
    other (an account number is the tail of its IBAN)."""
    have = _digits(on_file)
    if not seen or not have:
        return False
    parts = _MASK.split(seen, maxsplit=1)
    if len(parts) == 2:
        head, tail = _digits(parts[0]), _digits(parts[1])
        return (len(head) + len(tail) >= 4 and len(have) >= len(head) + len(tail)
                and have.startswith(head) and have.endswith(tail))
    got = _digits(seen)
    if len(got) < 6:
        return False
    short, long_ = sorted((got, have), key=len)
    return got == have or (len(short) >= 8 and long_.endswith(short))


def canonical_bank(name: str | None) -> str | None:
    """Which bank a name means — "ملت", "Mellat", "بانک ملت", "Mellat (SMS
    1405/07)" are all Mellat — using the SMS reader's spellings, whole words
    only ("شهر" is Shahr, "شهریور" is not)."""
    from app.services.bank_sms import normalize
    text = normalize(name or "").lower()
    if not text:
        return None
    labels, spellings = _label_banks()
    if text.strip() in labels:
        return labels[text.strip()]
    found = _bank_by_spelling(text)
    if found:
        return found
    for sp, canon in spellings:                       # the reader's own spellings: "Day Bank"
        if re.search(rf"(?<!\w){re.escape(sp)}(?!\w)", text):
            return canon
    return None


def _bank_by_spelling(text: str) -> str | None:
    from app.services.bank_sms import BANKS, normalize
    best: tuple[str, int] | None = None
    for canon, spellings in BANKS.items():
        for sp in (canon, *spellings):
            sp = normalize(sp).lower()
            if sp and re.search(rf"(?<!\w){re.escape(sp)}(?!\w)", text) and (best is None or len(sp) > best[1]):
                best = (canon, len(sp))
    return best[0] if best else None


@functools.lru_cache(maxsize=1)
def _label_banks() -> tuple[dict[str, str], tuple[tuple[str, str], ...]]:
    """A statement's bank as the reader names it («Day», "Day Bank", from ``_BANK_NAMES``) → the bank the
    SMS table knows («Dey», «بانک دی»): فرهنگ نو's Day Bank statement never found «بانک دی» and was
    compared with the chart's 1110 (2026-10-21, scenario AJ11). (labels, (spelling, bank) longest first)."""
    from app.services.bank_sms import normalize
    labels: dict[str, str] = {}
    for spelling, label in _BANK_NAMES:               # a label's bank: whichever of its spellings the table knows
        canon = _bank_by_spelling(normalize(spelling).lower())
        if canon:
            labels.setdefault(normalize(label).lower(), canon)
    spellings = [(normalize(sp).lower(), labels[normalize(label).lower()]) for sp, label in _BANK_NAMES
                 if normalize(label).lower() in labels]
    return labels, tuple(sorted(spellings, key=lambda x: -len(x[0])))


def _bank_entities(db: Session) -> list:
    """Bank entities whose ledger account exists."""
    from app.models.entity import Entity
    banks = [e for e in db.execute(select(Entity).where(Entity.type == "bank")).scalars().all()
             if (e.code or "").strip()]
    codes = {c for (c,) in db.execute(select(Account.code).where(
        Account.code.in_([e.code.strip() for e in banks]))).all()} if banks else set()
    return [e for e in banks if e.code.strip() in codes]


def _default_bank_code(db: Session) -> str | None:
    from app.services.account_resolver import resolve_account_code
    try:
        return resolve_account_code(db, "bank")
    except Exception:
        return None


def bank_account_choices(db: Session) -> list[dict]:
    """The accounts money can sit in at a bank: every bank on file with its own
    ledger account, the chart's bank account, and the accounts opened under it
    in the chart (111001 «بانک ملت جاری» under 1110 — a recurring rule couldn't
    choose one: deep browser test, 2026-10-02, finding #23)."""
    out: dict[str, dict] = {}
    for e in _bank_entities(db):
        out.setdefault(e.code.strip(), {"code": e.code.strip(), "bank": e.name})
    default = _default_bank_code(db)
    if default and default not in out:
        out[default] = {"code": default, "bank": None}
    if default:
        from app.services.chart_codes import class_expr, class_of
        subs = db.execute(select(Account.code).where(class_expr().like(f"{class_of(default)}%"), Account.code != default,
                                                     Account.is_active.is_(True)).order_by(Account.code)).scalars()
        for code in subs:
            out.setdefault(code, {"code": code, "bank": None})
    names = dict(db.execute(select(Account.code, Account.name).where(Account.code.in_(list(out)))).all()) if out else {}
    return [{**c, "name": names.get(c["code"]) or c["code"]} for c in out.values()]


def resolve_bank_account(db: Session, stmt: BankStatement) -> tuple[str | None, str]:
    """(the GL account this statement's bank posts to, how it was decided).
    In order: the one the user chose ("chosen"); the bank whose account number
    (or IBAN tail) the statement shows ("account_number"); the bank named
    exactly like it ("name"); the one bank of that name — ملت = Mellat =
    بانک ملت, Barclays = «Barclays Business» ("bank"); else the chart's bank ("default"). Two candidates at
    any step is not a guess — it falls through."""
    chosen = (stmt.bank_account_code or "").strip()
    if chosen and db.execute(select(Account.id).where(Account.code == chosen)).first():
        return chosen, "chosen"
    banks = _bank_entities(db)

    def one(found: list) -> str | None:
        found_codes = {e.code.strip() for e in found}
        return found_codes.pop() if len(found_codes) == 1 else None

    if stmt.account_number:
        hit = one([e for e in banks if account_number_matches(stmt.account_number, e.account_number)
                   or account_number_matches(stmt.account_number, e.iban)])
        if hit:
            return hit, "account_number"
    name = (stmt.bank_name or "").strip()
    if name and name.lower() != "unknown":
        hit = one([e for e in banks if (e.name or "").strip().lower() == name.lower()])
        if hit:
            return hit, "name"
        canon = canonical_bank(name)
        if canon:
            hit = one([e for e in banks if canonical_bank(e.name) == canon])
            if hit:
                return hit, "bank"
        # "Barclays" is the bank called "Barclays Business" — the name, whole
        # words, inside one bank's name (a UK statement fell back to the
        # chart's 1200 and none of its rows matched: phone QA, 2026-10-12).
        if len(name) >= 3:
            word = re.compile(rf"(?<!\w){re.escape(name.lower())}(?!\w)")
            hit = one([e for e in banks if word.search((e.name or "").lower())])
            if hit:
                return hit, "bank"
    hit = _account_named_for(db, stmt)
    if hit:
        return hit, "account_name"
    return _default_bank_code(db), "default"


def _account_named_for(db: Session, stmt: BankStatement) -> str | None:
    """A bank with no party whose account is named for it: its number in the name
    («بانک دی حساب 0105222028000»), else its bank («بانک دی») among the accounts
    named «بانک …» — one of them, or none (scenario AJ11)."""
    from app.services.bank_sms import normalize
    from app.models.account import AccountLevel
    accounts = db.execute(select(Account.code, Account.name).where(
        Account.level != AccountLevel.GROUP, Account.is_active.is_(True))).all()
    digits = re.sub(r"\D", "", normalize(stmt.account_number or ""))
    if len(digits) >= 6:
        found = {code for code, name in accounts if digits in re.sub(r"\D", "", normalize(name or ""))}
        if len(found) == 1:
            return found.pop()
    canon = canonical_bank(stmt.bank_name)
    if canon:
        found = {code for code, name in accounts
                 if "بانک" in (name or "") and canonical_bank(name) == canon and not re.search(r"وام|تسهیلات|قرض", name or "")}
        if len(found) == 1:
            return found.pop()
    return None


def bank_account_guessed(db: Session, stmt: BankStatement) -> bool:
    """The statement names its bank, or shows its number, but none of the books' banks matched, so it is
    compared with the chart's own bank account while other banks are on file: only a guess, and no row
    is posted to it before the account is chosen (scenario AJ12). A statement that names no bank, or
    books with one bank, use the chart's account as before."""
    from app.models.account import AccountLevel
    code, how = resolve_bank_account(db, stmt)
    if how != "default":
        return False
    if (stmt.bank_name or "").strip() in ("", "Unknown") and not (stmt.account_number or "").strip():
        return False
    if {e.code.strip() for e in _bank_entities(db)} - {code}:
        return True
    return any(c != code and "بانک" in (n or "") and not re.search(r"وام|تسهیلات|قرض", n or "")
               for c, n in db.execute(select(Account.code, Account.name).where(
                   Account.level != AccountLevel.GROUP, Account.is_active.is_(True))).all())


def bank_account_for_statement(db: Session, stmt: BankStatement) -> str | None:
    """The GL account this statement's bank posts to (see resolve_bank_account)."""
    return resolve_bank_account(db, stmt)[0]


def statement_currency(db: Session, stmt: BankStatement) -> str:
    """The currency a statement is in when the file doesn't say: its bank's
    (a bank entity can hold a USD account), else the company's."""
    from app.services.fx_base import base_currency
    code, _source = resolve_bank_account(db, stmt)
    if code:
        for e in _bank_entities(db):
            if e.code.strip() == code and (e.currency or "").strip():
                return e.currency.strip().upper()
    return base_currency(db)


def statement_predicates(db: Session, stmt: BankStatement):
    """(bank_code, match_predicate, missing_predicate) for ``stmt``.

    * matching is lenient: the statement bank's own account OR any locale
      cash/bank account, so entries a user keyed to the generic bank account
      before creating the bank entity still match;
    * "missing in bank" is strict: only entries on the statement bank's own
      account count — otherwise every petty-cash voucher, and every other
      bank's entry, would be reported as absent from the statement;
    * another bank entity's own account never matches (on a UK chart they're
      all cash codes).
    """
    from app.services.cash_service import cash_account_predicate
    from app.services.locale_service import get_reporting_locale

    cash = cash_account_predicate(get_reporting_locale(db))
    bank_code = bank_account_for_statement(db, stmt)
    # Another bank's own account is never this statement's: on a UK chart they
    # are all "cash" codes (120x), so a Barclays row matched an HSBC entry.
    others = {e.code.strip() for e in _bank_entities(db)} - {bank_code}
    match = lambda c, _b=bank_code, _cash=cash, _o=others: c == _b or (_cash(c) and c not in _o)  # noqa: E731
    if bank_code:
        # …and only this account's own entries can be missing from its statement
        # (not petty cash, not the other banks).
        missing = lambda c, _b=bank_code: c == _b  # noqa: E731
    else:
        missing = lambda c, _cash=cash, _o=others: _cash(c) and c not in _o  # noqa: E731
    return bank_code, match, missing


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

    _bank_code, match_pred, missing_pred = statement_predicates(db, stmt)
    results = _reconcile(db, rows, is_cash=match_pred, currency=stmt.currency)

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
            db, stmt.from_date or rows[0].tx_date, stmt.to_date or rows[-1].tx_date, matched_ids,
            is_cash=missing_pred, currency=stmt.currency,
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


def release_statement_rows(db: Session, transaction_id) -> int:
    """A transaction that was posted from a statement row and is now being
    undone, reversed or deleted must hand the row back: otherwise the row
    stays "posted" forever and the review never offers it again. Returns the
    number of rows released."""
    rows = db.execute(
        select(BankStatementRow).where(BankStatementRow.created_transaction_id == transaction_id)
    ).scalars().all()
    for row in rows:
        row.created_transaction_id = None
        row.user_approved = False
        row.recon_status = "unmatched"
    return len(rows)

"""Migrate from another accounting system: chart + تفصیلی entities + opening balances.

Confirm-gated: ``POST /migration/import/preview`` stages the parsed files in a
``migration_batches`` row (nothing is written to the books) and returns a
validation summary; ``POST /migration/import/confirm`` applies it — chart
merge, bank/counterparty entities, one balanced opening journal — and fills
the "Complete imported records" queue. Idempotent end to end: re-importing the
same files updates, never duplicates, and re-confirming an applied batch
returns the original result.

Restricted to Owner / Accountant (Perm.MIGRATION_WRITE — see
app/core/permissions.py).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import SessionUser, get_current_user
from app.core.messages import localize_detail, request_language, said, say_all
from app.db.session import get_db
from app.models.entity import Entity
from app.models.migration import MigrationBatch, MigrationPendingRecord
from app.schemas.migration import (
    MigrationBatchRead,
    MigrationConfirmRequest,
    MigrationConfirmResponse,
    MigrationPendingRead,
    MigrationPendingResolveRequest,
    MigrationPendingResolveResponse,
    MigrationPreviewResponse,
)
from app.services import migration_import as mig
from app.services.audit_service import log_audit_event
from app.core.money import json_dumps

router = APIRouter(prefix="/migration", tags=["migration"])

_MAX_FILE_SIZE = 20 * 1024 * 1024
_ALLOWED_EXTENSIONS = (".xls", ".xlsx", ".csv", ".xml")


@router.post("/import/preview", response_model=MigrationPreviewResponse)
def migration_import_preview(
    files: list[UploadFile] = File(...),
    request: Request = None,
    db: Session = Depends(get_db),
) -> MigrationPreviewResponse:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if len(files) > 4:
        raise HTTPException(status_code=400, detail="Upload at most 4 files (گروه / کل / معین / تفصیلی)")

    parsed: list[tuple[str, list[dict]]] = []
    hasher = hashlib.sha256()
    for f in files:
        name = f.filename or "upload"
        if not name.lower().endswith(_ALLOWED_EXTENSIONS):
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {name}")
        content = f.file.read()
        if len(content) > _MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"File too large: {name}")
        hasher.update(content)
        try:
            rows = mig.extract_rows(name, content)
        except mig.MigrationParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not rows:
            raise HTTPException(status_code=400, detail=f"No data rows found in {name}")
        parsed.append((name, rows))

    token = hasher.hexdigest()[:32]
    batch, summary, already_applied = mig.stage_preview_batch(db, parsed, token)
    log_audit_event(
        db, "migration_import_preview", "migration_batch", entity_id=str(batch.id),
        detail=json_dumps({"files": summary["files"], "tiers": summary["tiers"]}, ensure_ascii=False),
    )
    db.commit()

    return MigrationPreviewResponse(
        token=token,
        batch_id=batch.id,
        summary=said(summary, request, db),       # its errors and warnings in the page's language (#53), dates in its calendar
        default_opening_date=mig.default_opening_date(db),
        already_applied=already_applied,
    )


@router.post("/import/confirm", response_model=MigrationConfirmResponse)
def migration_import_confirm(
    payload: MigrationConfirmRequest,
    request: Request = None,
    db: Session = Depends(get_db),
) -> MigrationConfirmResponse:
    batch = db.execute(
        select(MigrationBatch).where(MigrationBatch.token == payload.token)
    ).scalars().first()
    if batch is None:
        raise HTTPException(status_code=404, detail="Unknown or expired import token — run preview again")
    if batch.status == "applied" and batch.result is not None:
        return MigrationConfirmResponse(
            batch_id=batch.id, status=batch.status, idempotent=True, result=batch.result
        )

    errors = ((batch.summary or {}).get("validation") or {}).get("errors") or []
    if errors:
        lang = request_language(request.headers) if request is not None else "en"
        raise HTTPException(status_code=400, detail={"message": localize_detail("Import has blocking errors", lang),
                                                     "errors": say_all(errors, lang)})

    opening_date = payload.opening_date or mig.default_opening_date(db)
    if opening_date > date.today():
        raise HTTPException(status_code=400, detail="Opening date cannot be in the future")

    result = mig.apply_batch(db, batch, opening_date, payload.opening)
    log_audit_event(
        db, "migration_import_apply", "migration_batch", entity_id=str(batch.id),
        detail=json_dumps(result, ensure_ascii=False),
    )
    db.commit()

    from app.api.reports import invalidate_dashboard_cache
    invalidate_dashboard_cache()

    if batch.opening_transaction_id is not None:
        try:
            from app.api.transactions import _log_transaction_audit
            from app.models.transaction import Transaction
            txn = db.get(Transaction, batch.opening_transaction_id)
            if txn is not None:
                _log_transaction_audit(db, "create", txn)
        except Exception:  # audit must never fail the import
            pass

    return MigrationConfirmResponse(batch_id=batch.id, status="applied", result=result)


@router.get("/batches", response_model=list[MigrationBatchRead])
def migration_batches(db: Session = Depends(get_db)) -> list[MigrationBatchRead]:
    rows = db.execute(
        select(MigrationBatch).order_by(MigrationBatch.created_at.desc()).limit(20)
    ).scalars().all()
    return [MigrationBatchRead.model_validate(r) for r in rows]


def _pending_to_read(rec: MigrationPendingRecord, entity: Entity | None) -> MigrationPendingRead:
    return MigrationPendingRead(
        id=rec.id, batch_id=rec.batch_id, entity_id=rec.entity_id,
        entity_type=rec.entity_type, entity_name=(entity.name if entity else "?"),
        source_code=rec.source_code, missing_fields=list(rec.missing_fields or []),
        review_flags=list(rec.review_flags or []), status=rec.status,
        created_at=rec.created_at,
    )


@router.get("/pending", response_model=list[MigrationPendingRead])
def migration_pending(db: Session = Depends(get_db)) -> list[MigrationPendingRead]:
    """The "Complete imported records" queue — imported entities still missing
    required data (address, bank account, …), with live re-checks so records
    completed through any path (entity edit, AI) show their current gaps."""
    rows = db.execute(
        select(MigrationPendingRecord)
        .where(MigrationPendingRecord.status == "pending")
        .order_by(MigrationPendingRecord.created_at)
    ).scalars().all()
    out: list[MigrationPendingRead] = []
    for rec in rows:
        entity = db.get(Entity, rec.entity_id)
        if entity is not None:
            rec.missing_fields = mig.missing_entity_fields(entity)
            # Fully completed elsewhere (entity form / AI update card) and no
            # review flags left → auto-resolve; the queue only shows real gaps.
            if not rec.missing_fields and not (rec.review_flags or []):
                from datetime import datetime, timezone
                rec.status = "resolved"
                rec.resolved_at = datetime.now(timezone.utc)
                continue
        out.append(_pending_to_read(rec, entity))
    db.commit()
    return out


@router.post("/pending/{pending_id}/resolve", response_model=MigrationPendingResolveResponse)
def migration_pending_resolve(
    pending_id: UUID,
    payload: MigrationPendingResolveRequest | None = None,
    db: Session = Depends(get_db),
) -> MigrationPendingResolveResponse:
    """Mark a queue record complete. Optional ``fields`` are patched onto the
    EXISTING entity (update-in-place — never a new record) before the
    completeness re-check; otherwise the fields must already be filled (via
    the entity form or the AI update card)."""
    rec = db.get(MigrationPendingRecord, pending_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Pending record not found")
    entity = db.get(Entity, rec.entity_id)
    fields = (payload.fields if payload else {}) or {}
    if fields and entity is not None:
        from app.services.ai_accountant.entity_create import DETAIL_FIELDS, _apply_details
        allowed = set(DETAIL_FIELDS)
        bad = [k for k in fields if k not in allowed]
        if bad:
            raise HTTPException(
                status_code=400,
                detail={"message": "Unknown fields", "fields": bad,
                        "allowed": sorted(allowed)},
            )
        _apply_details(entity, fields, only_blank=False)
        db.flush()
    missing = mig.missing_entity_fields(entity) if entity is not None else []
    if missing:
        raise HTTPException(
            status_code=400,
            detail={"message": "Entity still has missing fields", "missing_fields": missing},
        )
    from datetime import datetime, timezone
    rec.status = "resolved"
    rec.missing_fields = []
    rec.resolved_at = datetime.now(timezone.utc)
    log_audit_event(
        db, "migration_pending_resolve", "migration_pending_record", entity_id=str(rec.id),
        detail=json_dumps({"entity_id": str(rec.entity_id), "patched_fields": sorted(fields)},
                          ensure_ascii=False),
    )
    db.commit()
    return MigrationPendingResolveResponse(id=rec.id, status=rec.status, missing_fields=[])


@router.post("/pending/{pending_id}/dismiss", response_model=MigrationPendingResolveResponse)
def migration_pending_dismiss(
    pending_id: UUID, db: Session = Depends(get_db)
) -> MigrationPendingResolveResponse:
    rec = db.get(MigrationPendingRecord, pending_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Pending record not found")
    from datetime import datetime, timezone
    rec.status = "dismissed"
    rec.resolved_at = datetime.now(timezone.utc)
    log_audit_event(db, "migration_pending_dismiss", "migration_pending_record", entity_id=str(rec.id))
    db.commit()
    return MigrationPendingResolveResponse(
        id=rec.id, status=rec.status, missing_fields=list(rec.missing_fields or [])
    )


# --- Historical journals from another system (roadmap §4.11) ----------------------------

_JOURNAL_KIND = "journal_import"
_JOURNAL_EXTENSIONS = (".xlsx", ".xls", ".xml", ".csv", ".tsv", ".txt")


class JournalReviewIn(BaseModel):
    token: str = Field(..., min_length=8, max_length=64)
    preset: str = Field("auto", max_length=16)
    sheet: str | None = Field(None, max_length=200)          # a workbook's sheet; unsaid: the journal-like one
    years: Literal["fix", "keep"] | None = None              # vouchers typed in another year: move them or post as written
    columns: dict[str, int | None] = Field(default_factory=dict)
    account_map: dict[str, str] = Field(default_factory=dict)


class JournalApplyIn(JournalReviewIn):
    link_parties: bool = True
    currency: str | None = Field(None, max_length=8)
    # applied from a chat's card: what was imported is said in that chat too, so the
    # assistant knows (it answered «سند هارو ببینم» with "nothing is in the books", AH2)
    session_id: str | None = Field(None, max_length=64)
    file_name: str | None = Field(None, max_length=120)


# What an apply did, in the reader's language (scenario AH2).
_JOURNAL_SAID = {
    "posted": {"en": "Imported {n} vouchers from {file}, {start} to {end}.",
               "fa": "{n} سند از {file} وارد دفاتر شد، از {start} تا {end}.",
               "es": "Se importaron {n} asientos de {file}, del {start} al {end}.",
               "ar": "استُورد {n} قيدًا من {file}، من {start} إلى {end}."},
    "nothing": {"en": "Nothing was imported from {file}.", "fa": "از {file} سندی وارد نشد.",
                "es": "No se importó nada de {file}.", "ar": "لم يُستورد شيء من {file}."},
    "held": {"en": " Held back: {list}.", "fa": " کنار ماند: {list}.", "es": " Retenidos: {list}.",
             "ar": " حُجز: {list}."},
}
def _journal_said(db: Session, lang: str, out: dict, parsed: dict, file_name: str) -> str:
    from app.services import journal_import as ji
    from app.services.ai_accountant.blocks import display_date
    from app.services.locale_service import get_display_calendar
    lang = lang if lang in ("en", "fa", "es", "ar") else "en"
    fa = lang == "fa"

    def n(value: int) -> str:
        if fa:
            from app.utils.jalali import to_persian_digits
            return to_persian_digits(value)
        return str(value)
    calendar = get_display_calendar(db)
    if lang in ("fa", "ar"):
        file_name = f"\u2068{file_name}\u2069"              # a Latin file name kept whole in a right-to-left line
    dates = sorted(v.on for v in parsed["vouchers"] if v.on)
    if out.get("posted"):
        text = _JOURNAL_SAID["posted"][lang].format(
            n=n(out["posted"]), file=file_name,
            start=display_date(dates[0].isoformat(), calendar, lang) if dates else "",
            end=display_date(dates[-1].isoformat(), calendar, lang) if dates else "")
    else:
        text = _JOURNAL_SAID["nothing"][lang].format(file=file_name)
    held = [f"{n(count)} {(ji.HELD_FOR.get(why) or {}).get(lang) or why}" for why, count in (out.get("skipped") or {}).items()]
    if held:
        text += _JOURNAL_SAID["held"][lang].format(list=("، " if lang in ("fa", "ar") else ", ").join(held))
    return text


def _journal_path(token: str):
    import re
    import tempfile
    from pathlib import Path
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise HTTPException(status_code=422, detail="Unknown upload.")
    folder = Path(tempfile.gettempdir()) / "journal_imports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{token}.bin"


def _journal_parse(db: Session, token: str, preset: str, columns: dict, sheet: str | None = None):
    from app.core.shared_state import find_upload
    from app.services import journal_import as ji
    stored = find_upload(db, _JOURNAL_KIND, token)
    path = _journal_path(token)
    if not stored or not path.exists():
        raise HTTPException(status_code=410, detail="The upload has expired — choose the file again.")
    sheet_name, rows, sheets = ji.pick_sheet(ji.read_book(path.name, path.read_bytes()), sheet or None)
    # a sheet chosen anew starts from its own columns, not the last sheet's
    return ji.parse_file(rows, preset=preset, columns=columns), sheet_name, sheets


@router.post("/journals/preview")
def journal_import_preview(file: UploadFile = File(...), preset: str = Form("auto"), sheet: str | None = Form(None),
                           db: Session = Depends(get_db)) -> dict:
    """Read an exported journal and show what would be posted — nothing is yet."""
    from app.core.shared_state import store_upload
    from app.services import journal_import as ji
    name = file.filename or "journal"
    if not name.lower().endswith(_JOURNAL_EXTENSIONS):
        raise HTTPException(status_code=422, detail="Upload an Excel or CSV export.")
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=422, detail="The file is empty.")
    if len(content) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large (20 MB at most).")
    token = hashlib.sha256(content).hexdigest()[:32]
    sheet_name, rows, sheets = ji.pick_sheet(ji.read_book(name, content), sheet or None)
    parsed = ji.settle_years(db, ji.parse_file(rows, preset=preset), None)
    _journal_path(token).write_bytes(content)
    store_upload(db, _JOURNAL_KIND, token, str(_journal_path(token)))
    out = ji.review(db, parsed)
    out.update({"token": token, "filename": name[:120], "presets": list(ji.PRESETS), "fields": list(ji.FIELDS),
                "sheet": sheet_name, "sheets": sheets})
    return out


@router.post("/journals/review")
def journal_import_review(body: JournalReviewIn, db: Session = Depends(get_db)) -> dict:
    """The same file again with corrected columns, preset or account choices."""
    from app.services import journal_import as ji
    parsed, sheet_name, sheets = _journal_parse(db, body.token, body.preset, body.columns, body.sheet)
    out = ji.review(db, ji.settle_years(db, parsed, body.years), body.account_map)
    out.update({"token": body.token, "presets": list(ji.PRESETS), "fields": list(ji.FIELDS),
                "sheet": sheet_name, "sheets": sheets})
    return out


@router.post("/journals/apply")
def journal_import_apply(body: JournalApplyIn, request: Request, db: Session = Depends(get_db),
                         user: SessionUser = Depends(get_current_user)) -> dict:
    from app.core.shared_state import drop_upload
    from app.services import journal_import as ji
    parsed, _sheet, _sheets = _journal_parse(db, body.token, body.preset, body.columns, body.sheet)
    parsed = ji.settle_years(db, parsed, body.years)
    out = ji.apply(db, parsed, account_map=body.account_map,
                   currency=body.currency, link_parties=body.link_parties)
    log_audit_event(db, "journal_import", "migration", entity_id=body.token,
                    detail=json_dumps({"preset": parsed["preset"], **out}, ensure_ascii=False))
    drop_upload(db, _JOURNAL_KIND, body.token)
    db.commit()
    try:
        _journal_path(body.token).unlink()
    except OSError:
        pass
    out["said"] = _journal_said(db, request_language(request.headers), out, parsed,
                                (body.file_name or "").strip() or body.token[:8])
    if body.session_id:
        _say_in_chat(db, body.session_id, user, out["said"])
    return out


def _say_in_chat(db: Session, session_id: str, user: "SessionUser", text: str) -> None:
    """The import's outcome as the assistant's message in the chat it came from."""
    from app.models.ai_accountant import AIChatSession
    from app.services.ai_accountant.orchestrator import _persist_message
    try:
        sid = UUID(str(session_id))
    except ValueError:
        return
    chat = db.get(AIChatSession, sid)
    if chat is None or str(chat.user_id) != str(user.user_id):
        return                                   # someone else's chat: nothing is said there
    _persist_message(db, session_id=sid, role="assistant", content={"role": "assistant", "text": text})


class JournalRepairsIn(BaseModel):
    vouchers: list[str] = Field(default_factory=list, max_length=500)
    headings: list[str] = Field(default_factory=list, max_length=50)


@router.get("/journals/repairs")
def journal_repairs(db: Session = Depends(get_db)) -> dict:
    """What a misread journal import left in the books: vouchers numbered by a date
    and sitting a year away, one ledger made into two headings (scenarios AH5, AH6).
    Nothing is changed."""
    from app.services import journal_repairs
    return journal_repairs.find(db)


@router.post("/journals/repairs")
def journal_repairs_apply(body: JournalRepairsIn, db: Session = Depends(get_db),
                          user: SessionUser = Depends(get_current_user)) -> dict:
    """Put right exactly what the user confirmed of that list."""
    from app.services import journal_repairs
    out = journal_repairs.apply(db, vouchers=journal_repairs._ids(body.vouchers), headings=body.headings,
                                actor_user_id=str(user.user_id), actor_username=user.username)
    db.commit()
    return out

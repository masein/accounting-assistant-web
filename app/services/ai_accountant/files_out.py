"""Files the assistant hands over with a reply (2026-11-28, scenario CU8).

Someone who only uses the Telegram or Bale bot can't open the app's report
pages, so a file the books make comes to the chat itself: an invoice's or a
payslip's PDF (the cards' ``file`` blocks, app/services/ai_accountant/blocks.py),
and — through ``get_file`` — the journal as the tax office's Excel sheet, or a
month's close pack. On the phones the same ``file`` block opens from its
``path`` (/api/mobile/v1/documents/…, with that route's permission); the bot
sends the bytes as a document (app/services/messenger.py); the web chat links
``web_path``, the report page's own download.

A block's ``id`` says what it is, so the bytes are made only when a client asks:
``file:invoice:<id>``, ``file:payslip:<run>:<employee>``,
``file:journal:<from>:<to>``, ``file:close-pack:<YYYY-MM>:<zip|pdf|xlsx>``.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.services.ai_accountant.base import BaseTool, ToolContext, ToolError

_FORMATS = {
    "zip": "application/zip",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class FileOutError(Exception):
    """A file that can't be made (an unknown kind, a month that isn't one); the message says why."""


def journal_file(db: Session, from_date: date, to_date: date) -> dict[str, Any]:
    """The journal for a period as one Excel sheet, a row per line (scenarios BL1–BL7), as a file block."""
    from app.services.reporting import journal_sheet
    a, b = from_date.isoformat(), to_date.isoformat()
    name = journal_sheet.filename(db, from_date, to_date)
    query = f"from_date={a}&to_date={b}"
    return {"type": "file", "id": f"file:journal:{a}:{b}", "name": name, "mime": journal_sheet.MEDIA_TYPE,
            "path": f"/api/mobile/v1/documents/journal?{query}",
            "web_path": f"/manager-reports/books/general-journal/export?{query}",
            "fallback_text": f"{name} (Excel)"}


def close_pack_file(db: Session, month: str | None, fmt: str = "zip") -> dict[str, Any]:
    """A month's close pack (roadmap §4.9) as a file block: the ZIP of the PDF, the workbook and the journal's
    CSV, or just the PDF or the workbook."""
    from app.services.reporting.close_pack import ExportError, resolve_month
    if fmt not in _FORMATS:
        raise FileOutError(f"A close pack is a zip, a pdf or an xlsx, not {fmt!r}.")
    try:
        m = resolve_month(db, month, documents=False)
    except ExportError as e:
        raise FileOutError(str(e)) from e
    name = f"close-pack-{m.key}.{fmt}"
    query = f"month={m.key}&format={fmt}"
    return {"type": "file", "id": f"file:close-pack:{m.key}:{fmt}", "name": name, "mime": _FORMATS[fmt],
            "path": f"/api/mobile/v1/documents/close-pack?{query}",
            "web_path": f"/manager-reports/close-pack?{query}",
            "fallback_text": f"{m.label} — {name}"}


def file_bytes(db: Session, block: dict[str, Any]) -> tuple[str, str, bytes]:
    """``(name, mime, bytes)`` of a file block, made now under the current company and user — the same
    documents the app's own pages download (an invoice's party details redacted as there, an employee only
    their own payslip). Raises FileOutError for a block it doesn't know, HTTPException as those pages do."""
    parts = str(block.get("id") or "").split(":")
    if len(parts) < 3 or parts[0] != "file":
        raise FileOutError("That isn't a file the app makes.")
    kind = parts[1]
    try:
        if kind == "invoice":
            from app.api.invoices import invoice_pdf
            resp = invoice_pdf(invoice_id=uuid.UUID(parts[2]), db=db)
            return str(block.get("name") or "invoice.pdf"), "application/pdf", bytes(resp.body)
        if kind == "payslip" and len(parts) >= 4:
            from app.api.payroll import payslip_pdf
            resp = payslip_pdf(run_id=uuid.UUID(parts[2]), entity_id=uuid.UUID(parts[3]), db=db)
            return str(block.get("name") or "payslip.pdf"), "application/pdf", bytes(resp.body)
        if kind == "journal" and len(parts) >= 4:
            from app.services.reporting import journal_sheet
            a, b = date.fromisoformat(parts[2]), date.fromisoformat(parts[3])
            return (journal_sheet.filename(db, a, b), journal_sheet.MEDIA_TYPE,
                    journal_sheet.workbook(db, a, b, None, None))
        if kind == "close-pack" and len(parts) >= 4 and parts[3] in _FORMATS:
            from app.services.reporting import close_pack as cp
            m = cp.resolve_month(db, parts[2])
            make = {"zip": cp.zip_pack, "pdf": cp.pdf, "xlsx": cp.xlsx}[parts[3]]
            return f"close-pack-{m.key}.{parts[3]}", _FORMATS[parts[3]], make(db, m)
    except ValueError as e:                                  # an id that isn't one: a bad uuid or date
        raise FileOutError("That isn't a file the app makes.") from e
    raise FileOutError("That isn't a file the app makes.")


# --- the tool ---------------------------------------------------------------------------------------------------

class GetFileInput(BaseModel):
    kind: Literal["journal_excel", "close_pack"] = Field(
        ..., description="journal_excel: the journal as one Excel sheet, a row per line with its voucher number, "
                         "کل and معین (the quarterly file for the tax office). close_pack: a month's close pack.")
    from_date: date | None = Field(None, description="journal_excel: first day (YYYY-MM-DD, Gregorian); default the "
                                                     "first of to_date's month.")
    to_date: date | None = Field(None, description="journal_excel: last day (YYYY-MM-DD, Gregorian); default today.")
    month: str | None = Field(None, pattern=r"^\d{4}-\d{2}$",
                              description="close_pack: YYYY-MM in the company's calendar; default last month.")
    format: Literal["zip", "pdf", "xlsx"] = Field(
        "zip", description="close_pack: zip (the PDF, the workbook and the journal's CSV), or just the pdf or xlsx.")


class GetFile(BaseTool):
    name = "get_file"
    category = "read"
    description = (
        "Hand the user a file the app makes, sent with your reply as a document they can open or forward: the "
        "journal as one Excel sheet for a period («دفتر روزنامه رو اکسل بفرست», \"journal in Excel for this "
        "season\"), or a month's close pack («بستهٔ پایان مهر رو بفرست», \"send me August's close pack\"). For a "
        "Jalali season give its Gregorian first and last days. Say in one line what you sent and for which period; "
        "never paste its contents. Pure read."
    )
    InputSchema = GetFileInput

    async def run(self, ctx: ToolContext, args: GetFileInput) -> dict[str, Any]:
        from app.core.permissions import user_can_access
        from app.core.request_context import get_current_actor
        if args.kind == "journal_excel":
            from app.services.reporting.common import default_period
            period = default_period(args.from_date, args.to_date)
            block = journal_file(ctx.db, period.from_date, period.to_date)
            result: dict[str, Any] = {"kind": args.kind, "from_date": period.from_date.isoformat(),
                                      "to_date": period.to_date.isoformat()}
        else:
            try:
                block = close_pack_file(ctx.db, args.month, args.format)
            except FileOutError as e:
                raise ToolError(str(e), code="bad_month") from e
            result = {"kind": args.kind, "month": block["id"].split(":")[2], "format": args.format}
        actor = get_current_actor()
        if actor is not None and not user_can_access(actor, "GET", block["path"].split("?", 1)[0]):
            raise ToolError("Your role can't open this file.", code="not_allowed")
        return {**result, "file": block, "name": block["name"],
                "note": "The file goes with your reply; say what it is in one line."}


def register_file_tools(registry) -> None:
    registry.register(GetFile())

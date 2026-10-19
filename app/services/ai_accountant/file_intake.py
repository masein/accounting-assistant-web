"""Smart intake for spreadsheets dropped into the AI chat.

Classifies uploaded spreadsheet attachments by CONTENT (never guesses
silently — the detection is stated to the user, who can redirect) and routes:

1. Chart-of-accounts exports (گروه/کل/معین/تفصیلی shape, ``نوع تفصيلي``
   column, code+title headers) → the existing migration importer: the same
   idempotent preview is staged and rendered in chat; Confirm hits
   ``POST /migration/import/confirm`` (Owner/Accountant — enforced there).
2. Journals (dated lines with accounts and debit/credit; not the chart
   shape), every file dropped → the journal importer the migration page uses
   (``journal_import``): one card per file, its vouchers, dates and accounts
   reviewed; «وارد کن» hits ``POST /migration/journals/apply`` (only ready
   vouchers, each whole), and accounts to match open that page on the same
   upload (scenarios AH1–AH3).
3. Anything else → attached as Q&A context (a text digest of the sheet) so
   the user can ask "what's the total?" without recording anything.

Reuses the migration parser (`app/services/migration_import`) and the
journal importer (`app/services/journal_import`) — no new parsers. Nothing here writes to the books; every write stays confirm-gated
behind the existing endpoints.
"""
from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.services import migration_import as mig

CHART_TIERS = ("group", "kol", "moein", "tafsili")

# A message that is ONLY filesystem path(s) — the pre-fix failure mode where
# drag-and-drop pasted "/Users/…/حساب معين.xls" as text. The assistant must
# ask for a real upload instead of hallucinating file contents. Paths may
# contain spaces ("حساب معين.xls"), so match line-wise: starts like a path,
# ends in a file extension, no other sentence content.
_PATH_LINE_RE = re.compile(
    r"^\s*(?:~?/|[A-Za-z]:\\|file://)\S[^\n]*"
    r"\.(?:xls[xm]?|csv|tsv|pdf|jpe?g|png|webp|txt|docx?|xml)\s*$",
    re.IGNORECASE,
)


def is_path_only_message(message: str) -> bool:
    lines = [ln for ln in (message or "").splitlines() if ln.strip()]
    return bool(lines) and all(_PATH_LINE_RE.match(ln) for ln in lines)


@dataclass
class IntakeResult:
    kind: str                      # chart_export | journals | context
    detected: str                  # short English detection line (persisted)
    payload: dict[str, Any] = field(default_factory=dict)   # card data for the UI
    context_text: str = ""         # Q&A context fed to the model (kind=context)


def _read_attachment(att) -> bytes | None:
    try:
        return Path(att.file_path).read_bytes()
    except OSError:
        return None


def _try_chart(files: list[tuple[str, bytes]]) -> list[tuple[str, list[dict]]] | None:
    """Parse every file with the migration parser; chart-shaped when every
    sheet has the كد/عنوان header and classifies into a chart tier."""
    parsed: list[tuple[str, list[dict]]] = []
    for name, data in files:
        try:
            rows = mig.extract_rows(name, data)
        except Exception:
            return None
        if not rows:
            return None
        kind = mig.classify_rows(rows)
        if kind not in CHART_TIERS:
            return None
        # chart exports carry balances; a code+title sheet without any balance
        # columns is more likely a product/entity list — don't claim it
        if not any(r["balance_debit"] or r["balance_credit"] for r in rows) and kind != "tafsili":
            return None
        parsed.append((name, rows))
    return parsed or None


def _try_journals(db: Session, files: list[tuple[str, bytes]]) -> list[dict[str, Any]]:
    """Every journal-shaped file, read by the journal importer the migration page
    uses and staged under its own token for its review and apply. The chat had
    its own old parser and read only the first file: منادی حق's 1404 journal
    became 124 "vouchers" numbered by date, six of them posted one-sided on
    2027-01-30, and the 1405 file was never read (2026-10-19, scenario AH1).
    The accounts the chart has already learn the file's codes (scenario AH7)."""
    from app.api.migration import _JOURNAL_EXTENSIONS, _JOURNAL_KIND, _journal_path
    from app.core.shared_state import store_upload
    from app.services import journal_import as ji

    out: list[dict[str, Any]] = []
    for name, data in files:
        if not (name or "").lower().endswith(_JOURNAL_EXTENSIONS):
            continue
        try:
            sheet, rows, _sheets = ji.pick_sheet(ji.read_book(name, data))
            parsed = ji.settle_years(db, ji.parse_file(rows), None)
        except Exception:  # noqa: BLE001 — not a journal it can read: the next file, or the other paths
            continue
        cols = parsed["columns"]
        dated_amounts = "date" in cols and ("amount" in cols or {"debit", "credit"} <= set(cols))
        if parsed["needs"] and not (set(parsed["needs"]) <= {"account"} and dated_amounts):
            continue                  # not a journal: no dates, or no amounts
        if not parsed["needs"] and not parsed["vouchers"]:
            continue
        token = hashlib.sha256(data).hexdigest()[:32]
        _journal_path(token).write_bytes(data)
        store_upload(db, _JOURNAL_KIND, token, str(_journal_path(token)))
        if parsed["needs"]:
            # dated amounts whose accounts are in columns of its own («Title 1…3»): the
            # journal page asks which, on the same upload (scenario AH3)
            out.append({"file_name": (name or "")[:120], "token": token, "sheet": sheet, "needs_columns": True,
                        "voucher_count": 0, "from": None, "to": None, "counts": {}, "ready": 0,
                        "unplaced_accounts": 0, "new_accounts": 0, "stray_years": 0, "file_year": None,
                        "codes_reused": []})
            continue
        review = ji.review(db, parsed)
        ji.remember_codes(db, parsed, review["accounts"], only_existing=True)
        counts = review["counts"]
        out.append({
            "file_name": (name or "")[:120], "token": token, "sheet": sheet,
            "voucher_count": review["voucher_count"], "from": review["from"], "to": review["to"],
            "counts": counts, "ready": counts.get("ready", 0),
            "unplaced_accounts": sum(1 for a in review["accounts"] if not a["mapped_to"]),
            "new_accounts": sum(1 for a in review["accounts"] if (a["mapped_to"] or "").startswith("new:")),
            "stray_years": review["stray_years"], "file_year": review["file_year"],
            "codes_reused": review["codes_reused"][:5],
        })
    return out


def _sheet_digest(name: str, data: bytes, max_rows: int = 30) -> str:
    """Plain-text digest of an unrecognized sheet for Q&A context."""
    try:
        rows = _raw_rows(name, data)
    except Exception:
        return f"[Attached file {name}: could not read as a spreadsheet]"
    lines = [f"Attached spreadsheet {name!r} ({len(rows)} rows). Content:"]
    for row in rows[: max_rows + 1]:
        cells = [str(c).strip() if c is not None else "" for c in row]
        if any(cells):
            lines.append(" | ".join(cells))
    if len(rows) > max_rows + 1:
        lines.append(f"… ({len(rows) - max_rows - 1} more rows omitted)")
    return "\n".join(lines)


def _raw_rows(name: str, data: bytes) -> list[list[Any]]:
    suffix = Path(name).suffix.lower()
    if data[:2] == b"PK" or suffix == ".xlsx":
        import io

        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
        finally:
            wb.close()
    if b"<Workbook" in data[:4096]:
        return mig._rows_from_spreadsheetml(data)
    return mig._rows_from_csv(data)


# The detection line, in the reader's language: a Persian chat was told "Looks like a
# transaction list — 96 rows…" (the owner's father, 2026-10-18).
_SAID = {
    "chart_denied": {
        "en": "Looks like a chart-of-accounts export, but importing it needs the Owner or Accountant role — attached for questions only.",
        "fa": "به نظر خروجی کدینگ حساب‌هاست، اما وارد کردنش نقش مالک یا حسابدار می‌خواهد؛ فقط برای پرسش پیوست شد.",
        "es": "Parece una exportación del plan de cuentas, pero importarla requiere el rol de Propietario o Contable; adjunta solo para preguntas.",
        "ar": "يبدو أنه تصدير لدليل الحسابات، لكن استيراده يتطلب دور المالك أو المحاسب؛ أُرفق للأسئلة فقط.",
    },
    "chart": {
        "en": "Looks like a chart-of-accounts export ({tiers}) — preview staged.",
        "fa": "به نظر خروجی کدینگ حساب‌هاست ({tiers})؛ پیش‌نمایش آماده است.",
        "es": "Parece una exportación del plan de cuentas ({tiers}); vista previa lista.",
        "ar": "يبدو أنه تصدير لدليل الحسابات ({tiers})؛ المعاينة جاهزة.",
    },
    # one line for every file of a journal drop (scenarios AH1, AH11); the card says the same words
    "journals": {
        "en": "Looks like a journal: {files}.",
        "fa": "به نظر فهرست سندهاست: {files}.",
        "es": "Parece un libro diario: {files}.",
        "ar": "يبدو أنه دفتر يومية: {files}.",
    },
    "journal_file": {
        "en": "{name}, {vouchers} vouchers from {start} to {end}",
        "fa": "{name} با {vouchers} سند از {start} تا {end}",
        "es": "{name}, {vouchers} asientos del {start} al {end}",
        "ar": "{name}، {vouchers} قيدًا من {start} إلى {end}",
    },
    "journal_columns": {"en": "{name}, whose account columns are to be chosen first",
                        "fa": "{name} که اول باید ستون حساب‌هایش انتخاب شود",
                        "es": "{name}, cuyas columnas de cuentas hay que elegir antes",
                        "ar": "{name}، ويلزم اختيار أعمدة حساباته أولًا"},
    "journal_ready": {"en": "{n} ready to import", "fa": "{n} سند آمادهٔ ورود", "es": "{n} listos para importar",
                      "ar": "{n} جاهزة للاستيراد"},
    "journal_new_accounts": {"en": "{n} new account(s) will be made in the chart",
                             "fa": "{n} حساب تازه در کدینگ ساخته می‌شود",
                             "es": "se crearán {n} cuenta(s) nueva(s) en el plan",
                             "ar": "سيُنشأ {n} حسابًا جديدًا في الدليل"},
    "journal_held": {"en": "held back: {list}", "fa": "کنار می‌ماند: {list}", "es": "retenidos: {list}",
                     "ar": "يُحجز: {list}"},
    "journal_code_reused": {"en": "in the file, code {code} is {n} accounts: {names}",
                            "fa": "در فایل، کد {code} نام {n} حساب است: {names}",
                            "es": "en el archivo, el código {code} es {n} cuentas: {names}",
                            "ar": "في الملف، الرمز {code} هو {n} حسابات: {names}"},
    "journal_repairs": {"en": "an earlier import left {n} voucher(s) read wrongly — Migration lists them to remove",
                        "fa": "ورود قبلی {n} سند را نادرست ثبت کرده؛ «مهاجرت از سیستم قبلی» فهرستشان را برای حذف نشان می‌دهد",
                        "es": "una importación anterior dejó {n} asiento(s) mal leídos; Migración los lista para quitarlos",
                        "ar": "ترك استيراد سابق {n} قيدًا مقروءًا خطأً؛ صفحة «الترحيل» تعرضها لحذفها"},
    "journal_twins": {"en": "an earlier import made «{name}» twice — Migration lists it to merge",
                      "fa": "ورود قبلی «{name}» را دو بار ساخته؛ «مهاجرت از سیستم قبلی» آن را برای یکی کردن نشان می‌دهد",
                      "es": "una importación anterior creó «{name}» dos veces; Migración lo lista para unirlo",
                      "ar": "أنشأ استيراد سابق «{name}» مرتين؛ صفحة «الترحيل» تعرضه لدمجه"},
    "journal_unplaced": {
        "en": "{n} account(s) to match first",
        "fa": "{n} حساب نگاشت‌نشده",
        "es": "{n} cuenta(s) por asignar antes",
        "ar": "{n} حسابًا يلزم ربطه أولًا",
    },
    "context": {
        "en": "Attached for questions — not recognized as a chart export or transaction list.",
        "fa": "برای پرسش پیوست شد؛ نه کدینگ حساب‌ها شناخته شد و نه فهرست سند.",
        "es": "Adjunto para preguntas: no se reconoce como plan de cuentas ni como lista de asientos.",
        "ar": "أُرفق للأسئلة؛ لم يُعرف كدليل حسابات ولا كقائمة قيود.",
    },
}


def _digits(lang: str, value: int) -> str:
    if lang == "fa":
        from app.utils.jalali import to_persian_digits
        return to_persian_digits(value)
    return str(value)


def _said(lang: str, key: str, **params) -> str:
    text = _SAID[key].get(lang) or _SAID[key]["en"]
    if lang in ("fa", "ar"):
        # a file's Latin name kept whole: «manadi-1404.xlsx» first made the line read left to right
        if "name" in params:
            params["name"] = f"\u2068{params['name']}\u2069"
    if lang == "fa":
        from app.utils.jalali import to_persian_digits
        params = {k: to_persian_digits(v) if isinstance(v, int) or k == "code" else v for k, v in params.items()}
    return text.format(**params)


# A bank's own export: a running balance and amounts on dated rows, and no account columns.
# It is a statement, not a journal: the journal parser read one as 83 vouchers with every
# description an account of its own (the owner's father, 2026-10-18), and the statement
# import never saw it.
_BALANCE_HEADS = ("موجودی", "مانده", "balance", "saldo", "الرصيد", "رصيد")
_ACCOUNT_HEADS = ("کد حساب", "كد حساب", "عنوان حساب", "حساب کل", "حساب معین", "حساب معين", "account code",
                  "account name", "account title", "nominal", "معین", "تفصیلی", "تفصيلي")


def _statement_shaped(files: list[tuple[str, bytes]]) -> bool:
    try:
        rows = _raw_rows(*files[0])
    except Exception:  # noqa: BLE001 — unreadable here is the other paths' business
        return False
    from app.services.statement_import import _norm
    for row in rows[:6]:
        heads = [_norm(str(c)) for c in row if isinstance(c, str) and c.strip()]
        if len(heads) < 3:
            continue
        if any(any(w in h for w in _ACCOUNT_HEADS) for h in heads):
            return False
        if any(any(w in h for w in _BALANCE_HEADS) for h in heads):
            return True
    return False


def build_spreadsheet_intake(
    db: Session, attachments: list, *, can_migrate: bool, lang: str = "en"
) -> IntakeResult | None:
    """Classify the spreadsheet attachments of a chat turn and build the
    intake card. ``can_migrate`` gates the chart route (migration:write).
    None for a bank's own export: the statement import takes it."""
    files: list[tuple[str, bytes]] = []
    for att in attachments:
        data = _read_attachment(att)
        if data is not None:
            files.append((att.file_name or "sheet", data))
    if not files:
        return None

    chart = _try_chart(files)
    if chart is not None:
        tiers_present = {mig.classify_rows(rows) for _n, rows in chart}
        missing = [t for t in CHART_TIERS if t not in tiers_present]
        if not can_migrate:
            return IntakeResult(
                kind="context",
                detected=_said(lang, "chart_denied"),
                context_text="\n\n".join(_sheet_digest(n, d) for n, d in files),
                payload={"denied": "migration"},
            )
        hasher = hashlib.sha256()
        for _n, data in files:
            hasher.update(data)
        token = hasher.hexdigest()[:32]
        batch, summary, already_applied = mig.stage_preview_batch(db, chart, token)
        return IntakeResult(
            kind="chart_export",
            detected=_said(lang, "chart", tiers=", ".join(sorted(tiers_present))),
            payload={
                "token": token,
                "batch_id": str(batch.id),
                "summary": summary,
                "missing_tiers": missing,
                "already_applied": already_applied,
                "default_opening_date": mig.default_opening_date(db).isoformat(),
            },
        )

    if _statement_shaped(files):
        return None
    journals = _try_journals(db, files) if can_migrate else []
    if journals:
        from app.services.ai_accountant.blocks import display_date
        from app.services.locale_service import get_display_calendar
        calendar = get_display_calendar(db)
        sep = "؛ " if lang in ("fa", "ar") else "; "
        def file_line(j: dict) -> str:
            if j.get("needs_columns"):
                return _said(lang, "journal_columns", name=j["file_name"])
            return _said(lang, "journal_file", name=j["file_name"], vouchers=j["voucher_count"],
                         start=display_date(j["from"], calendar, lang) or "",
                         end=display_date(j["to"], calendar, lang) or "")
        said = sep.join(file_line(j) for j in journals)
        unplaced = sum(j["unplaced_accounts"] for j in journals)
        if unplaced:
            said += sep + _said(lang, "journal_unplaced", n=unplaced)
        from app.services.journal_import import HELD_FOR
        listed = "، " if lang in ("fa", "ar") else ", "
        for j in journals:
            # the card's own words, the same as the line above (scenario AH11)
            j["said"] = file_line(j)
            notes = []
            if j["ready"]:
                notes.append(_said(lang, "journal_ready", n=j["ready"]))
            if j["new_accounts"]:
                notes.append(_said(lang, "journal_new_accounts", n=j["new_accounts"]))
            if j["unplaced_accounts"]:
                notes.append(_said(lang, "journal_unplaced", n=j["unplaced_accounts"]))
            held = [f"{_digits(lang, count)} {(HELD_FOR.get(why) or {}).get(lang) or why}"
                    for why, count in j["counts"].items() if why != "ready"]
            if held:
                notes.append(_said(lang, "journal_held", list=listed.join(held)))
            for reused in j["codes_reused"]:
                notes.append(_said(lang, "journal_code_reused", code=reused["code"], n=len(reused["names"]),
                                   names=listed.join(reused["names"])))
            j["notes"] = notes
        # what an earlier, misread import left: said on the first card, put right on the
        # migration page by the user's word (scenario AH5)
        from app.services.journal_repairs import find as find_repairs
        repairs = find_repairs(db)
        broken = len(repairs["vouchers"])
        if broken:
            journals[0]["notes"].append(_said(lang, "journal_repairs", n=broken))
        for twin in repairs["headings"]:             # one ledger made into two headings (scenario AH6)
            journals[0]["notes"].append(_said(lang, "journal_twins", name=twin["name"]))
        return IntakeResult(kind="journals", detected=_said(lang, "journals", files=said),
                            payload={"files": journals, "calendar": calendar,
                                     "repairs": broken + len(repairs["headings"])})

    return IntakeResult(
        kind="context",
        detected=_said(lang, "context"),
        context_text="\n\n".join(_sheet_digest(n, d) for n, d in files),
    )

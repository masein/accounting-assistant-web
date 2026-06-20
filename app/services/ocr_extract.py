"""Document OCR / field extraction for invoices, receipts and bank statements.

Two failure modes this module is built to avoid (both seen with a real
Asiatech Persian invoice):

* **Digit concatenation** — scraping every number on a dense Persian invoice
  (economic codes, serials, postal codes) and gluing them into a 15-digit
  "amount". The fix: send the *image* to a vision model and ask for the
  labelled grand total, then coerce only that field — never max-of-all-digits.
* **Persian numerals** — ۰۱۲۳۴۵۶۷۸۹ / ٠١٢٣٤٥٦٧٨٩ and Persian thousands
  separators must be normalized before ``int()``.

Pipeline: rasterize the document to PNG (PyMuPDF for PDFs; images pass
through), send the page images to a vision-capable model (``OCR_MODEL``,
default ``gpt-4o``) on the active OpenAI-compatible backend, and parse the
returned JSON. If rasterization or the vision call is unavailable we fall
back to embedded-PDF-text extraction with a *total-targeted* parser (not a
digit scraper).
"""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx
from pypdf import PdfReader

from app.core.ai_runtime import resolve_active_ai_backend
from app.core.config import settings
from app.utils.jalali import try_parse_jalali

logger = logging.getLogger(__name__)

# Largest amount we will ever believe from a document. Anything above this is
# digit garbage (the Asiatech bug proposed 8.45e17). World GDP is ~1e14 USD;
# even in Rial a single SME invoice never exceeds 1e15.
MAX_SANE_AMOUNT = 10**15

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


class OCRExtractError(Exception):
    pass


def normalize_digits(text: str) -> str:
    """Persian/Arabic-Indic digits → ASCII. Also drops the Arabic decimal
    separator and thousands marks so the result is plain ASCII digits."""
    if not text:
        return ""
    return text.translate(_PERSIAN_DIGITS).translate(_ARABIC_DIGITS)


def coerce_amount(value: Any) -> int | None:
    """Coerce a model-provided amount (int, float, or a string possibly
    carrying Persian digits / thousands separators / a currency word) into a
    sane integer. Returns None for unparseable or insane magnitudes."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            n = int(round(float(value)))
        except (ValueError, OverflowError):
            return None
        return n if 0 <= n <= MAX_SANE_AMOUNT else None

    s = normalize_digits(str(value)).strip()
    if not s:
        return None
    # Normalize Arabic separators to ASCII, then keep only digits + , . so we
    # can reason about which is the decimal point. "۳٬۶۹۰٬۷۲۰ ریال" → "3690720".
    s = s.replace("٬", ",").replace("٫", ".")  # Arabic thousands / decimal
    s = re.sub(r"[^\d.,]", "", s)
    if not s or s in (".", ","):
        return None
    last_dot = s.rfind(".")
    last_comma = s.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        # Both present: the LATER separator is the decimal point, the other is
        # a thousands separator. "3,600.00" → 3600.00; "1.234.567,89" → 1234567.89.
        if last_dot > last_comma:
            s = s.replace(",", "")
        else:
            s = s.replace(".", "").replace(",", ".")
    elif last_comma >= 0:
        # Only commas: thousands separators in our convention. "3,690,720" →
        # 3690720. (A lone European decimal comma is rare here and the prompt
        # tells the model to use a period for decimals.)
        s = s.replace(",", "")
    else:
        # Only dots: a single dot is a decimal point ("21.60"); several dots are
        # thousands separators ("1.234.567" → 1234567).
        if s.count(".") > 1:
            s = s.replace(".", "")
    if not s or s == ".":
        return None
    try:
        n = int(round(float(s)))
    except (ValueError, OverflowError):
        return None
    return n if 0 <= n <= MAX_SANE_AMOUNT else None


_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _normalize_date(value: Any) -> str | None:
    """Normalize a model-provided date to ISO ``YYYY-MM-DD``. Accepts a
    Gregorian ISO date, a Jalali date (1404/10/15 — common on Iranian
    invoices), common day-first numeric receipt dates (``DD/MM/YYYY``), and
    text dates (``18 June 2026`` / ``June 18, 2026``).

    Numeric ``a/b/YYYY`` is read day-first (UK/most-of-world convention) unless
    that's impossible (a value > 12), in which case it's month-first. Returns
    None when no date is found so the caller can fall back to today."""
    if not value:
        return None
    from datetime import date as _date

    raw = normalize_digits(str(value)).strip()

    def _iso(y: int, mo: int, d: int) -> str | None:
        try:
            return _date(y, mo, d).isoformat() if 1900 <= y <= 2100 else None
        except ValueError:
            return None

    # Already a plausible Gregorian ISO date (YYYY-MM-DD).
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", raw)
    if m and 1900 <= int(m.group(1)) <= 2100:
        return _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # Jalali (year 13xx/14xx) → Gregorian.
    jalali = try_parse_jalali(raw)
    if jalali:
        return jalali.isoformat()

    # Numeric day/month/year: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY (day-first).
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", raw)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12 and b <= 12:        # unambiguous day-first
            return _iso(y, b, a)
        if b > 12 and a <= 12:        # unambiguous month-first
            return _iso(y, a, b)
        return _iso(y, b, a)          # ambiguous → day-first (UK/intl default)

    # Text dates: "18 June 2026", "18 Jun 2026", "June 18, 2026".
    low = raw.lower()
    m = re.match(r"^(\d{1,2})\s+([a-z]+)\.?,?\s+(\d{4})$", low)
    if m and m.group(2) in _MONTHS:
        return _iso(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
    m = re.match(r"^([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$", low)
    if m and m.group(1) in _MONTHS:
        return _iso(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
    return None


def _resolve_ocr_base_model() -> tuple[str, str]:
    """OCR uses the active OpenAI-compatible backend's URL/key but a stronger
    vision model (``OCR_MODEL``)."""
    cfg = resolve_active_ai_backend()
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    model = (settings.ocr_model or cfg.get("model") or "gpt-4o").strip()
    return base, model


def _chat_completions_url(base: str) -> str:
    b = (base or "").rstrip("/")
    if b.endswith("/chat/completions"):
        return b
    if "/openai/" in b or "/wrapper/" in b or re.search(r"/v\d+$", b):
        return f"{b}/chat/completions"
    return f"{b}/v1/chat/completions"


def _resolve_ai_headers() -> dict[str, str]:
    cfg = resolve_active_ai_backend()
    key = (cfg.get("api_key") or "").strip()
    if not key:
        return {}
    header = (cfg.get("api_key_header") or "Authorization").strip() or "Authorization"
    prefix = (cfg.get("api_key_prefix") or "").strip()
    if header.lower() == "authorization" and prefix and not prefix.endswith(" "):
        prefix = prefix + " "
    value = f"{prefix}{key}" if prefix else key
    return {header: value}


# Labels that mark the invoice grand total across Persian + English layouts,
# most-specific first so "جمع کل" beats a bare "جمع".
_TOTAL_LABELS = [
    "مبلغ کل بعلاوه مالیات",
    "جمع کل صورتحساب",
    "مبلغ قابل پرداخت",
    "جمع کل",
    "مبلغ کل",
    "قابل پرداخت",
    "grand total",
    "total payable",
    "amount due",
    "total",
]


def _amount_from_total_line(text: str) -> int | None:
    """Text fallback: find the amount on a line labelled as the grand total,
    instead of scraping every digit on the page (the concatenation bug)."""
    if not text:
        return None
    norm = normalize_digits(text)
    labelled: list[int] = []
    for raw_line in norm.splitlines():
        low = raw_line.lower()
        if any(lbl in low for lbl in _TOTAL_LABELS):
            nums = re.findall(r"\d[\d,٬\.]{2,}", raw_line)
            labelled.extend(c for c in (coerce_amount(n) for n in nums) if c)
    # The grand total is the largest of the total-labelled figures (it
    # includes tax, where the bare subtotal does not).
    return max(labelled) if labelled else None


def _extract_fields_from_text(text: str) -> dict[str, Any]:
    """Pure-text fallback parser (no vision). Targets labelled fields rather
    than scraping/concatenating numbers."""
    t = text or ""
    norm = normalize_digits(t)

    ref = None
    m_ref = re.search(
        r"\b(?:INVOICE|INV|RECEIPT|RCPT|شماره فاکتور|شماره صورتحساب)\b[\s#:.\-]*([A-Z0-9][A-Z0-9\-_/]{1,})",
        norm,
        re.IGNORECASE,
    )
    if m_ref:
        ref = m_ref.group(1).strip()

    # Date: Jalali first (Iranian docs), then Gregorian ISO.
    dt = None
    jalali = try_parse_jalali(t)
    if jalali:
        dt = jalali.isoformat()
    else:
        m_date = re.search(r"\b(20\d{2}[-/](?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01]))\b", norm)
        if m_date:
            dt = m_date.group(1).replace("/", "-")

    amount = _amount_from_total_line(t)

    currency = "IRR"
    low = t.lower()
    if "rial" in low or "irr" in low or "ریال" in t:
        currency = "IRR"
    elif "toman" in low or "تومان" in t:
        currency = "IRR"

    vendor = None
    m_vendor = re.search(
        r"(?:vendor|from|seller|payee|فروشنده|نام فروشنده)\s*[:\-]?\s*([^\r\n]{2,60})",
        t,
        re.IGNORECASE,
    )
    if m_vendor:
        vendor = m_vendor.group(1).strip()
    if not vendor:
        lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
        if lines:
            vendor = lines[0][:48]

    confidence = 0.15
    for v in (vendor, ref, dt, amount):
        if v:
            confidence += 0.15
    return {
        "vendor_name": vendor,
        "invoice_or_receipt_no": ref,
        "date": dt,
        "amount": amount,
        "currency": currency,
        "confidence": round(min(0.9, confidence), 2),
        "raw_text": t[:6000],
    }


class OCREngineMissing(OCRExtractError):
    """Raised when the PDF rasterizer (PyMuPDF / ``fitz``) isn't installed.

    Distinct from "document unreadable": this means the *image* was built
    without the OCR engine, so no PDF can be rasterized for the vision model.
    The cure is rebuilding the app image, not retrying the document."""


def ocr_engine_available() -> bool:
    """True iff PyMuPDF (``fitz``) imports — i.e. PDFs can be rasterized for
    the vision OCR path. Surfaced via /health and /brain/ocr-health so a
    missing engine is observable instead of silently degrading every scan."""
    try:
        import fitz  # noqa: F401  (PyMuPDF)

        return True
    except Exception:
        return False


def _rasterize_pages(path: Path, ctype: str, max_pages: int = 4) -> list[tuple[str, str]]:
    """Return ``(mime_type, base64)`` page images for the document. Images
    pass through as-is; PDFs are rendered page-by-page via PyMuPDF.

    Raises ``OCREngineMissing`` if a PDF needs rasterizing but PyMuPDF isn't
    installed (a build problem, not a document problem). Returns an empty
    list only when rasterization itself fails on a specific PDF (corrupt
    file), so the caller falls back to embedded text."""
    if ctype != "application/pdf":
        raw = path.read_bytes()
        return [(ctype or "image/png", base64.b64encode(raw).decode("ascii"))]
    try:
        import fitz  # PyMuPDF
    except Exception as e:
        raise OCREngineMissing(
            "OCR engine not installed (PyMuPDF missing) — rebuild the app "
            "image with: docker compose up -d --build app"
        ) from e
    pages: list[tuple[str, str]] = []
    try:
        doc = fitz.open(str(path))
        # 220 DPI keeps dense Persian invoice digits legible to the model.
        zoom = fitz.Matrix(220 / 72, 220 / 72)
        for page in doc[:max_pages]:
            pix = page.get_pixmap(matrix=zoom)
            png = pix.tobytes("png")
            pages.append(("image/png", base64.b64encode(png).decode("ascii")))
        doc.close()
    except Exception:
        logger.warning("PDF rasterization failed", exc_info=True)
        return []
    return pages


def _rasterize_to_png_data_urls(path: Path, ctype: str, max_pages: int = 4) -> list[str]:
    """Back-compat wrapper: page images as ``data:`` URLs."""
    return [f"data:{mime};base64,{b64}" for mime, b64 in _rasterize_pages(path, ctype, max_pages)]


_VISION_PROMPT = (
    "You are extracting fields from a financial document (invoice, receipt, or "
    "bank statement) that may be in Persian/Farsi and right-to-left. Read the "
    "document image carefully and return ONLY a JSON object with these keys:\n"
    "  vendor_name (string or null) — the seller/issuer name\n"
    "  invoice_or_receipt_no (string or null)\n"
    "  date (string or null) — the document/invoice date EXACTLY as printed "
    "(keep Jalali like 1404/10/15 if that is what is shown)\n"
    "  currency (string) — ISO code, e.g. IRR for Rial/ریال\n"
    "  subtotal (number or null), tax (number or null)\n"
    "  total (number or null) — the GRAND TOTAL the customer pays, i.e. the "
    "amount next to a label like 'جمع کل', 'مبلغ کل', 'مبلغ کل بعلاوه مالیات', "
    "'مبلغ قابل پرداخت', or 'Total'. This is usually subtotal + tax.\n"
    "  amount (number or null) — same as total\n"
    "  line_items (array of {description, amount} or empty)\n"
    "  confidence (0..1)\n"
    "Rules: Convert any Persian digits (۰۱۲۳۴۵۶۷۸۹) to normal digits. For "
    "subtotal, tax, total and amount, report the value in MAJOR currency units "
    "(pounds, dollars, euros, rials) exactly as printed. A period '.' is a "
    "DECIMAL point (pence/cents) — KEEP it (e.g. 21.60, 3600.00). Only commas "
    "',' and the Arabic mark '٬' are thousands separators — remove those "
    "(3,690,720 → 3690720). Do NOT remove the decimal point and do NOT multiply "
    "by 100 / scale to minor units (£21.60 stays 21.60, never 2160). Iranian "
    "Rial amounts have no minor units, so they have no decimals. NEVER "
    "concatenate unrelated numbers such as economic/tax codes, serial numbers, "
    "phone or postal codes — only report the labelled monetary total. If you "
    "cannot find the total, return null."
)


def _parse_json_blob(content: str) -> dict[str, Any]:
    content = (content or "").strip()
    m = re.search(r"\{[\s\S]*\}", content)
    if m:
        content = m.group(0)
    try:
        out = json.loads(content)
    except json.JSONDecodeError as e:
        raise OCRExtractError(f"Invalid OCR JSON: {e!s}") from e
    if not isinstance(out, dict):
        raise OCRExtractError("OCR output must be an object")
    return out


def _gemini_enabled(model: str) -> bool:
    """Use the Gemini wrapper when the OCR model is a Gemini model and we
    have an API key (the Metis key works for both wrappers)."""
    if not model.lower().startswith("gemini"):
        return False
    key = (resolve_active_ai_backend().get("api_key") or "").strip()
    return bool(key and (settings.gemini_base_url or "").strip())


async def _gemini_raw(pages: list[tuple[str, str]], model: str, prompt: str) -> str:
    """Raw vision call via Metis's Google-format Gemini wrapper — returns the
    model's text. gemini-2.5 reads Persian-script digits exactly where gpt-4o
    misreads them."""
    key = (resolve_active_ai_backend().get("api_key") or "").strip()
    base = (settings.gemini_base_url or "").strip().rstrip("/")
    url = f"{base}/models/{model}:generateContent"
    parts: list[dict[str, Any]] = [{"text": prompt}]
    for mime, b64 in pages:
        parts.append({"inline_data": {"mime_type": mime, "data": b64}})
    payload = {"contents": [{"parts": parts}], "generationConfig": {"temperature": 0}}
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            url, json=payload,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        )
        r.raise_for_status()
        body = r.json()
    candidates = body.get("candidates") or []
    if not candidates:
        raise OCRExtractError("No OCR output from Gemini")
    text_parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in text_parts)


async def _openai_vision_raw(pages: list[tuple[str, str]], model: str, prompt: str) -> str:
    """Raw vision call via the OpenAI-compatible endpoint — returns the
    model's text."""
    base, _ = _resolve_ocr_base_model()
    if not base:
        raise OCRExtractError("AI backend URL not configured")
    url = _chat_completions_url(base)
    headers = _resolve_ai_headers()
    user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for mime, b64 in pages:
        user_content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You extract structured data from financial documents. Output JSON only."},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.0,
        "max_tokens": 4000,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, json=payload, headers=headers or None)
        r.raise_for_status()
        body = r.json()
    choices = body.get("choices") or []
    if not choices:
        raise OCRExtractError("No OCR output from model")
    return ((choices[0].get("message") or {}).get("content") or "").strip()


async def _vision_raw(pages: list[tuple[str, str]], prompt: str) -> str:
    """Vision call with provider escalation: Gemini (accurate on Persian
    numerals) first, then the OpenAI-compatible gpt-4o path. Returns raw text;
    the caller parses it for the shape it asked for."""
    if not pages:
        raise OCRExtractError("No image to send to the vision model")
    _, model = _resolve_ocr_base_model()
    last_err: Exception | None = None
    if _gemini_enabled(model):
        try:
            return await _gemini_raw(pages, model, prompt)
        except Exception as e:  # fall through to the OpenAI-compatible model
            last_err = e
            logger.warning("Gemini OCR failed — falling back to %s", settings.ocr_fallback_model, exc_info=True)
    fallback = settings.ocr_fallback_model or "gpt-4o"
    try:
        return await _openai_vision_raw(pages, fallback, prompt)
    except Exception as e:
        raise OCRExtractError(f"Vision OCR failed: {e}") from (last_err or e)


async def _vision_extract(pages: list[tuple[str, str]]) -> dict[str, Any]:
    """Invoice/receipt extraction: ask for the labelled total, parse the
    object, normalize."""
    content = await _vision_raw(pages, _VISION_PROMPT)
    return _normalize_extracted(_parse_json_blob(content))


_STATEMENT_PROMPT = (
    "This is a bank statement (account transaction list), possibly in "
    "Persian/Farsi and right-to-left. Read EVERY transaction row in the table "
    "and return ONLY a JSON array. Each element is one row:\n"
    '  {"date": string (the row date EXACTLY as printed, keep Jalali like '
    "1405/02/06 if shown), \"description\": string (the row narrative / "
    'counterparty), "amount": integer (the transaction amount, positive, no '
    'separators), "balance": integer or null (the running balance after the '
    'row), "direction": "debit" or "credit"}.\n'
    "direction is 'credit' for money IN (deposit / واریز / بستانکار) and "
    "'debit' for money OUT (withdrawal / برداشت / بدهکار). Convert Persian "
    "digits (۰۱۲۳۴۵۶۷۸۹) to normal digits; amounts are WHOLE units, plain "
    "integers (e.g. 133425600, not 133,425,600 or ۱۳۳٬۴۲۵٬۶۰۰). Include every "
    "row in order. If the document is not a statement or has no rows, return []."
)


def _parse_json_array(content: str) -> list[dict[str, Any]]:
    content = (content or "").strip()
    m = re.search(r"\[[\s\S]*\]", content)
    if m:
        content = m.group(0)
    try:
        out = json.loads(content)
    except json.JSONDecodeError as e:
        raise OCRExtractError(f"Invalid statement OCR JSON: {e!s}") from e
    if not isinstance(out, list):
        raise OCRExtractError("Statement OCR output must be a JSON array")
    return [r for r in out if isinstance(r, dict)]


async def extract_statement_rows(path: str, content_type: str) -> list[dict[str, Any]]:
    """Vision-extract a bank statement (image/PDF) into normalized rows:
    ``{date(ISO|None), description, amount, balance, direction}``. Uses the
    same rasterize + Gemini/gpt-4o vision path as the invoice extractor (which
    reads dense Persian tables that the free-text row parser can't). Raises
    OCRExtractError on any failure so the caller can fall back / return a
    clean 422 — never a 500."""
    p = Path(path)
    if not p.exists():
        raise OCRExtractError("Attachment file not found")
    ctype = (content_type or "").lower()
    if ctype.startswith("image/jpeg") or ctype.startswith("image/jpg"):
        ctype = "image/jpeg"

    pages = _rasterize_pages(p, ctype)  # raises OCREngineMissing if no PyMuPDF
    content = await _vision_raw(pages, _STATEMENT_PROMPT)
    raw_rows = _parse_json_array(content)

    rows: list[dict[str, Any]] = []
    for r in raw_rows:
        amount = coerce_amount(r.get("amount"))
        if amount is None or amount <= 0:
            continue
        direction = str(r.get("direction") or "").strip().lower()
        if direction not in ("debit", "credit"):
            # Infer from a signed amount if the model didn't label it.
            direction = "debit" if str(r.get("amount") or "").strip().startswith("-") else "credit"
        rows.append({
            "date": _normalize_date(r.get("date")),
            "description": str(r.get("description") or "").strip(),
            "amount": amount,
            "balance": coerce_amount(r.get("balance")),
            "direction": direction,
        })
    return rows


def _normalize_extracted(out: dict[str, Any]) -> dict[str, Any]:
    """Coerce the model's JSON into our stable, validated shape: integer
    amounts (Persian-aware, sanity-capped), an ISO/Gregorian date, and the
    grand total preferred over any line/subtotal figure."""
    total = coerce_amount(out.get("total"))
    amount = coerce_amount(out.get("amount"))
    subtotal = coerce_amount(out.get("subtotal"))
    tax = coerce_amount(out.get("tax"))
    # Prefer an explicit grand total; else amount; else subtotal+tax.
    best = total or amount
    if best is None and subtotal is not None:
        best = subtotal + (tax or 0)
    return {
        "vendor_name": (out.get("vendor_name") or None),
        "invoice_or_receipt_no": (out.get("invoice_or_receipt_no") or None),
        "date": _normalize_date(out.get("date")),
        "amount": best,
        "subtotal": subtotal,
        "tax": tax,
        "total": total or best,
        "currency": (str(out.get("currency") or "IRR").upper() or "IRR"),
        "line_items": out.get("line_items") if isinstance(out.get("line_items"), list) else [],
        "confidence": out.get("confidence") if isinstance(out.get("confidence"), (int, float)) else 0.6,
        "raw_text": str(out.get("raw_text") or "")[:6000],
    }


async def extract_from_attachment(path: str, content_type: str) -> dict[str, Any]:
    """Extract structured fields from an invoice/receipt/statement file.

    Vision-first: rasterize to PNG and ask a vision model for the labelled
    total. On any failure, fall back to embedded-text parsing (PDFs) so the
    caller still gets a (lower-confidence) result instead of an exception.
    """
    p = Path(path)
    if not p.exists():
        raise OCRExtractError("Attachment file not found")
    ctype = (content_type or "").lower()
    if ctype.startswith("image/jpeg") or ctype.startswith("image/jpg"):
        ctype = "image/jpeg"

    try:
        pages = _rasterize_pages(p, ctype)
    except OCREngineMissing as e:
        # A build problem, not a document problem — log it distinctly so it
        # never again masquerades as "couldn't read the document". PDFs with
        # an embedded text layer still degrade to text below.
        logger.error("OCR unavailable: %s", e)
        pages = []
    if pages:
        try:
            result = await _vision_extract(pages)
            # Backfill raw text from the PDF so downstream parsers (bank
            # statement row extraction) still have text to work with.
            if not result.get("raw_text") and ctype == "application/pdf":
                result["raw_text"] = _extract_pdf_text(p)
            return result
        except Exception:
            logger.warning("Vision OCR failed — falling back to text", exc_info=True)

    # Fallback: embedded PDF text (or empty for images we couldn't send).
    if ctype == "application/pdf":
        txt = _extract_pdf_text(p)
        if txt:
            return _extract_fields_from_text(txt)
    return {
        "vendor_name": None,
        "invoice_or_receipt_no": None,
        "date": None,
        "amount": None,
        "currency": "IRR",
        "confidence": 0.1,
        "raw_text": "",
    }


def _extract_pdf_text(path: Path) -> str:
    try:
        reader = PdfReader(str(path))
        out = []
        for page in reader.pages[:6]:
            out.append(page.extract_text() or "")
        return "\n".join(out).strip()
    except Exception:
        return ""

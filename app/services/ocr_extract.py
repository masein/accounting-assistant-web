from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx
from pypdf import PdfReader

from app.core.ai_runtime import resolve_active_ai_backend
from app.core.config import settings


class OCRExtractError(Exception):
    pass


def _resolve_ai_base_model() -> tuple[str, str]:
    cfg = resolve_active_ai_backend()
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    model = (cfg.get("model") or settings.lm_studio_model or "qwen/qwen3-4b-thinking-2507").strip()
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


def _extract_fields_from_text(text: str) -> dict[str, Any]:
    t = text or ""
    vendor = None
    ref = None
    dt = None
    amount = None
    currency = "IRR"

    m_ref = re.search(
        r"\b(?:INVOICE|INV|RECEIPT|RCPT)\b[\s#:.-]+([A-Z0-9][A-Z0-9\-_/]{1,})",
        t,
        re.IGNORECASE,
    )
    if m_ref:
        ref = m_ref.group(1).strip()
    m_date = re.search(r"\b(20\d{2}[-/](?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01]))\b", t)
    if m_date:
        dt = m_date.group(1).replace("/", "-")
    m_amt = re.findall(r"(\d[\d,]{2,})", t)
    if m_amt:
        try:
            amount = max(int(x.replace(",", "")) for x in m_amt)
        except Exception:
            amount = None
    if "rial" in t.lower() or "irr" in t.lower():
        currency = "IRR"
    m_vendor = re.search(
        r"(?:vendor|from|seller|payee)\s*[:\-]?\s*([^\r\n]{2,60})",
        t,
        re.IGNORECASE,
    )
    if m_vendor:
        vendor = m_vendor.group(1).strip()
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if not vendor and lines:
        vendor = lines[0][:48]
    confidence = 0.2
    for v in (vendor, ref, dt, amount):
        if v:
            confidence += 0.2
    return {
        "vendor_name": vendor,
        "invoice_or_receipt_no": ref,
        "date": dt,
        "amount": amount,
        "currency": currency,
        "confidence": round(min(0.95, confidence), 2),
        "raw_text": t[:6000],
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


async def _vision_extract(data_url: str) -> dict[str, Any]:
    base, model = _resolve_ai_base_model()
    if not base:
        raise OCRExtractError("AI backend URL not configured")
    url = _chat_completions_url(base)
    headers = _resolve_ai_headers()
    prompt = (
        "Extract receipt/invoice fields and return JSON only with keys: "
        "vendor_name, invoice_or_receipt_no, date(YYYY-MM-DD or null), amount(integer or null), currency, confidence(0..1), raw_text."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You extract structured fields from financial documents. Output JSON only."},
            {"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": data_url}}]},
        ],
        "temperature": 0.1,
        "max_tokens": 800,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=payload, headers=headers or None)
        r.raise_for_status()
        body = r.json()
    choices = body.get("choices") or []
    if not choices:
        raise OCRExtractError("No OCR output from model")
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    m = re.search(r"\{[\s\S]*\}", content)
    if m:
        content = m.group(0)
    try:
        out = json.loads(content)
    except json.JSONDecodeError as e:
        raise OCRExtractError(f"Invalid OCR JSON: {e!s}") from e
    if not isinstance(out, dict):
        raise OCRExtractError("OCR output must be an object")
    out.setdefault("raw_text", "")
    out.setdefault("confidence", 0.6)
    return out


async def extract_from_attachment(path: str, content_type: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise OCRExtractError("Attachment file not found")
    ctype = (content_type or "").lower()
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
    raw = p.read_bytes()
    data_url = f"data:{ctype};base64,{base64.b64encode(raw).decode('ascii')}"
    try:
        return await _vision_extract(data_url)
    except Exception:
        return _extract_fields_from_text("")

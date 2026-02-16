"""
Call OpenAI-compatible backends (LM Studio, MetisAI, etc.) to suggest transactions.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, timedelta
from typing import Any

import httpx

from app.core.ai_runtime import resolve_active_ai_backend
from app.core.config import settings


# Thinking models can take 90+ seconds; allow longer and retry on timeout
LM_STUDIO_TIMEOUT = 180.0
LM_STUDIO_MAX_ATTEMPTS = 3
LM_STUDIO_RETRY_DELAY = 3.0


class AISuggestError(Exception):
    """Raised when LM Studio is unreachable or returns invalid data."""
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


def _to_non_negative_int(value: Any) -> int:
    """Best-effort int conversion for model output; invalid values become 0."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _parse_amount_to_int(value: Any) -> int:
    """Parse loose amount formats like 5000, '5k', '4M', '1 million' into integer rials."""
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if value is None:
        return 0
    text = str(value).strip().lower()
    if not text:
        return 0
    text = text.replace(",", "").replace("_", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*([mk])\b", text)
    if m:
        n = float(m.group(1))
        unit = m.group(2)
        return int(n * (1_000 if unit == "k" else 1_000_000))
    m = re.search(r"(\d+(?:\.\d+)?)\s*million\b", text)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"\d+", text)
    if m:
        return int(m.group(0))
    return 0


def _extract_date_from_text(text: str) -> str:
    t = (text or "").strip()
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", t)
    if m:
        return m.group(1)
    return date.today().isoformat()


def _looks_like_opening_balance(text: str) -> bool:
    t = (text or "").lower()
    return any(
        k in t
        for k in (
            "opening balance",
            "initial balance",
            "beginning balance",
            "start balance",
            "openning balance",
        )
    )


def _infer_opening_account_code(text: str, account_codes: set[str]) -> str | None:
    t = (text or "")
    # Prefer explicit account code mention if present.
    for c in re.findall(r"\b\d{4,6}\b", t):
        if c in account_codes:
            return c
    lower = t.lower()
    if any(k in lower for k in ("bank", "cash", "melli", "mellat", "tejarat", "saderat", "saman", "parsian", "pasargad")):
        return "1110" if "1110" in account_codes else None
    if any(k in lower for k in ("receivable", "accounts receivable", "ar")):
        return "1112" if "1112" in account_codes else None
    if any(k in lower for k in ("payable", "accounts payable", "ap")):
        return "2110" if "2110" in account_codes else None
    return None


def _opening_sides_for_account(code: str, text: str) -> tuple[str, str]:
    """Return (debit_code, credit_code) for opening balance using 3110 as balancing equity."""
    lower = (text or "").lower()
    if "credit" in lower or "cr " in lower:
        return "3110", code
    if "debit" in lower or "dr " in lower:
        return code, "3110"
    # Default by account class: assets debit, liabilities/equity/revenue credit.
    if code.startswith(("11", "12")):
        return code, "3110"
    return "3110", code


def _extract_bank_name(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"\b(?:called|named)\s+([A-Za-z][A-Za-z0-9\s]{1,30})\b", text, re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        if name:
            return _normalize_entity_name(name.replace("bank", "").strip())
    for bank_name in ("Melli", "Mellat", "Tejarat", "Saderat", "Saman", "Parsian", "Pasargad", "Sina"):
        if re.search(r"\b" + re.escape(bank_name) + r"\b", text, re.IGNORECASE):
            return bank_name
    m2 = re.search(r"\b([A-Za-z][A-Za-z0-9\s]{1,24})\s+bank\b", text, re.IGNORECASE)
    if m2:
        return _normalize_entity_name(m2.group(1).strip())
    return None


def _try_opening_balance_transaction(
    messages: list[dict[str, str]],
    accounts: list[dict[str, str]],
) -> dict[str, Any] | None:
    if not messages:
        return None
    last_user = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
    if not _looks_like_opening_balance(last_user):
        return None
    amount = _parse_amount_to_int(last_user)
    if amount <= 0:
        return {"message": "What is the opening balance amount (in Rials)?", "transaction": None}
    account_codes = {str(a.get("code") or "").strip() for a in accounts}
    target_code = _infer_opening_account_code(last_user, account_codes)
    if not target_code:
        return {
            "message": "Which account is this opening balance for? Please provide account code (e.g. 1110).",
            "transaction": None,
        }
    if "3110" not in account_codes:
        return {"message": "Opening balance equity account (3110) is missing in chart of accounts.", "transaction": None}
    debit_code, credit_code = _opening_sides_for_account(target_code, last_user)
    d_desc = f"Opening balance for account {target_code}"
    c_desc = "Opening balance offset - capital (3110)"
    bank = _extract_bank_name(last_user)
    description = (
        f"Opening balance for {bank} bank account ({target_code})"
        if bank and target_code == "1110"
        else f"Opening balance for account {target_code}"
    )
    txn = {
        "date": _extract_date_from_text(last_user),
        "reference": None,
        "description": description,
        "new_accounts": [],
        "lines": [
            {"account_code": debit_code, "debit": amount, "credit": 0, "line_description": d_desc if debit_code == target_code else c_desc},
            {"account_code": credit_code, "debit": 0, "credit": amount, "line_description": c_desc if credit_code == "3110" else d_desc},
        ],
    }
    entity_mentions: list[dict[str, str]] = []
    if bank:
        entity_mentions.append({"role": "bank", "name": bank})
    return {"message": "Opening balance voucher prepared.", "transaction": txn, "entity_mentions": entity_mentions}


def _coerce_legacy_transaction_shape(transaction: dict[str, Any]) -> dict[str, Any] | None:
    """
    Recover from malformed model output like:
    {"amount":"5000","line_description":["... (6110)", "... (1110)"], ...}
    """
    if not isinstance(transaction, dict) or transaction.get("lines"):
        return None
    amount = _parse_amount_to_int(transaction.get("amount"))
    if amount <= 0:
        return None
    desc = str(transaction.get("description") or "").strip()
    raw_ld = transaction.get("line_description")
    line_texts: list[str] = []
    if isinstance(raw_ld, list):
        line_texts = [str(x).strip() for x in raw_ld if str(x).strip()]
    elif isinstance(raw_ld, str) and raw_ld.strip():
        line_texts = [raw_ld.strip()]
    codes: list[str] = []
    for txt in line_texts:
        for c in re.findall(r"\b\d{4,6}\b", txt):
            if c not in codes:
                codes.append(c)
    lower_desc = desc.lower()
    is_payment = any(k in lower_desc for k in ("paid", "payed", "payment", "salary", "wage", "expense"))
    is_receipt = any(k in lower_desc for k in ("received", "receipt", "deposit", "sale", "revenue", "income"))

    if "1110" in codes and len(codes) >= 2:
        other = next((c for c in codes if c != "1110"), "6110" if is_payment and not is_receipt else "4110")
        if is_payment and not is_receipt:
            debit_code, credit_code = other, "1110"
        else:
            debit_code, credit_code = "1110", other
    elif len(codes) >= 2:
        debit_code, credit_code = codes[0], codes[1]
    else:
        if is_payment and not is_receipt:
            debit_code, credit_code = "6110", "1110"
        else:
            debit_code, credit_code = "1110", "4110"

    return {
        "date": transaction.get("date") or date.today().isoformat(),
        "reference": transaction.get("reference"),
        "description": desc or None,
        "new_accounts": transaction.get("new_accounts") or [],
        "lines": [
            {
                "account_code": debit_code,
                "debit": amount,
                "credit": 0,
                "line_description": line_texts[0] if line_texts else None,
            },
            {
                "account_code": credit_code,
                "debit": 0,
                "credit": amount,
                "line_description": line_texts[1] if len(line_texts) > 1 else None,
            },
        ],
    }


async def _post_lm_studio(url: str, payload: dict[str, Any], base: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """
    POST to LM Studio with retries on timeout, connection errors, and 503/429.
    Raises AISuggestError with a clear message after retries are exhausted.
    """
    last_error: Exception | None = None
    for attempt in range(LM_STUDIO_MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=LM_STUDIO_TIMEOUT) as client:
                r = await client.post(url, json=payload, headers=headers or None)
                r.raise_for_status()
                return r.json()
        except httpx.TimeoutException as e:
            last_error = e
            if attempt < LM_STUDIO_MAX_ATTEMPTS - 1:
                await asyncio.sleep(LM_STUDIO_RETRY_DELAY)
                continue
            raise AISuggestError(
                "AI backend did not respond in time. The model may be busy or slow; try again."
            ) from e
        except httpx.ConnectError as e:
            last_error = e
            if attempt < LM_STUDIO_MAX_ATTEMPTS - 1:
                await asyncio.sleep(LM_STUDIO_RETRY_DELAY)
                continue
            raise AISuggestError(
                f"Cannot reach AI backend at {base}. Check AI_BASE_URL / LM_STUDIO_BASE_URL."
            ) from e
        except httpx.HTTPStatusError as e:
            if (e.response.status_code in (429, 500, 502, 503, 504)) and attempt < LM_STUDIO_MAX_ATTEMPTS - 1:
                last_error = e
                await asyncio.sleep(LM_STUDIO_RETRY_DELAY)
                continue
            raise AISuggestError(
                f"AI backend returned {e.response.status_code} at {url}. Check model name/API key."
            ) from e
        except httpx.TransportError as e:
            # Covers transient upstream issues such as connection resets / channel errors.
            last_error = e
            if attempt < LM_STUDIO_MAX_ATTEMPTS - 1:
                await asyncio.sleep(LM_STUDIO_RETRY_DELAY)
                continue
            raise AISuggestError(
                "Connection to AI backend was interrupted. Please try again."
            ) from e
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            raise AISuggestError(f"Request to AI backend failed: {e!s}") from e
    raise AISuggestError(
        "AI backend did not respond after retries. Try again later."
    ) from last_error


def _build_account_list(accounts: list[dict[str, str]]) -> str:
    return "\n".join(f"- {a['code']}: {a['name']}" for a in accounts)


def _build_system_prompt(accounts_text: str) -> str:
    return f"""You are an AI accountant. The user describes a financial event in plain language. Produce one voucher that is useful for books and reports (by client, bank, purpose).

OUTPUT FORMAT (you must follow this exactly):
- Your reply must be ONLY one JSON object. No other text, no <think> tags, no reasoning, no explanation.
- Start your response with {{ and end with }}.
- Do not wrap in markdown code blocks. Do not add any text before or after the JSON.

JSON structure:
{{
  "date": "YYYY-MM-DD",
  "reference": "invoice/receipt number if mentioned, else null",
  "description": "clear one-line summary in English (who, amount, purpose, bank if relevant)",
  "new_accounts": [ {{ "code": "new code", "name": "account name" }} ],
  "lines": [
    {{ "account_code": "code", "debit": number, "credit": number, "line_description": "short note for this line when useful (e.g. Deposit from Client - Bank, or Purpose from Client)" }},
    ...
  ]
}}

Accounting rules:
- Prefer account codes from this list when they fit:
{accounts_text}
- If no existing account fits (e.g. rent, marketing), add entries in "new_accounts" and use those codes in "lines". Code format: 2-digit group (11, 61), 4-digit general (1110, 6110), 6-digit sub (611201).
- Double-entry: sum of debits must equal sum of credits.
- When user PAYS money: debit expense/asset, credit cash (1110). When user RECEIVES money: debit cash (1110), credit revenue or liability (e.g. 4110).
- Opening balance: if user says opening/initial balance for an account, create opening entry using 3110 as offset (assets usually debit, liabilities/equity usually credit).
- Amounts in Rials (integer). "1M" = 1000000.
- No date given → use today in YYYY-MM-DD. "new_accounts" can be [] if not needed.
- Fill "reference" when the user mentions an invoice or receipt number. Fill "line_description" for each line when you have context (e.g. bank line: "Deposit from [client] - [bank]"; revenue line: "[purpose] from [client]").

Reply with only the JSON object, starting with {{ and ending with }}."""


def _build_chat_system_prompt(accounts_text: str) -> str:
    return f"""You are an AI accountant. Your job is to gather the information needed to register transactions so the books support useful reports later (by client, by bank, by type of revenue/expense).

Always reply in English only. Your reply must be ONLY a single JSON object. No <think> tags. No explanation before or after.

Information you MUST gather before outputting a voucher:
- Amount (e.g. 4M, 1 million).
- Who: client/customer name (for receipts) or payee (for payments).
- Where the money went or came from: which bank account (e.g. Melli, Tejarat) for receipts; which account or payment method for payments.
- What it was for: purpose or service type (e.g. software development, sale, rent, invoice number). This is needed for reporting and audit.
- Reference (optional but recommended): invoice number, receipt number, or similar so the entry can be traced later.

Ask one or two short questions at a time. Do NOT output the transaction until you have at least: amount, who, which bank (or payment method), and what it was for. If the user gives all of this in one message, then output the transaction.

When you output the transaction:
- Set "reference" to the invoice/receipt number or similar if the user gave one; otherwise null.
- Set "description" to a clear one-line summary (e.g. "Received 4M from Innotech for software development - Melli bank").
- Set "line_description" on each line when useful for reports: e.g. for the bank line (1110): "Deposit from [Client] - [Bank name]"; for the revenue/receivable line (4110/1112): "[Purpose] from [Client]" or "Invoice XYZ".

Available accounts:
{accounts_text}

JSON format:
- To ask a follow-up: {{"message": "Your question in English", "transaction": null}}
- To return the voucher: {{"message": "Short confirmation", "transaction": {{ ... }}, "entity_mentions": [ {{ "role": "client", "name": "Exact name" }}, {{ "role": "bank", "name": "Bank name" }} ] }}
Always include "entity_mentions" when you output a transaction: list every client/customer, bank, payee, or supplier the user mentioned. Use role exactly: "client", "bank", "payee", "supplier". Use the exact name the user gave (e.g. "innotech" → name "Innotech", "Melli bank" → name "Melli"). Example: "received 325M from Innotech to our Melli bank" → entity_mentions: [ {{"role": "client", "name": "Innotech"}}, {{"role": "bank", "name": "Melli"}} ]. The app will create these in Entities if missing and link the voucher to them.

Example questions: "Which bank account was it deposited to?", "What was this for? (e.g. invoice number or type of service)", "Do you have an invoice or receipt number for this?"
Relative dates: If the user says "yesterday" use the date one day before today in YYYY-MM-DD; "last week" = 7 days before today. Put that date in the transaction "date" field.
Important: Output the JSON voucher at the end. Do not use all tokens on reasoning; keep thinking short so the final JSON is always included and not cut off.
Rules: Receipts = debit 1110 (bank), credit 4110 or 1112. Payments = debit expense, credit 1110. Opening balance = use 3110 as offset (asset opening usually debit asset / credit 3110). Amounts in Rials. 1M = 1000000. Always output valid JSON only."""


def _normalize_relative_dates_in_message(text: str) -> str:
    """Replace 'yesterday', 'last week', etc. with actual dates so the model doesn't burn tokens on reasoning."""
    if not text or not text.strip():
        return text
    today = date.today()
    lower = text.strip().lower()
    out = text
    # Replace whole-word "yesterday" with the actual date (YYYY-MM-DD)
    if re.search(r"\byesterday\b", lower):
        yesterday = (today - timedelta(days=1)).isoformat()
        out = re.sub(r"\byesterday\b", yesterday, out, flags=re.IGNORECASE)
    if re.search(r"\blast week\b", lower):
        last_week = (today - timedelta(days=7)).isoformat()
        out = re.sub(r"\blast week\b", last_week, out, flags=re.IGNORECASE)
    if re.search(r"\blast month\b", lower):
        # Approximate: 30 days back
        last_month = (today - timedelta(days=30)).isoformat()
        out = re.sub(r"\blast month\b", last_month, out, flags=re.IGNORECASE)
    if re.search(r"\b3 months ago\b", lower) or re.search(r"\bthree months ago\b", lower):
        three_months = (today - timedelta(days=90)).isoformat()
        out = re.sub(r"\b3 months ago\b", three_months, out, flags=re.IGNORECASE)
        out = re.sub(r"\bthree months ago\b", three_months, out, flags=re.IGNORECASE)
    return out


def _conversation_to_single_description(messages: list[dict[str, str]]) -> str:
    """Combine all user messages into one description for single-shot suggestion. Normalizes relative dates."""
    parts = [m["content"].strip() for m in messages if m.get("role") == "user" and (m.get("content") or "").strip()]
    combined = " . ".join(parts) if parts else ""
    return _normalize_relative_dates_in_message(combined)


def _attachment_summary(attachments: list[dict[str, str]] | None) -> str:
    if not attachments:
        return ""
    names = [str(a.get("file_name") or "attachment").strip() for a in attachments]
    names = [n for n in names if n]
    if not names:
        return "Attachments provided."
    sample = ", ".join(names[:3])
    extra = len(names) - 3
    return f"Attachments provided: {sample}" + (f" (+{extra} more)." if extra > 0 else ".")


def _build_chat_messages(
    system: str,
    messages: list[dict[str, str]],
    attachments: list[dict[str, str]] | None,
    include_images: bool,
) -> list[dict[str, Any]]:
    api_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    last_user_index = -1
    for i, m in enumerate(messages):
        if m.get("role") == "user":
            last_user_index = i
    attachment_note = _attachment_summary(attachments)
    for i, m in enumerate(messages):
        content = m["content"] or ""
        if m.get("role") == "user" and i == last_user_index:
            content = _normalize_relative_dates_in_message(content)
            if attachment_note:
                content = f"{content}\n\n{attachment_note}"
            if include_images and attachments:
                blocks: list[dict[str, Any]] = [{"type": "text", "text": content}]
                for a in attachments[:3]:
                    data_url = (a.get("data_url") or "").strip()
                    if data_url:
                        blocks.append({"type": "image_url", "image_url": {"url": data_url}})
                if len(blocks) > 1:
                    api_messages.append({"role": m["role"], "content": blocks})
                    continue
        api_messages.append({"role": m["role"], "content": content})
    return api_messages


def _parse_entity_mentions(raw: Any) -> list[dict[str, str]]:
    """Extract entity_mentions from AI JSON: list of { role, name } with valid role."""
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    valid_roles = {"client", "bank", "payee", "supplier"}
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = (item.get("role") or "").strip().lower()
        name = (item.get("name") or "").strip()
        if role in valid_roles and name:
            out.append({"role": role, "name": name})
    return out


def _normalize_entity_name(name: str) -> str:
    """Title-case names for consistent matching and display (e.g. 'Ali roshan' -> 'Ali Roshan')."""
    return " ".join(w[:1].upper() + w[1:].lower() for w in (name or "").strip().split() if w)


def _infer_entity_mentions_from_text(transaction: dict[str, Any], last_user_message: str) -> list[dict[str, str]]:
    """
    When the model omits entity_mentions, try to infer from description, line descriptions, and user message.
    Returns list of { role, name } for known patterns (bank names, "paid X", "from X", etc.).
    """
    out: list[dict[str, str]] = []
    desc = (transaction.get("description") or "") + " " + (last_user_message or "")
    for line in transaction.get("lines") or []:
        desc += " " + (line.get("line_description") or "")
    desc = " " + desc
    # Bank: "Melli Bank", "Melli", "Tejarat", "from Mellat", "to our X bank"
    for bank_name in ("Melli", "Tejarat", "Saderat", "Saman", "Parsian", "Pasargad", "Mellat", "Melat", "Sina"):
        if re.search(r"\b" + re.escape(bank_name) + r"\b", desc, re.IGNORECASE):
            out.append({"role": "bank", "name": bank_name})
            break
    # Generic bank phrase fallback: "from X bank", "to X bank"
    if not any(e.get("role") == "bank" for e in out):
        m = re.search(r"(?:from|to)\s+([A-Za-z][A-Za-z0-9\s]{1,30})\s+bank\b", desc, re.IGNORECASE)
        if m:
            name = _normalize_entity_name(m.group(1))
            if name:
                out.append({"role": "bank", "name": name})
    def _add_payee(name: str) -> None:
        name = _normalize_entity_name(name)
        if len(name) < 2 or name.lower() in ("the", "our", "your"):
            return
        # Skip generic phrases that look like line descriptions, not person names
        lower = name.lower()
        if any(w in lower for w in ("payment", "withdrawal", "salary", "wages", "employee salary")):
            return
        if len(name) > 50:
            return
        out.append({"role": "payee", "name": name})

    # Payee/employee: "Payment to Ali Roshan (Employee)", "to employee X via", "Paid Ali roshan my employee"
    m = re.search(r"(?:payment to|to)\s+([A-Za-z][A-Za-z0-9\s]{1,40}?)\s*\(employee\)", desc, re.IGNORECASE)
    if m:
        _add_payee(m.group(1))
    if not any(e.get("role") == "payee" for e in out):
        m = re.search(r"(?:to|payment of[^.]*?)\s+employee\s+([A-Za-z][A-Za-z0-9\s]{1,40}?)(?:\s*\-|\s+from|\s+via|\s+\d|,|\.|$)", desc, re.IGNORECASE)
        if m:
            _add_payee(m.group(1))
    if not any(e.get("role") == "payee" for e in out):
        m = re.search(r"(?:paid|payed)\s+(?:employee\s+)?([A-Za-z][A-Za-z0-9\s]{1,40}?)(?:\s+my employee|\s*\(employee\)|\s+from|\s+\d|\s*\-|,|\.|$)", desc, re.IGNORECASE)
        if m:
            _add_payee(m.group(1))
    if not any(e.get("role") == "payee" for e in out):
        m = re.search(r"(?:wages? payment to|payment to)\s+([A-Za-z][A-Za-z0-9\s]{1,40}?)(?:\s*\-|\s*\(|\s+from|\s+via|\s+\d|,|\.|$)", desc, re.IGNORECASE)
        if m:
            _add_payee(m.group(1))
    # Client: "from X", "received from X", "client X" — only for receipts; exclude bank names and long runs
    bank_words = ("melli", "tejarat", "saderat", "saman", "parsian", "pasargad", "mellat", "melat", "sina", "bank")
    # Stop capture at " bank", " to", " for", digit, so we don't grab "Melli Bank I Payed ..."
    m = re.search(
        r"(?:from|received from|client)\s+([A-Za-z][A-Za-z0-9\s]*?)(?:\s+bank|\s+to|\s+for|\s+\d|\s*\-|,|\.|$)",
        desc,
        re.IGNORECASE,
    )
    if m:
        name = _normalize_entity_name(m.group(1))
        if len(name) >= 2 and name.lower() not in ("the", "our", "your"):
            lower_name = name.lower()
            if not any(w in lower_name.split() for w in bank_words):
                if " bank" not in lower_name and len(name.split()) <= 4:
                    if not any(e.get("name") == name for e in out):
                        out.append({"role": "client", "name": name})
    return out


def _looks_like_complete_description(text: str) -> bool:
    """Heuristic: user likely gave amount + who + bank/purpose so we can try single-shot."""
    if not text or len(text) < 20:
        return False
    lower = text.lower()
    has_amount = bool(re.search(r"\d+\s*[MmKk]|\d+\s*million|received|paid|payment|واریز|دریافت", lower))
    has_who_or_what = any(
        x in lower
        for x in (
            "client", "customer", "from", "to", "bank", "account", "for ", "melli", "software", "sale", "rent",
            "service", "development", "innotech",
        )
    )
    return has_amount and has_who_or_what


def _normalize_transaction_output(out: dict[str, Any]) -> dict[str, Any]:
    """Raise AISuggestError if invalid. Returns dict with date, reference, description, new_accounts, lines."""
    # Parse optional new_accounts
    new_accounts: list[dict[str, str]] = []
    for item in out.get("new_accounts") or []:
        if isinstance(item, dict) and item.get("code") and item.get("name"):
            new_accounts.append({"code": str(item["code"]).strip(), "name": str(item["name"]).strip()})
    out["new_accounts"] = new_accounts
    if isinstance(out.get("date"), str):
        try:
            date.fromisoformat(out["date"])
        except ValueError:
            out["date"] = date.today().isoformat()
    else:
        out["date"] = date.today().isoformat()
    lines = []
    for row in out.get("lines") or []:
        if not isinstance(row, dict) or "account_code" not in row:
            continue
        code = str(row.get("account_code", "")).strip()
        if not code:
            continue
        try:
            debit = int(row.get("debit") or 0)
            credit = int(row.get("credit") or 0)
        except (TypeError, ValueError):
            continue
        if debit < 0:
            debit = 0
        if credit < 0:
            credit = 0
        desc = row.get("line_description")
        line_description = str(desc).strip() or None if desc else None
        lines.append({"account_code": code, "debit": debit, "credit": credit, "line_description": line_description})
    if len(lines) < 2:
        raise AISuggestError("Transaction must have at least two lines (debit and credit).")
    total_d = sum(l["debit"] for l in lines)
    total_c = sum(l["credit"] for l in lines)
    if total_d != total_c:
        diff = total_d - total_c
        if diff > 0:
            lines[-1]["credit"] = (lines[-1].get("credit") or 0) + diff
        else:
            lines[-1]["debit"] = (lines[-1].get("debit") or 0) + (-diff)
    out["lines"] = lines
    ref = out.get("reference")
    if isinstance(ref, str) and ref.strip().lower() in ("null", "none", "n/a", "na", "-"):
        ref = None
    out["reference"] = ref or None
    out["description"] = out.get("description") or None
    return out


async def suggest_transaction(user_message: str, accounts: list[dict[str, str]]) -> dict[str, Any]:
    """
    Call AI backend chat/completions and return a parsed suggested transaction.
    Raises AISuggestError with a clear message if backend is off or response is invalid.
    """
    base, model = _resolve_ai_base_model()
    headers = _resolve_ai_headers()
    if not base:
        raise AISuggestError(
            "AI base URL is not set. Configure AI_BASE_URL (or LM_STUDIO_BASE_URL) in .env."
        )
    url = _chat_completions_url(base)
    accounts_text = _build_account_list(accounts)
    system = _build_system_prompt(accounts_text)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": 4096,
        "temperature": 0.2,
    }
    data = await _post_lm_studio(url, payload, base, headers=headers)
    choices = data.get("choices") or []
    if not choices:
        raise AISuggestError(
            "AI backend returned no choices. Make sure model is available and API key is valid."
        )
    content = (choices[0].get("message") or {}).get("content") or ""
    content = content.strip()
    if not content:
        raise AISuggestError("AI backend returned an empty response. Try a different prompt/model.")
    # Strip <think>...</think> block (Qwen "thinking" models put reasoning there)
    content = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()
    # Strip markdown code block if present
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if m:
        content = m.group(1).strip()
    # If content has text before JSON (e.g. unclosed <think> block), extract the first {...} object
    if not content.strip().startswith("{"):
        brace = content.find("{")
        if brace < 0:
            raise AISuggestError(
                "Model returned no JSON object (response may be truncated or only reasoning). "
                "Try again or increase max_tokens. Reply must start with { and contain a 'lines' array."
            )
        depth = 0
        end = -1
        for i in range(brace, len(content)):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end >= 0:
            content = content[brace : end + 1]
        else:
            raise AISuggestError(
                "Model output contained { but no complete JSON object (truncated?). "
                "Try again or increase max_tokens."
            )
    try:
        out = json.loads(content)
    except json.JSONDecodeError as e:
        raise AISuggestError(
            f"Model output is not valid JSON: {e!s}. Raw (first 200 chars): {content[:200]!r}"
        ) from e
    if not isinstance(out, dict):
        raise AISuggestError(
            "Model output must be a JSON object. "
            f"Got: {type(out).__name__}"
        )
    # Model sometimes returns a single line object instead of { date, lines } — normalize it
    if "lines" not in out and "account_code" in out:
        single = out
        debit = _to_non_negative_int(single.get("debit"))
        credit = _to_non_negative_int(single.get("credit"))
        # Balance with cash (1110): add the missing side so debits = credits
        total_d = debit
        total_c = credit
        if total_d != total_c:
            if total_d > total_c:
                out = {
                    "date": date.today().isoformat(),
                    "reference": None,
                    "description": single.get("description") or single.get("line_description"),
                    "new_accounts": [],
                    "lines": [
                        {"account_code": str(single.get("account_code", "")).strip(), "debit": debit, "credit": credit, "line_description": single.get("line_description")},
                        {"account_code": "1110", "debit": 0, "credit": total_d - total_c, "line_description": None},
                    ],
                }
            else:
                out = {
                    "date": date.today().isoformat(),
                    "reference": None,
                    "description": single.get("description") or single.get("line_description"),
                    "new_accounts": [],
                    "lines": [
                        {"account_code": str(single.get("account_code", "")).strip(), "debit": debit, "credit": credit, "line_description": single.get("line_description")},
                        {"account_code": "1110", "debit": total_c - total_d, "credit": 0, "line_description": None},
                    ],
                }
        else:
            raise AISuggestError(
                "Model returned a single line but debits equal credits (need two sides). "
                "Expected object with 'lines' array (e.g. [{\"account_code\": \"6110\", \"debit\": 500000, \"credit\": 0}, {\"account_code\": \"1110\", \"debit\": 0, \"credit\": 500000}])."
            )
    elif "lines" not in out:
        coerced = _coerce_legacy_transaction_shape(out)
        if coerced is None:
            raise AISuggestError(
                "Model output must be a JSON object with a 'lines' array. "
                f"Got keys: {list(out.keys())}"
            )
        out = coerced
    return _normalize_transaction_output(out)


async def chat_turn(
    messages: list[dict[str, str]],
    accounts: list[dict[str, str]],
    attachment_context: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Multi-turn chat: model may ask which client, which bank, what for.
    Returns {"message": str, "transaction": dict | None}. Raises AISuggestError on failure.
    """
    opening = _try_opening_balance_transaction(messages, accounts)
    if opening is not None:
        return opening

    base, model = _resolve_ai_base_model()
    headers = _resolve_ai_headers()
    if not base:
        raise AISuggestError("AI base URL is not set. Configure AI_BASE_URL (or LM_STUDIO_BASE_URL) in .env.")
    url = _chat_completions_url(base)
    accounts_text = _build_account_list(accounts)
    system = _build_chat_system_prompt(accounts_text)
    api_messages = _build_chat_messages(system, messages, attachment_context, include_images=True)
    payload = {
        "model": model,
        "messages": api_messages,
        "max_tokens": 4096,
        "temperature": 0.2,
    }
    try:
        data = await _post_lm_studio(url, payload, base, headers=headers)
    except AISuggestError:
        # Retry once without image blocks for text-only models.
        if attachment_context:
            payload["messages"] = _build_chat_messages(system, messages, attachment_context, include_images=False)
            data = await _post_lm_studio(url, payload, base, headers=headers)
        else:
            raise
    choices = data.get("choices") or []
    if not choices:
        raise AISuggestError("AI backend returned no choices.")
    content = (choices[0].get("message") or {}).get("content") or ""
    content = content.strip()
    if not content:
        raise AISuggestError("AI backend returned an empty response.")

    # Strip <think>...</think> and think>... blocks (reasoning must not be shown to user)
    content = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()
    # Strip "think> ... " up to the first "{" (JSON start) or end
    content = re.sub(r"think>[\s\S]*?(?=\{|$)", "", content, flags=re.IGNORECASE).strip()
    if not content:
        # Response was truncated or all in think block. Try single-shot from conversation first.
        combined = _conversation_to_single_description(messages)
        if _looks_like_complete_description(combined):
            try:
                single = await suggest_transaction(combined, accounts)
                em = _infer_entity_mentions_from_text(single, combined)
                return {"message": "Here's the voucher based on what you said.", "transaction": single, "entity_mentions": em}
            except AISuggestError:
                pass
        last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "") or ""
        if any(x in last_user.lower() for x in ("client", "received", "مشتری", "دریافت", "واریز", "payment")):
            return {"message": "What was this payment for? (e.g. sale, service, advance)", "transaction": None}
        return {"message": "I didn't understand. Please say again.", "transaction": None}
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if m:
        content = m.group(1).strip()
    if not content.strip().startswith("{"):
        brace = content.find("{")
        if brace < 0:
            combined = _conversation_to_single_description(messages)
            if _looks_like_complete_description(combined):
                try:
                    single = await suggest_transaction(combined, accounts)
                    em = _infer_entity_mentions_from_text(single, combined)
                    return {"message": "Here's the voucher based on what you said.", "transaction": single, "entity_mentions": em}
                except AISuggestError:
                    pass
            return {"message": "I didn't understand. Please say again.", "transaction": None}
        depth = 0
        end = -1
        for i in range(brace, len(content)):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end >= 0:
            content = content[brace : end + 1]

    try:
        out = json.loads(content)
    except json.JSONDecodeError:
        combined = _conversation_to_single_description(messages)
        if _looks_like_complete_description(combined):
            try:
                single = await suggest_transaction(combined, accounts)
                em = _infer_entity_mentions_from_text(single, combined)
                return {"message": "Here's the voucher based on what you said.", "transaction": single, "entity_mentions": em}
            except AISuggestError:
                pass
        return {"message": "I didn't understand. Please say again.", "transaction": None}

    if not isinstance(out, dict):
        combined = _conversation_to_single_description(messages)
        if _looks_like_complete_description(combined):
            try:
                single = await suggest_transaction(combined, accounts)
                em = _infer_entity_mentions_from_text(single, combined)
                return {"message": "Here's the voucher based on what you said.", "transaction": single, "entity_mentions": em}
            except AISuggestError:
                pass
        return {"message": "I didn't understand. Please say again.", "transaction": None}
    # Model sometimes returns bare transaction (no "message", no "transaction" wrapper)
    if out.get("message") is None and out.get("transaction") is None and "lines" in out:
        out = {"message": "Here's the voucher based on what you said.", "transaction": out}
    msg = out.get("message") or "OK."
    if isinstance(msg, str):
        message_text = msg.strip()
    else:
        message_text = str(msg).strip() or "OK."
    # Don't show the literal prompt placeholder to the user
    if "سؤال یا درخواست" in message_text or ("question" in message_text.lower() and "user" in message_text.lower() and "e.g." in message_text):
        message_text = "Which client? Which bank account did it go to? What was it for?"

    transaction = out.get("transaction")
    if transaction is None or not isinstance(transaction, dict):
        # Fallback: when the chat model didn't return a transaction but the user gave full details, try single-shot
        combined = _conversation_to_single_description(messages)
        if _looks_like_complete_description(combined):
            try:
                single = await suggest_transaction(combined, accounts)
                em = _infer_entity_mentions_from_text(single, combined)
                return {"message": "Here's the voucher based on what you said.", "transaction": single, "entity_mentions": em}
            except AISuggestError:
                pass
        return {"message": message_text, "transaction": None}
    if "lines" not in transaction and "account_code" in transaction:
        single = transaction
        debit = _to_non_negative_int(single.get("debit"))
        credit = _to_non_negative_int(single.get("credit"))
        total_d, total_c = debit, credit
        if total_d != total_c:
            code = str(single.get("account_code", "")).strip()
            if total_d > total_c:
                transaction = {
                    "date": date.today().isoformat(),
                    "reference": None,
                    "description": single.get("description") or single.get("line_description"),
                    "new_accounts": [],
                    "lines": [
                        {"account_code": code, "debit": debit, "credit": credit, "line_description": single.get("line_description")},
                        {"account_code": "1110", "debit": 0, "credit": total_d - total_c, "line_description": None},
                    ],
                }
            else:
                transaction = {
                    "date": date.today().isoformat(),
                    "reference": None,
                    "description": single.get("description") or single.get("line_description"),
                    "new_accounts": [],
                    "lines": [
                        {"account_code": code, "debit": debit, "credit": credit, "line_description": single.get("line_description")},
                        {"account_code": "1110", "debit": total_c - total_d, "credit": 0, "line_description": None},
                    ],
                }
    if "lines" not in transaction:
        coerced = _coerce_legacy_transaction_shape(transaction)
        if coerced is not None:
            transaction = coerced
    try:
        normalized = _normalize_transaction_output(transaction)
    except AISuggestError:
        combined = _conversation_to_single_description(messages)
        if _looks_like_complete_description(combined):
            try:
                single = await suggest_transaction(combined, accounts)
                em = _infer_entity_mentions_from_text(single, combined)
                return {"message": "Here's the voucher based on what you said.", "transaction": single, "entity_mentions": em}
            except AISuggestError:
                pass
        return {"message": message_text, "transaction": None}
    entity_mentions = _parse_entity_mentions(out.get("entity_mentions"))
    if not entity_mentions:
        last_user = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
        entity_mentions = _infer_entity_mentions_from_text(
            {"description": normalized.get("description"), "lines": normalized.get("lines") or []},
            last_user,
        )
    return {"message": message_text, "transaction": normalized, "entity_mentions": entity_mentions}


def _build_edit_system_prompt() -> str:
    return """You extract transaction edit intents from chat messages.
Reply with ONLY one JSON object (no markdown, no extra text).

Schema:
{
  "intent": "edit_transaction" | "other",
  "search": {
    "transaction_id": "uuid or null",
    "date": "YYYY-MM-DD or null",
    "reference": "string or null",
    "description_contains": "string or null",
    "entity_name": "string or null"
  },
  "changes": {
    "date": "YYYY-MM-DD | null | omitted",
    "reference": "string | null | omitted",
    "description": "string | null | omitted",
    "amount": number | null | omitted
  },
  "entity_updates": [
    { "role": "client|bank|payee|supplier", "name": "entity name" }
  ]
}

Rules:
- Set intent=edit_transaction only if user asks to change/fix/update/correct an existing transaction.
- Put null when user explicitly asks to clear a field (e.g. remove reference).
- If not sure about a value, keep it null/omitted.
- Do not invent IDs.
"""


def _normalize_edit_intent(out: dict[str, Any]) -> dict[str, Any]:
    valid_roles = {"client", "bank", "payee", "supplier"}
    result: dict[str, Any] = {
        "intent": "other",
        "search": {},
        "changes": {},
        "entity_updates": [],
    }
    intent = str(out.get("intent") or "").strip().lower()
    if intent == "edit_transaction":
        result["intent"] = "edit_transaction"
    search = out.get("search") if isinstance(out.get("search"), dict) else {}
    if isinstance(search, dict):
        txid = search.get("transaction_id")
        if isinstance(txid, str) and txid.strip():
            result["search"]["transaction_id"] = txid.strip()
        d = search.get("date")
        if isinstance(d, str) and d.strip():
            result["search"]["date"] = d.strip()
        ref = search.get("reference")
        if isinstance(ref, str) and ref.strip():
            result["search"]["reference"] = ref.strip()
        desc = search.get("description_contains")
        if isinstance(desc, str) and desc.strip():
            result["search"]["description_contains"] = desc.strip()
        en = search.get("entity_name")
        if isinstance(en, str) and en.strip():
            result["search"]["entity_name"] = en.strip()
    changes = out.get("changes") if isinstance(out.get("changes"), dict) else {}
    if isinstance(changes, dict):
        for k in ("date", "reference", "description", "amount"):
            if k in changes:
                v = changes.get(k)
                if v is None:
                    result["changes"][k] = None
                elif isinstance(v, (str, int, float)):
                    result["changes"][k] = v
    raw_updates = out.get("entity_updates")
    if isinstance(raw_updates, list):
        for u in raw_updates:
            if not isinstance(u, dict):
                continue
            role = str(u.get("role") or "").strip().lower()
            name = str(u.get("name") or "").strip()
            if role in valid_roles and name:
                result["entity_updates"].append({"role": role, "name": _normalize_entity_name(name)})
    return result


def _contains_edit_verb(text: str) -> bool:
    lower = (text or "").lower()
    return any(k in lower for k in ("edit", "update", "change", "fix", "correct", "set "))


def _is_relative_date_phrase(text: str) -> bool:
    lower = (text or "").lower()
    return any(k in lower for k in ("today", "yesterday", "last week", "this week", "last month"))


def _fallback_edit_intent(messages: list[dict[str, str]]) -> dict[str, Any]:
    last_user = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
    text = (last_user or "").strip()
    lower = text.lower()
    user_msgs = [(m.get("content") or "") for m in messages if m.get("role") == "user"]
    assistant_msgs = [(m.get("content") or "") for m in messages if m.get("role") == "assistant"]
    prior_edit_context = any(_contains_edit_verb(x) for x in user_msgs[-4:])
    assistant_requested_edit_details = any(
        "transaction you want to edit" in (a or "").lower() or "provide the details" in (a or "").lower()
        for a in assistant_msgs[-2:]
    )
    has_identifier_cue = bool(
        re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
        or re.search(r"\b(?:ref(?:erence)?|invoice)\b", lower)
        or any(k in lower for k in ("client", "bank", "payee", "supplier", "transaction", "entry"))
        or _is_relative_date_phrase(text)
        or lower in ("it", "this one", "that one", "that transaction")
    )
    has_edit_verb = _contains_edit_verb(text) or (
        has_identifier_cue and (prior_edit_context or assistant_requested_edit_details)
    )
    if not has_edit_verb:
        return {"intent": "other", "search": {}, "changes": {}, "entity_updates": []}
    out: dict[str, Any] = {"intent": "edit_transaction", "search": {}, "changes": {}, "entity_updates": []}
    m_date = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if m_date:
        out["search"]["date"] = m_date.group(1)
    elif _is_relative_date_phrase(text):
        if "last week" in lower:
            out["search"]["date"] = "last week"
        elif "this week" in lower:
            out["search"]["date"] = "this week"
        elif "yesterday" in lower:
            out["search"]["date"] = "yesterday"
        elif "today" in lower:
            out["search"]["date"] = "today"
        elif "last month" in lower:
            out["search"]["date"] = "last month"
    m_ref = re.search(r"\b(?:ref(?:erence)?|invoice)\s*[:#]?\s*([A-Za-z0-9\-_\/]+)", text, re.IGNORECASE)
    if m_ref:
        out["search"]["reference"] = m_ref.group(1).strip()
    m_desc = re.search(r"(?:transaction|entry)\s+(?:for|with)\s+(.+?)(?:\s+and\s+|$)", text, re.IGNORECASE)
    if m_desc:
        out["search"]["description_contains"] = m_desc.group(1).strip()
    for role in ("client", "bank", "payee", "supplier"):
        m_role = re.search(rf"(?:set|change|update)\s+{role}\s+(?:to\s+)?([A-Za-z0-9][A-Za-z0-9\s\-]{{1,60}})", text, re.IGNORECASE)
        if m_role:
            out["entity_updates"].append({"role": role, "name": _normalize_entity_name(m_role.group(1).strip())})
    m_new_ref = re.search(r"(?:set|change|update)\s+reference\s+(?:to\s+)?([A-Za-z0-9\-_\/]+)", text, re.IGNORECASE)
    if m_new_ref:
        out["changes"]["reference"] = m_new_ref.group(1).strip()
    m_new_desc = re.search(r"(?:set|change|update)\s+description\s+(?:to\s+)?(.+)$", text, re.IGNORECASE)
    if m_new_desc:
        out["changes"]["description"] = m_new_desc.group(1).strip()
    m_amount = re.search(r"(?:set|change|update)\s+amount\s+(?:to\s+)?([\d,\.]+\s*[mkMK]?)", text, re.IGNORECASE)
    if m_amount:
        out["changes"]["amount"] = _parse_amount_to_int(m_amount.group(1))
    return out


async def parse_transaction_edit_intent(messages: list[dict[str, str]]) -> dict[str, Any]:
    """
    Parse whether the user is trying to edit an existing transaction and what to search/change.
    Returns normalized dict: intent/search/changes/entity_updates.
    """
    if not messages:
        return {"intent": "other", "search": {}, "changes": {}, "entity_updates": []}
    last_user = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
    base, model = _resolve_ai_base_model()
    headers = _resolve_ai_headers()
    if not base:
        return _fallback_edit_intent(messages)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _build_edit_system_prompt()},
            {"role": "user", "content": "\n".join([f"{m.get('role','user')}: {m.get('content','')}" for m in messages[-8:]])},
        ],
        "max_tokens": 800,
        "temperature": 0.0,
    }
    try:
        data = await _post_lm_studio(_chat_completions_url(base), payload, base, headers=headers)
        choices = data.get("choices") or []
        if not choices:
            return _fallback_edit_intent(messages)
        content = ((choices[0].get("message") or {}).get("content") or "").strip()
        content = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
        if m:
            content = m.group(1).strip()
        if not content.startswith("{"):
            s = content.find("{")
            e = content.rfind("}")
            if s >= 0 and e > s:
                content = content[s : e + 1]
        out = json.loads(content)
        if not isinstance(out, dict):
            return _fallback_edit_intent(messages)
        normalized = _normalize_edit_intent(out)
        if normalized.get("intent") != "edit_transaction":
            fb = _fallback_edit_intent(messages)
            if fb.get("intent") == "edit_transaction":
                return fb
        return normalized
    except Exception:
        return _fallback_edit_intent(messages)

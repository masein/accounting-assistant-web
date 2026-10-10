"""Typed reply blocks for chat clients (roadmap ROADMAP_ANDROID_CHAT P0.3).

A reply is a list of blocks the phone draws natively: the data the tools
read (a figure, a table), the vouchers to confirm, an import's review card,
then the accountant's short sentence. The numbers in a block come from the
tool results and the ledger, never from the model's prose; the model only
decides which tools to call.

Every block carries ``type``, a stable ``id`` and a ``fallback_text``: an older
app, a bot or a screen reader still gets a sentence for a block it can't draw.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

# Data blocks drawn per reply at most, newest tool calls first kept.
MAX_DATA_BLOCKS = 3


def _get(obj: Any, key: str, default=None):
    """Proposals arrive as dicts (orchestrator) or models (the API's ChatProposal)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)[:10]
    try:
        date.fromisoformat(text)
    except ValueError:
        return None
    return text


def display_date(iso: str | None, calendar: str, lang: str) -> str | None:
    """The date as this company writes it: «۱۸ مهر ۱۴۰۵» or "10 Oct 2026"."""
    if not iso:
        return None
    d = date.fromisoformat(iso)
    if calendar == "jalali":
        from app.utils.jalali import JALALI_MONTH_NAMES_EN, format_jalali_long, gregorian_to_jalali
        if lang == "fa":
            return format_jalali_long(d)
        y, m, day = gregorian_to_jalali(d)
        return f"{day} {JALALI_MONTH_NAMES_EN[m - 1]} {y}"
    return f"{d.day} {d.strftime('%b %Y')}"


def _account_names(db: Session, codes: Iterable[str]) -> dict[str, str]:
    from app.models.account import Account
    codes = sorted({str(c) for c in codes if c})
    if not codes:
        return {}
    rows = db.execute(select(Account.code, Account.name).where(Account.code.in_(codes))).all()
    return {str(code): name for code, name in rows}


# --- proposals ------------------------------------------------------------------------

_EDITABLE = {"propose_create_transaction"}           # kept in step with proposal_edit.EDITABLE (a test checks)


def proposal_block(db: Session, p: Any, *, calendar: str, lang: str, names: dict[str, str] | None = None) -> dict:
    """A voucher to confirm: what, how much, which accounts, when."""
    preview = _get(p, "preview") or {}
    summary = (_get(p, "summary") or "").strip()
    lines_in = preview.get("lines") if isinstance(preview, dict) else None
    lines = []
    amount = None
    if isinstance(lines_in, list) and lines_in:
        names = names if names is not None else _account_names(db, (ln.get("account_code") for ln in lines_in))
        for ln in lines_in:
            code = str(ln.get("account_code") or "")
            lines.append({"account": code, "name": names.get(code, code),
                          "debit": int(ln.get("debit") or 0), "credit": int(ln.get("credit") or 0)})
        amount = sum(x["debit"] for x in lines)
    elif isinstance(preview, dict):
        for key in ("amount", "total", "gross", "net"):
            if isinstance(preview.get(key), (int, float)):
                amount = int(preview[key])
                break
    currency = (preview.get("currency") if isinstance(preview, dict) else None) or None
    if currency is None and amount is not None:
        from app.services.fx_base import base_currency
        currency = base_currency(db)
    iso = None
    if isinstance(preview, dict):
        for key in ("date", "issue_date", "pay_date", "due_date", "on"):
            iso = _iso(preview.get(key))
            if iso:
                break
    title = (preview.get("description") if isinstance(preview, dict) else None) or (summary.splitlines() or [""])[0]
    token = str(_get(p, "confirmation_token"))
    needs_approval = bool(_get(p, "needs_approval"))
    return {
        "type": "proposal",
        "id": f"proposal:{token}",
        "token": token,
        "tool": _get(p, "tool_name") or "",
        "title": title,
        "summary": summary,
        "amount": {"value": amount, "currency": currency} if amount is not None else None,
        "date": {"iso": iso, "display": display_date(iso, calendar, lang)} if iso else None,
        "lines": lines,
        "new_entities": list(_get(p, "new_entities") or []),
        "needs_approval": needs_approval,
        "approval_threshold": _get(p, "approval_threshold"),
        "expires_at": _get(p, "expires_at"),
        # Edit only where the phone can change it (proposal_edit.EDITABLE)
        "actions": ["confirm", "edit", "cancel"] if _get(p, "tool_name") in _EDITABLE else ["confirm", "cancel"],
        "fallback_text": summary or title,
    }


# --- data the tools read ------------------------------------------------------------------

def _balance_figure(result: dict, **_) -> dict | None:
    if "balance" not in result:
        return None
    return {"type": "figure", "label": f"{result.get('account_code', '')} {result.get('account_name', '')}".strip(),
            "value": int(result["balance"]), "currency": result.get("currency"),
            "as_of": result.get("as_of"),
            "fallback_text": f"{result.get('account_name', '')}: {int(result['balance']):,} {result.get('currency') or ''}".strip()}


def _cash_figure(result: dict, lang: str = "en", **_) -> dict | None:
    """get_cash_position: every cash and bank account together, with each one."""
    if "total" not in result:
        return None
    value = int(result["total"])
    label = {"fa": "موجودی نقد و بانک", "es": "Efectivo y bancos", "ar": "النقد والبنوك"}.get(lang, "Cash and bank")
    return {"type": "figure", "label": label, "value": value, "currency": result.get("currency"),
            "as_of": result.get("as_of"),
            "breakdown": [{"label": a.get("account_name") or a.get("account_code"), "value": int(a.get("balance") or 0)}
                          for a in result.get("accounts") or []],
            "fallback_text": f"{label}: {value:,} {result.get('currency') or ''}".strip()}


def _spending_table(result: dict, **_) -> dict | None:
    """get_spending_summary: the period's total and its biggest categories."""
    rows = result.get("by_category")
    if not isinstance(rows, list) or not rows:
        return None
    out = [{"label": r.get("category_name") or r.get("category_code"), "sub": r.get("category_code"),
            "value": int(r.get("amount") or 0), "currency": result.get("currency")} for r in rows[:8]]
    return {"type": "table", "kind": "spending", "rows": out,
            "totals": {"total": result.get("total"), "currency": result.get("currency")},
            "fallback_text": "; ".join(f"{r['label']} {r['value']:,}" for r in out)}


def _invoice_table(result: dict, **_) -> dict | None:
    rows = result.get("invoices")
    if not isinstance(rows, list) or not rows:
        return None
    out = []
    for inv in rows[:8]:
        due = inv.get("balance_due", inv.get("amount_due", inv.get("amount")))
        out.append({"label": inv.get("party") or inv.get("entity_name") or inv.get("number") or "",
                    "sub": inv.get("number"), "value": int(due or 0), "currency": inv.get("currency"),
                    "due_date": inv.get("due_date"), "state": inv.get("status")})
    return {"type": "table", "kind": "invoices", "rows": out, "count": int(result.get("count") or len(rows)),
            "totals": result.get("totals_by_currency") or {},
            "fallback_text": "; ".join(f"{r['label']} {r['value']:,}" for r in out)}


def _budget_table(result: dict, **_) -> dict | None:
    rows = result.get("budgets")
    if not isinstance(rows, list) or not rows:
        return None
    out = [{"label": b.get("category"), "value": int(b.get("actual") or 0), "limit": int(b.get("budget") or 0),
            "used_pct": b.get("used_pct"), "state": b.get("state")} for b in rows[:8]]
    return {"type": "table", "kind": "budgets", "rows": out,
            "totals": {"budget": result.get("total_budget"), "actual": result.get("total_actual")},
            "fallback_text": "; ".join(f"{r['label']} {r['used_pct']}%" for r in out)}


def invoice_file(invoice_id, number) -> dict:
    """An invoice as a file the phone can open or share."""
    name = f"invoice-{number or str(invoice_id)[:8]}.pdf"
    return {"type": "file", "id": f"file:invoice:{invoice_id}", "name": name, "mime": "application/pdf",
            "path": f"/api/mobile/v1/documents/invoices/{invoice_id}",
            "fallback_text": f"Invoice {number} (PDF)" if number else "Invoice (PDF)"}


def _invoice_file(result: dict, **_) -> dict | None:
    """get_invoice: the invoice itself, as its PDF."""
    if not result.get("id"):
        return None
    return invoice_file(result["id"], result.get("number"))


RENDERERS = {
    "get_account_balance": _balance_figure,
    "get_cash_position": _cash_figure,
    "list_invoices": _invoice_table,
    "get_budget_status": _budget_table,
    "get_spending_summary": _spending_table,
    "get_invoice": _invoice_file,
}


def data_blocks(tool_calls: list[dict] | None, lang: str = "en") -> list[dict]:
    """The last successful call of each drawable read tool, in call order."""
    latest: dict[str, tuple[int, dict]] = {}
    for i, call in enumerate(tool_calls or []):
        name = call.get("name")
        result = call.get("result")
        if name in RENDERERS and isinstance(result, dict):
            latest[name] = (i, call)
    picked = sorted(latest.values(), key=lambda t: t[0])[-MAX_DATA_BLOCKS:]
    out = []
    for _i, call in picked:
        block = RENDERERS[call["name"]](call["result"], lang=lang)
        if block is not None:
            block.setdefault("id", f"{call['name']}:{call.get('tool_use_id') or uuid.uuid4().hex[:8]}")
            block["tool"] = call["name"]
            out.append(block)
    return out


# --- the whole reply ----------------------------------------------------------------------

def build_blocks(db: Session, *, text: str | None, proposals: list | None, tool_calls: list[dict] | None,
                 intake: dict | None, lang: str = "en", calendar: str | None = None) -> list[dict]:
    """Cards first, then the words (the block comes before the sentence)."""
    if calendar is None:
        from app.services.locale_service import get_display_calendar
        calendar = get_display_calendar(db)
    blocks = data_blocks(tool_calls, lang=lang)
    if intake:
        blocks.append({"type": "intake", "id": f"intake:{intake.get('kind')}:{intake.get('statement_id') or intake.get('batch_id') or ''}",
                       **intake, "fallback_text": text or ""})
    all_lines = [ln for p in (proposals or []) for ln in ((_get(p, "preview") or {}).get("lines") or [])
                 if isinstance(ln, dict)]
    names = _account_names(db, (ln.get("account_code") for ln in all_lines))
    for p in proposals or []:
        blocks.append(proposal_block(db, p, calendar=calendar, lang=lang, names=names))
    if text and text.strip():
        blocks.append({"type": "text", "id": f"text:{uuid.uuid4().hex[:8]}", "text": text.strip(),
                       "fallback_text": text.strip()})
    return blocks


def posted_block(*, token: str, transaction_id: str | None, audit_log_id: str, voucher: str | None,
                 date_iso: str | None, calendar: str, lang: str, undo_seconds: int, file: dict | None = None) -> dict:
    """What a confirmed voucher becomes: stamped, with the undo window, and
    the document it made (an invoice's PDF) when there is one."""
    return {
        "file": file,
        "type": "posted", "id": f"posted:{token}", "token": token,
        "transaction_id": transaction_id, "audit_log_id": audit_log_id,
        "voucher": voucher, "date": {"iso": date_iso, "display": display_date(date_iso, calendar, lang)} if date_iso else None,
        "undo_seconds": undo_seconds,
        "actions": ["undo"] if undo_seconds > 0 else ["reverse"],
        "fallback_text": "Posted" + (f" · {voucher}" if voucher else ""),
    }
